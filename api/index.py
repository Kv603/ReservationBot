from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Request, Response

from handlers.slack_ingress import handler as slack_handler

app = FastAPI(title="ReservationBot API")


@app.get("/")
async def health() -> dict[str, str]:
    return {"service": "reservationbot", "status": "ok"}


@app.api_route(
    "/slack/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def slack(path: str, request: Request) -> Response:
    body = await request.body()
    event = {
        "body": body.decode("utf-8"),
        "isBase64Encoded": False,
        "headers": dict(request.headers),
        "requestContext": {
            "http": {
                "method": request.method,
                "path": request.url.path,
            }
        },
        "rawPath": request.url.path,
        "rawQueryString": request.url.query,
    }
    result: dict[str, Any] = slack_handler(event, None)
    response_body = result.get("body", "")
    if isinstance(response_body, (dict, list)):
        response_body = json.dumps(response_body)
    return Response(
        content=response_body,
        status_code=int(result.get("statusCode", 200)),
        headers=result.get("headers", {}),
    )

