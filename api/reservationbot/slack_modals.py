from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reservationbot.models import Reservation, ReservationRequest, Resource, TenantSettings
from reservationbot.time_utils import format_plain_datetime

RESERVATION_MODAL_CALLBACK = "reservation_create_edit"
CANCEL_MODAL_CALLBACK = "reservation_cancel_one"


class SlackModalClient:
    def open_view(self, trigger_id: str, view: dict[str, Any]) -> None:
        raise NotImplementedError


class NoopSlackModalClient(SlackModalClient):
    def __init__(self) -> None:
        self.opened_views: list[tuple[str, dict[str, Any]]] = []

    def open_view(self, trigger_id: str, view: dict[str, Any]) -> None:
        self.opened_views.append((trigger_id, view))


class SlackWebApiModalClient(SlackModalClient):
    def __init__(self, bot_token: str | None = None) -> None:
        self.bot_token = bot_token

    @property
    def token(self) -> str | None:
        if self.bot_token:
            return self.bot_token
        token = os.environ.get("SLACK_BOT_TOKEN")
        if token:
            self.bot_token = token
            return token
        secret_arn = os.environ.get("SLACK_BOT_TOKEN_SECRET_ARN")
        if secret_arn:
            import boto3

            self.bot_token = boto3.client("secretsmanager").get_secret_value(
                SecretId=secret_arn
            )["SecretString"]
            return self.bot_token
        return None

    def open_view(self, trigger_id: str, view: dict[str, Any]) -> None:
        token = self.token
        if not token:
            raise RuntimeError("SLACK_BOT_TOKEN is required to open Slack modals")
        request = urllib.request.Request(
            "https://slack.com/api/views.open",
            data=json.dumps({"trigger_id": trigger_id, "view": view}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Slack views.open failed: {exc}") from exc
        if not payload.get("ok"):
            raise RuntimeError(f"Slack views.open rejected modal: {payload.get('error')}")


@dataclass(frozen=True)
class ModalSubmissionResult:
    action: str
    reservation_request: ReservationRequest | None = None
    reservation_id: str | None = None


def build_reservation_modal(
    *,
    tenant_id: str,
    channel_id: str,
    user_id: str,
    resources: list[Resource],
    settings: TenantSettings,
    existing: Reservation | None = None,
) -> dict[str, Any]:
    title = "Edit Reservation" if existing else "Reserve Resource"
    metadata = {
        "tenant_id": tenant_id,
        "channel_id": channel_id,
        "user_id": user_id,
        "reservation_id": existing.reservation_id if existing else None,
        "workspace_timezone": settings.workspace_timezone,
    }
    initial_date, initial_time, initial_duration = _initial_datetime(existing, settings)
    return {
        "type": "modal",
        "callback_id": RESERVATION_MODAL_CALLBACK,
        "title": {"type": "plain_text", "text": title},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": json.dumps(metadata),
        "blocks": [
            {
                "type": "input",
                "block_id": "resource",
                "label": {"type": "plain_text", "text": "Resource"},
                "element": {
                    "type": "static_select",
                    "action_id": "resource_id",
                    "placeholder": {"type": "plain_text", "text": "Choose a resource"},
                    "options": [_resource_option(resource) for resource in resources[:100]],
                    **_initial_resource_option(resources, existing),
                },
            },
            {
                "type": "input",
                "block_id": "date",
                "label": {"type": "plain_text", "text": "Date"},
                "element": {
                    "type": "datepicker",
                    "action_id": "start_date",
                    "initial_date": initial_date,
                },
            },
            {
                "type": "input",
                "block_id": "time",
                "label": {"type": "plain_text", "text": "Start time"},
                "element": {
                    "type": "timepicker",
                    "action_id": "start_time",
                    "initial_time": initial_time,
                },
            },
            {
                "type": "input",
                "block_id": "duration",
                "label": {"type": "plain_text", "text": "Duration"},
                "element": {
                    "type": "static_select",
                    "action_id": "duration_seconds",
                    "options": _duration_options(settings),
                    "initial_option": _duration_option(initial_duration),
                },
            },
            {
                "type": "input",
                "block_id": "reason",
                "optional": True,
                "label": {"type": "plain_text", "text": "Reason"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "reason",
                    "multiline": True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Optional markdown and emojis are welcome",
                    },
                    **({"initial_value": existing.reason} if existing and existing.reason else {}),
                },
            },
        ],
    }


def build_user_reservations_cancel_modal(
    *,
    tenant_id: str,
    channel_id: str,
    user_id: str,
    reservations: list[Reservation],
    settings: TenantSettings,
) -> dict[str, Any]:
    metadata = {"tenant_id": tenant_id, "channel_id": channel_id, "user_id": user_id}
    if not reservations:
        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "You do not have future reservations to cancel."},
            }
        ]
        submit = None
    else:
        blocks = [
            {
                "type": "input",
                "block_id": "reservation",
                "label": {"type": "plain_text", "text": "Reservation"},
                "element": {
                    "type": "static_select",
                    "action_id": "reservation_id",
                    "placeholder": {"type": "plain_text", "text": "Choose a reservation"},
                    "options": [
                        _reservation_option(reservation, settings) for reservation in reservations[:100]
                    ],
                },
            }
        ]
        submit = {"type": "plain_text", "text": "Cancel reservation"}
    view = {
        "type": "modal",
        "callback_id": CANCEL_MODAL_CALLBACK,
        "title": {"type": "plain_text", "text": "My Reservations"},
        "close": {"type": "plain_text", "text": "Close"},
        "private_metadata": json.dumps(metadata),
        "blocks": blocks,
    }
    if submit:
        view["submit"] = submit
    return view


