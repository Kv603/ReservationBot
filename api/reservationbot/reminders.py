from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reservationbot.audit import AuditService
from reservationbot.models import (
    Actor,
    AuditAction,
    ReminderResourceScope,
    ReminderScheduleKind,
    ReminderTimeframe,
    Reservation,
    ScheduledReminder,
    TenantSettings,
)
from reservationbot.repository import ReservationRepository
from reservationbot.slack_messages import SlackMessage
from reservationbot.time_utils import format_plain_datetime, format_slack_datetime


def build_private_reservation_reminder(
    *,
    reservation: Reservation,
    settings: TenantSettings,
    user_timezone: str | None,
) -> SlackMessage | None:
    if settings.private_reminder_lead_seconds is None:
        return None

    preferred_timezone = user_timezone or settings.workspace_timezone
    start = format_slack_datetime(reservation.start_epoch, settings.workspace_timezone)
    end = format_slack_datetime(reservation.end_epoch, settings.workspace_timezone)
    user_plain_start = format_plain_datetime(reservation.start_epoch, preferred_timezone)
    text = (
        f"Your reservation for `{reservation.resource_id}` starts at {start} "
        f"and ends at {end}. Local reminder time: {user_plain_start}."
    )
    return SlackMessage(
        destination_id=reservation.owner_user_id,
        text=text,
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Confirm"},
                        "action_id": "reservation_confirm",
                        "value": reservation.reservation_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Edit"},
                        "action_id": "reservation_edit",
                        "value": reservation.reservation_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Cancel"},
                        "style": "danger",
                        "action_id": "reservation_cancel",
                        "value": reservation.reservation_id,
                    },
                ],
            },
        ],
    )


