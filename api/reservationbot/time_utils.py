from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def configure_logging_for_execution_timezone() -> None:
    logging.Formatter.converter = time.localtime


def format_slack_datetime(epoch_seconds: int, workspace_timezone: str) -> str:
    fallback = format_plain_datetime(epoch_seconds, workspace_timezone)
    return f"<!date^{epoch_seconds}^{{date_short_pretty}} at {{time}}|{fallback}>"


def format_plain_datetime(epoch_seconds: int, timezone_name: str) -> str:
    timezone = _zoneinfo_or_utc(timezone_name)
    value = datetime.fromtimestamp(epoch_seconds, tz=timezone)
    return value.strftime("%Y-%m-%d %I:%M %p %Z")


def _zoneinfo_or_utc(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")

