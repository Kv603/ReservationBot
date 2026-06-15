from __future__ import annotations

import time
from dataclasses import replace
from typing import Protocol

from reservationbot.audit import AuditService
from reservationbot.authz import AuthorizationService
from reservationbot.models import (
    Actor,
    AuditAction,
    DurationPolicy,
    Reservation,
    ReservationRequest,
    Resource,
    ResourceKind,
)
from reservationbot.repository import ReservationRepository
from reservationbot.slack_messages import NoopSlackMessageClient


class ScheduleRefresher(Protocol):
    def refresh_schedules_for_reservation(
        self,
        reservation: Reservation,
        now_epoch: int,
    ) -> list:
        raise NotImplementedError


class ReservationService:
    def __init__(
        self,
        repository: ReservationRepository,
        authz: AuthorizationService,
        audit: AuditService | None = None,
        schedule_refresher: ScheduleRefresher | None = None,
    ) -> None:
        self.repository = repository
        self.authz = authz
        self.audit = audit or AuditService(repository, NoopSlackMessageClient())
        self.schedule_refresher = schedule_refresher

    def create_reservation(self, actor: Actor, request: ReservationRequest) -> Reservation:
        settings = self.repository.get_tenant_settings(request.tenant_id)
        resource = self.repository.get_resource(request.tenant_id, request.resource_id)
        if not resource:
            raise ValueError("resource not found")
        duration_policy = self._effective_duration_policy(resource, settings.duration_policy)
        self._validate_request(
            request,
            duration_policy.minimum_seconds,
            duration_policy.maximum_seconds,
        )
        if resource.channel_id != request.channel_id:
            raise ValueError("resource is not assigned to this Slack channel")
        if not self.authz.can_reserve_resource(actor, resource):
            raise ValueError("you are not allowed to reserve this resource")

        conflicts = self.find_conflicts(resource, request.start_epoch, request.end_epoch)
        self._raise_if_capacity_exceeded(resource, request, conflicts)

        reservation = Reservation.create(
            tenant_id=request.tenant_id,
            resource_id=request.resource_id,
            channel_id=request.channel_id,
            owner_user_id=request.owner_user_id,
            start_epoch=request.start_epoch,
            end_epoch=request.end_epoch,
            quantity=request.quantity,
            reason=request.reason,
        )
        self.repository.put_reservation(reservation)
        self.audit.record(
            actor=actor,
            action=AuditAction.RESERVATION_CREATED,
            summary=(
                f"created reservation `{reservation.reservation_id}` for resource "
                f"`{reservation.resource_id}`."
            ),
            entity_type="reservation",
            entity_id=reservation.reservation_id,
            metadata=reservation.to_json(),
            settings=settings,
        )
        if self.schedule_refresher:
            self.schedule_refresher.refresh_schedules_for_reservation(
                reservation,
                int(time.time()),
            )
        return reservation

    def update_reservation(
        self,
        actor: Actor,
        reservation_id: str,
        request: ReservationRequest,
    ) -> Reservation:
        existing = self.repository.get_reservation(actor.tenant_id, reservation_id)
        if not existing:
            raise ValueError("reservation not found")
        current_resource = self.repository.get_resource(actor.tenant_id, existing.resource_id)
        if not current_resource:
            raise ValueError("resource not found")
        if (
            existing.owner_user_id != actor.slack_user_id
            and not self.authz.can_manage_resource(actor, current_resource)
        ):
            raise ValueError("you are not allowed to edit this reservation")

        settings = self.repository.get_tenant_settings(request.tenant_id)
        new_resource = self.repository.get_resource(request.tenant_id, request.resource_id)
        if not new_resource:
            raise ValueError("resource not found")
        duration_policy = self._effective_duration_policy(new_resource, settings.duration_policy)
        self._validate_request(
            request,
            duration_policy.minimum_seconds,
            duration_policy.maximum_seconds,
        )
        if new_resource.channel_id != request.channel_id:
            raise ValueError("resource is not assigned to this Slack channel")
        if not self.authz.can_reserve_resource(actor, new_resource):
            raise ValueError("you are not allowed to reserve this resource")

        conflicts = [
            conflict
            for conflict in self.find_conflicts(
                new_resource,
                request.start_epoch,
                request.end_epoch,
            )
            if conflict.reservation_id != reservation_id
        ]
        self._raise_if_capacity_exceeded(new_resource, request, conflicts)

        updated = replace(
            existing,
            resource_id=request.resource_id,
            channel_id=request.channel_id,
            owner_user_id=request.owner_user_id,
            start_epoch=request.start_epoch,
            end_epoch=request.end_epoch,
            quantity=request.quantity,
            reason=request.reason,
            status="active",
        )
        self.repository.save_reservation(updated)
        self.audit.record(
            actor=actor,
            action=AuditAction.RESERVATION_CHANGED,
            summary=f"updated reservation `{updated.reservation_id}`.",
            entity_type="reservation",
            entity_id=updated.reservation_id,
            metadata=updated.to_json(),
            settings=settings,
        )
        if self.schedule_refresher:
            self.schedule_refresher.refresh_schedules_for_reservation(
                updated,
                int(time.time()),
            )
        return updated

    def cancel_reservation(
        self,
        actor: Actor,
        reservation_id: str,
        *,
        status: str = "cancelled",
        now_epoch: int | None = None,
    ) -> Reservation:
        reservation = self.repository.get_reservation(actor.tenant_id, reservation_id)
        if not reservation:
            raise ValueError("reservation not found")
        resource = self.repository.get_resource(actor.tenant_id, reservation.resource_id)
        if not resource:
            raise ValueError("resource not found")
        if (
            reservation.owner_user_id != actor.slack_user_id
            and not self.authz.can_manage_resource(actor, resource)
        ):
            raise ValueError("you are not allowed to cancel this reservation")
        updated = replace(reservation, status=status)
        self.repository.save_reservation(updated)
        self.audit.record(
            actor=actor,
            action=AuditAction.RESERVATION_CHANGED,
            summary=f"cancelled reservation `{updated.reservation_id}`.",
            entity_type="reservation",
            entity_id=updated.reservation_id,
            metadata=updated.to_json(),
        )
        if self.schedule_refresher:
            self.schedule_refresher.refresh_schedules_for_reservation(
                updated,
                now_epoch or int(time.time()),
            )
        return updated

    def find_conflicts(self, resource: Resource, start_epoch: int, end_epoch: int) -> list[Reservation]:
        related_resources = self._conflict_resource_ids(resource)
        conflicts: list[Reservation] = []
        for resource_id in related_resources:
            conflicts.extend(
                self.repository.list_reservations_for_resource(
                    resource.tenant_id,
                    resource_id,
                    start_epoch,
                    end_epoch,
                )
            )
        return conflicts

    def _conflict_resource_ids(self, resource: Resource) -> set[str]:
        resources = {item.resource_id: item for item in self.repository.list_resources(resource.tenant_id)}
        descendants = self._descendant_ids(resource.resource_id, resources)
        ancestors = self._ancestor_ids(resource, resources)
        if resource.kind == ResourceKind.CONTAINER:
            return {resource.resource_id, *descendants, *ancestors}
        return {resource.resource_id, *ancestors}

    def _descendant_ids(self, resource_id: str, resources: dict[str, Resource]) -> set[str]:
        descendants: set[str] = set()
        frontier = [resource_id]
        while frontier:
            current = frontier.pop()
            children = [
                resource
                for resource in resources.values()
                if resource.parent_resource_id == current
            ]
            for child in children:
                descendants.add(child.resource_id)
                frontier.append(child.resource_id)
        return descendants

    def _ancestor_ids(self, resource: Resource, resources: dict[str, Resource]) -> set[str]:
        ancestors: set[str] = set()
        current = resource
        while current.parent_resource_id:
            parent = resources.get(current.parent_resource_id)
            if not parent:
                break
            ancestors.add(parent.resource_id)
            current = parent
        return ancestors

    def _raise_if_capacity_exceeded(
        self,
        resource: Resource,
        request: ReservationRequest,
        conflicts: list[Reservation],
    ) -> None:
        ancestor_or_descendant_conflicts = [
            conflict for conflict in conflicts if conflict.resource_id != resource.resource_id
        ]
        if ancestor_or_descendant_conflicts:
            raise ValueError("reservation conflicts with an enclosing or enclosed resource")

        if resource.kind == ResourceKind.FUNGIBLE:
            used_capacity = sum(conflict.quantity for conflict in conflicts)
            if used_capacity + request.quantity > resource.capacity:
                raise ValueError("not enough capacity is available")
            return

        if conflicts:
            raise ValueError("resource is already reserved for that time")
        if request.quantity != 1:
            raise ValueError("quantity can only be greater than one for fungible resources")

    def _effective_duration_policy(
        self,
        resource: Resource,
        tenant_policy: DurationPolicy,
    ) -> DurationPolicy:
        resource_policy = resource.metadata.get("duration_policy")
        if resource_policy:
            return DurationPolicy.from_json(resource_policy)
        channel_policy = self.repository.get_channel_settings(
            resource.tenant_id,
            resource.channel_id,
        ).duration_policy
        return channel_policy or tenant_policy

    @staticmethod
    def _validate_request(
        request: ReservationRequest,
        minimum_seconds: int,
        maximum_seconds: int,
    ) -> None:
        if request.tenant_id == "":
            raise ValueError("tenant is required")
        if request.start_epoch >= request.end_epoch:
            raise ValueError("reservation end must be after start")
        if request.quantity < 1:
            raise ValueError("quantity must be at least one")
        duration = request.end_epoch - request.start_epoch
        if duration < minimum_seconds:
            raise ValueError("reservation is shorter than the minimum duration")
        if duration > maximum_seconds:
            raise ValueError("reservation is longer than the maximum duration")
