from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from reservationbot.models import (
    AuditEvent,
    ChannelSettings,
    Reservation,
    Resource,
    ScheduledReminder,
    TenantSettings,
)


class ReservationRepository(ABC):
    @abstractmethod
    def get_resource(self, tenant_id: str, resource_id: str) -> Resource | None:
        raise NotImplementedError

    @abstractmethod
    def list_resources_for_channel(self, tenant_id: str, channel_id: str) -> list[Resource]:
        raise NotImplementedError

    @abstractmethod
    def list_resources(self, tenant_id: str) -> list[Resource]:
        raise NotImplementedError

    @abstractmethod
    def list_reservations_for_resource(
        self,
        tenant_id: str,
        resource_id: str,
        start_epoch: int,
        end_epoch: int,
    ) -> list[Reservation]:
        raise NotImplementedError

    @abstractmethod
    def put_reservation(self, reservation: Reservation) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_reservation(self, tenant_id: str, reservation_id: str) -> Reservation | None:
        raise NotImplementedError

    @abstractmethod
    def save_reservation(self, reservation: Reservation) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_reservations_in_window(
        self,
        tenant_id: str,
        start_epoch: int,
        end_epoch: int,
    ) -> list[Reservation]:
        raise NotImplementedError

    @abstractmethod
    def list_user_future_reservations(
        self,
        tenant_id: str,
        user_id: str,
        after_epoch: int,
    ) -> list[Reservation]:
        raise NotImplementedError

    @abstractmethod
    def get_tenant_settings(self, tenant_id: str) -> TenantSettings:
        raise NotImplementedError

    @abstractmethod
    def put_tenant_settings(self, settings: TenantSettings) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_channel_settings(self, tenant_id: str, channel_id: str) -> ChannelSettings:
        raise NotImplementedError

    @abstractmethod
    def put_channel_settings(self, settings: ChannelSettings) -> None:
        raise NotImplementedError

    @abstractmethod
    def put_audit_event(self, event: AuditEvent) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_scheduled_reminder(self, tenant_id: str, reminder_id: str) -> ScheduledReminder | None:
        raise NotImplementedError

    @abstractmethod
    def list_scheduled_reminders(self, tenant_id: str) -> list[ScheduledReminder]:
        raise NotImplementedError

    @abstractmethod
    def put_scheduled_reminder(self, reminder: ScheduledReminder) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_scheduled_reminder(self, tenant_id: str, reminder_id: str) -> None:
        raise NotImplementedError


