import pytest

from utils import db_migrations


pytestmark = pytest.mark.unit


def test_portable_datetime_type_uses_timestamp_for_postgresql(monkeypatch):
    assert db_migrations._portable_datetime_type('postgresql') == 'TIMESTAMP'


def test_portable_datetime_type_keeps_datetime_for_sqlite(monkeypatch):
    assert db_migrations._portable_datetime_type('sqlite') == 'DATETIME'
