import unittest
from dataclasses import replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from reservationbot.audit import AuditService
from reservationbot.authz import AuthorizationService
from reservationbot.models import (
    Actor,
    ReminderResourceScope,
    ReminderScheduleKind,
    ReminderTimeframe,
    Reservation,
    Resource,
    ResourceKind,
    ScheduledReminder,
    TenantSettings,
)
from reservationbot.reminders import ScheduledReminderService
from reservationbot.repository import InMemoryReservationRepository
from reservationbot.reservations import ReservationService
from reservationbot.slack_messages import RecordingSlackMessageClient


TENANT = "slack:T123"
CHANNEL = "C123"
NOW = 1_704_067_200


def admin():
    return Actor(
        tenant_id=TENANT,
        slack_user_id="UADMIN",
        slack_channel_id=CHANNEL,
        is_workspace_admin=True,
        user_group_ids=frozenset(),
        slack_handle="@admin",
    )


def manager():
    return Actor(
        tenant_id=TENANT,
        slack_user_id="UMANAGER",
        slack_channel_id=CHANNEL,
        is_workspace_admin=False,
        user_group_ids=frozenset(),
        managed_channel_ids=frozenset({CHANNEL}),
        slack_handle="@manager",
    )


def resource(resource_id, channel_id=CHANNEL):
    return Resource(
        tenant_id=TENANT,
        resource_id=resource_id,
        channel_id=channel_id,
        name=resource_id,
        kind=ResourceKind.SPECIFIC,
    )


def reservation(resource_id, start=NOW + 3600, end=NOW + 7200, reason=None):
    return Reservation.create(
        tenant_id=TENANT,
        resource_id=resource_id,
        channel_id=CHANNEL,
        owner_user_id="U123",
        start_epoch=start,
        end_epoch=end,
        reason=reason,
    )


def service(repo):
    slack = RecordingSlackMessageClient()
    reminder_service = ScheduledReminderService(repo, slack, AuditService(repo, slack))
    return reminder_service, slack


