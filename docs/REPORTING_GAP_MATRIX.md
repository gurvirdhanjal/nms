# Reporting Gap Matrix

Current state for the reporting stack as implemented in March 2026. This matrix is intentionally aligned to persisted functionality that already exists in the app and is meant to drive PDF/CSV/XLSX reporting scope.

## Live Data Snapshot

| Table | Live rows | Readiness note |
| --- | ---: | --- |
| `device` | 239 | Core inventory populated |
| `device_scan_history` | 92,155 | Core availability history populated |
| `dashboard_events` | 1,275 | Alerts/event history populated |
| `server_health_logs` | 905 | Raw server telemetry populated |
| `server_health_hourly_rollups` | 1 | Hourly rollups barely seeded |
| `server_health_daily_rollups` | 0 | Daily server rollups missing |
| `daily_device_stats` | 0 | Daily uptime rollups missing |
| `tracked_devices` | 3 | Small but present |
| `tracking_samples` | 1,221 | Tracking telemetry populated |
| `device_application_logs` | 819 | Productivity data populated |
| `device_activity_logs` | 533 | Activity data populated |
| `tracked_device_availability_events` | 1,835 | Tracking availability populated |
| `tracking_hourly_rollups` | 0 | Tracking hourly rollups missing |
| `tracking_daily_rollups` | 0 | Tracking daily rollups missing |
| `device_interfaces` | 0 | Interface inventory missing |
| `interface_traffic_history` | 0 | Bandwidth history missing |
| `printer_metrics` | 0 | Printer telemetry missing |
| `print_job_audit` | 0 | Print audit missing |
| `maintenance_window` | 2 | Maintenance data present |
| `audit_logs` | 614 | Audit trail present |
| `restricted_site_events` | 51 | Restricted-site violations present |
| `sites` | 0 | Site dimension missing in live DB |
| `departments` | 0 | Department dimension missing in live DB |
| `subnets` | 0 | Subnet dimension missing in live DB |

## Domain Matrix

| Domain | Current UI surface | Endpoint | Primary tables | Readiness | Export parity | Enterprise gap |
| --- | --- | --- | --- | --- | --- | --- |
| Executive Health | Reports tab | `/api/reports/executive` | `device`, `daily_device_stats`, `device_scan_history`, `dashboard_events` | Partial | CSV/XLSX/PDF | Blocked on `daily_device_stats` backfill for true uptime rollups |
| Operational | Reports tab | `/api/reports/operational` | `server_health_logs`, `server_health_hourly_rollups`, `server_health_daily_rollups`, `dashboard_events`, `device` | Partial | CSV/XLSX/PDF | Long-range heatmap quality depends on hourly/daily rollups |
| Device Health | Reports tab | `/api/reports/device-health` | `server_health_logs`, `server_health_hourly_rollups`, `server_health_daily_rollups`, `device` | Partial | CSV/XLSX/PDF | 30d+ reporting depends on rollup coverage |
| Productivity | Reports tab | `/api/reports/productivity` | `tracking_samples`, `device_application_logs`, `device_activity_logs` | Partial | CSV/XLSX/PDF | Long-range tracking rollups still missing |
| Network | Reports tab | `/api/reports/network` | `daily_device_stats`, `device_interfaces`, `interface_traffic_history`, `dashboard_events` | Blocked-by-empty-data | CSV/XLSX/PDF | Missing daily stats and interface/bandwidth ingestion |
| Alerts | Reports tab | `/api/reports/alerts` | `dashboard_events`, `device` | Ready | CSV/XLSX/PDF | Needs sustained SLA semantics, but core dataset exists |
| Device Inspector | Reports tab | existing diagnostic UI | mixed live inventory tables | Diagnostic only | Excluded | Explicitly out of enterprise reporting/export scope |
| Maintenance & Availability | New enterprise API | `/api/reports/maintenance-availability` | `maintenance_window`, `daily_device_stats`, `device_scan_history`, `tracked_device_availability_events`, `device` | Partial | CSV/XLSX/PDF | Falls back to raw availability until daily stats mature |
| Security & Compliance | New enterprise API | `/api/reports/security-compliance` | `dashboard_events`, `audit_logs`, `restricted_site_events`, `tracking_history_integrity_audit`, `server_metric_threshold_state` | Partial | CSV/XLSX/PDF | Integrity audits and threshold-state coverage still sparse |
| Inventory & Asset | New enterprise API | `/api/reports/inventory-assets` | `device`, `tracked_devices`, `device_identity_links`, `device_identity_link_candidates`, `sites`, `departments`, `subnets` | Partial | CSV/XLSX/PDF | Site/department/subnet dimensions missing in live DB |
| Tracking Operations | New enterprise API | `/api/reports/tracking-operations` | `tracked_devices`, `tracking_samples`, `device_activity_logs`, `device_application_logs`, `tracked_device_availability_events`, rollups | Partial | CSV/XLSX/PDF | Rollups missing for reliable 7d/30d aggregation |
| Printer Operations | New enterprise API | `/api/reports/printer-operations` | `printer_metrics`, `print_job_audit`, `device` | Blocked-by-empty-data | CSV/XLSX/PDF | Awaiting printer telemetry and audit ingestion triggers |

## Decisions Locked In Code

- RBAC scope is enforced in report generation:
  - `admin`: global
  - `manager`: site
  - `operator`, `viewer`, `user`: department
- Reporting rollups now run on closed recent windows instead of waiting for raw-retention cutoff:
  - `daily_device_stats`: previous day
  - `server_health_hourly_rollups`: every hour
  - `server_health_daily_rollups`: every day
  - `tracking_hourly_rollups`: every hour
  - `tracking_daily_rollups`: every day
- Maintenance exposes `/api/maintenance/backfill-rollups` to backfill the reporting foundation across daily stats, server health, and tracking rollups.
- Export jobs now support persistent DB storage through `report_export_jobs` with memory fallback.
- Sync report caching is range-aware:
  - `24h`: 60s
  - `7d` / `30d`: 180s
  - `90d`, `executive`, `operational`: 300s
- Each report response now includes `meta` with scope, freshness, cache, source-table, and completeness-warning fields.
- `freshness_state` is based on report telemetry sources and now distinguishes `fresh`, `delayed`, `stale`, and `empty`.
- Rollup coverage gaps are surfaced through `completeness_warnings` using `rollup_coverage_low`.
- `PDF` export is part of the supported format contract for enterprise reports.
