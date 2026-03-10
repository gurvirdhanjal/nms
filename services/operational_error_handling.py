from __future__ import annotations

import asyncio
import logging
import socket
from urllib.error import URLError

import requests
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError


SCHEMA_DRIFT_MARKERS = (
    'undefinedcolumn',
    'undefinedtable',
    'undefinedobject',
    'does not exist',
)

EXPECTED_OS_ERROR_CODES = {
    101,   # network unreachable
    111,   # connection refused
    113,   # no route to host
    10060, # windows timed out
    10061, # windows connection refused
    10065, # windows no route to host
}


def summarize_exception(exc: Exception, max_length: int = 240) -> str:
    text = ' '.join(str(exc or '').split())
    if len(text) <= max_length:
        return text
    return f'{text[: max_length - 3]}...'


def is_expected_operational_exception(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ProxyError,
            asyncio.TimeoutError,
            TimeoutError,
            ConnectionRefusedError,
            socket.timeout,
            URLError,
            InterfaceError,
            OperationalError,
        ),
    ):
        return True

    if isinstance(exc, ProgrammingError):
        lowered = summarize_exception(exc, max_length=800).lower()
        return any(marker in lowered for marker in SCHEMA_DRIFT_MARKERS)

    if isinstance(exc, OSError):
        return getattr(exc, 'errno', None) in EXPECTED_OS_ERROR_CODES

    return False


def log_operational_exception(
    logger: logging.Logger,
    context: str,
    exc: Exception,
    *,
    error_code: str | None = None,
    expected_level: str = 'warning',
) -> bool:
    summary = summarize_exception(exc)
    if is_expected_operational_exception(exc):
        getattr(logger, expected_level, logger.warning)(
            "%s: code=%s error=%s",
            context,
            error_code or type(exc).__name__,
            summary,
        )
        return True

    logger.exception(
        "%s: code=%s error=%s",
        context,
        error_code or type(exc).__name__,
        summary,
    )
    return False
