import logging

import pytest
import requests
from sqlalchemy.exc import ProgrammingError

from services import operational_error_handling as service


pytestmark = pytest.mark.unit


class StubLogger:
    def __init__(self):
        self.calls = []

    def warning(self, message, *args):
        self.calls.append(('warning', message % args))

    def debug(self, message, *args):
        self.calls.append(('debug', message % args))

    def exception(self, message, *args):
        self.calls.append(('exception', message % args))


def test_summarize_exception_compacts_multiline_text():
    summary = service.summarize_exception(Exception("line one\nline two\tline three"))
    assert summary == 'line one line two line three'


def test_summarize_exception_truncates_long_text():
    summary = service.summarize_exception(Exception('x' * 20), max_length=10)
    assert summary == 'xxxxxxx...'


def test_expected_operational_exception_detects_network_and_schema_drift():
    timeout_exc = requests.exceptions.Timeout('connect timed out')
    schema_exc = ProgrammingError(
        "SELECT * FROM tracked_devices",
        {},
        Exception('psycopg2.errors.UndefinedColumn: column tracked_devices.last_policy_sync_at does not exist'),
    )

    assert service.is_expected_operational_exception(timeout_exc) is True
    assert service.is_expected_operational_exception(schema_exc) is True


def test_expected_operational_exception_detects_expected_oserror_errno():
    assert service.is_expected_operational_exception(OSError(10061, 'connection refused')) is True


def test_log_operational_exception_uses_warning_for_expected_issue():
    logger = StubLogger()

    handled = service.log_operational_exception(
        logger,
        '[TrackingReconcile] reconciliation failed',
        requests.exceptions.ConnectionError('connection refused'),
        error_code='TRACKING_RECONCILIATION_FAILED',
    )

    assert handled is True
    assert logger.calls == [
        (
            'warning',
            '[TrackingReconcile] reconciliation failed: code=TRACKING_RECONCILIATION_FAILED error=connection refused',
        )
    ]


def test_log_operational_exception_uses_custom_expected_level():
    logger = StubLogger()

    handled = service.log_operational_exception(
        logger,
        '[AgentScan] identity fetch failed',
        TimeoutError('timed out'),
        error_code='AGENT_IDENTITY_FAILED',
        expected_level='debug',
    )

    assert handled is True
    assert logger.calls == [
        (
            'debug',
            '[AgentScan] identity fetch failed: code=AGENT_IDENTITY_FAILED error=timed out',
        )
    ]


def test_log_operational_exception_uses_exception_for_unexpected_issue():
    logger = StubLogger()

    handled = service.log_operational_exception(
        logger,
        '[TrackingReconcile] reconciliation failed',
        ValueError('unexpected bug'),
        error_code='TRACKING_RECONCILIATION_FAILED',
    )

    assert handled is False
    assert logger.calls == [
        (
            'exception',
            '[TrackingReconcile] reconciliation failed: code=TRACKING_RECONCILIATION_FAILED error=unexpected bug',
        )
    ]
