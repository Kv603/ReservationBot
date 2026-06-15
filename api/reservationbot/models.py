from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class ResourceKind(StrEnum):
    SPECIFIC = "specific"
    FUNGIBLE = "fungible"
    CONTAINER = "container"


class Role(StrEnum):
    TENANT_ADMIN = "tenant_admin"
    CHANNEL_MANAGER = "channel_manager"
    RESOURCE_MANAGER = "resource_manager"
    RESERVER = "reserver"
    VIEWER = "viewer"


class AuditAction(StrEnum):
    AUDIT_DESTINATION_CHANGED = "audit_destination_changed"
    AUDIT_DESTINATION_REMOVED = "audit_destination_removed"
    RESERVATION_CREATED = "reservation_created"
    RESERVATION_CHANGED = "reservation_changed"
    REMINDER_CHANGED = "reminder_changed"
    RESOURCE_CHANGED = "resource_changed"
    WORKSPACE_SETTINGS_CHANGED = "workspace_settings_changed"


class ReminderScheduleKind(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


class ReminderTimeframe(StrEnum):
    TODAY = "today"
    TOMORROW = "tomorrow"
    UPCOMING_WEEK = "upcoming_week"


class ReminderResourceScope(StrEnum):
    CHANNEL = "channel"
    RESOURCES = "resources"
    EVERYTHING = "everything"


@dataclass(frozen=True)
class PrincipalSet:
    users: frozenset[str] = frozenset()
    user_groups: frozenset[str] = frozenset()

    def contains(self, user_id: str, user_group_ids: frozenset[str]) -> bool:
        return user_id in self.users or bool(self.user_groups.intersection(user_group_ids))

    @classmethod
    def from_json(cls, value: dict[str, Any] | None) -> "PrincipalSet":
        value = value or {}
        return cls(
            users=frozenset(value.get("users", [])),
            user_groups=frozenset(value.get("user_groups", [])),
        )

    def to_json(self) -> dict[str, list[str]]:
        return {
            "users": sorted(self.users),
            "user_groups": sorted(self.user_groups),
        }


@dataclass(frozen=True)
class AccessPolicy:
    allow_view: PrincipalSet = field(default_factory=PrincipalSet)
    allow_reserve: PrincipalSet = field(default_factory=PrincipalSet)
    allow_manage: PrincipalSet = field(default_factory=PrincipalSet)
    deny: PrincipalSet = field(default_factory=PrincipalSet)
    inherit_channel_defaults: bool = True

    @classmethod
    def from_json(cls, value: dict[str, Any] | None) -> "AccessPolicy":
        value = value or {}
        return cls(
            allow_view=PrincipalSet.from_json(value.get("allow_view")),
            allow_reserve=PrincipalSet.from_json(value.get("allow_reserve")),
            allow_manage=PrincipalSet.from_json(value.get("allow_manage")),
            deny=PrincipalSet.from_json(value.get("deny")),
            inherit_channel_defaults=value.get("inherit_channel_defaults", True),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "allow_view": self.allow_view.to_json(),
            "allow_reserve": self.allow_reserve.to_json(),
            "allow_manage": self.allow_manage.to_json(),
            "deny": self.deny.to_json(),
            "inherit_channel_defaults": self.inherit_channel_defaults,
        }


@dataclass(frozen=True)
class Actor:
    tenant_id: str
    slack_user_id: str
    slack_channel_id: str
    is_workspace_admin: bool
    user_group_ids: frozenset[str]
    is_workspace_owner: bool = False
    managed_channel_ids: frozenset[str] = frozenset()
    slack_handle: str | None = None

    @property
    def mention(self) -> str:
        return f"<@{self.slack_user_id}>"

    @property
    def can_manage_workspace(self) -> bool:
        return self.is_workspace_admin or self.is_workspace_owner


@dataclass(frozen=True)
class BotProfile:
    display_name: str = "ReservationBot"
    avatar_url: str | None = None

    @classmethod
    def from_json(cls, value: dict[str, Any] | None) -> "BotProfile":
        value = value or {}
        return cls(
            display_name=value.get("display_name", "ReservationBot"),
            avatar_url=value.get("avatar_url"),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
        }


@dataclass(frozen=True)
class AuditDestination:
    destination_id: str

    @classmethod
    def from_json(cls, value: dict[str, Any] | None) -> "AuditDestination | None":
        if not value or not value.get("destination_id"):
            return None
        return cls(destination_id=value["destination_id"])

    def to_json(self) -> dict[str, str]:
        return {"destination_id": self.destination_id}


@dataclass(frozen=True)
class DurationPolicy:
    minimum_seconds: int = 15 * 60
    maximum_seconds: int = 12 * 60 * 60

    @classmethod
    def from_json(cls, value: dict[str, Any] | None) -> "DurationPolicy":
        value = value or {}
        return cls(
            minimum_seconds=int(value.get("minimum_seconds", 15 * 60)),
            maximum_seconds=int(value.get("maximum_seconds", 12 * 60 * 60)),
        )

    def to_json(self) -> dict[str, int]:
        return {
            "minimum_seconds": self.minimum_seconds,
            "maximum_seconds": self.maximum_seconds,
        }


@dataclass(frozen=True)
class TenantSettings:
    tenant_id: str
    workspace_timezone: str = "UTC"
    slash_command_aliases: tuple[str, ...] = ()
    bot_profile: BotProfile = field(default_factory=BotProfile)
    duration_policy: DurationPolicy = field(default_factory=DurationPolicy)
    private_reminder_lead_seconds: int | None = None
    audit_destination: AuditDestination | None = None

    @classmethod
    def default(cls, tenant_id: str) -> "TenantSettings":
        return cls(tenant_id=tenant_id)

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> "TenantSettings":
        return cls(
            tenant_id=item["tenant_id"],
            workspace_timezone=item.get("workspace_timezone", "UTC"),
            slash_command_aliases=tuple(item.get("slash_command_aliases", [])),
            bot_profile=BotProfile.from_json(item.get("bot_profile")),
            duration_policy=DurationPolicy.from_json(item.get("duration_policy")),
            private_reminder_lead_seconds=item.get("private_reminder_lead_seconds"),
            audit_destination=AuditDestination.from_json(item.get("audit_destination")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "workspace_timezone": self.workspace_timezone,
            "slash_command_aliases": list(self.slash_command_aliases),
            "bot_profile": self.bot_profile.to_json(),
            "duration_policy": self.duration_policy.to_json(),
            "private_reminder_lead_seconds": self.private_reminder_lead_seconds,
            "audit_destination": (
                self.audit_destination.to_json() if self.audit_destination else None
            ),
        }


@dataclass(frozen=True)
class ChannelSettings:
    tenant_id: str
    channel_id: str
    duration_policy: DurationPolicy | None = None
    private_reminder_lead_seconds: int | None = None

    @classmethod
    def default(cls, tenant_id: str, channel_id: str) -> "ChannelSettings":
        return cls(tenant_id=tenant_id, channel_id=channel_id)

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> "ChannelSettings":
        return cls(
            tenant_id=item["tenant_id"],
            channel_id=item["channel_id"],
            duration_policy=(
                DurationPolicy.from_json(item["duration_policy"])
                if item.get("duration_policy")
                else None
            ),
            private_reminder_lead_seconds=item.get("private_reminder_lead_seconds"),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "channel_id": self.channel_id,
            "duration_policy": (
                self.duration_policy.to_json() if self.duration_policy else None
            ),
            "private_reminder_lead_seconds": self.private_reminder_lead_seconds,
        }


@dataclass(frozen=True)
class AuditEvent:
    tenant_id: str
    action: AuditAction
    actor_user_id: str
    actor_handle: str | None
    summary: str
    entity_type: str
    entity_id: str
    occurred_at_epoch: int
    old_audit_destination_id: str | None = None
    new_audit_destination_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> "AuditEvent":
        return cls(
            tenant_id=item["tenant_id"],
            action=AuditAction(item["action"]),
            actor_user_id=item["actor_user_id"],
            actor_handle=item.get("actor_handle"),
            summary=item["summary"],
            entity_type=item["entity_type"],
            entity_id=item["entity_id"],
            occurred_at_epoch=int(item["occurred_at_epoch"]),
            old_audit_destination_id=item.get("old_audit_destination_id"),
            new_audit_destination_id=item.get("new_audit_destination_id"),
            metadata=item.get("metadata", {}),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "action": self.action.value,
            "actor_user_id": self.actor_user_id,
            "actor_handle": self.actor_handle,
            "summary": self.summary,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "occurred_at_epoch": self.occurred_at_epoch,
            "old_audit_destination_id": self.old_audit_destination_id,
            "new_audit_destination_id": self.new_audit_destination_id,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ScheduledReminder:
    tenant_id: str
    reminder_id: str
    owner_user_id: str
    owner_handle: str | None
    source_channel_id: str
    title: str
    post_time_minutes: int
    schedule_kind: ReminderScheduleKind
    timeframe: ReminderTimeframe
    destination_id: str | None = None
    suppress_empty: bool = False
    resource_scope: ReminderResourceScope = ReminderResourceScope.CHANNEL
    resource_ids: tuple[str, ...] = ()
    paused: bool = False
    last_post_ts: str | None = None

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        owner_user_id: str,
        owner_handle: str | None,
        source_channel_id: str,
        title: str,
        post_time_minutes: int,
        schedule_kind: ReminderScheduleKind,
        timeframe: ReminderTimeframe,
        destination_id: str | None = None,
        suppress_empty: bool = False,
        resource_scope: ReminderResourceScope = ReminderResourceScope.CHANNEL,
        resource_ids: tuple[str, ...] = (),
    ) -> "ScheduledReminder":
        return cls(
            tenant_id=tenant_id,
            reminder_id=str(uuid4()),
            owner_user_id=owner_user_id,
            owner_handle=owner_handle,
            source_channel_id=source_channel_id,
            title=title,
            post_time_minutes=post_time_minutes,
            schedule_kind=schedule_kind,
            timeframe=timeframe,
            destination_id=destination_id,
            suppress_empty=suppress_empty,
            resource_scope=resource_scope,
            resource_ids=resource_ids,
        )

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> "ScheduledReminder":
        return cls(
            tenant_id=item["tenant_id"],
            reminder_id=item["reminder_id"],
            owner_user_id=item["owner_user_id"],
            owner_handle=item.get("owner_handle"),
            source_channel_id=item["source_channel_id"],
            title=item["title"],
            post_time_minutes=int(item["post_time_minutes"]),
            schedule_kind=ReminderScheduleKind(item["schedule_kind"]),
            timeframe=ReminderTimeframe(item["timeframe"]),
            destination_id=item.get("destination_id"),
            suppress_empty=bool(item.get("suppress_empty", False)),
            resource_scope=ReminderResourceScope(item.get("resource_scope", "channel")),
            resource_ids=tuple(item.get("resource_ids", [])),
            paused=bool(item.get("paused", False)),
            last_post_ts=item.get("last_post_ts"),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "reminder_id": self.reminder_id,
            "owner_user_id": self.owner_user_id,
            "owner_handle": self.owner_handle,
            "source_channel_id": self.source_channel_id,
            "title": self.title,
            "post_time_minutes": self.post_time_minutes,
            "schedule_kind": self.schedule_kind.value,
            "timeframe": self.timeframe.value,
            "destination_id": self.destination_id,
            "suppress_empty": self.suppress_empty,
            "resource_scope": self.resource_scope.value,
            "resource_ids": list(self.resource_ids),
            "paused": self.paused,
            "last_post_ts": self.last_post_ts,
        }

    @property
    def effective_destination_id(self) -> str:
        return self.destination_id or self.source_channel_id


@dataclass(frozen=True)
class Resource:
    tenant_id: str
    resource_id: str
    channel_id: str
    name: str
    kind: ResourceKind
    capacity: int = 1
    parent_resource_id: str | None = None
    policy: AccessPolicy = field(default_factory=AccessPolicy)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> "Resource":
        return cls(
            tenant_id=item["tenant_id"],
            resource_id=item["resource_id"],
            channel_id=item["channel_id"],
            name=item["name"],
            kind=ResourceKind(item["kind"]),
            capacity=int(item.get("capacity", 1)),
            parent_resource_id=item.get("parent_resource_id"),
            policy=AccessPolicy.from_json(item.get("policy")),
            metadata=item.get("metadata", {}),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "resource_id": self.resource_id,
            "channel_id": self.channel_id,
            "name": self.name,
            "kind": self.kind.value,
            "capacity": self.capacity,
            "parent_resource_id": self.parent_resource_id,
            "policy": self.policy.to_json(),
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class Reservation:
    tenant_id: str
    reservation_id: str
    resource_id: str
    channel_id: str
    owner_user_id: str
    start_epoch: int
    end_epoch: int
    quantity: int = 1
    reason: str | None = None
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        resource_id: str,
        channel_id: str,
        owner_user_id: str,
        start_epoch: int,
        end_epoch: int,
        quantity: int = 1,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "Reservation":
        return cls(
            tenant_id=tenant_id,
            reservation_id=str(uuid4()),
            resource_id=resource_id,
            channel_id=channel_id,
            owner_user_id=owner_user_id,
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            quantity=quantity,
            reason=reason,
            metadata=metadata or {},
        )

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> "Reservation":
        return cls(
            tenant_id=item["tenant_id"],
            reservation_id=item["reservation_id"],
            resource_id=item["resource_id"],
            channel_id=item["channel_id"],
            owner_user_id=item["owner_user_id"],
            start_epoch=int(item["start_epoch"]),
            end_epoch=int(item["end_epoch"]),
            quantity=int(item.get("quantity", 1)),
            reason=item.get("reason"),
            status=item.get("status", "active"),
            metadata=item.get("metadata", {}),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "reservation_id": self.reservation_id,
            "resource_id": self.resource_id,
            "channel_id": self.channel_id,
            "owner_user_id": self.owner_user_id,
            "start_epoch": self.start_epoch,
            "end_epoch": self.end_epoch,
            "quantity": self.quantity,
            "reason": self.reason,
            "status": self.status,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ReservationRequest:
    tenant_id: str
    resource_id: str
    channel_id: str
    owner_user_id: str
    start_epoch: int
    end_epoch: int
    quantity: int = 1
    reason: str | None = None
    user_timezone: str | None = None

    @classmethod
    def from_slack_text(
        cls,
        *,
        tenant_id: str,
        channel_id: str,
        user_id: str,
        text: str,
    ) -> "ReservationRequest":
        parts = text.split(maxsplit=4)
        if len(parts) not in (3, 4, 5):
            raise ValueError("Use `/reserve <resource-id> <start-epoch> <end-epoch> [quantity] [reason]`.")
        resource_id, start_raw, end_raw = parts[:3]
        quantity = 1
        reason = None
        if len(parts) >= 4:
            try:
                quantity = int(parts[3])
                reason = parts[4] if len(parts) == 5 else None
            except ValueError:
                reason = " ".join(parts[3:])
        return cls(
            tenant_id=tenant_id,
            resource_id=resource_id,
            channel_id=channel_id,
            owner_user_id=user_id,
            start_epoch=int(start_raw),
            end_epoch=int(end_raw),
            quantity=quantity,
            reason=reason,
        )