class ScheduledReminderTests(unittest.TestCase):
    def test_channel_manager_can_create_channel_posting_reminder(self):
        repo = InMemoryReservationRepository(resources=[resource("room")])
        reminders, _ = service(repo)

        created = reminders.create_reminder(
            manager(),
            title="Today",
            post_time_minutes=8 * 60,
            schedule_kind=ReminderScheduleKind.DAILY,
            timeframe=ReminderTimeframe.TODAY,
        )

        self.assertEqual(created.effective_destination_id, CHANNEL)
        self.assertEqual(created.resource_scope, ReminderResourceScope.CHANNEL)

    def test_post_time_must_use_15_minute_granularity(self):
        repo = InMemoryReservationRepository(resources=[resource("room")])
        reminders, _ = service(repo)

        with self.assertRaisesRegex(ValueError, "15-minute"):
            reminders.create_reminder(
                admin(),
                title="Odd",
                post_time_minutes=8 * 60 + 7,
                schedule_kind=ReminderScheduleKind.DAILY,
                timeframe=ReminderTimeframe.TODAY,
            )

    def test_everything_scope_requires_workspace_admin_or_owner(self):
        repo = InMemoryReservationRepository(resources=[resource("room")])
        reminders, _ = service(repo)

        with self.assertRaisesRegex(ValueError, "only Slack admins"):
            reminders.create_reminder(
                manager(),
                title="Everything",
                post_time_minutes=8 * 60,
                schedule_kind=ReminderScheduleKind.WEEKLY,
                timeframe=ReminderTimeframe.UPCOMING_WEEK,
                resource_scope=ReminderResourceScope.EVERYTHING,
            )

    def test_successful_post_saves_returned_ts(self):
        repo = InMemoryReservationRepository(
            resources=[resource("room")],
            reservations=[reservation("room", reason="Discuss *roadmap* :calendar:")],
            settings=[TenantSettings(tenant_id=TENANT, workspace_timezone="UTC")],
        )
        reminders, slack = service(repo)
        reminder = reminders.create_reminder(
            admin(),
            title="Today",
            post_time_minutes=8 * 60,
            schedule_kind=ReminderScheduleKind.DAILY,
            timeframe=ReminderTimeframe.TODAY,
        )

        posted = reminders.post_due_reminder(reminder, NOW)

        self.assertEqual(posted.last_post_ts, "1700000000.000001")
        self.assertIn("Discuss *roadmap* :calendar:", slack.messages[-1].text)

    def test_skip_empty_clears_saved_ts(self):
        repo = InMemoryReservationRepository(resources=[resource("room")])
        reminders, slack = service(repo)
        reminder = ScheduledReminder.create(
            tenant_id=TENANT,
            owner_user_id="UADMIN",
            owner_handle="@admin",
            source_channel_id=CHANNEL,
            title="Empty",
            post_time_minutes=8 * 60,
            schedule_kind=ReminderScheduleKind.DAILY,
            timeframe=ReminderTimeframe.TODAY,
            suppress_empty=True,
        )
        reminder = replace(reminder, last_post_ts="old.ts")
        repo.put_scheduled_reminder(reminder)

        posted = reminders.post_due_reminder(reminder, NOW)

        self.assertIsNone(posted.last_post_ts)
        self.assertEqual(slack.messages, [])

    def test_refresh_updates_existing_post_when_reservation_changes_in_timeframe(self):
        repo = InMemoryReservationRepository(
            resources=[resource("room")],
            reservations=[reservation("room")],
            settings=[TenantSettings(tenant_id=TENANT, workspace_timezone="UTC")],
        )
        reminders, slack = service(repo)
        reminder = ScheduledReminder.create(
            tenant_id=TENANT,
            owner_user_id="UADMIN",
            owner_handle="@admin",
            source_channel_id=CHANNEL,
            title="Today",
            post_time_minutes=8 * 60,
            schedule_kind=ReminderScheduleKind.DAILY,
            timeframe=ReminderTimeframe.TODAY,
        )
        reminder = replace(reminder, last_post_ts="old.ts")
        repo.put_scheduled_reminder(reminder)

        refreshed = reminders.refresh_schedules_for_reservation(reservation("room"), NOW)

        self.assertEqual(refreshed[0].last_post_ts, "old.ts")
        self.assertEqual(slack.updates[0][1], "old.ts")

    def test_cancelling_reservation_refreshes_existing_scheduled_post(self):
        existing = reservation("room")
        repo = InMemoryReservationRepository(
            resources=[resource("room")],
            reservations=[existing],
            settings=[TenantSettings(tenant_id=TENANT, workspace_timezone="UTC")],
        )
        reminders, slack = service(repo)
        reminder = ScheduledReminder.create(
            tenant_id=TENANT,
            owner_user_id="UADMIN",
            owner_handle="@admin",
            source_channel_id=CHANNEL,
            title="Today",
            post_time_minutes=8 * 60,
            schedule_kind=ReminderScheduleKind.DAILY,
            timeframe=ReminderTimeframe.TODAY,
        )
        repo.put_scheduled_reminder(replace(reminder, last_post_ts="old.ts"))
        reservation_service = ReservationService(
            repo,
            AuthorizationService(repo),
            schedule_refresher=reminders,
        )

        reservation_service.cancel_reservation(admin(), existing.reservation_id, now_epoch=NOW)

        self.assertEqual(slack.updates[0][1], "old.ts")
        self.assertIn("No upcoming reservations.", slack.updates[0][0].text)

    def test_refresh_posts_fresh_message_when_no_saved_ts(self):
        repo = InMemoryReservationRepository(
            resources=[resource("room")],
            reservations=[reservation("room")],
        )
        reminders, slack = service(repo)
        reminder = ScheduledReminder.create(
            tenant_id=TENANT,
            owner_user_id="UADMIN",
            owner_handle="@admin",
            source_channel_id=CHANNEL,
            title="Today",
            post_time_minutes=8 * 60,
            schedule_kind=ReminderScheduleKind.DAILY,
            timeframe=ReminderTimeframe.TODAY,
        )
        repo.put_scheduled_reminder(reminder)

        refreshed = reminders.refresh_schedules_for_reservation(reservation("room"), NOW)

        self.assertEqual(refreshed[0].last_post_ts, "1700000000.000001")
        self.assertEqual(len(slack.messages), 1)

    def test_view_reminder_includes_schedule_owner_and_linked_ts(self):
        repo = InMemoryReservationRepository(resources=[resource("room")])
        reminders, _ = service(repo)
        created = reminders.create_reminder(
            manager(),
            title="Today",
            post_time_minutes=8 * 60 + 15,
            schedule_kind=ReminderScheduleKind.DAILY,
            timeframe=ReminderTimeframe.TODAY,
        )
        updated = reminders.update_reminder(manager(), created.reminder_id, last_post_ts="123.456")

        view = reminders.view_reminder(manager(), updated.reminder_id)

        self.assertIn("Schedule: daily at 08:15", view)
        self.assertIn("<@UMANAGER> (@manager)", view)
        self.assertIn("message_ts=123.456|123.456", view)


if __name__ == "__main__":
    unittest.main()
