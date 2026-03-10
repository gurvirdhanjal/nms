import pytest
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from extensions import db
from models.department import Department
from models.site import Site
from models.user import User


@pytest.fixture(scope='session')
def app(tmp_path_factory):
    db_dir = tmp_path_factory.mktemp('db')
    db_file = db_dir / 'test_device_console.db'
    app = create_app(
        {
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': f'sqlite:///{db_file}',
            'WTF_CSRF_ENABLED': False,
            'REPORT_RATE_LIMIT_PER_MINUTE': 100,
            'REPORT_EXPORT_RATE_LIMIT_PER_MINUTE': 100,
        }
    )

    with app.app_context():
        db.drop_all()
        db.create_all()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _app_context(app):
    with app.app_context():
        yield


@pytest.fixture(autouse=True)
def _reset_db(app, _app_context):
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()

        site_alpha = Site(site_name='Alpha Site', site_code='ALPHA')
        site_beta = Site(site_name='Beta Site', site_code='BETA')
        db.session.add_all([site_alpha, site_beta])
        db.session.flush()

        dept_alpha = Department(name='Alpha Department', site_id=site_alpha.id)
        dept_beta = Department(name='Beta Department', site_id=site_beta.id)
        db.session.add_all([dept_alpha, dept_beta])
        db.session.flush()

        db.session.add_all(
            [
                User(id=1, username='test-admin', password='x', role='admin', email='test-admin@example.com', is_active=True),
                User(
                    id=2,
                    username='test-manager',
                    password='x',
                    role='manager',
                    email='test-manager@example.com',
                    is_active=True,
                    site_id=site_alpha.id,
                ),
                User(
                    id=3,
                    username='test-viewer',
                    password='x',
                    role='viewer',
                    email='test-viewer@example.com',
                    is_active=True,
                    site_id=site_alpha.id,
                    department_id=dept_alpha.id,
                ),
                User(
                    id=4,
                    username='test-operator',
                    password='x',
                    role='operator',
                    email='test-operator@example.com',
                    is_active=True,
                    site_id=site_alpha.id,
                    department_id=dept_alpha.id,
                ),
            ]
        )
        db.session.commit()
    yield


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['role'] = 'admin'
        sess['username'] = 'test-admin'
        sess['user_id'] = 1
        sess['last_activity'] = datetime.utcnow().isoformat()
    return client


@pytest.fixture()
def manager_client(app):
    client = app.test_client()
    manager = User.query.get(2)
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['role'] = 'manager'
        sess['username'] = 'test-manager'
        sess['user_id'] = 2
        sess['site_id'] = manager.site_id
        sess['department_id'] = manager.department_id
        sess['last_activity'] = datetime.utcnow().isoformat()
    return client


@pytest.fixture()
def viewer_client(app):
    client = app.test_client()
    viewer = User.query.get(3)
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['role'] = 'viewer'
        sess['username'] = 'test-viewer'
        sess['user_id'] = 3
        sess['site_id'] = viewer.site_id
        sess['department_id'] = viewer.department_id
        sess['last_activity'] = datetime.utcnow().isoformat()
    return client


@pytest.fixture()
def operator_client(app):
    client = app.test_client()
    operator = User.query.get(4)
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['role'] = 'operator'
        sess['username'] = 'test-operator'
        sess['user_id'] = 4
        sess['site_id'] = operator.site_id
        sess['department_id'] = operator.department_id
        sess['last_activity'] = datetime.utcnow().isoformat()
    return client
