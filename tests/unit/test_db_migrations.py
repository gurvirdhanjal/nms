import pytest

from utils import db_migrations


pytestmark = pytest.mark.unit


def test_portable_datetime_type_uses_timestamp_for_postgresql(monkeypatch):
    assert db_migrations._portable_datetime_type('postgresql') == 'TIMESTAMP'


def test_portable_datetime_type_keeps_datetime_for_sqlite(monkeypatch):
    assert db_migrations._portable_datetime_type('sqlite') == 'DATETIME'


class _FakeUrl:
    def __init__(self, backend_name):
        self._backend_name = backend_name

    def get_backend_name(self):
        return self._backend_name


class _FakeEngine:
    def __init__(self, backend_name):
        self.url = _FakeUrl(backend_name)


class _FakeSession:
    def __init__(self):
        self.executed = []
        self.committed = False
        self.rolled_back = False

    def execute(self, statement):
        self.executed.append(str(statement))

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _FakeDb:
    def __init__(self, backend_name):
        self.engine = _FakeEngine(backend_name)
        self.session = _FakeSession()

    @staticmethod
    def text(statement):
        return statement


class _FakeInspector:
    def __init__(self, table_names, columns):
        self._table_names = table_names
        self._columns = columns

    def get_table_names(self):
        return self._table_names

    def get_columns(self, table_name):
        return self._columns[table_name]


def test_ensure_device_name_locked_column_skips_alter_when_column_already_exists(monkeypatch):
    fake_db = _FakeDb('postgresql')
    fake_inspector = _FakeInspector(
        ['device'],
        {'device': [{'name': 'device_id'}, {'name': 'name_locked'}]},
    )
    monkeypatch.setattr(db_migrations, 'db', fake_db)

    db_migrations._ensure_device_name_locked_column(inspector=fake_inspector)

    assert fake_db.session.executed == []
    assert fake_db.session.committed is True
    assert fake_db.session.rolled_back is False


def test_ensure_device_name_locked_column_adds_missing_postgres_column(monkeypatch):
    fake_db = _FakeDb('postgresql')
    fake_inspector = _FakeInspector(
        ['device'],
        {'device': [{'name': 'device_id'}]},
    )
    monkeypatch.setattr(db_migrations, 'db', fake_db)

    db_migrations._ensure_device_name_locked_column(inspector=fake_inspector)

    assert fake_db.session.executed == [
        'ALTER TABLE device ADD COLUMN IF NOT EXISTS name_locked BOOLEAN NOT NULL DEFAULT FALSE'
    ]
    assert fake_db.session.committed is True
    assert fake_db.session.rolled_back is False
