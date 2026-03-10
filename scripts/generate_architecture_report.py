from __future__ import annotations
import ast, json, re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / 'artifacts'
ROUTES = json.loads((ART / 'route_map.json').read_text(encoding='utf-8'))
CONTRACTS = json.loads((ART / 'endpoint_contract_index_v2.json').read_text(encoding='utf-8'))
MODELS = json.loads((ART / 'models_inventory.json').read_text(encoding='utf-8'))
USAGE = { (x['module'], x['class_name']): x for x in json.loads((ART / 'model_usage_index.json').read_text(encoding='utf-8')) }
CONTRACT_BY = { (x.get('file'), x.get('function')): x for x in CONTRACTS }
TOP = {
 'routes':'Flask blueprints and HTTP/HTML handlers.','models':'SQLAlchemy model definitions and table mappings.','services':'Business logic, background jobs, integrations, and helper services.','middleware':'Authentication, RBAC, session, and request pipeline helpers.','templates':'Jinja2 HTML templates rendered by Flask routes.','static':'Static frontend assets: JavaScript, CSS, fonts, and images.','tests':'Pytest suites for unit, integration, RBAC, route, and performance coverage.','docs':'Project documentation, conventions, and skill rulebooks.','scripts':'Operational and quality-gate automation scripts.','utils':'Cross-cutting utility functions.','client_modules':'Agent-side telemetry collectors used by service.py.','instance':'Runtime instance data such as SQLite DBs and generated state.','artifacts':'Generated analysis, coverage, and quality-gate outputs.','node_modules':'Installed frontend test/build dependencies.'}
SUB = {
 'routes/api_v1':'Versioned maintenance/configuration API namespace.','templates/tracking':'Live device console, history, and tracking fleet pages.','templates/admin':'Admin-only policy pages.','templates/auth':'Login, registration, OTP, and password reset pages.','templates/sites':'Site inventory and site dashboard pages.','templates/departments':'Department inventory pages.','templates/printers':'Printer inventory and detail pages.','templates/print_jobs':'Print job inventory pages.','static/js/dashboard':'Dashboard state, API, SSE, cards, tables, and modal controllers.','static/js/tracking':'Tracking fleet pages, live console, history, workstation monitor, and console submodules.','static/js/tracking/console':'Device console helper module for state, risk, telemetry, cache, mutation locks, and normalizers.','static/css/tracking':'Tracking-specific presentation layers for live console, history, and workstation pages.','docs/skills':'Rulebooks grouped by backend/frontend/security standards.','tests/unit':'Fast isolated tests for helpers and services.','tests/integration':'Route/API/RBAC/template integration tests.','tests/performance':'Performance/SLA-oriented pytest benchmarks.'}

def rel(p): return p.relative_to(ROOT).as_posix()
def fmt(v):
    vals=[str(x) for x in v if str(x).strip()]
    return ', '.join(vals) if vals else 'none'

def static_desc(path, refs):
    p=path.lower()
    if '/__tests__/' in p or p.endswith('.test.js'): return 'Vitest test module for frontend logic.'
    if 'dashboard/' in p: return 'Dashboard UI state, polling/SSE, cards, tables, or RBAC guard logic.'
    if 'tracking/console/' in p: return 'Device console helper module for state, risk, telemetry, cache, mutation locks, or alert normalization.'
    if 'tracking/' in p and p.endswith('.js'): return 'Tracking UI controller for device fleet, live console, history, workstation, or related pages.'
    if p.endswith('session_manager.js'): return 'Session timeout and browser session-status handling.'
    if p.endswith('maintenance.js'): return 'Maintenance mode modal/device toggles.'
    if p.endswith('scanning.js'): return 'Network scan/discovery UI behavior.'
    if p.endswith('.css') and 'tracking/' in p: return 'Tracking-specific styling for live/history/workstation pages.'
    if p.endswith('tactical.css'): return 'Global enterprise tactical design system styles.'
    if p.endswith('session_warning.css'): return 'Session timeout warning styles.'
    return 'Referenced by templates: ' + ', '.join(refs) if refs else 'Static asset with no direct template reference found; inspect lazy-loading or tests.'

def consumer(path, module, fn, service_local=False):
    if service_local:
        if path.startswith('/api/files/') or path in {'/api/health','/api/secure/stats','/api/secure/sync','/api/tracking/register','/api/tracking/sync','/api/maintenance/mode','/api/identity'}: return 'server_to_agent_or_agent_internal'
        return 'agent_local'
    if fn in {'api_tracking_register','api_tracking_sync','api_ingest_restricted_site_events'}: return 'agent_service'
    if path == '/api/tracking/restricted-sites/policy': return 'both_frontend_and_agent_service'
    if module == 'routes.file_transfer': return 'frontend_js_to_server_then_server_to_agent'
    if module == 'routes.agent': return 'external_agent_or_push_client'
    return 'frontend_js_or_html_form'

def template_renderers():
    out=defaultdict(list)
    for py in sorted((ROOT/'routes').rglob('*.py')):
        src=py.read_text(encoding='utf-8', errors='replace')
        try: tree=ast.parse(src)
        except SyntaxError: continue
        mod='routes.'+py.relative_to(ROOT/'routes').with_suffix('').as_posix().replace('/','.')
        lookup=defaultdict(list)
        for r in ROUTES:
            if r['module']==mod: lookup[r['function']].append({'path':r['path'],'methods':r['methods']})
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef): continue
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id=='render_template' and child.args and isinstance(child.args[0], ast.Constant) and isinstance(child.args[0].value, str):
                    out[child.args[0].value].append({'module':mod,'function':node.name,'routes':lookup.get(node.name,[])})
    return out

def static_refs():
    refs=defaultdict(set); pat=re.compile(r"filename\s*=\s*['\"]([^'\"]+)['\"]")
    for tpl in sorted((ROOT/'templates').rglob('*.html')):
        txt=tpl.read_text(encoding='utf-8', errors='replace')
        for m in pat.findall(txt): refs[m.replace('\\','/')].add(rel(tpl))
    return refs

def service_routes():
    src=(ROOT/'service.py').read_text(encoding='utf-8', errors='replace'); tree=ast.parse(src); out=[]
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef): continue
        defs=[]; q=set(); h=set(); b=set(); rk=set(); payload=set()
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute) and isinstance(deco.func.value, ast.Name) and deco.func.value.id=='app' and deco.func.attr=='route' and deco.args and isinstance(deco.args[0], ast.Constant) and isinstance(deco.args[0].value, str):
                methods=['GET']
                for kw in deco.keywords:
                    if kw.arg=='methods' and isinstance(kw.value, (ast.List, ast.Tuple)):
                        vals=[e.value.upper() for e in kw.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                        if vals: methods=vals
                defs.append({'path':deco.args[0].value,'methods':methods})
        if not defs: continue
        class V(ast.NodeVisitor):
            def visit_Assign(self, inner):
                if isinstance(inner.value, ast.Call) and isinstance(inner.value.func, ast.Attribute) and isinstance(inner.value.func.value, ast.Name) and inner.value.func.value.id=='request' and inner.value.func.attr=='get_json':
                    for t in inner.targets:
                        if isinstance(t, ast.Name): payload.add(t.id)
                self.generic_visit(inner)
            def visit_Call(self, inner):
                if isinstance(inner.func, ast.Attribute):
                    v=inner.func.value
                    if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id=='request' and inner.args and isinstance(inner.args[0], ast.Constant) and isinstance(inner.args[0].value, str):
                        if v.attr=='args' and inner.func.attr=='get': q.add(inner.args[0].value)
                        if v.attr=='headers' and inner.func.attr=='get': h.add(inner.args[0].value)
                        if v.attr=='form' and inner.func.attr=='get': b.add(inner.args[0].value)
                    if isinstance(v, ast.Name) and v.id in payload and inner.func.attr=='get' and inner.args and isinstance(inner.args[0], ast.Constant) and isinstance(inner.args[0].value, str): b.add(inner.args[0].value)
                if isinstance(inner.func, ast.Name) and inner.func.id=='jsonify':
                    for arg in inner.args:
                        if isinstance(arg, ast.Dict):
                            for key in arg.keys:
                                if isinstance(key, ast.Constant) and isinstance(key.value, str): rk.add(key.value)
                self.generic_visit(inner)
            def visit_Subscript(self, inner):
                if isinstance(inner.value, ast.Attribute) and isinstance(inner.value.value, ast.Name) and inner.value.value.id=='request' and inner.value.attr=='files' and isinstance(inner.slice, ast.Constant) and isinstance(inner.slice.value, str): b.add(inner.slice.value)
                self.generic_visit(inner)
            def visit_Compare(self, inner):
                if isinstance(inner.left, ast.Constant) and isinstance(inner.left.value, str):
                    for c in inner.comparators:
                        if isinstance(c, ast.Attribute) and isinstance(c.value, ast.Name) and c.value.id=='request' and c.attr=='files': b.add(inner.left.value)
                self.generic_visit(inner)
        V().visit(node)
        out.append({'function':node.name,'routes':defs,'query_keys':sorted(q),'header_keys':sorted(h),'body_keys':sorted(b),'response_keys':sorted(rk)})
    return out

def model_lines(m):
    u=USAGE.get((m['module'], m['class_name']), {})
    rows=[f"- {m['module']}.{m['class_name']} -> table `{m['table']}`"]
    cols=[]
    for c in m.get('columns', []):
        attrs=[]
        if c.get('primary_key'): attrs.append('PK')
        if c.get('foreign_keys'): attrs.append('FK='+'|'.join(c['foreign_keys']))
        attrs.append('NULL' if c.get('nullable') else 'NOT NULL')
        if c.get('default') not in {None,'None'}: attrs.append(f"default={c['default']}")
        cols.append(f"{c['name']} {c['type']} [{', '.join(attrs)}]")
    rels=[]
    for r in m.get('relationships', []):
        rels.append(f"{r['name']} -> {r['target']} ({r['direction']}, uselist={r['uselist']}, local={','.join(r.get('local_columns') or []) or '-'}, remote={','.join(r.get('remote_side') or []) or '-'})")
    rows.append('  Fields: ' + ('; '.join(cols) if cols else 'none'))
    rows.append('  Relationships: ' + ('; '.join(rels) if rels else 'none'))
    rows.append('  Usage: status=' + ('likely_orphan' if u.get('likely_orphan') else 'active') + f"; prod_non_model_ref_files={u.get('prod_non_model_ref_files',0)}; sample_refs=" + (', '.join(u.get('sample_non_model_files') or []) or 'none'))
    return rows
FRONTEND = """**5. FRONTEND DATA FLOW**

/devices/<id> -> routes.tracking.tracked_device_live -> templates/tracking/device_live.html
  Load: static/js/tracking/device_live.js bootstraps DOM cache, sets state.activeTab=overview, starts 5s polling, calls /api/tracking/real-time/<mac> immediately, lazily calls /api/devices/<id>/website-policy and /api/devices/<id>/alerts on tab activation, and calls /api/tracking/history/<id>/summary.
  User actions: website-policy add/remove uses POST/DELETE /api/devices/<id>/website-policy; alert acknowledge uses POST /api/devices/<id>/alerts/<event_id>/acknowledge; policy-history navigates to /devices/<id>/policy-history; remote-view/camera/mic actions hit tracking media endpoints.
  In-memory state: state.live, state.policy, state.tabCounters, state.lazyLoaded, state.deviceState, state.mutationLocks, state.eventFeed, state.remoteView, cameraStreaming, micStreaming, 10s cache store.
  Polling/loops: telemetry every 5000ms; lazy policy refresh gating; toast auto-hide timers.

/tracking/history/<id> -> routes.tracking.device_history -> templates/tracking/device_history.html
  Load: static/js/tracking/device_history.js calls /api/tracking/history/<id>/dashboard, /activity, /resources, /applications, /integrity, and /api/devices/<id>/alerts with fallback /api/tracking/devices/<id>/alerts.
  User actions: range selector refreshes all queries; load-more buttons use cursor pagination; run-integrity posts /api/tracking/history/<id>/run-integrity; archive posts /api/tracking/archive-device/<id>; focus=policy switches to policy tab.
  In-memory state: state.range, state.focus, state.cursors.{activity,resources,applications,integrity}, state.datasets.{activity,resources,applications,integrity,policy}.
  Polling/loops: none.

/tracking -> routes.tracking.device_tracking -> templates/tracking/device_tracking.html
  Load: static/js/tracking/device_tracking.js calls /api/tracking/live-summary.
  User actions: /api/tracking/scan, /api/tracking/save-device, /api/tracking/delete-device, /api/tracking/sync-ips.
  In-memory state: DOM-centric rows plus refresh ticker and scan results.
  Polling/loops: status refresh interval plus 1s ticker update.

/tracking/live and /tracking/live/<mac>
  /tracking/live uses static/js/tracking/live_fleet.js to poll /api/tracking/live-summary; /tracking/live/<mac> uses static/js/tracking/live_tracking.js for one-device real-time telemetry and media controls.

/tracking/workstation/<id> -> routes.tracking.workstation_monitor -> templates/tracking/workstation_monitor.html
  Load/user actions: static/js/tracking/workstation_monitor.js calls /api/tracking/workstation/<id>/overview, /reports, /availability, /anomalies, plus history endpoints for activity/resources/applications.

/admin/restricted-sites-policy -> routes.tracking.restricted_sites_policy_page -> templates/admin/restricted_sites_policy.html
  Load: inline JS calls /api/admin/restricted-sites-policy, then /api/tracking/restricted-sites/policy.
  User actions: mode toggle posts /api/admin/restricted-sites-policy/mode; add/remove domains uses POST/DELETE /api/admin/restricted-sites-policy/domains; legacy settings posts /api/tracking/restricted-sites/policy.
  In-memory state: currentDomains, selectedDomains.
  Polling/loops: none.

/api/dashboard-backed dashboard pages -> routes.monitoring.dashboard -> templates/dashboard.html + static/js/dashboard/dashboard.js
  Load: dashboard.js hydrates localStorage cache, validates window.__RBAC_CONTEXT__, fetches /api/dashboard/full_snapshot, and renders summary/top-problems/alerts/inventory/server-health modules. It also fetches /api/dashboard/subnet-details, /api/maintenance/devices, /api/maintenance/toggle, /api/server/<id>/metrics, /api/devices/<id>/connections, and discovery endpoints as needed.
  In-memory state: centralized dashboard state store in static/js/dashboard/state.js, SSE connection state, modal timers, subnet-details cache, RBAC mismatch refresh guard.
  Polling/loops: dashboard polling fallback; SSE heartbeat/reconnect timers; discovery status polling; server-detail modal refresh interval."""

SYNC = """**6. SYNC MECHANISM**

Server endpoint: routes/tracking.py::api_tracking_sync at POST /api/tracking/sync.
Request shape accepted by server:
  mac_address required; hostname; unique_client_id; ip_address; ip_candidates; ip_source; network_signature; api_key; restricted_sites_policy_version; restricted_site_events; current_stats.
  Compatibility fallback: if current_stats missing but payload includes current_activity, today_stats, system_metrics, device_info, or meta, server wraps those into current_stats.
Request shape sent by agent (service.py::AutoDiscoveryService.sync_with_admin):
  mac_address, hostname, ip_address, ip_candidates, ip_source, network_signature, unique_client_id, current_stats, api_key, restricted_sites_policy_version, restricted_site_events.
  current_stats from build_live_stats_payload() contains timestamp, activity.{keyboard_active,mouse_active,idle_seconds,total_active_today}, system.{cpu,memory,current_app}, network.
  restricted_site_events contains queued rows with domain, matched_rule, source, confidence, process_name, raw_evidence, observed_at_utc.
Server DB reads during sync response build:
  TrackedDevice lookup/create by mac_address and unique_client_id; TrackingAgentKeyBinding lookup/create; RestrictedSitePolicy.get_singleton(); RestrictedSiteDomainMeta rows for device; availability and tracking sample persistence.
Server response shape from api_tracking_sync:
  success, message, device, sample, integrity_error_code, resolved_ip, resolved_from, ip_changed, ip_resolution_code, synced_at, restricted_sites_policy_version, optional restricted_sites_policy, optional restricted_site_ingest, optional agent_binding.
  restricted_sites_policy is only returned when client version differs. Server merges global RestrictedSitePolicy.blocked_domains with device-specific RestrictedSiteDomainMeta.domain and sets a synthetic version policy.policy_version + _ + len(merged_domains).
Agent handling of sync response:
  Stores agent_binding.key_id + agent_binding.agent_key in agent_auth.json, applies restricted_sites_policy to RestrictedSiteMonitor, and forces explicit refresh if only the version changed.
Device-specific vs global policy divergence:
  Global policy lives in RestrictedSitePolicy and is managed from /admin/restricted-sites-policy. Device-specific domains live in RestrictedSiteDomainMeta and are managed from device console APIs. Merge occurs only in api_tracking_sync."""

POLICY = """**7. POLICY SYSTEM**

Tables involved:
  restricted_site_policy, restricted_site_domain_meta, tracking_agent_key_bindings, restricted_site_events, restricted_site_alert_state.
Global policy definition/storage:
  models/restricted_site_policy.py::RestrictedSitePolicy.get_singleton() ensures row id=1 exists; domains are normalized by normalize_domain() and versioned by build_policy_version(). Admin UI uses /api/admin/restricted-sites-policy and legacy /api/tracking/restricted-sites/policy.
Device-specific policy definition/storage:
  routes/device_console.py::{get_device_website_policy,add_device_website_policy,remove_device_website_policy} read/write RestrictedSiteDomainMeta rows per tracked device. POST accepts domain required and optional category/reason; DELETE accepts domains[].
Merge behavior:
  Merge is implemented inline in routes/tracking.py::api_tracking_sync using merged_domains = sorted(list(set(policy.blocked_domains + device_domains))). Device metadata is not sent to the agent.
Agent policy read path:
  Agent reads restricted_sites_policy from sync responses and GET /api/tracking/restricted-sites/policy refresh responses, applies it through service.py::RestrictedSiteMonitor.apply_policy(), and uses only enabled, blocked_domains, cooldown, DNS/window poll intervals, DNS seen TTL, and policy_version."""

ALERTS = """**8. ALERT AND VIOLATION SYSTEM**

Detection on agent side:
  service.py::RestrictedSiteMonitor.handle_window_event() inspects active browser window titles and enqueues HIGH-confidence events.
  service.py::RestrictedSiteMonitor.poll_dns_cache() parses ipconfig /displaydns and enqueues LOW-confidence events with TTL suppression.
Reporting path:
  Events are persisted locally into SQLite table restricted_site_event_queue by _enqueue_event(). AutoDiscoveryService.sync_with_admin() drains up to 50 pending events, sends them inside restricted_site_events on /api/tracking/sync, and marks them sent/failed. A dedicated POST /api/tracking/restricted-sites/events also exists.
Server persistence path:
  routes/tracking.py::_ingest_restricted_site_events_internal() writes RestrictedSiteEvent, updates/creates RestrictedSiteAlertState, and creates/updates DashboardEvent with metric name restricted_site:tracked:<device_id>:<domain>.
Alert fan-out:
  New alerts trigger best-effort SSE broadcast via broadcast_event() and best-effort email via NotificationService.send_warning_alert(). Maintenance-mode devices suppress alert creation.
UI surfacing:
  Device console uses GET /api/devices/<id>/alerts from routes/device_console.py::get_device_alerts and renders cards/timeline/actions in static/js/tracking/device_live.js. History policy tab also loads the same endpoint with fallback /api/tracking/devices/<id>/alerts. Dashboard consumes DashboardEvent aggregates through /api/dashboard/alerts and /api/dashboard/full_snapshot."""

AUTH = """**9. AUTHENTICATION AND ADMIN**

Browser auth model:
  Session/cookie auth in routes/auth.py; login writes session[logged_in, username, user_id, role, auth_source, site_id, department_id, session_id, last_activity, login_time]. Passwords are checked with extensions.bcrypt. Optional LDAP auth is attempted first when LDAP_ENABLED is true, then local DB fallback is used.
Session enforcement:
  middleware/session_middleware.py updates last-activity timestamps and enforces timeout; middleware/rbac.py provides require_login, require_role, require_permission, scope filtering, and API/browser-specific unauthorized responses.
Role model:
  Roles in RBAC are admin, manager, operator, viewer, and user. ROLE_PERMISSIONS and ENDPOINT_PERMISSIONS in middleware/rbac.py drive route-level auth.
Admin vs regular distinction:
  Role is stored on models.user.User.role and copied into session on login. build_scope_context() derives global/site/department scope from session.site_id and session.department_id or the loaded User record. get_ui_rbac_context() injects { role, scope_key, scope_label, capabilities } into templates as window.__RBAC_CONTEXT__.
Protected routes:
  Most /api/dashboard/*, device console, file transfer, devices, scanning, maintenance, user management, sites, departments, subnets, reports export, and history endpoints are protected through explicit decorators or ENDPOINT_PERMISSIONS. Public exceptions include auth routes, tracking_bp.api_tracking_sync, tracking_bp.api_ingest_restricted_site_events, and tracking_bp.api_tracking_register which use shared API key / bound-agent auth instead of browser session auth.
Non-browser auth paths:
  Agent-to-server sync allows bootstrap with shared X-API-Key / api_key payload via _require_tracking_api_key(), then upgrades to per-device X-Agent-Key-Id + X-Agent-Key checked against TrackingAgentKeyBinding hashes. Agent local Flask app separately enforces X-API-Key on /api/files/* and /api/secure/*."""

GAPS = """**10. IDENTIFIED GAPS AND INCONSISTENCIES**

- routes/dashboard.py defines acknowledge_alert(event_id) but it has no @dashboard_bp.route decorator, while static/js/dashboard/tables/topProblems.js calls POST /api/dashboard/alerts/<id>/acknowledge.
- templates/file_transfer.html and templates/file_transfer_temp.html call /api/files/local/delete and /api/files/local/create_folder, but routes/file_transfer.py defines no matching local delete/create-folder routes.
- templates/ssh_profiles.html calls /api/ssh_profiles endpoints, but no matching backend routes appear in the server URL map.
- routes/tracking.py contains duplicate helper definitions for _ingest_restricted_site_events_internal and related helpers; later definitions override earlier ones silently.
- Two SQLAlchemy model classes share the same class name DeviceScanHistory in different modules/tables (models.scan_history and models.tracked_device).
- Device-specific policy metadata category/reason is stored on the server but stripped before policy is sent to the agent.
- service.py contains duplicate require_api_key definitions near the top of the file.
- Several source files contain mojibake/encoding artifacts such as — and →.
- There is an anomalous template path templates/auth/{{ url_for('auth_bp.forgot_password') }} that appears accidental/orphaned.
- Frontend fallback from /api/devices/<id>/alerts to /api/tracking/devices/<id>/alerts indicates overlapping surfaces rather than one canonical endpoint.
- service.py::verify_admin_key() exists but is not part of the active sync auth path.
- Some models show likely_orphan=true in artifacts/model_usage_index.json; these need confirmation because ORM relationships can hide usage."""

def main():
    tr = template_renderers(); sr = static_refs(); svc = service_routes(); out=[]
    out += ['COMPREHENSIVE ARCHITECTURE REPORT','Project root: d:/device_monitoring_tactical','Generated from live repository code, route map, model inventory, and endpoint contract extraction.','', '**1. PROJECT STRUCTURE**','', 'Top-level directories']
    for child in sorted([p for p in ROOT.iterdir() if p.is_dir()], key=lambda p: p.name.lower()): out.append(f"- {rel(child)}/: {TOP.get(child.name, 'Project directory; inspect contents for exact purpose.')}")
    out += ['', 'Key nested directories']
    for k in sorted(SUB):
        if (ROOT/k).exists(): out.append(f"- {k}/: {SUB[k]}")
    out += ['', 'Route files and endpoints defined']
    groups=defaultdict(list)
    for r in ROUTES:
        fk = r['module'].replace('.','/') + '.py' if r['module'] != 'routes.api_v1' else 'routes/api_v1/__init__.py'
        groups[fk].append(r)
    for fk in sorted(groups):
        out.append(f"- {fk}")
        for r in sorted(groups[fk], key=lambda x:(x['path'], ','.join(x['methods']), x['function'])):
            out.append(f"  {','.join(r['methods'])} {r['path']} -> {r['function']} (endpoint={r['endpoint']})")
    out += ['', 'Template files and pages served']
    for tpl in sorted(rel(p) for p in (ROOT/'templates').rglob('*') if p.is_file()):
        rs = tr.get(tpl.replace('templates/',''), [])
        if rs:
            det=[]
            for item in rs:
                rd='; '.join(f"{','.join(x['methods'])} {x['path']}" for x in item['routes']) or 'no route metadata'
                det.append(f"{item['module']}.{item['function']} [{rd}]")
            out.append(f"- {tpl}: served by {' | '.join(det)}")
        else:
            out.append(f"- {tpl}: no direct render_template() caller found; likely include/partial/orphan/template artifact")
    out += ['', 'Static JS/CSS files and what they control']
    for asset in [p for p in sorted((ROOT/'static').rglob('*')) if p.is_file() and p.suffix.lower() in {'.js','.css'}]:
        ar = rel(asset); refs = sorted(sr.get(ar.replace('static/',''), set())); out.append(f"- {ar}: {static_desc(ar, refs)}")
        if refs: out.append(f"  Referenced by templates: {', '.join(refs)}")
    out += ['', '**2. DATABASE MODELS**','']
    for m in MODELS: out += model_lines(m)
    out += ['', '**3. API SURFACE**','', 'Server Flask API endpoints']
    for r in sorted([x for x in ROUTES if x['path'].startswith('/api/')], key=lambda x:(x['module'], x['path'], ','.join(x['methods']))):
        fk = r['module'].replace('.','/') + '.py' if r['module'] != 'routes.api_v1' else 'routes/api_v1/__init__.py'; c = CONTRACT_BY.get((fk, r['function']), {})
        out.append(f"- {','.join(r['methods'])} {r['path']} -> {r['module']}.{r['function']}")
        out.append(f"  Consumer: {consumer(r['path'], r['module'], r['function'])}")
        out.append(f"  Query keys: {fmt(c.get('query_keys', []))}")
        out.append(f"  Header keys: {fmt(c.get('header_keys', []))}")
        out.append(f"  Body keys: {fmt(c.get('body_keys', []))}")
        out.append(f"  Response keys: {fmt(c.get('response_keys', []))}")
    out += ['', 'Agent-local Flask API endpoints defined in service.py']
    for item in sorted(svc, key=lambda x:(x['routes'][0]['path'], ','.join(x['routes'][0]['methods']))):
        for r in item['routes']:
            if not r['path'].startswith('/api/'): continue
            out.append(f"- {','.join(r['methods'])} {r['path']} -> service.py::{item['function']}")
            out.append(f"  Consumer: {consumer(r['path'], 'service', item['function'], True)}")
            out.append(f"  Query keys: {fmt(item.get('query_keys', []))}")
            out.append(f"  Header keys: {fmt(item.get('header_keys', []))}")
            out.append(f"  Body keys: {fmt(item.get('body_keys', []))}")
            out.append(f"  Response keys: {fmt(item.get('response_keys', []))}")
    out += ['', '**4. AGENT (service.py)**','', 'Startup sequence', '- ensure_single_instance() enforces a single running service process via file locking before Flask starts.', '- initialize_enhanced_tracker() initializes secure SQLite storage, loads prior daily stats, registers/updates the device identity, initializes RestrictedSiteMonitor, starts keyboard/mouse listeners, starts background activity threads, starts AutoDiscoveryService, and performs immediate restricted-policy refresh.', 'Threads/loops and intervals', '- enhanced_activity_tracker() runs continuously with 1s sleep to accumulate keyboard/mouse active time and idle duration.', '- explicit_interval_monitor() runs with 0.5s resolution and schedules CPU/RAM every 2s, network every 5s, top processes every 60s, active-window capture every max(5, policy.window_poll_seconds) (default 10s), DNS cache polling every max(15, policy.dns_poll_seconds) (default 60s), and policy refresh every 300s.', '- AutoDiscoveryService.start_auto_sync() runs a daemon worker that discovers admin servers, syncs every sync_interval seconds (default 60, min 15) with exponential backoff to 4x on failure, and also checks for network signature changes.', '- Keyboard and mouse listeners each run in their own daemon thread from start_enhanced_listeners().', 'Data collected locally', '- Identity: MAC, hostname/FQDN, selected IP, IP candidates, IP source, network signature, persistent client ID.', '- Activity: keyboard/mouse active flags, idle seconds, daily active duration, typed-text counters.', '- System telemetry: CPU, memory, current app, top processes, boot time, OS/platform info.', '- Network telemetry: upload/download/network metrics from client_modules.system_core.NetworkMonitor.', '- Restricted-site evidence: active browser window title hostnames and DNS cache hostnames.', 'Outbound server calls', '- GET /api/tracking/register during admin discovery/probing.', '- POST /api/tracking/sync for regular sync with sync_data payload described in section 6.', '- GET /api/tracking/restricted-sites/policy?current_version=... for explicit policy refresh when a server is known.', 'What agent receives and how it uses it', '- agent_binding.key_id and agent_binding.agent_key are saved in agent_auth.json and used on subsequent sync requests as bound-agent auth headers.', '- restricted_sites_policy or restricted_sites_policy_version is applied to RestrictedSiteMonitor to control blocked domains, cooldown, and polling intervals.', 'What agent currently does not do', '- It does not consume device-specific metadata like policy category/reason; only merged domains matter on agent side.', '- It does not directly receive or apply alert acknowledge/resolve state from the server.', '- It does not upload system-info on the standard sync path; comment notes this was intentionally omitted to shrink payloads.', '- It does not use verify_admin_key() in the active sync path.', '- It does not persist a durable remote event/history model beyond its local SQLite queue and daily activity snapshots.', '']
    out += FRONTEND.splitlines() + [''] + SYNC.splitlines() + [''] + POLICY.splitlines() + [''] + ALERTS.splitlines() + [''] + AUTH.splitlines() + [''] + GAPS.splitlines()
    path = ROOT/'artifacts'/'architecture_report.txt'; path.write_text('\n'.join(out), encoding='utf-8'); print(f'Wrote {path} with {len(out)} lines')

if __name__ == '__main__':
    main()
