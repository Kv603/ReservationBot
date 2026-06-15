from __future__ import annotations

import time

from reservationbot.models import Actor, AuditAction, AuditEvent, TenantSettings
from reservationbot.repository import ReservationRepository
from reservationbot.slack_messages import SlackMessage, SlackMessageClient


class AuditService:
    def __init__(self, repository: ReservationRepository, slack_client: SlackMessageClient) -> None:
        self.repository = repository
        self.slack_client = slack_client

    def record(
        self,
        *,
        actor: Actor,
        action: AuditAction,
        summary: str,
        entity_type: str,
        entity_id: str,
        metadata: dict | None = None,
        settings: TenantSettings | None = None,
        destination_id: str | None = None,
        old_audit_destination_id: str | None = None,
        new_audit_destination_id: str | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            tenant_id=actor.tenant_id,
            action=action,
            actor_user_id=actor.slack_user_id,
            actor_handle=actor.slack_handle,
            summary=summary,
            entity_type=entity_type,
            entity_id=entity_id,
            occurred_at_epoch=int(time.time()),
            old_audit_destination_id=old_audit_destination_id,
            new_audit_destination_id=new_audit_destination_id,
            metadata=metadata or {},
        )
        self.repository.put_audit_event(event)
        target = destination_id or self._audit_destination_id(actor.tenant_id, settings)
        if target:
            self.slack_client.post_message(
                SlackMessage(
                    destination_id=target,
                    text=self._render_event(event, actor),
                )
            )
        return event

    def _audit_destination_id(
        self,
        tenant_id: str,
        settings: TenantSettings | None,
    ) -> str | None:
        effective = settings or self.repository.get_tenant_settings(tenant_id)
        if not effective.audit_destination:
            return None
        return effective.audit_destination.destination_id

    @staticmethod
    def _render_event(event: AuditEvent, actor: Actor) -> str:
        handle = actor.mention
        if event.actor_handle:
            handle = f"{handle} ({event.actor_handle})"
        return f"{handle} {event.summary}"

