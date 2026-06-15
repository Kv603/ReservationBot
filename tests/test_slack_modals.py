import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from reservationbot.models import Reservation, Resource, ResourceKind, TenantSettings
from reservationbot.slack_modals import (
    CANCEL_MODAL_CALLBACK,
    RESERVATION_MODAL_CALLBACK,
    build_reservation_modal,
    build_user_reservations_cancel_modal,
    parse_modal_submission,
    wants_cancel_dialog,
)


TENANT = "slack:T123"
CHANNEL = "C123"


def resource(resource_id="room"):
    return Resource(
        tenant_id=TENANT,
        resource_id=resource_id,
        channel_id=CHANNEL,
        name="Conference Room",
        kind=ResourceKind.SPECIFIC,
    )


def reservation():
    return Reservation.create(
        tenant_id=TENANT,
        resource_id="room",
        channel_id=CHANNEL,
        owner_user_id="U123",
        start_epoch=1_704_067_200,
        end_epoch=1_704_070_800,
        reason="Planning *Q3* :rocket:",
    )


class SlackModalTests(unittest.TestCase):
    def test_blank_reserve_modal_contains_resource_time_duration_and_reason_inputs(self):
        view = build_reservation_modal(
            tenant_id=TENANT,
            channel_id=CHANNEL,
            user_id="U123",
            resources=[resource()],
            settings=TenantSettings(tenant_id=TENANT, workspace_timezone="UTC"),
        )

        self.assertEqual(view["callback_id"], RESERVATION_MODAL_CALLBACK)
        self.assertEqual(
            [block["block_id"] for block in view["blocks"]],
            ["resource", "date", "time", "duration", "reason"],
        )
        self.assertTrue(view["blocks"][4]["optional"])

    def test_edit_modal_prefills_existing_reservation_reason_and_resource(self):
        existing = reservation()
        view = build_reservation_modal(
            tenant_id=TENANT,
            channel_id=CHANNEL,
            user_id="U123",
            resources=[resource()],
            settings=TenantSettings(tenant_id=TENANT, workspace_timezone="UTC"),
            existing=existing,
        )

        self.assertEqual(
            view["blocks"][0]["element"]["initial_option"]["value"],
            "room",
        )
        self.assertEqual(
            view["blocks"][4]["element"]["initial_value"],
            "Planning *Q3* :rocket:",
        )

    def test_cancel_modal_lists_user_future_reservations(self):
        view = build_user_reservations_cancel_modal(
            tenant_id=TENANT,
            channel_id=CHANNEL,
            user_id="U123",
            reservations=[reservation()],
            settings=TenantSettings(tenant_id=TENANT, workspace_timezone="UTC"),
        )

        self.assertEqual(view["callback_id"], CANCEL_MODAL_CALLBACK)
        self.assertEqual(view["blocks"][0]["block_id"], "reservation")
        self.assertEqual(view["submit"]["text"], "Cancel reservation")

    def test_cancel_modal_without_reservations_has_no_submit(self):
        view = build_user_reservations_cancel_modal(
            tenant_id=TENANT,
            channel_id=CHANNEL,
            user_id="U123",
            reservations=[],
            settings=TenantSettings(tenant_id=TENANT, workspace_timezone="UTC"),
        )

        self.assertNotIn("submit", view)
        self.assertIn("do not have future reservations", view["blocks"][0]["text"]["text"])

    def test_parse_reservation_submission_preserves_reason(self):
        payload = {
            "view": {
                "callback_id": RESERVATION_MODAL_CALLBACK,
                "private_metadata": (
                    '{"tenant_id":"slack:T123","channel_id":"C123",'
                    '"user_id":"U123","workspace_timezone":"UTC"}'
                ),
                "state": {
                    "values": {
                        "resource": {
                            "resource_id": {
                                "selected_option": {"value": "room"}
                            }
                        },
                        "date": {"start_date": {"selected_date": "2024-01-01"}},
                        "time": {"start_time": {"selected_time": "09:30"}},
                        "duration": {
                            "duration_seconds": {
                                "selected_option": {"value": "3600"}
                            }
                        },
                        "reason": {
                            "reason": {"value": "Planning *Q3* :rocket:"}
                        },
                    }
                },
            }
        }

        result = parse_modal_submission(payload)

        self.assertEqual(result.action, "create")
        self.assertEqual(result.reservation_request.reason, "Planning *Q3* :rocket:")
        self.assertEqual(result.reservation_request.start_epoch, 1_704_101_400)
        self.assertEqual(result.reservation_request.end_epoch, 1_704_105_000)

    def test_parse_cancel_submission_returns_reservation_id(self):
        payload = {
            "view": {
                "callback_id": CANCEL_MODAL_CALLBACK,
                "private_metadata": '{"tenant_id":"slack:T123","channel_id":"C123","user_id":"U123"}',
                "state": {
                    "values": {
                        "reservation": {
                            "reservation_id": {
                                "selected_option": {"value": "reservation-1"}
                            }
                        }
                    }
                },
            }
        }

        result = parse_modal_submission(payload)

        self.assertEqual(result.action, "cancel")
        self.assertEqual(result.reservation_id, "reservation-1")

    def test_wants_cancel_dialog_aliases(self):
        self.assertTrue(wants_cancel_dialog("my reservations"))
        self.assertTrue(wants_cancel_dialog("cancel"))
        self.assertFalse(wants_cancel_dialog("room 1 2"))


if __name__ == "__main__":
    unittest.main()
