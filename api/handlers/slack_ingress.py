import base64
import json
import os
import time
import urllib.parse
from typing import Any

from aws_lambda_powertools import Logger, Tracer

from reservationbot.authz import AuthorizationService
from reservationbot.models import Actor, ReservationRequest
from reservationbot.repository import DynamoReservationRepository
from reservationbot.reservations import ReservationService
from reservationbot.slack_modals import (
    CANCEL_MODAL_CALLBACK,
    RESERVATION_MODAL_CALLBACK,
    SlackWebApiModalClient,
    build_reservation_modal,
    build_user_reservations_cancel_modal,
    parse_modal_submission,
    wants_cancel_dialog,
)
from reservationbot.slack_security import SlackRequestVerifier
from reservationbot.time_utils import configure_logging_for_execution_timezone, format_slack_datetime

configure_logging_for_execution_timezone()
logger = Logger()
tracer = Tracer()

repository = DynamoReservationRepository(os.environ.get("RESERVATION_TABLE_NAME", ""))
authz = AuthorizationService(repository)
reservation_service = ReservationService(repository, authz)
verifier = SlackRequestVerifier.from_environment()
modal_client = SlackWebApiModalClient()


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    body = _decode_body(event)
    headers = {key.lower(): value for key, value in event.get("headers", {}).items()}

    if not verifier.verify(
        timestamp=headers.get("x-slack-request-timestamp", ""),
        signature=headers.get("x-slack-signature", ""),
        raw_body=body,
    ):
        logger.warning("Rejected request with invalid Slack signature")
        return _json_response(401, {"error": "invalid_signature"})

    payload = _parse_payload(body, headers.get("content-type", ""))
    if payload.get("type") in {"view_submission", "block_actions", "view_closed"}:
        return _handle_interactivity(payload)

    command = payload.get("command")
    team_id = payload.get("team_id")
    channel_id = payload.get("channel_id")
    user_id = payload.get("user_id")

    if not team_id or not channel_id or not user_id:
        return _json_response(400, {"error": "missing_slack_context"})

    tenant_id = f"slack:{team_id}"
    logger.append_keys(
        tenant_id=tenant_id,
        slack_team_id=team_id,
        slack_channel_id=channel_id,
        slack_user_id=user_id,
        command_type=command or "unknown",
    )

    settings = repository.get_tenant_settings(tenant_id)
    reserve_commands = {"/reserve", *settings.slash_command_aliases}

    if command in reserve_commands:
        return _handle_reserve(tenant_id, payload)
    if command == "/resources":
        return _handle_resources(tenant_id, payload)

    return _json_response(
        200,
        {
            "response_type": "ephemeral",
            "text": "ReservationBot is installed. Try `/resources` or `/reserve <resource> <start> <end>`.",
        },
    )


def _handle_resources(tenant_id: str, payload: dict[str, str]) -> dict[str, Any]:
    channel_id = payload["channel_id"]
    actor = Actor(
        tenant_id=tenant_id,
        slack_user_id=payload["user_id"],
        slack_channel_id=channel_id,
        is_workspace_admin=False,
        user_group_ids=frozenset(),
    )
    resources = repository.list_resources_for_channel(tenant_id, channel_id)
    visible = [resource for resource in resources if authz.can_view_resource(actor, resource)]
    if not visible:
        text = "No reservable resources are configured for this channel."
    else:
        lines = [f"- {item.name} (`{item.resource_id}`): {item.kind}" for item in visible]
        text = "Resources in this channel:\n" + "\n".join(lines)
    return _json_response(200, {"response_type": "ephemeral", "text": text})


def _handle_reserve(tenant_id: str, payload: dict[str, str]) -> dict[str, Any]:
    text = payload.get("text", "").strip()
    settings = repository.get_tenant_settings(tenant_id)
    actor = Actor(
        tenant_id=tenant_id,
        slack_user_id=payload["user_id"],
        slack_channel_id=payload["channel_id"],
        is_workspace_admin=False,
        user_group_ids=frozenset(),
    )
    if text == "":
        return _open_reservation_dialog(tenant_id, payload, actor, settings)
    if wants_cancel_dialog(text):
        return _open_cancel_dialog(tenant_id, payload, settings)
    if text.lower().startswith("edit "):
        return _open_edit_reservation_dialog(tenant_id, payload, actor, settings, text)

    try:
        request = ReservationRequest.from_slack_text(
            tenant_id=tenant_id,
            channel_id=payload["channel_id"],
            user_id=payload["user_id"],
            text=text,
        )
        reservation = reservation_service.create_reservation(actor, request)
    except ValueError as exc:
        return _json_response(
            200,
            {"response_type": "ephemeral", "text": f"Could not create reservation: {exc}"},
        )

    return _json_response(
        200,
        {
            "response_type": "in_channel",
            "text": (
                f"Reserved `{reservation.resource_id}` for <@{reservation.owner_user_id}> "
                f"from {format_slack_datetime(reservation.start_epoch, settings.workspace_timezone)} "
                f"to {format_slack_datetime(reservation.end_epoch, settings.workspace_timezone)}."
                f"{f' Reason: {reservation.reason}' if reservation.reason else ''}"
            ),
        },
    )