class DynamoReservationRepository(ReservationRepository):
    def __init__(self, table_name: str) -> None:
        self.table_name = table_name
        if table_name:
            import boto3

            self.table = boto3.resource("dynamodb").Table(table_name)
        else:
            self.table = None

    def get_resource(self, tenant_id: str, resource_id: str) -> Resource | None:
        item = self._get_item(tenant_id, f"RESOURCE#{resource_id}")
        return Resource.from_json(item["document"]) if item else None

    def list_resources_for_channel(self, tenant_id: str, channel_id: str) -> list[Resource]:
        resources = self.list_resources(tenant_id)
        return [resource for resource in resources if resource.channel_id == channel_id]

    def list_resources(self, tenant_id: str) -> list[Resource]:
        table = self._table()
        from boto3.dynamodb.conditions import Key

        response = table.query(
            KeyConditionExpression=Key("PK").eq(self._tenant_pk(tenant_id))
            & Key("SK").begins_with("RESOURCE#")
        )
        return [Resource.from_json(item["document"]) for item in response.get("Items", [])]

    def list_reservations_for_resource(
        self,
        tenant_id: str,
        resource_id: str,
        start_epoch: int,
        end_epoch: int,
    ) -> list[Reservation]:
        table = self._table()
        from boto3.dynamodb.conditions import Key

        response = table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq(self._resource_gsi_pk(tenant_id, resource_id))
            & Key("GSI1SK").between(
                f"START#{0:020d}",
                f"START#{end_epoch:020d}#RESERVATION#zzzzzzzz",
            ),
        )
        reservations = [Reservation.from_json(item["document"]) for item in response.get("Items", [])]
        return [
            reservation
            for reservation in reservations
            if reservation.status == "active" and intervals_overlap(
                reservation.start_epoch,
                reservation.end_epoch,
                start_epoch,
                end_epoch,
            )
        ]

    def put_reservation(self, reservation: Reservation) -> None:
        table = self._table()
        table.put_item(
            Item={
                "PK": self._tenant_pk(reservation.tenant_id),
                "SK": f"RESERVATION#{reservation.reservation_id}",
                "GSI1PK": self._resource_gsi_pk(reservation.tenant_id, reservation.resource_id),
                "GSI1SK": (
                    f"START#{reservation.start_epoch:020d}#"
                    f"RESERVATION#{reservation.reservation_id}"
                ),
                "document": reservation.to_json(),
            },
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )

    def get_reservation(self, tenant_id: str, reservation_id: str) -> Reservation | None:
        item = self._get_item(tenant_id, f"RESERVATION#{reservation_id}")
        return Reservation.from_json(item["document"]) if item else None

    def save_reservation(self, reservation: Reservation) -> None:
        table = self._table()
        table.put_item(
            Item={
                "PK": self._tenant_pk(reservation.tenant_id),
                "SK": f"RESERVATION#{reservation.reservation_id}",
                "GSI1PK": self._resource_gsi_pk(reservation.tenant_id, reservation.resource_id),
                "GSI1SK": (
                    f"START#{reservation.start_epoch:020d}#"
                    f"RESERVATION#{reservation.reservation_id}"
                ),
                "document": reservation.to_json(),
            }
        )

    def list_reservations_in_window(
        self,
        tenant_id: str,
        start_epoch: int,
        end_epoch: int,
    ) -> list[Reservation]:
        table = self._table()
        from boto3.dynamodb.conditions import Key

        response = table.query(
            KeyConditionExpression=Key("PK").eq(self._tenant_pk(tenant_id))
            & Key("SK").begins_with("RESERVATION#")
        )
        reservations = [Reservation.from_json(item["document"]) for item in response.get("Items", [])]
        return [
            reservation
            for reservation in reservations
            if reservation.status == "active"
            and intervals_overlap(
                reservation.start_epoch,
                reservation.end_epoch,
                start_epoch,
                end_epoch,
            )
        ]

    def list_user_future_reservations(
        self,
        tenant_id: str,
        user_id: str,
        after_epoch: int,
    ) -> list[Reservation]:
        table = self._table()
        from boto3.dynamodb.conditions import Key

        response = table.query(
            KeyConditionExpression=Key("PK").eq(self._tenant_pk(tenant_id))
            & Key("SK").begins_with("RESERVATION#")
        )
        reservations = [Reservation.from_json(item["document"]) for item in response.get("Items", [])]
        return sorted(
            [
                reservation
                for reservation in reservations
                if reservation.status == "active"
                and reservation.owner_user_id == user_id
                and reservation.end_epoch > after_epoch
            ],
            key=lambda item: item.start_epoch,
        )

    def get_tenant_settings(self, tenant_id: str) -> TenantSettings:
        item = self._get_item(tenant_id, "SETTINGS#GLOBAL")
        return TenantSettings.from_json(item["document"]) if item else TenantSettings.default(tenant_id)

    def put_tenant_settings(self, settings: TenantSettings) -> None:
        table = self._table()
        table.put_item(
            Item={
                "PK": self._tenant_pk(settings.tenant_id),
                "SK": "SETTINGS#GLOBAL",
                "document": settings.to_json(),
            }
        )

    def get_channel_settings(self, tenant_id: str, channel_id: str) -> ChannelSettings:
        item = self._get_item(tenant_id, f"CHANNEL#{channel_id}#SETTINGS")
        return (
            ChannelSettings.from_json(item["document"])
            if item
            else ChannelSettings.default(tenant_id, channel_id)
        )

    def put_channel_settings(self, settings: ChannelSettings) -> None:
        table = self._table()
        table.put_item(
            Item={
                "PK": self._tenant_pk(settings.tenant_id),
                "SK": f"CHANNEL#{settings.channel_id}#SETTINGS",
                "document": settings.to_json(),
            }
        )

    def put_audit_event(self, event: AuditEvent) -> None:
        table = self._table()
        table.put_item(
            Item={
                "PK": self._tenant_pk(event.tenant_id),
                "SK": f"AUDIT#{event.occurred_at_epoch:020d}#{event.action.value}",
                "document": event.to_json(),
            }
        )

    def get_scheduled_reminder(self, tenant_id: str, reminder_id: str) -> ScheduledReminder | None:
        item = self._get_item(tenant_id, f"REMINDER#{reminder_id}")
        return ScheduledReminder.from_json(item["document"]) if item else None

    def list_scheduled_reminders(self, tenant_id: str) -> list[ScheduledReminder]:
        table = self._table()
        from boto3.dynamodb.conditions import Key

        response = table.query(
            KeyConditionExpression=Key("PK").eq(self._tenant_pk(tenant_id))
            & Key("SK").begins_with("REMINDER#")
        )
        return [ScheduledReminder.from_json(item["document"]) for item in response.get("Items", [])]

    def put_scheduled_reminder(self, reminder: ScheduledReminder) -> None:
        table = self._table()
        table.put_item(
            Item={
                "PK": self._tenant_pk(reminder.tenant_id),
                "SK": f"REMINDER#{reminder.reminder_id}",
                "document": reminder.to_json(),
            }
        )

    def delete_scheduled_reminder(self, tenant_id: str, reminder_id: str) -> None:
        table = self._table()
        table.delete_item(
            Key={"PK": self._tenant_pk(tenant_id), "SK": f"REMINDER#{reminder_id}"}
        )

    def _get_item(self, tenant_id: str, sk: str) -> dict | None:
        table = self._table()
        response = table.get_item(Key={"PK": self._tenant_pk(tenant_id), "SK": sk})
        return response.get("Item")

    def _table(self):
        if self.table is None:
            raise RuntimeError("RESERVATION_TABLE_NAME is required")
        return self.table

    @staticmethod
    def _tenant_pk(tenant_id: str) -> str:
        return f"TENANT#{tenant_id}"

    @staticmethod
    def _resource_gsi_pk(tenant_id: str, resource_id: str) -> str:
        return f"TENANT#{tenant_id}#RESOURCE#{resource_id}"