class ScheduledReminderService:
    def __init__(
        self,
        repository: ReservationRepository,
        slack_client,
        audit: AuditService,
    ) -> None:
        self.repository = repository
        self.slack_client = slack_client
        self.audit = audit

    def create_reminder(
        self,
        actor: Actor,
        *,
        title: str,
        post_time_minutes: int,
        schedule_kind: ReminderScheduleKind,
        timeframe: ReminderTimeframe,
        source_channel_id: str | None = None,
        destination_id: str | None = None,
        suppress_empty: bool = False,
        resource_scope: ReminderResourceScope = ReminderResourceScope.CHANNEL,
        resource_ids: tuple[str, ...] = (),
    ) -> ScheduledReminder:
        source = source_channel_id or actor.slack_channel_id
        self._validate_schedule(post_time_minutes)
        reminder = ScheduledReminder.create(
            tenant_id=actor.tenant_id,
            owner_user_id=actor.slack_user_id,
            owner_handle=actor.slack_handle,
            source_channel_id=source,
            title=title,
            post_time_minutes=post_time_minutes,
            schedule_kind=schedule_kind,
            timeframe=timeframe,
            destination_id=destination_id,
            suppress_empty=suppress_empty,
            resource_scope=resource_scope,
            resource_ids=resource_ids,
        )
        self._ensure_can_manage(actor, reminder)
        self._validate_resource_scope(actor, reminder)
        self.repository.put_scheduled_reminder(reminder)
        self.audit.record(
            actor=actor,
            action=AuditAction.REMINDER_CHANGED,
            summary=f"created scheduled reminder `{reminder.title}`.",
            entity_type="scheduled_reminder",
            entity_id=reminder.reminder_id,
            metadata=reminder.to_json(),
        )
        return reminder

    def update_reminder(
        self,
        actor: Actor,
        reminder_id: str,
        **changes,
    ) -> ScheduledReminder:
        current = self._get_existing(actor.tenant_id, reminder_id)
        self._ensure_can_manage(actor, current)
        if "post_time_minutes" in changes:
            self._validate_schedule(changes["post_time_minutes"])
        updated = replace(current, **changes)
        self._ensure_can_manage(actor, updated)
        self._validate_resource_scope(actor, updated)
        self.repository.put_scheduled_reminder(updated)
        self.audit.record(
            actor=actor,
            action=AuditAction.REMINDER_CHANGED,
            summary=f"updated scheduled reminder `{updated.title}`.",
            entity_type="scheduled_reminder",
            entity_id=updated.reminder_id,
            metadata=updated.to_json(),
        )
        return updated

    def pause_reminder(self, actor: Actor, reminder_id: str, paused: bool = True) -> ScheduledReminder:
        return self.update_reminder(actor, reminder_id, paused=paused)

    def delete_reminder(self, actor: Actor, reminder_id: str) -> None:
        reminder = self._get_existing(actor.tenant_id, reminder_id)
        self._ensure_can_manage(actor, reminder)
        self.repository.delete_scheduled_reminder(actor.tenant_id, reminder_id)
        self.audit.record(
            actor=actor,
            action=AuditAction.REMINDER_CHANGED,
            summary=f"deleted scheduled reminder `{reminder.title}`.",
            entity_type="scheduled_reminder",
            entity_id=reminder.reminder_id,
            metadata=reminder.to_json(),
        )

    def list_visible_reminders(self, actor: Actor) -> list[ScheduledReminder]:
        reminders = self.repository.list_scheduled_reminders(actor.tenant_id)
        if actor.can_manage_workspace:
            return reminders
        return [reminder for reminder in reminders if self._posts_to_managed_channel(actor, reminder)]

    def view_reminder(self, actor: Actor, reminder_id: str) -> str:
        reminder = self._get_existing(actor.tenant_id, reminder_id)
        if not actor.can_manage_workspace and not self._posts_to_managed_channel(actor, reminder):
            raise ValueError("you are not allowed to view this scheduled reminder")
        post_time = _format_post_time(reminder.post_time_minutes)
        owner = f"<@{reminder.owner_user_id}>"
        if reminder.owner_handle:
            owner = f"{owner} ({reminder.owner_handle})"
        ts = self._format_last_post(reminder)
        return (
            f"*{reminder.title}*\n"
            f"Schedule: {reminder.schedule_kind.value} at {post_time}; {reminder.timeframe.value}\n"
            f"Owner: {owner}\n"
            f"Last post: {ts}"
        )

    def post_due_reminder(self, reminder: ScheduledReminder, now_epoch: int) -> ScheduledReminder:
        if reminder.paused:
            return reminder
        settings = self.repository.get_tenant_settings(reminder.tenant_id)
        message = self.build_public_reminder_message(reminder, settings, now_epoch)
        if message is None:
            cleared = replace(reminder, last_post_ts=None)
            self.repository.put_scheduled_reminder(cleared)
            return cleared
        ts = self.slack_client.post_message(message)
        posted = replace(reminder, last_post_ts=ts or None)
        self.repository.put_scheduled_reminder(posted)
        return posted

    def refresh_schedules_for_reservation(
        self,
        reservation: Reservation,
        now_epoch: int,
    ) -> list[ScheduledReminder]:
        refreshed: list[ScheduledReminder] = []
        for reminder in self.repository.list_scheduled_reminders(reservation.tenant_id):
            if reminder.paused or not self._reservation_in_reminder_scope(reservation, reminder):
                continue
            settings = self.repository.get_tenant_settings(reminder.tenant_id)
            start, end = self._timeframe_window(reminder, settings, now_epoch)
            if not (reservation.start_epoch < end and start < reservation.end_epoch):
                continue
            message = self.build_public_reminder_message(reminder, settings, now_epoch)
            if message is None:
                cleared = replace(reminder, last_post_ts=None)
                self.repository.put_scheduled_reminder(cleared)
                refreshed.append(cleared)
            elif reminder.last_post_ts:
                ts = self.slack_client.update_message(message, reminder.last_post_ts)
                updated = replace(reminder, last_post_ts=ts)
                self.repository.put_scheduled_reminder(updated)
                refreshed.append(updated)
            else:
                refreshed.append(self.post_due_reminder(reminder, now_epoch))
        return refreshed

    def build_public_reminder_message(
        self,
        reminder: ScheduledReminder,
        settings: TenantSettings,
        now_epoch: int,
    ) -> SlackMessage | None:
        start, end = self._timeframe_window(reminder, settings, now_epoch)
        resources = self._resources_for_reminder(reminder)
        resource_ids = {resource.resource_id for resource in resources}
        reservations = [
            item
            for item in self.repository.list_reservations_in_window(
                reminder.tenant_id,
                start,
                end,
            )
            if item.resource_id in resource_ids
        ]
        reservations.sort(key=lambda item: (item.start_epoch, item.resource_id))
        if reminder.suppress_empty and not reservations:
            return None
        resource_names = {resource.resource_id: resource.name for resource in resources}
        lines = [f"*{reminder.title}*"]
        if reservations:
            for reservation in reservations:
                when = format_slack_datetime(reservation.start_epoch, settings.workspace_timezone)
                resource_name = resource_names.get(reservation.resource_id, reservation.resource_id)
                owner = f"<@{reservation.owner_user_id}>"
                reason = f" - {reservation.reason}" if reservation.reason else ""
                lines.append(f"- {when}: `{resource_name}` for {owner}{reason}")
        else:
            lines.append("No upcoming reservations.")
        text = "\n".join(lines)
        return SlackMessage(
            destination_id=reminder.effective_destination_id,
            text=text,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
        )

    def _resources_for_reminder(self, reminder: ScheduledReminder):
        resources = self.repository.list_resources(reminder.tenant_id)
        if reminder.resource_scope == ReminderResourceScope.EVERYTHING:
            return resources
        if reminder.resource_scope == ReminderResourceScope.RESOURCES:
            requested = set(reminder.resource_ids)
            return [resource for resource in resources if resource.resource_id in requested]
        return [resource for resource in resources if resource.channel_id == reminder.source_channel_id]

    def _reservation_in_reminder_scope(
        self,
        reservation: Reservation,
        reminder: ScheduledReminder,
    ) -> bool:
        if reminder.resource_scope == ReminderResourceScope.EVERYTHING:
            return True
        if reminder.resource_scope == ReminderResourceScope.RESOURCES:
            return reservation.resource_id in set(reminder.resource_ids)
        resource = self.repository.get_resource(reservation.tenant_id, reservation.resource_id)
        return bool(resource and resource.channel_id == reminder.source_channel_id)

    def _timeframe_window(
        self,
        reminder: ScheduledReminder,
        settings: TenantSettings,
        now_epoch: int,
    ) -> tuple[int, int]:
        timezone = _zoneinfo_or_utc(settings.workspace_timezone)
        now = datetime.fromtimestamp(now_epoch, tz=timezone)
        today_start = datetime.combine(now.date(), time.min, tzinfo=timezone)
        if reminder.timeframe == ReminderTimeframe.TODAY:
            start = today_start
            end = start + timedelta(days=1)
        elif reminder.timeframe == ReminderTimeframe.TOMORROW:
            start = today_start + timedelta(days=1)
            end = start + timedelta(days=1)
        else:
            start = today_start
            end = start + timedelta(days=7)
        return int(start.timestamp()), int(end.timestamp())

    def _validate_schedule(self, post_time_minutes: int) -> None:
        if post_time_minutes < 0 or post_time_minutes >= 24 * 60:
            raise ValueError("post time must be within the day")
        if post_time_minutes % 15 != 0:
            raise ValueError("post time must use 15-minute granularity")

    def _validate_resource_scope(self, actor: Actor, reminder: ScheduledReminder) -> None:
        if reminder.resource_scope == ReminderResourceScope.EVERYTHING and not actor.can_manage_workspace:
            raise ValueError("only Slack admins and workspace owners can include every resource")
        if reminder.resource_scope == ReminderResourceScope.RESOURCES and not reminder.resource_ids:
            raise ValueError("specific resource reminders need at least one resource")

    def _ensure_can_manage(self, actor: Actor, reminder: ScheduledReminder) -> None:
        if actor.can_manage_workspace:
            return
        if self._posts_to_managed_channel(actor, reminder):
            return
        raise ValueError("you are not allowed to manage this scheduled reminder")

    @staticmethod
    def _posts_to_managed_channel(actor: Actor, reminder: ScheduledReminder) -> bool:
        return (
            reminder.effective_destination_id.startswith("C")
            and reminder.effective_destination_id in actor.managed_channel_ids
        )

    def _get_existing(self, tenant_id: str, reminder_id: str) -> ScheduledReminder:
        reminder = self.repository.get_scheduled_reminder(tenant_id, reminder_id)
        if not reminder:
            raise ValueError("scheduled reminder not found")
        return reminder

    @staticmethod
    def _format_last_post(reminder: ScheduledReminder) -> str:
        if not reminder.last_post_ts:
            return "never"
        return (
            f"<https://slack.com/app_redirect?channel={reminder.effective_destination_id}"
            f"&message_ts={reminder.last_post_ts}|{reminder.last_post_ts}>"
        )


def _format_post_time(post_time_minutes: int) -> str:
    hours = post_time_minutes // 60
    minutes = post_time_minutes % 60
    return f"{hours:02d}:{minutes:02d}"


def _zoneinfo_or_utc(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")