def _open_reservation_dialog(
    tenant_id: str,
    payload: dict[str, str],
    actor: Actor,
    settings,
) -> dict[str, Any]:
    resources = [
        resource
        for resource in repository.list_resources_for_channel(tenant_id, payload["channel_id"])
        if authz.can_reserve_resource(actor, resource)
    ]
    if not resources:
        return _json_response(
            200,
            {
                "response_type": "ephemeral",
                "text": "There are no reservable resources available in this channel.",
            },
        )
    modal_client.open_view(
        payload["trigger_id"],
        build_reservation_modal(
            tenant_id=tenant_id,
            channel_id=payload["channel_id"],
            user_id=payload["user_id"],
            resources=resources,
            settings=settings,
        ),
    )
    return _json_response(200, {})


def _open_edit_reservation_dialog(
    tenant_id: str,
    payload: dict[str, str],
    actor: Actor,
    settings,
    text: str,
) -> dict[str, Any]:
    reservation_id = text.split(maxsplit=1)[1].strip()
    reservation = repository.get_reservation(tenant_id, reservation_id)
    if (
        not reservation
        or reservation.owner_user_id != payload["user_id"]
        or reservation.end_epoch <= int(time.time())
    ):
        return _json_response(
            200,
            {"response_type": "ephemeral", "text": "I could not find that future reservation for you."},
        )
    resources = [
        resource
        for resource in repository.list_resources_for_channel(tenant_id, reservation.channel_id)
        if authz.can_reserve_resource(actor, resource)
    ]
    if not resources:
        return _json_response(
            200,
            {"response_type": "ephemeral", "text": "There are no editable resources available."},
        )
    modal_client.open_view(
        payload["trigger_id"],
        build_reservation_modal(
            tenant_id=tenant_id,
            channel_id=reservation.channel_id,
            user_id=payload["user_id"],
            resources=resources,
            settings=settings,
            existing=reservation,
        ),
    )
    return _json_response(200, {})


def _open_cancel_dialog(
    tenant_id: str,
    payload: dict[str, str],
    settings,
) -> dict[str, Any]:
    reservations = repository.list_user_future_reservations(
        tenant_id,
        payload["user_id"],
        int(time.time()),
    )
    modal_client.open_view(
        payload["trigger_id"],
        build_user_reservations_cancel_modal(
            tenant_id=tenant_id,
            channel_id=payload["channel_id"],
            user_id=payload["user_id"],
            reservations=reservations,
            settings=settings,
        ),
    )
    return _json_response(200, {})


def _handle_interactivity(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("type") != "view_submission":
        return _json_response(200, {})
    callback_id = payload.get("view", {}).get("callback_id")
    if callback_id not in {RESERVATION_MODAL_CALLBACK, CANCEL_MODAL_CALLBACK}:
        return _json_response(200, {})
    try:
        result = parse_modal_submission(payload)
        metadata = json.loads(payload["view"].get("private_metadata") or "{}")
        actor = Actor(
            tenant_id=metadata["tenant_id"],
            slack_user_id=metadata["user_id"],
            slack_channel_id=metadata["channel_id"],
            is_workspace_admin=False,
            user_group_ids=frozenset(),
        )
        if result.action == "create" and result.reservation_request:
            reservation_service.create_reservation(actor, result.reservation_request)
        elif result.action == "edit" and result.reservation_request and result.reservation_id:
            reservation_service.update_reservation(
                actor,
                result.reservation_id,
                result.reservation_request,
            )
        elif result.action == "cancel" and result.reservation_id:
            reservation_service.cancel_reservation(actor, result.reservation_id)
    except ValueError as exc:
        return _json_response(
            200,
            {
                "response_action": "errors",
                "errors": {"resource": str(exc)},
            },
        )
    return _json_response(200, {"response_action": "clear"})


def _decode_body(event: dict[str, Any]) -> str:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body).decode("utf-8")
    return body


def _parse_payload(body: str, content_type: str) -> dict[str, Any]:
    if "application/json" in content_type:
        return json.loads(body)
    parsed = urllib.parse.parse_qs(body, keep_blank_values=True)
    if "payload" in parsed:
        return json.loads(parsed["payload"][0])
    return {key: values[0] for key, values in parsed.items()}


def _json_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }
