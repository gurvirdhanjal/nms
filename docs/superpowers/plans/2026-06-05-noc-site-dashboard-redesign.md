# NOC Site Dashboard Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static, flat site dashboard with a live-polling three-tier NOC layout (KPI cards → dept health cards → expandable dept panels with device rows) and add a global Alerts sidebar page.

**Architecture:** The existing `/api/sites/<id>/dashboard-stats` endpoint + JS polling loop are already wired; this plan enhances the endpoint with dept aggregates, adds two new JSON endpoints (device modal, alerts), rewrites the HTML template, extends the JS, and creates the Alerts blueprint. No schema changes — `DashboardEvent` already has `site_id`, `department_id`, `resolved`, and `resolved_at` columns.

**Tech Stack:** Flask/SQLAlchemy (Python), Jinja2 templates, vanilla JS (no framework), Bootstrap 5.1 dark theme, `sites_bp` and new `alerts_bp` Blueprints, `@require_login` + `scoped_query` RBAC, Lucide icons via data attribute.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `routes/sites.py` | Modify | Enhance `dashboard-stats`; add device modal endpoint |
| `routes/alerts.py` | Create | Alerts Blueprint: HTML page + JSON API + resolve action |
| `app.py` | Modify | Register `alerts_bp` |
| `templates/sites/dashboard.html` | Rewrite | Three-tier NOC layout |
| `templates/alerts.html` | Create | Global alerts page (filterable by site) |
| `templates/base.html` | Modify | Add Alerts sidebar link with live badge |
| `static/css/sites.css` | Append | Dept card, panel, search box, modal, alert banner styles |
| `static/js/site_dashboard.js` | Rewrite | Dept panels, search, modal, alert banner, dept card update |
| `static/js/alerts.js` | Create | Filter bar, table polling, resolve action |
| `tests/test_site_dashboard_api.py` | Create | API tests for all three new/modified endpoints |

---

## Task 1: Enhance `dashboard-stats` — add dept aggregates + alert count

**Files:**
- Modify: `routes/sites.py`
- Test: `tests/test_site_dashboard_api.py`

### Background

`build_device_availability_snapshot()` already returns `device_states` (`{device_id: state_string}`) and `device_scan_details`. The current endpoint only returns raw device list. We need to add:
- `dept_aggregates` — per-department health computed from `device_states`
- `active_alert_count` — count of unresolved `DashboardEvent` rows for this site

`DashboardEvent` has a denormalized `site_id` column for efficient queries.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_site_dashboard_api.py`:

```python
import pytest
from app import create_app
from extensions import db as _db
from models.site import Site
from models.device import Device
from models.dashboard import DashboardEvent
from models.department import Department
import uuid


@pytest.fixture(scope='module')
def app():
    application = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:', 'WTF_CSRF_ENABLED': False})
    with application.app_context():
        _db.create_all()
        yield application
        _db.drop_all()


@pytest.fixture(scope='module')
def client(app):
    return app.test_client()


@pytest.fixture(scope='module')
def seed_data(app):
    with app.app_context():
        site = Site(site_name='Test Site', address='123 Test St', timezone='UTC')
        _db.session.add(site)
        _db.session.flush()

        dept_it = Department(name='IT', site_id=site.id)
        dept_hr = Department(name='HR', site_id=site.id)
        _db.session.add_all([dept_it, dept_hr])
        _db.session.flush()

        dev1 = Device(device_name='switch-01', device_ip='10.0.0.1', device_type='Switch', site_id=site.id, department_id=dept_it.id)
        dev2 = Device(device_name='ap-01', device_ip='10.0.0.2', device_type='AP', site_id=site.id, department_id=dept_it.id)
        dev3 = Device(device_name='server-hr-01', device_ip='10.0.0.3', device_type='Server', site_id=site.id, department_id=dept_hr.id)
        _db.session.add_all([dev1, dev2, dev3])
        _db.session.flush()

        alert = DashboardEvent(
            event_id=str(uuid.uuid4()),
            device_id=dev3.device_id,
            device_ip='10.0.0.3',
            severity='CRITICAL',
            message='Ping timeout',
            site_id=site.id,
            department_id=dept_hr.id,
            resolved=False,
        )
        _db.session.add(alert)
        _db.session.commit()
        return {'site_id': site.id, 'dept_it_id': dept_it.id, 'dept_hr_id': dept_hr.id, 'dev3_id': dev3.device_id}