def parse_modal_submission(payload: dict[str, Any]) -> ModalSubmissionResult:
    callback_id = payload["view"]["callback_id"]
    metadata = json.loads(payload["view"].get("private_metadata") or "{}")
    values = payload["view"]["state"]["values"]
    if callback_id == RESERVATION_MODAL_CALLBACK:
        resource_id = values["resource"]["resource_id"]["selected_option"]["value"]
        start_date = values["date"]["start_date"]["selected_date"]
        start_time = values["time"]["start_time"]["selected_time"]
        duration = int(values["duration"]["duration_seconds"]["selected_option"]["value"])
        reason_value = values.get("reason", {}).get("reason", {}).get("value")
        start_epoch = _epoch_from_date_time(
            start_date,
            start_time,
            metadata.get("workspace_timezone") or "UTC",
        )
        return ModalSubmissionResult(
            action="edit" if metadata.get("reservation_id") else "create",
            reservation_id=metadata.get("reservation_id"),
            reservation_request=ReservationRequest(
                tenant_id=metadata["tenant_id"],
                resource_id=resource_id,
                channel_id=metadata["channel_id"],
                owner_user_id=metadata["user_id"],
                start_epoch=start_epoch,
                end_epoch=start_epoch + duration,
                reason=reason_value,
            ),
        )
    if callback_id == CANCEL_MODAL_CALLBACK:
        return ModalSubmissionResult(
            action="cancel",
            reservation_id=values["reservation"]["reservation_id"]["selected_option"]["value"],
        )
    raise ValueError(f"unsupported modal callback: {callback_id}")


def wants_cancel_dialog(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"list", "mine", "my", "cancel", "reservations", "my reservations"}


def _resource_option(resource: Resource) -> dict[str, Any]:
    return {
        "text": {"type": "plain_text", "text": resource.name[:75]},
        "value": resource.resource_id,
    }


def _initial_resource_option(resources: list[Resource], existing: Reservation | None) -> dict[str, Any]:
    if not existing:
        return {}
    selected = next(
        (resource for resource in resources if resource.resource_id == existing.resource_id),
        None,
    )
    return {"initial_option": _resource_option(selected)} if selected else {}


def _reservation_option(reservation: Reservation, settings: TenantSettings) -> dict[str, Any]:
    label = (
        f"{reservation.resource_id} "
        f"{format_plain_datetime(reservation.start_epoch, settings.workspace_timezone)}"
    )
    return {
        "text": {"type": "plain_text", "text": label[:75]},
        "value": reservation.reservation_id,
    }


def _duration_options(settings: TenantSettings) -> list[dict[str, Any]]:
    minimum = settings.duration_policy.minimum_seconds
    maximum = settings.duration_policy.maximum_seconds
    candidates = [15, 30, 45, 60, 90, 120, 180, 240, 360, 480, 720]
    options = [
        _duration_option(minutes * 60)
        for minutes in candidates
        if minimum <= minutes * 60 <= maximum
    ]
    return options or [_duration_option(minimum)]


def _duration_option(seconds: int) -> dict[str, Any]:
    minutes = seconds // 60
    label = f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"
    return {"text": {"type": "plain_text", "text": label}, "value": str(seconds)}


def _initial_datetime(existing: Reservation | None, settings: TenantSettings) -> tuple[str, str, int]:
    timezone = _zoneinfo_or_utc(settings.workspace_timezone)
    if existing:
        value = datetime.fromtimestamp(existing.start_epoch, tz=timezone)
        duration = existing.end_epoch - existing.start_epoch
    else:
        now = datetime.fromtimestamp(int(time.time()), tz=timezone)
        minute = ((now.minute // 15) + 1) * 15
        if minute >= 60:
            now = now.replace(hour=(now.hour + 1) % 24, minute=0, second=0, microsecond=0)
        else:
            now = now.replace(minute=minute, second=0, microsecond=0)
        value = now
        duration = max(settings.duration_policy.minimum_seconds, 30 * 60)
    return value.strftime("%Y-%m-%d"), value.strftime("%H:%M"), duration


def _epoch_from_date_time(date_value: str, time_value: str, timezone_name: str) -> int:
    timezone = _zoneinfo_or_utc(timezone_name)
    value = datetime.strptime(f"{date_value} {time_value}", "%Y-%m-%d %H:%M")
    return int(value.replace(tzinfo=timezone).timestamp())


def _zoneinfo_or_utc(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")