class InMemoryReservationRepository(ReservationRepository):
    def __init__(
        self,
        resources: Iterable[Resource] | None = None,
        reservations: Iterable[Reservation] | None = None,
        settings: Iterable[TenantSettings] | None = None,
        channel_settings: Iterable[ChannelSettings] | None = None,
    ) -> None:
        self.resources: dict[tuple[str, str], Resource] = {}
        self.reservations: dict[tuple[str, str], Reservation] = {}
        self.settings: dict[str, TenantSettings] = {}
        self.channel_settings: dict[tuple[str, str], ChannelSettings] = {}
        self.audit_events: list[AuditEvent] = []
        self.scheduled_reminders: dict[tuple[str, str], ScheduledReminder] = {}
        for resource in resources or []:
            self.resources[(resource.tenant_id, resource.resource_id)] = resource
        for reservation in reservations or []:
            self.reservations[(reservation.tenant_id, reservation.reservation_id)] = reservation
        for tenant_settings in settings or []:
            self.settings[tenant_settings.tenant_id] = tenant_settings
        for item in channel_settings or []:
            self.channel_settings[(item.tenant_id, item.channel_id)] = item

    def get_resource(self, tenant_id: str, resource_id: str) -> Resource | None:
        return self.resources.get((tenant_id, resource_id))

    def list_resources_for_channel(self, tenant_id: str, channel_id: str) -> list[Resource]:
        return [
            resource
            for (resource_tenant_id, _), resource in self.resources.items()
            if resource_tenant_id == tenant_id and resource.channel_id == channel_id
        ]

    def list_resources(self, tenant_id: str) -> list[Resource]:
        return [
            resource
            for (resource_tenant_id, _), resource in self.resources.items()
            if resource_tenant_id == tenant_id
        ]

    def list_reservations_for_resource(
        self,
        tenant_id: str,
        resource_id: str,
        start_epoch: int,
        end_epoch: int,
    ) -> list[Reservation]:
        return [
            reservation
            for (reservation_tenant_id, _), reservation in self.reservations.items()
            if reservation_tenant_id == tenant_id
            and reservation.resource_id == resource_id
            and reservation.status == "active"
            and intervals_overlap(
                reservation.start_epoch,
                reservation.end_epoch,
                start_epoch,
                end_epoch,
            )
        ]

    def put_reservation(self, reservation: Reservation) -> None:
        key = (reservation.tenant_id, reservation.reservation_id)
        if key in self.reservations:
            raise ValueError("reservation already exists")
        self.reservations[key] = reservation

    def get_reservation(self, tenant_id: str, reservation_id: str) -> Reservation | None:
        return self.reservations.get((tenant_id, reservation_id))

    def save_reservation(self, reservation: Reservation) -> None:
        self.reservations[(reservation.tenant_id, reservation.reservation_id)] = reservation

    def list_reservations_in_window(
        self,
        tenant_id: str,
        start_epoch: int,
        end_epoch: int,
    ) -> list[Reservation]:
        return [
            reservation
            for (reservation_tenant_id, _), reservation in self.reservations.items()
            if reservation_tenant_id == tenant_id
            and reservation.status == "active"
            and intervals_overlap(
                reservation.start_epoch,
                reservation.end_epoch,
                start_epoch,
                end_epoch,
            )
        ]

    def list_user_future_reservations(
        self,
        tenant_id: str,
        user_id: str,
        after_epoch: int,
    ) -> list[Reservation]:
        return sorted(
            [
                reservation
                for (reservation_tenant_id, _), reservation in self.reservations.items()
                if reservation_tenant_id == tenant_id
                and reservation.status == "active"
                and reservation.owner_user_id == user_id
                and reservation.end_epoch > after_epoch
            ],
            key=lambda item: item.start_epoch,
        )

    def get_tenant_settings(self, tenant_id: str) -> TenantSettings:
        return self.settings.get(tenant_id, TenantSettings.default(tenant_id))

    def put_tenant_settings(self, settings: TenantSettings) -> None:
        self.settings[settings.tenant_id] = settings

    def get_channel_settings(self, tenant_id: str, channel_id: str) -> ChannelSettings:
        return self.channel_settings.get(
            (tenant_id, channel_id),
            ChannelSettings.default(tenant_id, channel_id),
        )

    def put_channel_settings(self, settings: ChannelSettings) -> None:
        self.channel_settings[(settings.tenant_id, settings.channel_id)] = settings

    def put_audit_event(self, event: AuditEvent) -> None:
        self.audit_events.append(event)

    def get_scheduled_reminder(self, tenant_id: str, reminder_id: str) -> ScheduledReminder | None:
        return self.scheduled_reminders.get((tenant_id, reminder_id))

    def list_scheduled_reminders(self, tenant_id: str) -> list[ScheduledReminder]:
        return [
            reminder
            for (reminder_tenant_id, _), reminder in self.scheduled_reminders.items()
            if reminder_tenant_id == tenant_id
        ]

    def put_scheduled_reminder(self, reminder: ScheduledReminder) -> None:
        self.scheduled_reminders[(reminder.tenant_id, reminder.reminder_id)] = reminder

    def delete_scheduled_reminder(self, tenant_id: str, reminder_id: str) -> None:
        self.scheduled_reminders.pop((tenant_id, reminder_id), None)


def intervals_overlap(first_start: int, first_end: int, second_start: int, second_end: int) -> bool:
    return first_start < second_end and second_start < first_end