def login(client):
    """Helper: create a session cookie mimicking a logged-in admin."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['role'] = 'admin'


class TestDashboardStats:
    def test_dept_aggregates_present(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/dashboard-stats')
        assert rv.status_code == 200
        data = rv.get_json()
        assert 'dept_aggregates' in data
        assert len(data['dept_aggregates']) == 2

    def test_dept_aggregate_fields(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/dashboard-stats')
        data = rv.get_json()
        dept = next(d for d in data['dept_aggregates'] if d['dept_name'] == 'HR')
        assert 'dept_id' in dept
        assert 'total' in dept
        assert 'online' in dept
        assert 'offline' in dept
        assert 'alerts' in dept
        assert 'health_pct' in dept
        assert dept['alerts'] == 1

    def test_active_alert_count_present(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/dashboard-stats')
        data = rv.get_json()
        assert 'active_alert_count' in data
        assert data['active_alert_count'] == 1

    def test_health_pct_formula(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/dashboard-stats')
        data = rv.get_json()
        # IT: 2 devices, assume both unknown (no scan history) → online=0, total=2, health_pct=0
        dept_it = next(d for d in data['dept_aggregates'] if d['dept_name'] == 'IT')
        assert dept_it['health_pct'] == round(dept_it['online'] / dept_it['total'] * 100) if dept_it['total'] > 0 else 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd D:\nms_final
python -m pytest tests/test_site_dashboard_api.py::TestDashboardStats -v 2>&1 | head -40
```

Expected: FAIL — `dept_aggregates` key missing from response.

- [ ] **Step 3: Enhance the endpoint in `routes/sites.py`**

Find the `site_dashboard_stats` function (currently ends around line 219). Replace its body with:

```python
@sites_bp.route('/api/sites/<int:site_id>/dashboard-stats')
@require_login
def site_dashboard_stats(site_id):
    from middleware.rbac import scoped_query
    from models.dashboard import DashboardEvent
    from models.department import Department
    from sqlalchemy import func

    scoped_query(Site).get_or_404(site_id)

    sites_service = SitesService()
    devices = sites_service.get_site_devices(site_id)
    snapshot = build_device_availability_snapshot(devices)
    stats = sites_service.get_site_stats(site_id, devices=devices, availability_snapshot=snapshot)

    scan_details = snapshot.get("device_scan_details", {})
    device_states = snapshot.get("device_states", {})

    # ── Per-device payload ──────────────────────────────────────────
    device_payload = []
    for d in devices:
        did = d.device_id
        detail = scan_details.get(did, {})
        device_payload.append({
            "device_id":    did,
            "dept_id":      d.department_id,
            "state":        device_states.get(did, "unknown"),
            "ping_ms":      detail.get("ping_ms"),
            "packet_loss":  detail.get("packet_loss"),
            "last_scan_at": detail.get("last_scan_at"),
        })

    # ── Dept aggregates ────────────────────────────────────────────
    # Group devices by department, count online/offline from device_states.
    dept_device_map = {}  # {dept_id: [device, ...]}
    for d in devices:
        dept_id = d.department_id  # may be None
        dept_device_map.setdefault(dept_id, []).append(d)

    # Alert counts per dept for this site (single query)
    alert_rows = (
        db.session.query(DashboardEvent.department_id, func.count(DashboardEvent.event_id))
        .filter(DashboardEvent.site_id == site_id, DashboardEvent.resolved == False)
        .group_by(DashboardEvent.department_id)
        .all()
    )
    alert_by_dept = {row[0]: row[1] for row in alert_rows}
    active_alert_count = sum(alert_by_dept.values())

    # Build dept aggregate list
    dept_aggregates = []
    dept_ids = [did for did in dept_device_map if did is not None]
    dept_objs = {d.id: d for d in Department.query.filter(Department.id.in_(dept_ids)).all()} if dept_ids else {}

    for dept_id, dept_devices in dept_device_map.items():
        if dept_id is None:
            continue
        online = sum(
            1 for dev in dept_devices
            if device_states.get(dev.device_id, "unknown") in ("healthy", "degraded")
        )
        total = len(dept_devices)
        offline = total - online
        health_pct = round(online / total * 100) if total > 0 else 0
        dept_obj = dept_objs.get(dept_id)
        dept_aggregates.append({
            "dept_id":   dept_id,
            "dept_name": dept_obj.name if dept_obj else f"Dept {dept_id}",
            "total":     total,
            "online":    online,
            "offline":   offline,
            "alerts":    alert_by_dept.get(dept_id, 0),
            "health_pct": health_pct,
        })

    dept_aggregates.sort(key=lambda x: x["dept_name"])

    return jsonify({
        "stats":                 stats,
        "dept_aggregates":       dept_aggregates,
        "devices":               device_payload,
        "active_alert_count":    active_alert_count,
        "monitoring_interval_s": snapshot.get("monitoring_interval_s", 15),
        "generated_at":          datetime.utcnow().isoformat(),
    })
```

Also ensure `db` is imported at the top of `routes/sites.py` — it is already via `from extensions import db`.

- [ ] **Step 4: Run tests to verify they pass**

```
python -m pytest tests/test_site_dashboard_api.py::TestDashboardStats -v
```

Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```
git add routes/sites.py tests/test_site_dashboard_api.py
git commit -m "feat(api): enhance dashboard-stats with dept_aggregates and active_alert_count"
```

---

## Task 2: Add device modal endpoint

**Files:**
- Modify: `routes/sites.py`
- Test: `tests/test_site_dashboard_api.py` (add class)

### Background

`DeviceScanHistory` is keyed by `device_ip`, not `device_id`. `ServerHealthLog` is keyed by `device_id`. Floor plan placement lives directly on `Device` (`floor_plan_id`, `map_x`, `map_y`). A device is "placed" when `floor_plan_id IS NOT NULL` and `map_x IS NOT NULL`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_site_dashboard_api.py`:

```python
class TestDeviceModal:
    def test_modal_returns_200(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/device/{seed_data["dev3_id"]}/modal')
        assert rv.status_code == 200

    def test_modal_device_fields(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/device/{seed_data["dev3_id"]}/modal')
        data = rv.get_json()
        assert data['device']['device_name'] == 'server-hr-01'
        assert data['device']['device_ip'] == '10.0.0.3'

    def test_modal_network_section(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/device/{seed_data["dev3_id"]}/modal')
        data = rv.get_json()
        assert 'network' in data
        assert 'state' in data['network']
        assert 'ping_ms' in data['network']
        assert 'packet_loss' in data['network']
        assert 'last_scan_at' in data['network']

    def test_modal_health_section(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/device/{seed_data["dev3_id"]}/modal')
        data = rv.get_json()
        assert 'health' in data
        assert 'available' in data['health']

    def test_modal_active_alerts(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/device/{seed_data["dev3_id"]}/modal')
        data = rv.get_json()
        assert 'active_alerts' in data
        assert len(data['active_alerts']) == 1
        assert data['active_alerts'][0]['severity'] == 'CRITICAL'

    def test_modal_floor_plan_placement(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/device/{seed_data["dev3_id"]}/modal')
        data = rv.get_json()
        assert 'floor_plan_placement' in data
        assert 'has_placement' in data['floor_plan_placement']

    def test_modal_wrong_site_returns_404(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/9999/device/{seed_data["dev3_id"]}/modal')
        assert rv.status_code == 404
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_site_dashboard_api.py::TestDeviceModal -v 2>&1 | head -20
```

Expected: FAIL — 404 (route doesn't exist).

- [ ] **Step 3: Add the endpoint to `routes/sites.py`**

Add after the `site_dashboard_stats` function:

```python
@sites_bp.route('/api/sites/<int:site_id>/device/<int:device_id>/modal')
@require_login
def site_device_modal(site_id, device_id):
    from middleware.rbac import scoped_query
    from models.dashboard import DashboardEvent
    from models.server_health import ServerHealthLog
    from models.scan_history import DeviceScanHistory

    # Verify site access
    scoped_query(Site).get_or_404(site_id)

    # Verify device belongs to this site
    device = Device.query.filter_by(device_id=device_id, site_id=site_id).first_or_404()

    # ── Network state (latest scan by IP) ──────────────────────────
    latest_scan = (
        DeviceScanHistory.query
        .filter_by(device_ip=device.device_ip)
        .order_by(DeviceScanHistory.scan_id.desc())
        .first()
    )

    from services.dashboard_availability import _classify_scan_state
    network_state = _classify_scan_state(latest_scan)

    network = {
        "state":        network_state,
        "ping_ms":      latest_scan.ping_time_ms if latest_scan else None,
        "packet_loss":  latest_scan.packet_loss if latest_scan else None,
        "last_scan_at": latest_scan.scan_timestamp.isoformat() if latest_scan and latest_scan.scan_timestamp else None,
    }

    # ── Server health (latest log by device_id) ────────────────────
    latest_health = (
        ServerHealthLog.query
        .filter_by(device_id=device_id)
        .order_by(ServerHealthLog.id.desc())
        .first()
    )

    if latest_health:
        health = {
            "available":    True,
            "cpu_pct":      latest_health.cpu_usage,
            "memory_pct":   latest_health.memory_usage,
            "disk_pct":     latest_health.disk_usage,
            "recorded_at":  latest_health.recorded_at.isoformat() if getattr(latest_health, 'recorded_at', None) else None,
        }
    else:
        health = {"available": False}

    # ── Active alerts ──────────────────────────────────────────────
    active_alerts = (
        DashboardEvent.query
        .filter_by(device_id=device_id, resolved=False)
        .order_by(DashboardEvent.timestamp.desc())
        .limit(10)
        .all()
    )

    alerts_payload = [
        {
            "alert_id":   ev.event_id,
            "severity":   ev.severity,
            "message":    ev.message,
            "metric_name": ev.metric_name,
            "timestamp":  ev.timestamp.isoformat() if ev.timestamp else None,
        }
        for ev in active_alerts
    ]

    # ── Floor plan placement ───────────────────────────────────────
    has_placement = bool(device.floor_plan_id and device.map_x is not None)
    floor_plan_placement = {
        "has_placement":    has_placement,
        "floor_plan_id":    device.floor_plan_id,
        "floor_plan_name":  device.floor_plan.name if has_placement and device.floor_plan else None,
    }

    return jsonify({
        "device": {
            "device_id":   device.device_id,
            "device_name": device.device_name,
            "device_type": device.device_type,
            "device_ip":   device.device_ip,
            "dept_name":   device.department.name if device.department else None,
            "site_id":     site_id,
        },
        "network":              network,
        "health":               health,
        "active_alerts":        alerts_payload,
        "floor_plan_placement": floor_plan_placement,
    })
```

Also add to the imports block at the top of `routes/sites.py` if not present:
```python
from models.device import Device
```

- [ ] **Step 4: Run tests to verify they pass**

```
python -m pytest tests/test_site_dashboard_api.py::TestDeviceModal -v
```

Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```
git add routes/sites.py tests/test_site_dashboard_api.py
git commit -m "feat(api): add device modal snapshot endpoint"
```

---

## Task 3: Create `routes/alerts.py` blueprint

**Files:**
- Create: `routes/alerts.py`
- Test: `tests/test_site_dashboard_api.py` (add class)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_site_dashboard_api.py`:

```python
class TestAlertsAPI:
    def test_alerts_json_returns_200(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/alerts?site_id={seed_data["site_id"]}')
        assert rv.status_code == 200

    def test_alerts_json_structure(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/alerts?site_id={seed_data["site_id"]}')
        data = rv.get_json()
        assert 'alerts' in data
        assert 'total' in data
        assert 'active_count' in data

    def test_alerts_filtered_by_site(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/alerts?site_id={seed_data["site_id"]}')
        data = rv.get_json()
        assert data['active_count'] == 1

    def test_alerts_filter_active_only(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/alerts?site_id={seed_data["site_id"]}&status=active')
        data = rv.get_json()
        assert all(not a['resolved'] for a in data['alerts'])

    def test_resolve_alert(self, client, seed_data):
        login(client)
        # Get the alert_id first
        rv = client.get(f'/api/alerts?site_id={seed_data["site_id"]}&status=active')
        alerts = rv.get_json()['alerts']
        assert len(alerts) == 1
        alert_id = alerts[0]['alert_id']

        rv = client.patch(f'/api/alerts/{alert_id}/resolve')
        assert rv.status_code == 200
        data = rv.get_json()
        assert data['resolved'] is True
        assert data['resolved_at'] is not None

    def test_alerts_page_returns_html(self, client):
        login(client)
        rv = client.get('/alerts')
        assert rv.status_code == 200
        assert b'alerts' in rv.data.lower()
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_site_dashboard_api.py::TestAlertsAPI -v 2>&1 | head -20
```

Expected: FAIL — 404 (routes don't exist).

- [ ] **Step 3: Create `routes/alerts.py`**

```python
from flask import Blueprint, render_template, request, jsonify, abort
from extensions import db
from models.dashboard import DashboardEvent
from models.device import Device
from models.department import Department
from models.site import Site
from middleware.rbac import require_login
from datetime import datetime

alerts_bp = Blueprint('alerts', __name__)


@alerts_bp.route('/alerts')
@require_login
def alerts_page():
    """Global alerts page — filterable by site."""
    site_id = request.args.get('site_id', type=int)
    sites = Site.query.order_by(Site.site_name).all()
    return render_template('alerts.html', sites=sites, selected_site_id=site_id)


@alerts_bp.route('/api/alerts')
@require_login
def alerts_json():
    """JSON alerts API. Query params: site_id, dept_id, severity, status, limit, offset."""
    site_id   = request.args.get('site_id',  type=int)
    dept_id   = request.args.get('dept_id',  type=int)
    severity  = request.args.get('severity')
    status    = request.args.get('status', 'all')   # active | resolved | all
    limit     = min(request.args.get('limit', 100, type=int), 500)
    offset    = request.args.get('offset', 0, type=int)

    q = DashboardEvent.query

    if site_id:
        q = q.filter(DashboardEvent.site_id == site_id)
    if dept_id:
        q = q.filter(DashboardEvent.department_id == dept_id)
    if severity:
        q = q.filter(DashboardEvent.severity == severity.upper())
    if status == 'active':
        q = q.filter(DashboardEvent.resolved == False)
    elif status == 'resolved':
        q = q.filter(DashboardEvent.resolved == True)

    total = q.count()
    active_count = q.filter(DashboardEvent.resolved == False).count() if status == 'all' else (total if status == 'active' else 0)

    rows = q.order_by(DashboardEvent.timestamp.desc()).offset(offset).limit(limit).all()

    # Bulk-load device names to avoid N+1
    device_ids = list({r.device_id for r in rows if r.device_id})
    devices_by_id = {d.device_id: d for d in Device.query.filter(Device.device_id.in_(device_ids)).all()} if device_ids else {}
    dept_ids = list({d.department_id for d in devices_by_id.values() if d.department_id})
    depts_by_id = {d.id: d for d in Department.query.filter(Department.id.in_(dept_ids)).all()} if dept_ids else {}

    alerts_payload = []
    for ev in rows:
        dev = devices_by_id.get(ev.device_id)
        dept = depts_by_id.get(dev.department_id) if dev else None
        alerts_payload.append({
            "alert_id":    ev.event_id,
            "severity":    ev.severity,
            "device_id":   ev.device_id,
            "device_name": dev.device_name if dev else "Unknown",
            "device_ip":   ev.device_ip,
            "dept_name":   dept.name if dept else None,
            "metric_name": ev.metric_name,
            "message":     ev.message,
            "timestamp":   ev.timestamp.isoformat() if ev.timestamp else None,
            "resolved":    ev.resolved,
            "resolved_at": ev.resolved_at.isoformat() if ev.resolved_at else None,
        })

    return jsonify({
        "alerts":       alerts_payload,
        "total":        total,
        "active_count": active_count,
    })


@alerts_bp.route('/api/alerts/<string:alert_id>/resolve', methods=['PATCH'])
@require_login
def resolve_alert(alert_id):
    """Mark a DashboardEvent as resolved."""
    event = DashboardEvent.query.get_or_404(alert_id)
    if event.resolved:
        return jsonify({"error": "Already resolved"}), 409

    event.resolved    = True
    event.resolved_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "alert_id":    event.event_id,
        "resolved":    event.resolved,
        "resolved_at": event.resolved_at.isoformat(),
    })
```

- [ ] **Step 4: Run tests to confirm they still fail** (blueprint not registered yet)

```
python -m pytest tests/test_site_dashboard_api.py::TestAlertsAPI -v 2>&1 | head -20
```

Expected: FAIL — 404 (blueprint not in app).

- [ ] **Step 5: Register the blueprint in `app.py`**

Add the import with the other route imports (around line 413):

```python
from routes.alerts import alerts_bp
```

Add `alerts_bp` to the `protected_blueprints` list:

```python
protected_blueprints = [
    # ... existing entries ...
    alerts_bp,
]
```

- [ ] **Step 6: Run tests to verify they pass**

```
python -m pytest tests/test_site_dashboard_api.py::TestAlertsAPI -v
```

Expected: all 6 PASS.

- [ ] **Step 7: Commit**

```
git add routes/alerts.py app.py tests/test_site_dashboard_api.py
git commit -m "feat(alerts): add alerts blueprint with JSON API and resolve endpoint"
```

---

## Task 4: Rewrite `templates/sites/dashboard.html`

**Files:**
- Rewrite: `templates/sites/dashboard.html`

This replaces all sections below the site header. The Jinja2 context available in this route is: `site`, `stats`, `metrics`, `devices`, `dept_device_stats`, `recent_alerts`, `online_device_ids`.

- [ ] **Step 1: Replace the file contents**

```html
{% extends "base.html" %}

{% block title %}{{ site.site_name }} Dashboard - Device Monitoring System{% endblock %}

{% block extra_css %}
<link rel="stylesheet" href="{{ url_for('static', filename='css/sites.css') }}?v={{ asset_ver }}">
{% endblock %}

{% block content %}
<div class="container-fluid mt-3 dashboard-enterprise site-dashboard-page" data-site-id="{{ site.id }}">

  {# ── Site Header ──────────────────────────────────────────────── #}
  <div class="d-flex justify-content-between align-items-center mb-3">
    <div>
      <h2 class="mb-1"><i class="fas fa-building me-2"></i>{{ site.site_name }}</h2>
      <p class="site-header-meta mb-0">
        <i class="fas fa-map-marker-alt me-1"></i>{{ site.address or 'No address specified' }}
        <span class="ms-3"><i class="fas fa-clock me-1"></i>{{ site.timezone }}</span>
      </p>
    </div>
    <div class="d-flex gap-2">
      <a href="/sites/{{ site.id }}/floor-plans" class="tactical-btn tactical-btn-primary">
        <i class="fas fa-map me-2"></i>Floor Plans
      </a>
      <a href="/sites" class="tactical-btn tactical-btn-outline">
        <i class="fas fa-arrow-left me-2"></i>Back to Sites
      </a>
    </div>
  </div>

  {# ── Freshness Bar ─────────────────────────────────────────────── #}
  <div id="dash-freshness" class="dash-freshness-bar mb-3">
    <span class="dash-live-dot"></span>
    <span id="dash-freshness-text">Loading live data…</span>
  </div>

  {# ── KPI Cards ─────────────────────────────────────────────────── #}
  <div class="row g-3 mb-3">
    <div class="col-6 col-md-3">
      <div class="card site-kpi-card site-kpi-total">
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-center">
            <div>
              <h6 class="text-muted mb-1">Total Devices</h6>
              <h3 class="mb-0" id="siteKpiTotal">{{ stats.device_count }}</h3>
            </div>
            <i class="fas fa-server fa-2x text-primary"></i>
          </div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="card site-kpi-card site-kpi-online">
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-center">
            <div>
              <h6 class="text-muted mb-1">Online</h6>
              <h3 class="mb-0" id="siteKpiOnline">{{ stats.online_count }}</h3>
            </div>
            <i class="fas fa-check-circle fa-2x text-success"></i>
          </div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="card site-kpi-card site-kpi-offline">
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-center">
            <div>
              <h6 class="text-muted mb-1">Offline</h6>
              <h3 class="mb-0" id="siteKpiOffline">{{ stats.offline_count }}</h3>
            </div>
            <i class="fas fa-times-circle fa-2x text-danger"></i>
          </div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="card site-kpi-card site-kpi-warning">
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-center">
            <div>
              <h6 class="text-muted mb-1">Active Alerts</h6>
              <h3 class="mb-0" id="siteKpiAlerts">{{ stats.warning_count }}</h3>
            </div>
            <i class="fas fa-bell fa-2x text-warning"></i>
          </div>
        </div>
      </div>
    </div>
  </div>

  {# ── Active Alerts Banner (hidden when 0) ─────────────────────── #}
  <div id="dash-alert-banner" class="dash-alert-banner mb-3" style="display:none">
    <i class="fas fa-exclamation-triangle me-2"></i>
    <span id="dash-alert-banner-text">Active alerts detected</span>
    <a href="/alerts?site_id={{ site.id }}" class="ms-3 tactical-btn tactical-btn-sm tactical-btn-outline">
      View all alerts →
    </a>
  </div>

  {# ── Department Health Score Cards ────────────────────────────── #}
  <div class="card mb-3">
    <div class="card-header">
      <i class="fas fa-sitemap me-2"></i>Departments
    </div>
    <div class="card-body pb-2">
      <div id="dept-score-grid" class="dept-score-grid">
        {% if dept_device_stats %}
          {% for row in dept_device_stats %}
          <div class="dept-score-card" data-dept-id="{{ row.id }}"
               data-dept-name="{{ row.name }}"
               data-health-pct="{{ row.health_pct | default(100) }}">
            <div class="dept-score-name">{{ row.name }}</div>
            <div class="dept-score-pct" id="dept-pct-{{ row.id }}">{{ row.health_pct | default(100) }}%</div>
            <div class="dept-score-counts">
              <span class="text-success" id="dept-online-{{ row.id }}">{{ row.online_count }}</span>
              <span class="text-muted"> / {{ row.device_count }}</span>
              {% if row.warning_count > 0 %}
              <span class="ms-1 ops-badge severity-warning" id="dept-alerts-{{ row.id }}">{{ row.warning_count }}!</span>
              {% else %}
              <span class="ms-1" id="dept-alerts-{{ row.id }}" style="display:none"></span>
              {% endif %}
            </div>
          </div>
          {% endfor %}
        {% else %}
          <p class="text-muted mb-0">No departments assigned to this site.</p>
        {% endif %}
      </div>
    </div>
  </div>

  {# ── Device Search ─────────────────────────────────────────────── #}
  <div class="mb-3">
    <div class="dash-search-wrap">
      <i class="fas fa-search dash-search-icon"></i>
      <input type="search" id="dashDeviceSearch" class="dash-search-input"
             placeholder="Search devices by name, IP, or type…" autocomplete="off">
    </div>
  </div>

  {# ── Expandable Department Panels ─────────────────────────────── #}
  {% if dept_device_stats %}
    {% for row in dept_device_stats %}
    {# Panel is auto-open when unhealthy: health_pct < 100 OR warning_count > 0 #}
    {% set is_unhealthy = (row.health_pct is defined and row.health_pct < 100) or row.warning_count > 0 or row.offline_count > 0 %}
    <div class="dept-panel card mb-2" data-dept-panel-id="{{ row.id }}">
      <div class="dept-panel-header" data-bs-toggle="collapse"
           data-bs-target="#dept-panel-body-{{ row.id }}"
           aria-expanded="{{ 'true' if is_unhealthy else 'false' }}">
        <div class="d-flex align-items-center gap-2">
          <i class="fas fa-chevron-right dept-panel-chevron"></i>
          <span class="dept-panel-name">{{ row.name }}</span>
          {% if row.offline_count > 0 or row.warning_count > 0 %}
          <span class="ops-badge severity-warning">{{ row.offline_count }} offline</span>
          {% endif %}
        </div>
        <div class="dept-panel-summary">
          <span class="text-success me-2">{{ row.online_count }} online</span>
          <span class="text-muted">/ {{ row.device_count }}</span>
        </div>
      </div>
      <div id="dept-panel-body-{{ row.id }}"
           class="collapse {{ 'show' if is_unhealthy else '' }}">
        <div class="card-body p-0">
          <div class="table-responsive">
            <table class="table tactical-table table-hover mb-0 dept-device-table">
              <thead>
                <tr>
                  <th style="width:2.5rem"></th>
                  <th>Device</th>
                  <th>IP</th>
                  <th>Type</th>
                  <th>Ping</th>
                  <th>Loss</th>
                  <th>Last Seen</th>
                  <th style="width:1rem"></th>
                </tr>
              </thead>
              <tbody>
                {% set dept_devices = devices | selectattr('department_id', 'equalto', row.id) | list %}
                {% if dept_devices %}
                  {# Sort: offline first, then by name #}
                  {% set offline_first = dept_devices | sort(attribute='device_name') %}
                  {% for device in offline_first %}
                  <tr class="dept-device-row"
                      data-device-id="{{ device.device_id }}"
                      data-device-name="{{ device.device_name | lower }}"
                      data-device-ip="{{ device.device_ip }}"
                      data-device-type="{{ device.device_type | lower }}">
                    <td>
                      {% if device.device_id in online_device_ids %}
                      <span class="dash-status-dot dot-online" data-device-status="online"></span>
                      {% else %}
                      <span class="dash-status-dot dot-offline" data-device-status="offline"></span>
                      {% endif %}
                    </td>
                    <td>
                      <span class="dept-device-name">{{ device.device_name }}</span>
                      <a href="/devices/{{ device.device_id }}/details"
                         target="_blank"
                         class="dept-device-ext-link ms-1"
                         title="Open device page"
                         onclick="event.stopPropagation()">
                        <i class="fas fa-external-link-alt fa-xs"></i>
                      </a>
                    </td>
                    <td><span class="ip-pill">{{ device.device_ip }}</span></td>
                    <td class="text-muted">{{ device.device_type }}</td>
                    <td data-ping-cell data-device-id="{{ device.device_id }}" class="ping-unknown">—</td>
                    <td data-loss-cell data-device-id="{{ device.device_id }}" class="text-muted">—</td>
                    <td data-last-ping data-device-id="{{ device.device_id }}" class="ping-unknown">—</td>
                    <td>
                      {% if device.health_alert_strikes >= 2 or device.latency_strikes >= 2 or device.packet_loss_strikes >= 2 %}
                      <span class="ops-badge severity-warning dept-alert-chip"
                            data-alert-device-id="{{ device.device_id }}"
                            onclick="event.stopPropagation(); openDeviceModal({{ device.device_id }}, true)">!</span>
                      {% endif %}
                    </td>
                  </tr>
                  {% endfor %}
                {% else %}
                <tr>
                  <td colspan="8" class="text-muted text-center py-3">No devices in this department.</td>
                </tr>
                {% endif %}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
    {% endfor %}
  {% else %}
  <div class="card">
    <div class="card-body text-muted text-center py-4">No departments or devices assigned to this site.</div>
  </div>
  {% endif %}

</div>

{# ── Device Modal ──────────────────────────────────────────────── #}
<div id="device-modal-overlay" class="device-modal-overlay" style="display:none" onclick="closeDeviceModal(event)">
  <div class="device-modal-box" role="dialog" aria-modal="true">
    <div class="device-modal-header">
      <div class="d-flex align-items-center gap-2">
        <span id="modal-status-dot" class="dash-status-dot"></span>
        <span id="modal-device-name" class="device-modal-title">Loading…</span>
        <span id="modal-state-badge" class="ops-badge"></span>
      </div>
      <button class="device-modal-close" onclick="closeDeviceModal()">&times;</button>
    </div>
    <div id="modal-device-meta" class="device-modal-meta"></div>
    <div id="modal-body" class="device-modal-body">
      <div class="text-muted text-center py-4">Loading device data…</div>
    </div>
    <div class="device-modal-actions">
      <a id="modal-btn-device" href="#" target="_blank" class="tactical-btn tactical-btn-primary tactical-btn-sm">
        Full device page ↗
      </a>
      <a id="modal-btn-floorplan" href="#" target="_blank" class="tactical-btn tactical-btn-outline tactical-btn-sm" style="display:none">
        Open floor plan ↗
      </a>
      <button id="modal-btn-ping" class="tactical-btn tactical-btn-outline tactical-btn-sm" onclick="pingDeviceFromModal()">
        Ping now
      </button>
    </div>
  </div>
</div>

<script src="{{ url_for('static', filename='js/site_dashboard.js') }}?v={{ asset_ver }}"></script>
{% endblock %}
```

Note: `dept_device_stats` must supply `row.id`, `row.health_pct`, `row.online_count`, `row.offline_count`, `row.warning_count`, `row.device_count`. If `health_pct` isn't already on the row object, update `SitesService.get_dept_device_stats()` (or wherever this is populated) to add it. **Check `routes/sites.py` `site_dashboard` route to see what is passed as `dept_device_stats`** and add `health_pct` to the query result if needed.

- [ ] **Step 2: Manual verification**

```
python app.py
```

Open `http://localhost:5000/sites/<any_id>/dashboard`. Verify:
- Four KPI cards render with correct numbers
- Dept score cards row shows
- Dept panels appear; unhealthy ones are open by default
- Device rows are visible in open panels
- No console JS errors

- [ ] **Step 3: Check `dept_device_stats` includes `health_pct`**

Find in `routes/sites.py` where `dept_device_stats` is built and passed to the template. It likely comes from `SitesService`. Open that service file, find the method, and verify it returns `health_pct`. If it doesn't, add it:

```python
# Wherever the dept_device_stats list is built, add:
for row in result:
    total = row.device_count or 0
    online = row.online_count or 0
    row.health_pct = round(online / total * 100) if total > 0 else 0
```

- [ ] **Step 4: Commit**

```
git add templates/sites/dashboard.html
git commit -m "feat(dashboard): rewrite site dashboard with NOC three-tier layout"
```

---

## Task 5: Add CSS to `static/css/sites.css`

**Files:**
- Append: `static/css/sites.css`

- [ ] **Step 1: Append the following CSS block to the end of `static/css/sites.css`**

```css
/* ══════════════════════════════════════════════════════════════════
   NOC DASHBOARD — dept cards, panels, search, modal, alert banner
   ══════════════════════════════════════════════════════════════════ */

/* ── Alert banner ──────────────────────────────────────────────── */
.dash-alert-banner {
  display: flex;
  align-items: center;
  background: var(--status-critical-bg);
  border: 1px solid var(--status-critical-bd);
  border-radius: 6px;
  padding: 0.6rem 1rem;
  color: var(--status-critical);
  font-size: 0.85rem;
  font-weight: 500;
}

/* ── Dept score cards grid ─────────────────────────────────────── */
.dept-score-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 0.75rem;
}

.dept-score-card {
  background: var(--ui-surface-3);
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  padding: 0.7rem 0.9rem;
  text-align: center;
  cursor: default;
  transition: border-color var(--ui-transition-fast);
}

.dept-score-card[data-health-pct]:not([data-health-pct="100"]) {
  border-color: var(--status-warning-bd);
}

.dept-score-name {
  font-size: 0.72rem;
  color: var(--ui-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dept-score-pct {
  font-size: 1.4rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--status-success);
  line-height: 1.1;
}

.dept-score-card[data-health-pct]:not([data-health-pct="100"]) .dept-score-pct {
  color: var(--status-warning);
}

.dept-score-counts {
  font-size: 0.72rem;
  margin-top: 3px;
  color: var(--ui-text-muted);
}

/* ── Device search ─────────────────────────────────────────────── */
.dash-search-wrap {
  position: relative;
}

.dash-search-icon {
  position: absolute;
  left: 0.85rem;
  top: 50%;
  transform: translateY(-50%);
  color: var(--ui-text-dim);
  pointer-events: none;
  font-size: 0.85rem;
}

.dash-search-input {
  width: 100%;
  background: var(--ui-surface-2);
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  padding: 0.5rem 0.9rem 0.5rem 2.2rem;
  color: var(--ui-text);
  font-size: 0.875rem;
  outline: none;
  transition: border-color var(--ui-transition-fast);
}

.dash-search-input:focus {
  border-color: var(--ui-border-accent);
}

.dash-search-input::placeholder {
  color: var(--ui-text-dim);
}

/* ── Dept panel ────────────────────────────────────────────────── */
.dept-panel .card-header,
.dept-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.65rem 1rem;
  cursor: pointer;
  user-select: none;
  border-bottom: 1px solid rgba(255,255,255,0.04);
}

.dept-panel-header:hover {
  background: var(--ui-surface-hover);
}

.dept-panel-name {
  font-weight: 600;
  font-size: 0.875rem;
}

.dept-panel-chevron {
  font-size: 0.7rem;
  color: var(--ui-text-dim);
  transition: transform var(--ui-transition-fast);
}

.dept-panel-header[aria-expanded="true"] .dept-panel-chevron {
  transform: rotate(90deg);
}

.dept-panel-summary {
  font-size: 0.78rem;
}

/* ── Dept device table ─────────────────────────────────────────── */
.dept-device-table td {
  vertical-align: middle;
  padding: 0.55rem 0.75rem;
  font-size: 0.82rem;
}

.dept-device-row {
  cursor: pointer;
}

.dept-device-row:hover {
  background: var(--ui-surface-hover) !important;
}

.dept-device-name {
  font-weight: 500;
}

.dept-device-ext-link {
  color: var(--ui-text-dim);
  opacity: 0;
  transition: opacity var(--ui-transition-fast);
}

.dept-device-row:hover .dept-device-ext-link {
  opacity: 1;
}

/* ── Status dots ───────────────────────────────────────────────── */
.dash-status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot-online  { background: var(--status-success); }
.dot-offline { background: var(--status-critical); }
.dot-degraded { background: var(--status-degraded); }
.dot-unknown { background: var(--status-unknown); }

/* ── Alert chip in row ─────────────────────────────────────────── */
.dept-alert-chip {
  cursor: pointer;
  font-size: 0.7rem;
  min-width: 1.2rem;
  text-align: center;
}

/* ── Device Modal overlay ──────────────────────────────────────── */
.device-modal-overlay {
  position: fixed;
  inset: 0;
  background: var(--ui-overlay);
  z-index: 1050;
  display: flex;
  align-items: center;
  justify-content: center;
}

.device-modal-box {
  background: var(--ui-surface-2);
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  box-shadow: var(--ui-shadow-modal);
  width: min(520px, 94vw);
  max-height: 88vh;
  overflow-y: auto;
  scrollbar-width: thin;
}

.device-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.9rem 1.1rem;
  border-bottom: 1px solid var(--ui-border);
  background: var(--ui-surface-3);
  border-radius: 10px 10px 0 0;
  position: sticky;
  top: 0;
  z-index: 1;
}

.device-modal-title {
  font-weight: 600;
  font-size: 1rem;
}

.device-modal-close {
  background: none;
  border: none;
  color: var(--ui-text-dim);
  font-size: 1.3rem;
  cursor: pointer;
  line-height: 1;
  padding: 0 0.2rem;
}

.device-modal-close:hover { color: var(--ui-text); }

.device-modal-meta {
  padding: 0.4rem 1.1rem;
  font-size: 0.78rem;
  color: var(--ui-text-muted);
  border-bottom: 1px solid rgba(255,255,255,0.04);
}

.device-modal-body {
  padding: 0.9rem 1.1rem;
}

.device-modal-section-title {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--ui-text-dim);
  margin-bottom: 0.5rem;
  margin-top: 0.8rem;
}

.device-modal-section-title:first-child {
  margin-top: 0;
}

.device-modal-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}

.device-modal-stat {
  background: var(--ui-surface-3);
  border-radius: 6px;
  padding: 0.5rem 0.65rem;
  text-align: center;
}

.device-modal-stat-val {
  font-size: 1.15rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  display: block;
}

.device-modal-stat-lbl {
  font-size: 0.7rem;
  color: var(--ui-text-dim);
  text-transform: uppercase;
}

.device-modal-alert-row {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.4rem 0;
  border-bottom: 1px solid rgba(255,255,255,0.04);
  font-size: 0.82rem;
}

.device-modal-alert-row:last-child { border-bottom: none; }

.device-modal-alert-time {
  margin-left: auto;
  font-size: 0.75rem;
  color: var(--ui-text-dim);
  white-space: nowrap;
  font-family: var(--ui-font-mono);
}

.device-modal-actions {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1.1rem;
  border-top: 1px solid var(--ui-border);
  background: var(--ui-surface-3);
  border-radius: 0 0 10px 10px;
}

/* ── Health progress bars in modal ────────────────────────────── */
.modal-health-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.5rem;
  font-size: 0.82rem;
}

.modal-health-label {
  width: 3rem;
  color: var(--ui-text-muted);
  flex-shrink: 0;
}

.modal-health-bar {
  flex: 1;
  height: 5px;
  background: rgba(255,255,255,0.08);
  border-radius: 3px;
  overflow: hidden;
}

.modal-health-fill {
  height: 5px;
  border-radius: 3px;
  transition: width 0.3s ease;
}

.modal-health-val {
  width: 3rem;
  text-align: right;
  font-family: var(--ui-font-mono);
  font-size: 0.78rem;
}

/* ── Alerts page ───────────────────────────────────────────────── */
.alerts-filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 1rem;
}

.alerts-filter-bar select,
.alerts-filter-bar input {
  background: var(--ui-surface-2);
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  padding: 0.4rem 0.7rem;
  color: var(--ui-text);
  font-size: 0.85rem;
}
```

- [ ] **Step 2: Verify CSS renders**

Reload `http://localhost:5000/sites/<id>/dashboard`. Check that:
- Dept score cards are in a grid
- Dept panel headers are styled and chevron visible
- Device rows have hover highlight
- Search input renders with icon
- Modal overlay styles apply (open modal temporarily via browser console: `document.getElementById('device-modal-overlay').style.display='flex'`)

- [ ] **Step 3: Commit**

```
git add static/css/sites.css
git commit -m "feat(css): add NOC dashboard dept cards, panels, search, modal, and alert banner styles"
```

---

## Task 6: Rewrite `static/js/site_dashboard.js`

**Files:**
- Rewrite: `static/js/site_dashboard.js`

- [ ] **Step 1: Replace the file**

```javascript
/**
 * site_dashboard.js — NOC site dashboard live polling + interactivity.
 *
 * Polls /api/sites/<id>/dashboard-stats every max(2×interval, 20)s.
 * Manages: KPI cards, dept score cards, dept panel device rows,
 *          freshness bar, active alert banner, device modal.
 */
(function () {
  'use strict';

  const root = document.querySelector('[data-site-id]');
  if (!root) return;

  const SITE_ID    = root.dataset.siteId;
  const STATS_URL  = `/api/sites/${SITE_ID}/dashboard-stats`;
  const MODAL_URL  = (deviceId) => `/api/sites/${SITE_ID}/device/${deviceId}/modal`;
  let   pollTimer  = null;
  let   currentModalDeviceId = null;

  /* ── KPI elements ──────────────────────────────────────────── */
  const KPI = {
    total:    document.getElementById('siteKpiTotal'),
    online:   document.getElementById('siteKpiOnline'),
    offline:  document.getElementById('siteKpiOffline'),
    alerts:   document.getElementById('siteKpiAlerts'),
  };

  const freshnessText = document.getElementById('dash-freshness-text');
  const freshnessDot  = document.querySelector('.dash-live-dot');
  const alertBanner   = document.getElementById('dash-alert-banner');
  const alertBannerTxt = document.getElementById('dash-alert-banner-text');

  /* ── Staleness helpers ──────────────────────────────────────── */
  function ageSeconds(isoString) {
    if (!isoString) return null;
    const ts = isoString.endsWith('Z') ? isoString : isoString + 'Z';
    return (Date.now() - new Date(ts).getTime()) / 1000;
  }

  function staleClass(lastScanAt, intervalS) {
    const age = ageSeconds(lastScanAt);
    if (age === null) return 'ping-unknown';
    if (age > intervalS * 5) return 'ping-critical';
    if (age > intervalS * 2) return 'ping-stale';
    return 'ping-fresh';
  }

  function formatAge(lastScanAt) {
    const age = ageSeconds(lastScanAt);
    if (age === null) return '—';
    if (age < 60)   return `${Math.round(age)}s ago`;
    if (age < 3600) return `${Math.round(age / 60)}m ago`;
    return `${Math.round(age / 3600)}h ago`;
  }

  function formatPing(ms) {
    if (ms == null) return '—';
    return `${Math.round(ms)} ms`;
  }

  function timeLabel(isoString) {
    if (!isoString) return '—';
    const ts = isoString.endsWith('Z') ? isoString : isoString + 'Z';
    return new Date(ts).toLocaleTimeString();
  }

  /* ── DOM update: KPI cards ──────────────────────────────────── */
  function updateKpis(stats) {
    if (!stats) return;
    if (KPI.total   && stats.device_count  != null) KPI.total.textContent   = stats.device_count;
    if (KPI.online  && stats.online_count  != null) KPI.online.textContent  = stats.online_count;
    if (KPI.offline && stats.offline_count != null) KPI.offline.textContent = stats.offline_count;
    if (KPI.alerts  && stats.warning_count != null) KPI.alerts.textContent  = stats.warning_count;
  }

  /* ── DOM update: dept score cards ──────────────────────────── */
  function updateDeptCards(deptAggregates) {
    if (!Array.isArray(deptAggregates)) return;
    deptAggregates.forEach(function (d) {
      const card = document.querySelector(`.dept-score-card[data-dept-id="${d.dept_id}"]`);
      if (!card) return;
      const pctEl = document.getElementById(`dept-pct-${d.dept_id}`);
      const onlineEl = document.getElementById(`dept-online-${d.dept_id}`);
      const alertsEl = document.getElementById(`dept-alerts-${d.dept_id}`);
      if (pctEl)   pctEl.textContent = d.health_pct + '%';
      if (onlineEl) onlineEl.textContent = d.online;
      if (alertsEl) {
        if (d.alerts > 0) {
          alertsEl.textContent = d.alerts + '!';
          alertsEl.style.display = '';
        } else {
          alertsEl.style.display = 'none';
        }
      }
      card.dataset.healthPct = d.health_pct;
    });
  }

  /* ── DOM update: device rows ────────────────────────────────── */
  function updateDeviceRows(devices, intervalS) {
    if (!Array.isArray(devices)) return;
    devices.forEach(function (d) {
      const rows = root.querySelectorAll(`tr[data-device-id="${d.device_id}"]`);
      rows.forEach(function (row) {
        const dot = row.querySelector('[data-device-status]');
        if (dot) {
          const isOnline = d.state === 'healthy' || d.state === 'degraded';
          dot.className = 'dash-status-dot ' + (isOnline ? 'dot-online' : 'dot-offline');
          dot.dataset.deviceStatus = isOnline ? 'online' : 'offline';
        }

        const pingCell = row.querySelector('[data-ping-cell]');
        if (pingCell) {
          pingCell.className = d.ping_ms != null ? 'ping-fresh' : 'ping-unknown';
          pingCell.textContent = formatPing(d.ping_ms);
        }

        const lossCell = row.querySelector('[data-loss-cell]');
        if (lossCell) {
          const loss = d.packet_loss;
          lossCell.textContent = loss != null ? loss.toFixed(1) + '%' : '—';
          lossCell.className = loss > 5 ? 'ping-stale' : loss > 0 ? 'ping-fresh' : 'text-muted';
        }

        const lastCell = row.querySelector('[data-last-ping]');
        if (lastCell) {
          const sc = staleClass(d.last_scan_at, intervalS);
          lastCell.className = sc;
          lastCell.textContent = formatAge(d.last_scan_at);
          lastCell.title = formatPing(d.ping_ms) + (d.last_scan_at ? ` · ${timeLabel(d.last_scan_at)}` : '');
        }
      });
    });
  }

  /* ── DOM update: freshness bar ──────────────────────────────── */
  function updateFreshnessBar(generatedAt) {
    if (!freshnessText) return;
    freshnessText.textContent = generatedAt ? `Live · ${timeLabel(generatedAt)}` : 'Live';
    if (freshnessDot) freshnessDot.style.background = 'var(--status-success)';
  }

  function markFreshnessError() {
    if (!freshnessText) return;
    freshnessText.textContent = 'Update failed — retrying…';
    if (freshnessDot) freshnessDot.style.background = 'var(--status-warning)';
  }

  /* ── DOM update: alert banner ───────────────────────────────── */
  function updateAlertBanner(activeCount) {
    if (!alertBanner) return;
    if (activeCount > 0) {
      alertBanner.style.display = 'flex';
      if (alertBannerTxt) alertBannerTxt.textContent = `Active Alerts: ${activeCount} unresolved`;
    } else {
      alertBanner.style.display = 'none';
    }
  }

  /* ── Polling loop ───────────────────────────────────────────── */
  function poll() {
    fetch(STATS_URL, { credentials: 'same-origin' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        const intervalS = data.monitoring_interval_s || 15;
        updateKpis(data.stats);
        updateDeptCards(data.dept_aggregates);
        updateDeviceRows(data.devices, intervalS);
        updateFreshnessBar(data.generated_at);
        updateAlertBanner(data.active_alert_count || 0);

        clearTimeout(pollTimer);
        pollTimer = setTimeout(poll, Math.max(intervalS * 2, 20) * 1000);
      })
      .catch(function () {
        markFreshnessError();
        clearTimeout(pollTimer);
        pollTimer = setTimeout(poll, 60000);
      });
  }

  /* ── Device search ──────────────────────────────────────────── */
  const searchInput = document.getElementById('dashDeviceSearch');
  if (searchInput) {
    searchInput.addEventListener('input', function () {
      const q = this.value.trim().toLowerCase();
      root.querySelectorAll('.dept-device-row').forEach(function (row) {
        const name = (row.dataset.deviceName || '').toLowerCase();
        const ip   = (row.dataset.deviceIp   || '').toLowerCase();
        const type = (row.dataset.deviceType || '').toLowerCase();
        row.style.display = (!q || name.includes(q) || ip.includes(q) || type.includes(q)) ? '' : 'none';
      });
    });
  }

  /* ── Row click → modal ──────────────────────────────────────── */
  root.addEventListener('click', function (e) {
    const row = e.target.closest('.dept-device-row');
    if (!row) return;
    // Ignore clicks on external link or alert chip (they handle themselves)
    if (e.target.closest('.dept-device-ext-link') || e.target.closest('.dept-alert-chip')) return;
    const deviceId = parseInt(row.dataset.deviceId, 10);
    if (deviceId) openDeviceModal(deviceId, false);
  });

  /* ── Modal ──────────────────────────────────────────────────── */
  const modalOverlay   = document.getElementById('device-modal-overlay');
  const modalTitle     = document.getElementById('modal-device-name');
  const modalStateDot  = document.getElementById('modal-status-dot');
  const modalStateBadge = document.getElementById('modal-state-badge');
  const modalMeta      = document.getElementById('modal-device-meta');
  const modalBody      = document.getElementById('modal-body');
  const modalBtnDevice = document.getElementById('modal-btn-device');
  const modalBtnFloor  = document.getElementById('modal-btn-floorplan');
  const modalBtnPing   = document.getElementById('modal-btn-ping');

  window.openDeviceModal = function (deviceId, focusAlerts) {
    currentModalDeviceId = deviceId;
    if (modalTitle) modalTitle.textContent = 'Loading…';
    if (modalBody) modalBody.innerHTML = '<div class="text-muted text-center py-4">Loading device data…</div>';
    if (modalBtnFloor) modalBtnFloor.style.display = 'none';
    if (modalOverlay) modalOverlay.style.display = 'flex';

    fetch(MODAL_URL(deviceId), { credentials: 'same-origin' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        renderModal(data, focusAlerts);
      })
      .catch(function () {
        if (modalBody) modalBody.innerHTML = '<div class="text-danger text-center py-4">Unable to load device data. <a href="#" onclick="openDeviceModal(' + deviceId + ', false)">Retry?</a></div>';
      });
  };

  function renderModal(data, focusAlerts) {
    const dev     = data.device || {};
    const net     = data.network || {};
    const health  = data.health || {};
    const alerts  = data.active_alerts || [];
    const fp      = data.floor_plan_placement || {};

    // Header
    if (modalTitle) modalTitle.textContent = dev.device_name || 'Unknown';
    if (modalStateDot) {
      const stateClass = { healthy: 'dot-online', degraded: 'dot-degraded', offline: 'dot-offline' }[net.state] || 'dot-unknown';
      modalStateDot.className = 'dash-status-dot ' + stateClass;
    }
    if (modalStateBadge) {
      const stateLabel = { healthy: 'Online', degraded: 'Degraded', offline: 'Offline', unknown: 'Unknown' }[net.state] || 'Unknown';
      modalStateBadge.textContent = stateLabel;
      modalStateBadge.className = 'ops-badge ' + (net.state === 'offline' ? 'status-offline' : net.state === 'healthy' ? 'status-healthy' : 'severity-warning');
    }

    // Meta line
    const metaParts = [dev.device_type, dev.device_ip, dev.dept_name].filter(Boolean);
    if (modalMeta) modalMeta.textContent = metaParts.join(' · ');

    // Action buttons
    if (modalBtnDevice) modalBtnDevice.href = `/devices/${dev.device_id}/details`;
    if (fp.has_placement && modalBtnFloor) {
      modalBtnFloor.href = `/sites/${dev.site_id}/floor-plans`;
      modalBtnFloor.style.display = '';
    }

    // Body HTML
    let html = '';

    // Network section
    html += `<div class="device-modal-section-title">Network</div>`;
    html += `<div class="device-modal-grid">
      <div class="device-modal-stat">
        <span class="device-modal-stat-val ${net.state === 'offline' ? 'text-danger' : 'text-success'}">${net.state || '—'}</span>
        <span class="device-modal-stat-lbl">Status</span>
      </div>
      <div class="device-modal-stat">
        <span class="device-modal-stat-val">${formatPing(net.ping_ms)}</span>
        <span class="device-modal-stat-lbl">Ping</span>
      </div>
      <div class="device-modal-stat">
        <span class="device-modal-stat-val ${net.packet_loss > 5 ? 'text-warning' : ''}">${net.packet_loss != null ? net.packet_loss.toFixed(1) + '%' : '—'}</span>
        <span class="device-modal-stat-lbl">Pkt Loss</span>
      </div>
    </div>`;
    if (net.last_scan_at) {
      html += `<div class="text-muted mb-3" style="font-size:0.75rem">Last scan: ${formatAge(net.last_scan_at)}</div>`;
    }

    // Server health section
    html += `<div class="device-modal-section-title">Server Health</div>`;
    if (health.available) {
      function healthBar(label, pct) {
        const color = pct > 90 ? 'var(--status-critical)' : pct > 75 ? 'var(--status-warning)' : 'var(--status-success)';
        return `<div class="modal-health-row">
          <span class="modal-health-label">${label}</span>
          <div class="modal-health-bar"><div class="modal-health-fill" style="width:${Math.min(pct,100)}%;background:${color}"></div></div>
          <span class="modal-health-val" style="color:${color}">${pct != null ? pct.toFixed(1) + '%' : '—'}</span>
        </div>`;
      }
      html += healthBar('CPU', health.cpu_pct);
      html += healthBar('RAM', health.memory_pct);
      html += healthBar('Disk', health.disk_pct);
    } else {
      html += `<div class="text-muted mb-3" style="font-size:0.82rem">No health data — agent not installed or not reporting.</div>`;
    }

    // Active alerts section
    html += `<div class="device-modal-section-title" id="modal-alerts-section">Active Alerts (${alerts.length})</div>`;
    if (alerts.length > 0) {
      alerts.forEach(function (a) {
        const sevClass = a.severity === 'CRITICAL' ? 'severity-critical' : a.severity === 'WARNING' ? 'severity-warning' : 'severity-info';
        const time = a.timestamp ? new Date(a.timestamp.endsWith('Z') ? a.timestamp : a.timestamp + 'Z').toLocaleTimeString() : '—';
        html += `<div class="device-modal-alert-row">
          <span class="ops-badge ${sevClass}">${a.severity}</span>
          <span>${a.message || a.metric_name || '—'}</span>
          <span class="device-modal-alert-time">${time}</span>
        </div>`;
      });
    } else {
      html += `<div class="text-muted mb-2" style="font-size:0.82rem">No active alerts for this device.</div>`;
    }

    if (modalBody) modalBody.innerHTML = html;

    // Scroll to alerts if triggered from alert chip
    if (focusAlerts) {
      const alertsSection = document.getElementById('modal-alerts-section');
      if (alertsSection) setTimeout(function () { alertsSection.scrollIntoView({ behavior: 'smooth' }); }, 100);
    }
  }

  window.closeDeviceModal = function (event) {
    if (event && event.target !== modalOverlay) return;
    if (modalOverlay) modalOverlay.style.display = 'none';
    currentModalDeviceId = null;
  };

  // Close on Escape
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && modalOverlay && modalOverlay.style.display !== 'none') {
      modalOverlay.style.display = 'none';
      currentModalDeviceId = null;
    }
  });

  window.pingDeviceFromModal = function () {
    if (!currentModalDeviceId) return;
    const btn = modalBtnPing;
    if (btn) { btn.disabled = true; btn.textContent = 'Pinging…'; }
    // Trigger a stats refresh which will update the modal row; re-open modal after delay
    fetch(STATS_URL, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        const intervalS = data.monitoring_interval_s || 15;
        updateDeviceRows(data.devices, intervalS);
        if (currentModalDeviceId) openDeviceModal(currentModalDeviceId, false);
      })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.textContent = 'Ping now'; }
      });
  };

  /* ── Bootstrap ──────────────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', function () {
    poll();
  });
})();
```

- [ ] **Step 2: Verify in browser**

Reload the dashboard. Open DevTools Console. Verify:
- No JS errors on load
- After ~2s, KPI numbers update and freshness bar shows time
- Clicking a device row opens a modal
- Modal shows network section, health section (or "no data"), alerts section
- Modal closes on Escape or click-outside
- Search input filters device rows live
- Alert chip click opens modal and scrolls to alerts

- [ ] **Step 3: Commit**

```
git add static/js/site_dashboard.js
git commit -m "feat(js): rewrite site_dashboard.js with dept panels, search, device modal, and alert banner"
```

---

## Task 7: Create `templates/alerts.html`

**Files:**
- Create: `templates/alerts.html`

- [ ] **Step 1: Create the file**

```html
{% extends "base.html" %}

{% block title %}Alerts — Device Monitoring System{% endblock %}

{% block extra_css %}
<link rel="stylesheet" href="{{ url_for('static', filename='css/sites.css') }}?v={{ asset_ver }}">
{% endblock %}

{% block content %}
<div class="container-fluid mt-3">

  <div class="d-flex justify-content-between align-items-center mb-4">
    <h2><i class="fas fa-bell me-2"></i>Alerts</h2>
  </div>

  {# Filter bar #}
  <div class="alerts-filter-bar mb-3">
    <select id="filterSite" class="form-select form-select-sm" style="width:auto">
      <option value="">All Sites</option>
      {% for s in sites %}
      <option value="{{ s.id }}" {% if selected_site_id == s.id %}selected{% endif %}>{{ s.site_name }}</option>
      {% endfor %}
    </select>

    <select id="filterSeverity" class="form-select form-select-sm" style="width:auto">
      <option value="">All Severity</option>
      <option value="CRITICAL">Critical</option>
      <option value="WARNING">Warning</option>
      <option value="INFO">Info</option>
    </select>

    <div class="btn-group btn-group-sm" role="group" id="filterStatus">
      <button type="button" class="btn btn-outline-secondary active" data-status="active">Active</button>
      <button type="button" class="btn btn-outline-secondary" data-status="resolved">Resolved</button>
      <button type="button" class="btn btn-outline-secondary" data-status="all">All</button>
    </div>

    <input type="search" id="filterSearch" class="form-control form-control-sm" placeholder="Filter by device or message…" style="width:220px">
  </div>

  {# Alert table #}
  <div class="card">
    <div class="card-body p-0">
      <div class="table-responsive">
        <table class="table tactical-table table-hover mb-0" id="alertsTable">
          <thead>
            <tr>
              <th style="width:6rem">Severity</th>
              <th>Device</th>
              <th>Dept</th>
              <th>Message</th>
              <th>Metric</th>
              <th style="width:9rem">Time</th>
              <th style="width:6rem">Status</th>
              <th style="width:5rem"></th>
            </tr>
          </thead>
          <tbody id="alertsTableBody">
            <tr><td colspan="8" class="text-muted text-center py-4">Loading alerts…</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <div id="alerts-empty" class="text-muted text-center py-4" style="display:none">
    No alerts match your current filters.
  </div>
  <div id="alerts-error" class="text-danger text-center py-4" style="display:none">
    Unable to load alerts. <a href="#" onclick="loadAlerts()">Retry?</a>
  </div>

</div>
<script src="{{ url_for('static', filename='js/alerts.js') }}?v={{ asset_ver }}"></script>
{% endblock %}
```

- [ ] **Step 2: Verify the page renders**

Navigate to `http://localhost:5000/alerts`. Verify:
- Page loads without errors
- Filter bar renders with site dropdown populated
- Table renders with "Loading alerts…" row (JS not yet present)

- [ ] **Step 3: Commit**

```
git add templates/alerts.html
git commit -m "feat(alerts): add global alerts page template"
```

---

## Task 8: Create `static/js/alerts.js`

**Files:**
- Create: `static/js/alerts.js`

- [ ] **Step 1: Create the file**

```javascript
/**
 * alerts.js — Global alerts page: filter, polling, resolve.
 */
(function () {
  'use strict';

  const API_URL = '/api/alerts';
  let pollTimer = null;
  let currentStatus = 'active';

  const filterSite     = document.getElementById('filterSite');
  const filterSeverity = document.getElementById('filterSeverity');
  const filterSearch   = document.getElementById('filterSearch');
  const statusBtns     = document.querySelectorAll('#filterStatus [data-status]');
  const tableBody      = document.getElementById('alertsTableBody');
  const emptyMsg       = document.getElementById('alerts-empty');
  const errorMsg       = document.getElementById('alerts-error');

  function buildUrl() {
    const params = new URLSearchParams();
    if (filterSite     && filterSite.value)     params.set('site_id',  filterSite.value);
    if (filterSeverity && filterSeverity.value) params.set('severity', filterSeverity.value);
    params.set('status', currentStatus);
    params.set('limit', '200');
    return `${API_URL}?${params.toString()}`;
  }

  function severityClass(sev) {
    return { CRITICAL: 'severity-critical', WARNING: 'severity-warning', INFO: 'severity-info' }[sev] || 'severity-info';
  }

  function formatTime(iso) {
    if (!iso) return '—';
    const ts = iso.endsWith('Z') ? iso : iso + 'Z';
    return new Date(ts).toLocaleString();
  }

  window.loadAlerts = function () {
    if (errorMsg) errorMsg.style.display = 'none';
    if (emptyMsg) emptyMsg.style.display = 'none';

    fetch(buildUrl(), { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        const q = (filterSearch && filterSearch.value.trim().toLowerCase()) || '';
        let alerts = data.alerts || [];

        if (q) {
          alerts = alerts.filter(function (a) {
            return (a.device_name || '').toLowerCase().includes(q) ||
                   (a.message || '').toLowerCase().includes(q) ||
                   (a.device_ip || '').toLowerCase().includes(q);
          });
        }

        if (!tableBody) return;

        if (alerts.length === 0) {
          tableBody.innerHTML = '';
          if (emptyMsg) emptyMsg.style.display = '';
          return;
        }

        tableBody.innerHTML = alerts.map(function (a) {
          const resolved = a.resolved;
          const resolveBtn = !resolved
            ? `<button class="tactical-btn tactical-btn-sm tactical-btn-outline"
                onclick="resolveAlert('${a.alert_id}', this)">Resolve</button>`
            : '';
          return `<tr>
            <td><span class="ops-badge ${severityClass(a.severity)}">${a.severity}</span></td>
            <td>
              <strong>${a.device_name || '—'}</strong><br>
              <small class="ip-pill">${a.device_ip || ''}</small>
            </td>
            <td class="text-muted">${a.dept_name || '—'}</td>
            <td>${a.message || '—'}</td>
            <td><code>${a.metric_name || '—'}</code></td>
            <td style="font-size:0.78rem;font-family:var(--ui-font-mono)">${formatTime(a.timestamp)}</td>
            <td>${resolved
              ? '<span class="ops-badge status-resolved">Resolved</span>'
              : '<span class="ops-badge status-active">Active</span>'}</td>
            <td>${resolveBtn}</td>
          </tr>`;
        }).join('');

        clearTimeout(pollTimer);
        pollTimer = setTimeout(loadAlerts, 30000);
      })
      .catch(function () {
        if (errorMsg) errorMsg.style.display = '';
        clearTimeout(pollTimer);
        pollTimer = setTimeout(loadAlerts, 60000);
      });
  };

  window.resolveAlert = function (alertId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Resolving…'; }
    fetch(`/api/alerts/${alertId}/resolve`, {
      method: 'PATCH',
      credentials: 'same-origin',
    })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function () {
        loadAlerts();
      })
      .catch(function () {
        if (btn) { btn.disabled = false; btn.textContent = 'Resolve'; }
        alert('Failed to resolve alert. Please try again.');
      });
  };

  /* ── Filter listeners ──────────────────────────────────── */
  [filterSite, filterSeverity].forEach(function (el) {
    if (el) el.addEventListener('change', loadAlerts);
  });

  if (filterSearch) {
    let debounceTimer;
    filterSearch.addEventListener('input', function () {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(loadAlerts, 250);
    });
  }

  statusBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      statusBtns.forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      currentStatus = btn.dataset.status;
      loadAlerts();
    });
  });

  /* ── Init ───────────────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', loadAlerts);
})();
```

- [ ] **Step 2: Verify in browser**

Navigate to `http://localhost:5000/alerts`. Verify:
- Alert rows load on page open
- "Active" filter shows unresolved alerts only
- "All" filter shows everything
- Resolve button POPs the row out of Active view and re-loads
- Site dropdown filters to one site
- Search filters inline by device name and message

- [ ] **Step 3: Commit**

```
git add static/js/alerts.js
git commit -m "feat(js): add alerts page filter, polling, and resolve logic"
```

---

## Task 9: Add Alerts sidebar link to `templates/base.html`

**Files:**
- Modify: `templates/base.html`

- [ ] **Step 1: Find the Sites sidebar link block and insert Alerts after it**

In `templates/base.html`, find this block (around line 1970):

```html
                    {% if rbac_context.capabilities.sites %}
                    <a href="{{ url_for('sites.sites_list_page') }}"
                        class="sidebar-link {% if request.endpoint and 'sites' in request.endpoint %}active{% endif %}">
                        <i data-lucide="building"></i><span class="link-text">Sites</span>
                    </a>
                    {% endif %}
```

Insert the Alerts link immediately after this block (before the `{% if rbac_context.capabilities.departments %}` block):

```html
                    <a href="{{ url_for('alerts.alerts_page') }}"
                        class="sidebar-link {% if request.endpoint and 'alerts' in request.endpoint %}active{% endif %}">
                        <i data-lucide="bell"></i>
                        <span class="link-text">Alerts</span>
                        <span id="sidebar-alert-badge" class="sidebar-badge" style="display:none"></span>
                    </a>
```

Also add the badge CSS inline in `base.html`'s `<style>` block (or append to `static/css/tactical.css`) — add this rule after the existing `.sidebar-link` styles:

```css
.sidebar-badge {
    background: var(--status-critical);
    color: #fff;
    font-size: 0.62rem;
    font-weight: 700;
    border-radius: 99px;
    padding: 1px 5px;
    margin-left: auto;
    min-width: 1.1rem;
    text-align: center;
}
```

- [ ] **Step 2: Add badge polling**

Find the `</body>` tag in `base.html` (or the bottom scripts block). Add a small inline script that polls the alert count and updates the badge:

```html
<script>
(function () {
  function refreshAlertBadge() {
    fetch('/api/alerts?status=active&limit=1', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        var badge = document.getElementById('sidebar-alert-badge');
        if (!badge) return;
        var count = data.active_count || 0;
        if (count > 0) {
          badge.textContent = count > 99 ? '99+' : count;
          badge.style.display = '';
        } else {
          badge.style.display = 'none';
        }
      })
      .catch(function () {});
    setTimeout(refreshAlertBadge, 30000);
  }
  document.addEventListener('DOMContentLoaded', refreshAlertBadge);
})();
</script>
```

- [ ] **Step 3: Verify in browser**

Reload any page. Verify:
- "Alerts" link appears in the sidebar between Sites and Departments
- Link becomes active when on `/alerts`
- Badge appears with the active alert count if > 0
- Badge disappears when all alerts are resolved

- [ ] **Step 4: Commit**

```
git add templates/base.html
git commit -m "feat(nav): add global Alerts sidebar link with live badge"
```

---

## Task 10: Add `id` and `health_pct` to `dept_device_stats` in `routes/sites.py`

**Files:**
- Modify: `routes/sites.py` (the `site_dashboard` HTML route, around lines 126–165)

The template uses `row.id` (for Bootstrap collapse IDs and data attributes) and `row.health_pct` (for the dept score card). The current `dept_stats_map` dicts do not include these keys. `dept_stats_map` is keyed by `dept.id` but the id is not stored inside the dict value.

- [ ] **Step 1: Add `id` key when building each bucket (line ~128)**

Find this block in `routes/sites.py`:

```python
    for dept in departments:
        dept_stats_map[dept.id] = {
            'name': dept.name,
            'device_count': 0,
            'online_count': 0,
            'offline_count': 0,
            'warning_count': 0
        }
```

Replace with:

```python
    for dept in departments:
        dept_stats_map[dept.id] = {
            'id': dept.id,
            'name': dept.name,
            'device_count': 0,
            'online_count': 0,
            'offline_count': 0,
            'warning_count': 0,
        }
```

- [ ] **Step 2: Add `health_pct` after counting is complete (after line ~165)**

Find this line:

```python
    dept_device_stats = sorted(dept_stats_map.values(), key=lambda row: row['name'].lower())
```

Replace with:

```python
    for row in dept_stats_map.values():
        total  = row['device_count'] or 0
        online = row['online_count'] or 0
        row['health_pct'] = round(online / total * 100) if total > 0 else 0

    dept_device_stats = sorted(dept_stats_map.values(), key=lambda row: row['name'].lower())
```

Also add `id` to the `unassigned_bucket` dict (find around line 136):

```python
    unassigned_bucket = {
        'id': 0,
        'name': 'Unassigned',
        'device_count': 0,
        'online_count': 0,
        'offline_count': 0,
        'warning_count': 0,
        'health_pct': 100,
    }
```

- [ ] **Step 3: Verify dept panel auto-open behavior**

Reload `http://localhost:5000/sites/<id>/dashboard`. On a site with at least one offline device:
- The dept panel containing that device should be expanded on load
- Healthy depts (100% online, 0 warnings) should be collapsed
- Dept score cards should show correct `%` values

- [ ] **Step 4: Commit**

```
git add routes/sites.py
git commit -m "fix(dashboard): add id and health_pct to dept_device_stats context"
```

---

## Task 11: Run full test suite + end-to-end smoke check

- [ ] **Step 1: Run all API tests**

```
python -m pytest tests/test_site_dashboard_api.py -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run existing test suite to check for regressions**

```
python -m pytest tests/ -v 2>&1 | tail -20
```

Expected: no new failures.

- [ ] **Step 3: End-to-end smoke check**

Start the app and verify each of the following manually:

1. **Site dashboard loads** — `GET /sites/<id>/dashboard` returns 200, shows three-tier layout
2. **KPI cards update** — after ~30s, numbers reflect latest scan data
3. **Unhealthy dept panels open** — any dept with offline device is expanded on load
4. **Healthy dept panels collapsed** — depts with 100% online start closed; click to expand
5. **Device search works** — type a device name, rows filter in real time
6. **Row click opens modal** — clicking device row shows modal with network + health + alerts
7. **External link** — `↗` on device name opens `/devices/<id>/details` in new tab
8. **Alert chip** — clicking `!` chip opens modal scrolled to Alerts section
9. **Floor plan button** — only appears for devices with `floor_plan_id` set
10. **Ping now** — clicking "Ping now" in modal triggers refresh and re-renders modal
11. **Alert banner** — visible when `active_alert_count > 0`, hidden otherwise; links to `/alerts?site_id=<id>`
12. **Alerts page** — `GET /alerts` returns 200, table loads via JS, Active filter works
13. **Resolve button** — clicking Resolve on alerts page marks alert resolved, row disappears from Active view
14. **Sidebar badge** — shows active alert count; disappears when count is 0
15. **Freshness bar** — shows "Live · HH:MM:SS"; turns amber on network error

- [ ] **Step 4: Final commit**

```
git add .
git commit -m "test(dashboard): add smoke check verification for NOC dashboard redesign"
```
