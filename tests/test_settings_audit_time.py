import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from reservationbot.audit import AuditService
from reservationbot.authz import AuthorizationService
from reservationbot.models import (
    Actor,
    AuditDestination,
    ChannelSettings,
    DurationPolicy,
    Reservation,
    ReservationRequest,
    Resource,
    ResourceKind,
    TenantSettings,
)
from reservationbot.reminders import build_private_reservation_reminder
from reservationbot.repository import InMemoryReservationRepository
from reservationbot.reservations import ReservationService
from reservationbot.settings import TenantSettingsService
from reservationbot.slack_messages import RecordingSlackMessageClient
from reservationbot.time_utils import format_slack_datetime


TENANT = "slack:T123"
CHANNEL = "C123"


def admin_actor():
    return Actor(
        tenant_id=TENANT,
        slack_user_id="UADMIN",
        slack_channel_id=CHANNEL,
        is_workspace_admin=True,
        is_workspace_owner=False,
        user_group_ids=frozenset(),
        slack_handle="@admin",
    )


class SettingsAuditTimeTests(unittest.TestCase):
    def test_audit_destination_change_posts_final_message_to_old_destination(self):
        repo = InMemoryReservationRepository(
            settings=[
                TenantSettings(
                    tenant_id=TENANT,
                    audit_destination=AuditDestination("COLD"),
                )
            ]
        )
        slack = RecordingSlackMessageClient()
        service = TenantSettingsService(repo, AuditService(repo, slack))

        service.update_workspace_settings(
            admin_actor(),
            audit_destination=AuditDestination("CNEW"),
        )

        self.assertEqual(slack.messages[0].destination_id, "COLD")
        self.assertIn("final audit message", slack.messages[0].text)
        self.assertEqual(slack.messages[1].destination_id, "CNEW")

    def test_non_admin_cannot_change_workspace_settings(self):
        repo = InMemoryReservationRepository()
        service = TenantSettingsService(repo, AuditService(repo, RecordingSlackMessageClient()))
        actor = Actor(
            tenant_id=TENANT,
            slack_user_id="U123",
            slack_channel_id=CHANNEL,
            is_workspace_admin=False,
            user_group_ids=frozenset(),
        )

        with self.assertRaisesRegex(ValueError, "only Slack admins"):
            service.update_workspace_settings(actor, workspace_timezone="America/New_York")

    def test_channel_duration_policy_overrides_workspace_default(self):
        resource = Resource(
            tenant_id=TENANT,
            resource_id="room",
            channel_id=CHANNEL,
            name="Room",
            kind=ResourceKind.SPECIFIC,
        )
        repo = InMemoryReservationRepository(
            resources=[resource],
            settings=[
                TenantSettings(
                    tenant_id=TENANT,
                    duration_policy=DurationPolicy(minimum_seconds=900, maximum_seconds=1200),
                )
            ],
            channel_settings=[
                ChannelSettings(
                    tenant_id=TENANT,
                    channel_id=CHANNEL,
                    duration_policy=DurationPolicy(minimum_seconds=900, maximum_seconds=7200),
                )
            ],
        )
        service = ReservationService(repo, AuthorizationService(repo))
        request = ReservationRequest(
            tenant_id=TENANT,
            resource_id="room",
            channel_id=CHANNEL,
            owner_user_id="U123",
            start_epoch=1000,
            end_epoch=5000,
        )
        actor = Actor(
            tenant_id=TENANT,
            slack_user_id="U123",
            slack_channel_id=CHANNEL,
            is_workspace_admin=False,
            user_group_ids=frozenset(),
        )

        created = service.create_reservation(actor, request)

        self.assertEqual(created.resource_id, "room")

    def test_resource_duration_policy_overrides_channel_policy(self):
        resource = Resource(
            tenant_id=TENANT,
            resource_id="printer",
            channel_id=CHANNEL,
            name="Printer",
            kind=ResourceKind.SPECIFIC,
            metadata={
                "duration_policy": {
                    "minimum_seconds": 900,
                    "maximum_seconds": 10_800,
                }
            },
        )
        repo = InMemoryReservationRepository(
            resources=[resource],
            channel_settings=[
                ChannelSettings(
                    tenant_id=TENANT,
                    channel_id=CHANNEL,
                    duration_policy=DurationPolicy(minimum_seconds=900, maximum_seconds=1200),
                )
            ],
        )
        service = ReservationService(repo, AuthorizationService(repo))
        actor = Actor(
            tenant_id=TENANT,
            slack_user_id="U123",
            slack_channel_id=CHANNEL,
            is_workspace_admin=False,
            user_group_ids=frozenset(),
        )

        created = service.create_reservation(
            actor,
            ReservationRequest(
                tenant_id=TENANT,
                resource_id="printer",
                channel_id=CHANNEL,
                owner_user_id="U123",
                start_epoch=1000,
                end_epoch=5000,
            ),
        )

        self.assertEqual(created.resource_id, "printer")

    def test_slack_date_uses_special_format_with_workspace_timezone_fallback(self):
        value = format_slack_datetime(1_704_067_200, "America/New_York")

        self.assertTrue(value.startswith("<!date^1704067200^"))
        self.assertIn("|2023-12-31 07:00 PM EST>", value)

    def test_private_reminder_uses_dm_destination_and_action_buttons(self):
        settings = TenantSettings(
            tenant_id=TENANT,
            workspace_timezone="America/New_York",
            private_reminder_lead_seconds=3600,
        )
        reservation = Reservation.create(
            tenant_id=TENANT,
            resource_id="room",
            channel_id=CHANNEL,
            owner_user_id="U123",
            start_epoch=1_704_067_200,
            end_epoch=1_704_070_800,
        )

        message = build_private_reservation_reminder(
            reservation=reservation,
            settings=settings,
            user_timezone="America/Los_Angeles",
        )

        self.assertIsNotNone(message)
        self.assertEqual(message.destination_id, "U123")
        action_ids = [item["action_id"] for item in message.blocks[1]["elements"]]
        self.assertEqual(action_ids, ["reservation_confirm", "reservation_edit", "reservation_cancel"])


if __name__ == "__main__":
    unittest.main()
