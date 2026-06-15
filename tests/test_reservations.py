import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from reservationbot.authz import AuthorizationService
from reservationbot.models import (
    AccessPolicy,
    Actor,
    PrincipalSet,
    Reservation,
    ReservationRequest,
    Resource,
    ResourceKind,
)
from reservationbot.repository import InMemoryReservationRepository
from reservationbot.reservations import ReservationService


TENANT = "slack:T123"
CHANNEL = "C123"


def actor(**overrides):
    values = {
        "tenant_id": TENANT,
        "slack_user_id": "U123",
        "slack_channel_id": CHANNEL,
        "is_workspace_admin": False,
        "user_group_ids": frozenset(),
        "managed_channel_ids": frozenset(),
    }
    values.update(overrides)
    return Actor(**values)


def resource(resource_id, kind=ResourceKind.SPECIFIC, **overrides):
    values = {
        "tenant_id": TENANT,
        "resource_id": resource_id,
        "channel_id": CHANNEL,
        "name": resource_id,
        "kind": kind,
        "capacity": 1,
    }
    values.update(overrides)
    return Resource(**values)


def reservation(resource_id, start=1000, end=5000, quantity=1, tenant_id=TENANT):
    return Reservation.create(
        tenant_id=tenant_id,
        resource_id=resource_id,
        channel_id=CHANNEL,
        owner_user_id="U999",
        start_epoch=start,
        end_epoch=end,
        quantity=quantity,
    )


def service(resources, reservations=()):
    repo = InMemoryReservationRepository(resources=resources, reservations=reservations)
    return ReservationService(repo, AuthorizationService(repo))


class RecordingRefresher:
    def __init__(self):
        self.calls = []

    def refresh_schedules_for_reservation(self, reservation, now_epoch):
        self.calls.append((reservation, now_epoch))
        return []


def request(resource_id, start=2000, end=4000, quantity=1, tenant_id=TENANT):
    return ReservationRequest(
        tenant_id=tenant_id,
        resource_id=resource_id,
        channel_id=CHANNEL,
        owner_user_id="U123",
        start_epoch=start,
        end_epoch=end,
        quantity=quantity,
    )


class ReservationServiceTests(unittest.TestCase):
    def test_specific_resource_rejects_overlapping_reservation(self):
        svc = service([resource("printer")], [reservation("printer")])

        with self.assertRaisesRegex(ValueError, "already reserved"):
            svc.create_reservation(actor(), request("printer"))

    def test_specific_resource_allows_adjacent_reservation(self):
        svc = service([resource("printer")], [reservation("printer", start=1000, end=5000)])

        created = svc.create_reservation(actor(), request("printer", start=5000, end=7000))

        self.assertEqual(created.resource_id, "printer")

    def test_fungible_resource_allows_capacity_until_full(self):
        parking = resource("parking", ResourceKind.FUNGIBLE, capacity=3)
        svc = service(
            [parking],
            [
                reservation("parking", quantity=1),
                reservation("parking", quantity=1),
            ],
        )

        created = svc.create_reservation(actor(), request("parking", quantity=1))

        self.assertEqual(created.quantity, 1)

    def test_fungible_resource_rejects_request_over_capacity(self):
        parking = resource("parking", ResourceKind.FUNGIBLE, capacity=2)
        svc = service([parking], [reservation("parking", quantity=2)])

        with self.assertRaisesRegex(ValueError, "not enough capacity"):
            svc.create_reservation(actor(), request("parking", quantity=1))

    def test_container_reservation_blocks_child_resource(self):
        room = resource("room", ResourceKind.CONTAINER)
        seat = resource("seat-a", ResourceKind.SPECIFIC, parent_resource_id="room")
        svc = service([room, seat], [reservation("room")])

        with self.assertRaisesRegex(ValueError, "enclosing"):
            svc.create_reservation(actor(), request("seat-a"))

    def test_child_reservation_blocks_container_resource(self):
        room = resource("room", ResourceKind.CONTAINER)
        seat = resource("seat-a", ResourceKind.SPECIFIC, parent_resource_id="room")
        svc = service([room, seat], [reservation("seat-a")])

        with self.assertRaisesRegex(ValueError, "enclosing"):
            svc.create_reservation(actor(), request("room"))

    def test_sibling_child_resources_do_not_conflict(self):
        room = resource("room", ResourceKind.CONTAINER)
        left = resource("left-table", ResourceKind.SPECIFIC, parent_resource_id="room")
        right = resource("right-table", ResourceKind.SPECIFIC, parent_resource_id="room")
        svc = service([room, left, right], [reservation("left-table")])

        created = svc.create_reservation(actor(), request("right-table"))

        self.assertEqual(created.resource_id, "right-table")

    def test_tenant_isolation_ignores_same_resource_id_in_other_tenant(self):
        svc = service(
            [resource("printer")],
            [reservation("printer", tenant_id="slack:OTHER")],
        )

        created = svc.create_reservation(actor(), request("printer"))

        self.assertEqual(created.tenant_id, TENANT)

    def test_deny_policy_overrides_channel_member_default(self):
        printer = resource(
            "printer",
            policy=AccessPolicy(deny=PrincipalSet(users=frozenset({"U123"}))),
        )
        svc = service([printer])

        with self.assertRaisesRegex(ValueError, "not allowed"):
            svc.create_reservation(actor(), request("printer"))

    def test_channel_manager_can_reserve_even_when_resource_defaults_disabled(self):
        printer = resource(
            "printer",
            policy=AccessPolicy(inherit_channel_defaults=False),
        )
        svc = service([printer])

        created = svc.create_reservation(
            actor(managed_channel_ids=frozenset({CHANNEL})),
            request("printer"),
        )

        self.assertEqual(created.resource_id, "printer")

    def test_reservation_reason_preserves_freeform_markdown_and_emoji(self):
        svc = service([resource("room")])
        created = svc.create_reservation(
            actor(),
            request("room", start=2000, end=4000).__class__(
                tenant_id=TENANT,
                resource_id="room",
                channel_id=CHANNEL,
                owner_user_id="U123",
                start_epoch=2000,
                end_epoch=4000,
                reason="Planning *Q3* launch :rocket: with _design_",
            ),
        )

        self.assertEqual(created.reason, "Planning *Q3* launch :rocket: with _design_")

    def test_successful_reservation_refreshes_scheduled_reminders_when_configured(self):
        repo = InMemoryReservationRepository(resources=[resource("room")])
        refresher = RecordingRefresher()
        svc = ReservationService(repo, AuthorizationService(repo), schedule_refresher=refresher)

        created = svc.create_reservation(actor(), request("room"))

        self.assertEqual(refresher.calls[0][0], created)


if __name__ == "__main__":
    unittest.main()
