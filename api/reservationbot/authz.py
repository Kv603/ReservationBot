from __future__ import annotations

from reservationbot.models import Actor, Resource
from reservationbot.repository import ReservationRepository


class AuthorizationService:
    def __init__(self, repository: ReservationRepository) -> None:
        self.repository = repository

    def can_view_resource(self, actor: Actor, resource: Resource) -> bool:
        if self._is_denied(actor, resource):
            return False
        if self.can_manage_resource(actor, resource):
            return True
        if resource.policy.allow_view.contains(actor.slack_user_id, actor.user_group_ids):
            return True
        return actor.slack_channel_id == resource.channel_id

    def can_reserve_resource(self, actor: Actor, resource: Resource) -> bool:
        if self._is_denied(actor, resource):
            return False
        if self.can_manage_resource(actor, resource):
            return True
        if resource.policy.allow_reserve.contains(actor.slack_user_id, actor.user_group_ids):
            return True
        if not resource.policy.inherit_channel_defaults:
            return False
        return actor.slack_channel_id == resource.channel_id

    def can_manage_resource(self, actor: Actor, resource: Resource) -> bool:
        if self._is_denied(actor, resource):
            return False
        if actor.is_workspace_admin:
            return True
        if resource.policy.allow_manage.contains(actor.slack_user_id, actor.user_group_ids):
            return True
        return resource.channel_id in actor.managed_channel_ids

    def _is_denied(self, actor: Actor, resource: Resource) -> bool:
        return resource.policy.deny.contains(actor.slack_user_id, actor.user_group_ids)

