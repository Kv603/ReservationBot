from __future__ import annotations

from dataclasses import replace

from reservationbot.audit import AuditService
from reservationbot.models import (
    Actor,
    AuditAction,
    AuditDestination,
    BotProfile,
    DurationPolicy,
    TenantSettings,
)
from reservationbot.repository import ReservationRepository

_UNSET = object()


class TenantSettingsService:
    MIN_PRIVATE_REMINDER_SECONDS = 15 * 60
    MAX_PRIVATE_REMINDER_SECONDS = 24 * 60 * 60

    def __init__(self, repository: ReservationRepository, audit: AuditService) -> None:
        self.repository = repository
        self.audit = audit

    def update_workspace_settings(
        self,
        actor: Actor,
        *,
        workspace_timezone: str | None = None,
        slash_command_aliases: tuple[str, ...] | None = None,
        bot_profile: BotProfile | None = None,
        duration_policy: DurationPolicy | None = None,
        private_reminder_lead_seconds: int | None | object = _UNSET,
        audit_destination: AuditDestination | None | object = _UNSET,
    ) -> TenantSettings:
        if not actor.can_manage_workspace:
            raise ValueError("only Slack admins and workspace owners can change workspace settings")

        current = self.repository.get_tenant_settings(actor.tenant_id)
        new_settings = replace(
            current,
            workspace_timezone=workspace_timezone or current.workspace_timezone,
            slash_command_aliases=(
                slash_command_aliases
                if slash_command_aliases is not None
                else current.slash_command_aliases
            ),
            bot_profile=bot_profile or current.bot_profile,
            duration_policy=duration_policy or current.duration_policy,
            private_reminder_lead_seconds=(
                current.private_reminder_lead_seconds
                if private_reminder_lead_seconds is _UNSET
                else self._validate_private_reminder(private_reminder_lead_seconds)
            ),
            audit_destination=(
                current.audit_destination
                if audit_destination is _UNSET
                else audit_destination
            ),
        )
        self._validate_duration_policy(new_settings.duration_policy)
        self.repository.put_tenant_settings(new_settings)

        old_destination = (
            current.audit_destination.destination_id if current.audit_destination else None
        )
        new_destination = (
            new_settings.audit_destination.destination_id if new_settings.audit_destination else None
        )
        if old_destination and old_destination != new_destination:
            action = (
                AuditAction.AUDIT_DESTINATION_CHANGED
                if new_destination
                else AuditAction.AUDIT_DESTINATION_REMOVED
            )
            self.audit.record(
                actor=actor,
                action=action,
                summary="changed the audit destination. This is the final audit message sent here.",
                entity_type="tenant_settings",
                entity_id=actor.tenant_id,
                settings=current,
                destination_id=old_destination,
                old_audit_destination_id=old_destination,
                new_audit_destination_id=new_destination,
            )

        self.audit.record(
            actor=actor,
            action=AuditAction.WORKSPACE_SETTINGS_CHANGED,
            summary="changed workspace settings.",
            entity_type="tenant_settings",
            entity_id=actor.tenant_id,
            settings=new_settings,
            old_audit_destination_id=old_destination,
            new_audit_destination_id=new_destination,
        )
        return new_settings

    def _validate_private_reminder(self, value: int | None | object) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int):
            raise ValueError("private reminder lead time must be seconds or None")
        if not self.MIN_PRIVATE_REMINDER_SECONDS <= value <= self.MAX_PRIVATE_REMINDER_SECONDS:
            raise ValueError("private reminder lead time must be off or between 15 minutes and 24 hours")
        return value

    @staticmethod
    def _validate_duration_policy(policy: DurationPolicy) -> None:
        if policy.minimum_seconds < 1:
            raise ValueError("minimum reservation duration must be positive")
        if policy.maximum_seconds < policy.minimum_seconds:
            raise ValueError("maximum reservation duration must be at least the minimum duration")
