# Implementation Plan: Enterprise Monitoring Expansion

## Overview

This implementation plan breaks down the enterprise monitoring expansion into 12 phases following a 24-week roadmap. Each phase builds incrementally on previous work, with comprehensive testing and validation at each checkpoint. The expansion adds printer monitoring, camera integration, multi-site support, distributed polling, advanced alerting, compliance reporting, performance analytics, custom dashboards, RBAC enhancements, bulk operations, and comprehensive REST API.

## Tasks

- [ ] 1. Phase 1: Foundation (Weeks 1-2)
  - [ ] 1.1 Create database schema and migrations
    - Create migration file for all 18 new models
    - Add new columns to existing Device model (site_id, department_id, agent_version, last_agent_checkin)
    - Add new columns to existing User model (department_isolation_enabled, default_dashboard_id, session_timeout_minutes, password_last_changed, password_complexity_required)
    - Create all indexes specified in design document
    - Test migration on development database
    - _Requirements: 1.1, 4.1, 9.1, 19.1, 25.1_

  - [ ]* 1.2 Write property test for database schema
    - **Property 1: Printer Device Classification**
    - **Validates: Requirements 1.1**

  - [ ] 1.3 Implement SecurityService
    - Create services/security_service.py
    - Implement AES-256 encryption/decryption methods (encrypt_aes256, decrypt_aes256, get_encryption_key)
    - Implement API token hashing methods (hash_api_token, verify_api_token, generate_api_token)
    - Implement input sanitization (sanitize_input)
    - Implement password complexity validation (validate_password_complexity)
    - _Requirements: 25.1, 25.2, 25.3, 25.7, 25.9_

  - [ ]* 1.4 Write property test for encryption round-trip
    - **Property 6: Configuration Round-Trip**
    - **Validates: Requirements 23.6**

  - [ ] 1.5 Implement DepartmentIsolationService
    - Create services/department_isolation_service.py
    - Implement get_accessible_devices method with department filtering
    - Implement get_user_departments with hierarchical support
    - Implement check_device_access permission checking
    - Implement filter_query_by_department for SQLAlchemy queries
    - Implement assign_user_to_department
    - _Requirements: 19.4, 19.5, 19.6, 19.8_


  - [ ]* 1.6 Write property test for department isolation
    - **Property 17: Department Isolation Enforcement**
    - **Validates: Requirements 19.4, 19.5**

  - [ ] 1.7 Implement ConfigurationService
    - Create services/configuration_service.py
    - Implement export_configuration method (sites, departments, devices, alert_policies, sla_metrics, rbac_roles)
    - Implement validate_configuration with schema validation
    - Implement import_configuration with modes (full, partial, merge)
    - Implement parse_configuration JSON parser
    - Implement pretty_print_configuration with 2-space indentation
    - Implement calculate_config_hash for integrity verification
    - _Requirements: 23.1, 23.2, 23.3, 23.4, 23.5, 23.9, 23.10_

  - [ ]* 1.8 Write unit tests for ConfigurationService
    - Test export_configuration with all sections
    - Test validate_configuration with valid and invalid configs
    - Test import_configuration with different modes
    - Test partial import functionality
    - _Requirements: 23.1-23.10_

- [ ] 2. Checkpoint - Foundation Complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 3. Phase 2: Printer Monitoring (Weeks 3-4)
  - [ ] 3.1 Implement PrinterMonitoringService
    - Create services/printer_monitoring_service.py
    - Implement poll_printer_snmp using RFC 3805 Printer MIB OIDs
    - Implement store_printer_metrics to save PrinterMetrics records
    - Implement check_printer_alerts for toner and status thresholds
    - Implement parse_print_job_log for Windows Event Log and CUPS
    - Implement get_print_audit_trail with filtering
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.8, 2.2, 2.4, 3.1, 3.4_

  - [ ]* 3.2 Write property test for printer classification
    - **Property 1: Printer Device Classification**
    - **Validates: Requirements 1.1**

  - [ ]* 3.3 Write property test for printer metrics completeness
    - **Property 2: Printer Metrics Completeness**
    - **Validates: Requirements 1.3**

  - [ ]* 3.4 Write property test for toner alert threshold
    - **Property 3: Toner Alert Threshold**
    - **Validates: Requirements 1.4**

  - [ ] 3.5 Extend SNMP worker for printer polling
    - Modify workers/snmp_worker.py to handle task_type='printer_snmp'
    - Add printer SNMP OID polling logic
    - Integrate with PrinterMonitoringService.poll_printer_snmp
    - Add error handling for SNMP timeouts and auth failures
    - _Requirements: 1.2, 1.7_

  - [ ] 3.6 Extend Server_Agent for print server monitoring
    - Modify server_agent.py to detect printer shares (Windows)
    - Add Windows Event Log parsing for print jobs
    - Add CUPS log parsing for Linux print servers
    - Send print job metadata to Monitoring_System API
    - _Requirements: 2.1, 2.2, 2.5, 2.8_

  - [ ] 3.7 Create printer API endpoints
    - Create routes/printers.py
    - Implement GET /api/v1/printers (list all printers)
    - Implement GET /api/v1/printers/{id} (printer details)
    - Implement GET /api/v1/printers/{id}/metrics (printer metrics)
    - Implement GET /api/v1/printers/{id}/jobs (print jobs)
    - Implement GET /api/v1/print-jobs (list with filters)
    - Implement GET /api/v1/print-audit (audit trail with filters)
    - Add pagination, filtering, and sorting
    - _Requirements: 2.4, 3.3, 3.4_

  - [ ]* 3.8 Write property test for print job retention
    - **Property 4: Print Job Retention**
    - **Validates: Requirements 2.3**

  - [ ]* 3.9 Write property test for print job correlation
    - **Property 5: Print Job Correlation**
    - **Validates: Requirements 3.2**

  - [ ] 3.10 Add printer UI pages
    - Create templates/printers.html (printer list page)
    - Create templates/printer_detail.html (printer detail with toner visualizations)
    - Create templates/print_audit.html (audit trail page)
    - Add printer metrics charts and toner level gauges
    - _Requirements: 1.6, 2.4, 3.3_

- [ ] 4. Checkpoint - Printer Monitoring Complete
  - Ensure all tests pass, ask the user if questions arise.


- [ ] 5. Phase 3: Camera Integration (Weeks 5-6)
  - [ ] 5.1 Implement CameraMonitoringService
    - Create services/camera_monitoring_service.py
    - Implement test_rtsp_connection using OpenCV
    - Implement capture_frame with resize and JPEG compression
    - Implement cleanup_old_frames with retention period
    - Implement get_latest_frame and get_frame_gallery
    - Implement encrypt_rtsp_credentials and decrypt_rtsp_credentials using SecurityService
    - _Requirements: 7.1, 7.3, 7.10, 8.1, 8.2, 8.3, 8.5, 25.1_

  - [ ]* 5.2 Write property test for camera frame resolution limit
    - **Property 12: Camera Frame Resolution Limit**
    - **Validates: Requirements 8.2**

  - [ ]* 5.3 Write property test for camera three-strike alert
    - **Property 11: Camera Three-Strike Alert**
    - **Validates: Requirements 7.4**

  - [ ] 5.4 Create camera worker
    - Create workers/camera_worker.py
    - Implement execute_camera_capture task handler
    - Use SELECT FOR UPDATE SKIP LOCKED pattern for concurrency
    - Integrate with CameraMonitoringService.capture_frame
    - Track consecutive failures for alert generation
    - _Requirements: 7.3, 7.4, 7.5_

  - [ ] 5.5 Create camera cleanup worker
    - Add cleanup_old_frames scheduled job to workers/camera_worker.py
    - Schedule daily execution at 03:00 UTC
    - Delete frames older than retention period (default 30 days)
    - Update storage statistics
    - _Requirements: 7.6, 8.5_

  - [ ] 5.6 Create camera API endpoints
    - Create routes/cameras.py
    - Implement GET /api/v1/cameras (list all cameras)
    - Implement POST /api/v1/cameras (add camera)
    - Implement GET /api/v1/cameras/{id} (camera details)
    - Implement PUT /api/v1/cameras/{id} (update camera)
    - Implement DELETE /api/v1/cameras/{id} (delete camera)
    - Implement GET /api/v1/cameras/{id}/frames (get frames)
    - Implement POST /api/v1/cameras/{id}/capture (on-demand capture)
    - Implement GET /api/v1/cameras/{id}/latest (latest frame)
    - Implement GET /api/v1/camera-gallery (gallery view)
    - _Requirements: 7.1, 7.7, 8.8_

  - [ ] 5.7 Add camera UI pages
    - Create templates/cameras.html (camera list page)
    - Create templates/camera_detail.html (camera detail with latest frame)
    - Create templates/camera_gallery.html (gallery view with thumbnails)
    - Add click-to-enlarge functionality for frames
    - _Requirements: 7.7, 7.8_

  - [ ] 5.8 Extend scheduler for camera tasks
    - Modify services/scheduler.py to add enqueue_camera_tasks method
    - Schedule camera frame capture tasks based on capture_interval_seconds
    - _Requirements: 7.5_

- [ ] 6. Checkpoint - Camera Integration Complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Phase 4: Multi-Site and Distributed Polling (Weeks 7-9)
  - [ ] 7.1 Create site management
    - Create routes/sites.py
    - Implement GET /api/v1/sites (list all sites)
    - Implement POST /api/v1/sites (create site)
    - Implement GET /api/v1/sites/{id} (site details)
    - Implement PUT /api/v1/sites/{id} (update site)
    - Implement DELETE /api/v1/sites/{id} (delete site)
    - Implement GET /api/v1/sites/{id}/devices (devices in site)
    - Implement GET /api/v1/sites/{id}/dashboard (site dashboard metrics)
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.6_

  - [ ] 7.2 Add site UI pages
    - Create templates/sites.html (site list page)
    - Create templates/site_detail.html (site detail with dashboard)
    - Add site filtering to device list page
    - Display site affiliation on device pages
    - _Requirements: 9.3, 9.4, 9.5, 9.8_

  - [ ] 7.3 Implement PollingNodeService
    - Create services/polling_node_service.py
    - Implement register_polling_node with API token generation
    - Implement process_heartbeat to update node status
    - Implement assign_devices_to_node with assignment methods (site, subnet, manual)
    - Implement forward_metrics to receive and store metrics from nodes
    - Implement check_node_health for stale heartbeat detection
    - Implement get_node_assignments
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.9, 10.10_

  - [ ]* 7.4 Write property test for polling node metric attribution
    - **Property 13: Polling Node Metric Attribution**
    - **Validates: Requirements 10.4**

  - [ ]* 7.5 Write property test for polling node offline caching
    - **Property 14: Polling Node Offline Caching**
    - **Validates: Requirements 10.8**

  - [ ] 7.6 Create polling node API endpoints
    - Create routes/polling_nodes.py
    - Implement GET /api/v1/polling-nodes (list nodes)
    - Implement POST /api/v1/polling-nodes (register node)
    - Implement GET /api/v1/polling-nodes/{id} (node details)
    - Implement PUT /api/v1/polling-nodes/{id} (update node)
    - Implement DELETE /api/v1/polling-nodes/{id} (delete node)
    - Implement POST /api/v1/polling-nodes/{id}/heartbeat (heartbeat)
    - Implement POST /api/v1/polling-nodes/{id}/metrics (forward metrics)
    - Implement GET /api/v1/polling-nodes/{id}/assignments (device assignments)
    - Implement POST /api/v1/polling-nodes/{id}/assign (assign devices)
    - Add token-based authentication for node endpoints
    - _Requirements: 10.9, 10.10_

  - [ ] 7.7 Add polling node health checks to scheduler
    - Modify services/scheduler.py to add check_polling_node_health method
    - Schedule health checks every 5 minutes
    - Generate CRITICAL alert for stale heartbeats (> 5 minutes)
    - _Requirements: 10.6_

  - [ ] 7.8 Implement device-to-node assignment logic
    - Add automatic assignment based on site
    - Add automatic assignment based on subnet matching
    - Add manual assignment override
    - Update device polling to use assigned node
    - _Requirements: 10.7_

  - [ ] 7.9 Add polling node UI pages
    - Create templates/polling_nodes.html (node list page)
    - Create templates/polling_node_detail.html (node detail with health status)
    - Add node assignment interface for devices
    - _Requirements: 10.5_

- [ ] 8. Checkpoint - Multi-Site and Distributed Polling Complete
  - Ensure all tests pass, ask the user if questions arise.


- [ ] 9. Phase 5: Advanced Alerting (Weeks 10-11)
  - [ ] 9.1 Implement AlertEscalationService
    - Create services/alert_escalation_service.py
    - Implement initiate_escalation to start escalation sequence
    - Implement process_escalation_queue to check for ready escalations
    - Implement escalate_to_next_level with notification sending
    - Implement acknowledge_alert to halt escalation
    - Implement send_escalation_notifications (email and webhook)
    - Implement test_escalation_policy for validation
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.9_

  - [ ]* 9.2 Write property test for escalation timing
    - **Property 15: Alert Escalation Timing**
    - **Validates: Requirements 12.4**

  - [ ]* 9.3 Write property test for escalation halt on acknowledgment
    - **Property 16: Alert Escalation Halt on Acknowledgment**
    - **Validates: Requirements 12.5**

  - [ ] 9.4 Create webhook worker
    - Create workers/webhook_worker.py
    - Implement execute_webhook_delivery task handler
    - Add retry logic with exponential backoff (60s, 120s, 240s)
    - Log delivery attempts and responses
    - _Requirements: 17.2, 17.5, 17.6_

  - [ ]* 9.5 Write property test for webhook retry
    - **Property 20: Webhook Retry Logic**
    - **Validates: Requirements 17.5**

  - [ ] 9.6 Create escalation policy API endpoints
    - Create routes/escalation_policies.py
    - Implement GET /api/v1/escalation-policies (list policies)
    - Implement POST /api/v1/escalation-policies (create policy)
    - Implement GET /api/v1/escalation-policies/{id} (policy details)
    - Implement PUT /api/v1/escalation-policies/{id} (update policy)
    - Implement DELETE /api/v1/escalation-policies/{id} (delete policy)
    - Implement POST /api/v1/escalation-policies/{id}/test (test policy)
    - _Requirements: 12.1, 12.2, 12.9_

  - [ ] 9.7 Create webhook integration API endpoints
    - Create routes/webhooks.py
    - Implement GET /api/v1/webhooks (list webhooks)
    - Implement POST /api/v1/webhooks (create webhook)
    - Implement GET /api/v1/webhooks/{id} (webhook details)
    - Implement PUT /api/v1/webhooks/{id} (update webhook)
    - Implement DELETE /api/v1/webhooks/{id} (delete webhook)
    - Implement POST /api/v1/webhooks/{id}/test (test webhook)
    - Implement GET /api/v1/webhooks/{id}/deliveries (list deliveries)
    - Implement POST /api/v1/webhooks/deliveries/{id}/retry (retry delivery)
    - _Requirements: 17.1, 17.4, 17.7, 17.8_

  - [ ] 9.8 Extend alert endpoints for escalation
    - Modify routes/alerts.py
    - Add POST /api/v1/alerts/{id}/acknowledge endpoint
    - Add GET /api/v1/alerts/{id}/escalation endpoint
    - Display escalation status on alert detail pages
    - _Requirements: 12.5, 12.7_

  - [ ] 9.9 Add escalation queue processor to scheduler
    - Modify services/scheduler.py to add process_alert_escalations method
    - Schedule escalation processing every minute
    - Query AlertEscalationState records with next_escalation_time <= now
    - _Requirements: 12.4_

  - [ ] 9.10 Add escalation UI pages
    - Create templates/escalation_policies.html (policy list page)
    - Create templates/escalation_policy_detail.html (policy configuration)
    - Create templates/webhooks.html (webhook list page)
    - Create templates/webhook_detail.html (webhook configuration with test interface)
    - Update alert detail page to show escalation status
    - _Requirements: 12.7, 17.8_

- [ ] 10. Checkpoint - Advanced Alerting Complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 11. Phase 6: Compliance and Reporting (Weeks 12-13)
  - [ ] 11.1 Implement ComplianceReportingService
    - Create services/compliance_reporting_service.py
    - Implement generate_report with data querying and rendering
    - Implement get_report_template for SOC2, ISO27001, HIPAA, PCI_DSS
    - Implement render_pdf using ReportLab or WeasyPrint
    - Implement render_excel using openpyxl
    - Implement schedule_report for automated generation
    - Implement deliver_report via email
    - Implement log_report_access for audit trail
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.9_

  - [ ] 11.2 Create compliance report worker
    - Create workers/compliance_worker.py
    - Implement generate_scheduled_reports task handler
    - Schedule based on report frequency configuration
    - _Requirements: 13.3, 13.4_

  - [ ] 11.3 Create compliance report API endpoints
    - Create routes/compliance_reports.py
    - Implement GET /api/v1/compliance-reports (list reports)
    - Implement POST /api/v1/compliance-reports (create report)
    - Implement GET /api/v1/compliance-reports/{id} (report details)
    - Implement PUT /api/v1/compliance-reports/{id} (update report)
    - Implement DELETE /api/v1/compliance-reports/{id} (delete report)
    - Implement POST /api/v1/compliance-reports/{id}/generate (generate report)
    - Implement GET /api/v1/compliance-reports/{id}/executions (list executions)
    - Implement GET /api/v1/compliance-reports/executions/{id} (execution details)
    - Implement GET /api/v1/compliance-reports/executions/{id}/download (download report)
    - Implement GET /api/v1/compliance-reports/templates (list templates)
    - _Requirements: 13.1, 13.5, 13.7, 13.8_

  - [ ] 11.4 Add compliance report UI pages
    - Create templates/compliance_reports.html (report list page)
    - Create templates/compliance_report_detail.html (report configuration)
    - Create templates/compliance_report_execution.html (execution details with download)
    - Add report template selection interface
    - _Requirements: 13.5, 13.6, 13.7_

  - [ ]* 11.5 Write unit tests for compliance report generation
    - Test report generation for each template type
    - Test PDF and Excel rendering
    - Test scheduled report execution
    - _Requirements: 13.1-13.5_

- [ ] 12. Checkpoint - Compliance and Reporting Complete
  - Ensure all tests pass, ask the user if questions arise.


- [ ] 13. Phase 7: Performance Analytics (Weeks 14-16)
  - [ ] 13.1 Implement BaselineAnomalyService
    - Create services/baseline_anomaly_service.py
    - Implement calculate_baseline using 30-day rolling window
    - Implement detect_anomaly with configurable sensitivity (2σ, 3σ, 4σ)
    - Implement get_baseline_comparison for reporting
    - Implement reset_baseline for manual resets
    - Implement exclude_maintenance_windows from calculations
    - _Requirements: 14.1, 14.2, 14.3, 14.5, 14.6, 14.8_

  - [ ]* 13.2 Write property test for anomaly detection threshold
    - **Property 17: Anomaly Detection Threshold**
    - **Validates: Requirements 14.2**

  - [ ] 13.3 Create baseline calculator worker
    - Create workers/baseline_worker.py
    - Implement calculate_all_baselines task handler
    - Schedule daily execution at 04:00 UTC
    - Process metrics: cpu_usage, memory_usage, disk_usage, network_in_bps, network_out_bps
    - _Requirements: 14.1_

  - [ ] 13.4 Implement CapacityPlanningService
    - Create services/capacity_planning_service.py
    - Implement calculate_forecast using linear regression on 90-day data
    - Implement get_capacity_dashboard with urgency sorting
    - Implement generate_capacity_report aggregated by site, department, device type
    - Generate WARNING alert when exhaustion < 30 days
    - Generate CRITICAL alert when exhaustion < 7 days
    - _Requirements: 15.1, 15.2, 15.3, 15.5, 15.6, 15.7, 15.8_

  - [ ]* 13.5 Write property test for capacity forecast calculation
    - **Property 18: Capacity Forecast Calculation**
    - **Validates: Requirements 15.2**

  - [ ] 13.6 Create capacity forecast worker
    - Create workers/capacity_worker.py
    - Implement calculate_all_forecasts task handler
    - Schedule daily execution at 05:00 UTC
    - Process resources: disk, memory
    - _Requirements: 15.1, 15.2_

  - [ ] 13.7 Implement SLATrackingService
    - Create services/sla_tracking_service.py
    - Implement calculate_sla_measurement for metric types (uptime_percentage, avg_response_time, max_downtime, alert_resolution_time)
    - Implement check_sla_breach with CRITICAL alert generation
    - Implement get_sla_dashboard with visual indicators
    - Implement generate_sla_report with trend analysis
    - Implement exclude_maintenance_windows from calculations
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 16.9_

  - [ ]* 13.8 Write property test for SLA maintenance window exclusion
    - **Property 19: SLA Maintenance Window Exclusion**
    - **Validates: Requirements 16.9**

  - [ ] 13.9 Create SLA calculator worker
    - Create workers/sla_worker.py
    - Implement calculate_sla_measurements task handler
    - Schedule monthly execution on 1st day at 00:00 UTC
    - Schedule quarterly execution on 1st day of quarter at 00:00 UTC
    - _Requirements: 16.7_

  - [ ] 13.10 Create performance analytics API endpoints
    - Create routes/baselines.py
    - Implement GET /api/v1/baselines (list baselines)
    - Implement GET /api/v1/baselines/{id} (baseline details)
    - Implement POST /api/v1/baselines/{id}/reset (reset baseline)
    - Implement GET /api/v1/baselines/{id}/anomalies (list anomalies)
    - Create routes/capacity.py
    - Implement GET /api/v1/capacity-forecasts (list forecasts)
    - Implement GET /api/v1/capacity-forecasts/{id} (forecast details)
    - Implement POST /api/v1/capacity-forecasts/{id}/recalculate (recalculate)
    - Implement GET /api/v1/capacity-dashboard (capacity dashboard)
    - Create routes/sla.py
    - Implement GET /api/v1/sla-metrics (list SLA metrics)
    - Implement POST /api/v1/sla-metrics (create SLA metric)
    - Implement GET /api/v1/sla-metrics/{id} (metric details)
    - Implement PUT /api/v1/sla-metrics/{id} (update metric)
    - Implement DELETE /api/v1/sla-metrics/{id} (delete metric)
    - Implement GET /api/v1/sla-metrics/{id}/measurements (list measurements)
    - Implement GET /api/v1/sla-metrics/{id}/breaches (list breaches)
    - Implement GET /api/v1/sla-dashboard (SLA dashboard)
    - _Requirements: 14.4, 14.7, 15.4, 15.8, 16.4, 16.5_

  - [ ] 13.11 Add performance analytics UI pages
    - Create templates/baselines.html (baseline list page)
    - Create templates/baseline_detail.html (baseline detail with anomaly history)
    - Create templates/capacity_dashboard.html (capacity planning dashboard)
    - Create templates/capacity_forecast_detail.html (forecast detail with trend graph)
    - Create templates/sla_dashboard.html (SLA dashboard with visual indicators)
    - Create templates/sla_metric_detail.html (SLA metric configuration and measurements)
    - _Requirements: 14.4, 14.7, 15.4, 15.8, 16.4_

- [ ] 14. Checkpoint - Performance Analytics Complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 15. Phase 8: Custom Dashboards and RBAC (Weeks 17-18)
  - [ ] 15.1 Implement DashboardService
    - Create services/dashboard_service.py
    - Implement create_dashboard with layout configuration
    - Implement update_dashboard_layout
    - Implement add_widget with widget types (device_status, alert_list, performance_chart, capacity_gauge, sla_status)
    - Implement get_widget_data with filtering
    - Implement share_dashboard with users or roles
    - Implement get_dashboard_templates for common roles
    - _Requirements: 18.1, 18.2, 18.3, 18.5, 18.6_

  - [ ] 15.2 Create dashboard API endpoints
    - Create routes/dashboards.py
    - Implement GET /api/v1/dashboards (list dashboards)
    - Implement POST /api/v1/dashboards (create dashboard)
    - Implement GET /api/v1/dashboards/{id} (dashboard details)
    - Implement PUT /api/v1/dashboards/{id} (update dashboard)
    - Implement DELETE /api/v1/dashboards/{id} (delete dashboard)
    - Implement POST /api/v1/dashboards/{id}/share (share dashboard)
    - Implement GET /api/v1/dashboards/{id}/data (get dashboard data)
    - Implement POST /api/v1/dashboards/{id}/widgets (add widget)
    - Implement PUT /api/v1/dashboards/widgets/{id} (update widget)
    - Implement DELETE /api/v1/dashboards/widgets/{id} (delete widget)
    - Implement GET /api/v1/dashboard-templates (list templates)
    - _Requirements: 18.1, 18.5, 18.6, 18.8_

  - [ ] 15.3 Add dashboard UI pages
    - Create templates/dashboards.html (dashboard list page)
    - Create templates/dashboard_view.html (dashboard view with widgets)
    - Create templates/dashboard_edit.html (dashboard editor with drag-and-drop)
    - Add widget library with preview
    - Add dashboard sharing interface
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.7_

  - [ ] 15.4 Create department API endpoints
    - Create routes/departments.py
    - Implement GET /api/v1/departments (list departments)
    - Implement POST /api/v1/departments (create department)
    - Implement GET /api/v1/departments/{id} (department details)
    - Implement PUT /api/v1/departments/{id} (update department)
    - Implement DELETE /api/v1/departments/{id} (delete department)
    - Implement GET /api/v1/departments/{id}/devices (devices in department)
    - Implement GET /api/v1/departments/{id}/users (users in department)
    - _Requirements: 19.1, 19.2, 19.3, 19.7_

  - [ ] 15.5 Add department UI pages
    - Create templates/departments.html (department list page)
    - Create templates/department_detail.html (department detail with hierarchy)
    - Add department assignment interface for devices
    - Add user-to-department assignment interface
    - Display department affiliation on device pages
    - _Requirements: 19.2, 19.3, 19.7_

  - [ ] 15.6 Implement department isolation middleware
    - Create middleware/department_isolation.py
    - Apply department filtering to all device queries
    - Enforce isolation on API endpoints
    - Add hierarchical access support
    - Log unauthorized access attempts
    - _Requirements: 19.4, 19.5, 19.6, 19.9_

  - [ ]* 15.7 Write unit tests for dashboard widgets
    - Test widget data fetching for each widget type
    - Test filtering by site, department, device type
    - Test dashboard sharing permissions
    - _Requirements: 18.2, 18.3_

- [ ] 16. Checkpoint - Custom Dashboards and RBAC Complete
  - Ensure all tests pass, ask the user if questions arise.


- [ ] 17. Phase 9: Bulk Operations and API (Weeks 19-20)
  - [ ] 17.1 Implement BulkOperationsService
    - Create services/bulk_operations_service.py
    - Implement initiate_bulk_operation with validation
    - Implement execute_bulk_operation with async processing
    - Implement execute_single_device_operation for each operation type
    - Implement get_operation_status with progress tracking
    - Implement import_devices_csv with validation and preview
    - Implement export_devices_csv
    - _Requirements: 20.1, 20.2, 20.3, 20.4, 20.5, 20.7, 20.8_

  - [ ]* 17.2 Write property test for bulk operation progress
    - **Property 21: Bulk Operation Progress Tracking**
    - **Validates: Requirements 20.4**

  - [ ] 17.3 Create bulk operations API endpoints
    - Create routes/bulk_operations.py
    - Implement POST /api/v1/bulk-operations (create bulk operation)
    - Implement GET /api/v1/bulk-operations/{id} (operation status)
    - Implement GET /api/v1/bulk-operations/{id}/results (operation results)
    - Implement DELETE /api/v1/bulk-operations/{id} (cancel operation)
    - _Requirements: 20.4, 20.5_

  - [ ] 17.4 Add bulk operations UI
    - Add bulk operation interface to device list page
    - Add device selection checkboxes and "Select All Filtered"
    - Add bulk operation confirmation dialog
    - Add bulk operation progress modal with real-time updates
    - Add bulk operation results page
    - _Requirements: 20.2, 20.3, 20.4, 20.5_

  - [ ] 17.5 Implement API token management
    - Create routes/api_tokens.py
    - Implement GET /api/v1/api-tokens (list user's tokens)
    - Implement POST /api/v1/api-tokens (create token)
    - Implement GET /api/v1/api-tokens/{id} (token details)
    - Implement PUT /api/v1/api-tokens/{id} (update token)
    - Implement DELETE /api/v1/api-tokens/{id} (revoke token)
    - Implement GET /api/v1/api-tokens/{id}/usage (usage stats)
    - _Requirements: 21.4, 25.2_

  - [ ] 17.6 Implement API rate limiting
    - Create middleware/rate_limiting.py
    - Implement sliding window rate limiting using RateLimitBucket
    - Default limit: 1000 requests/hour per token
    - Return rate limit headers (X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset)
    - Return 429 status when limit exceeded
    - _Requirements: 21.10_

  - [ ]* 17.7 Write property test for API rate limiting
    - **Property 22: API Rate Limiting**
    - **Validates: Requirements 21.10**

  - [ ] 17.8 Extend device API endpoints
    - Modify routes/devices.py
    - Add POST /api/v1/devices/bulk endpoint
    - Add POST /api/v1/devices/import endpoint
    - Add GET /api/v1/devices/export endpoint
    - Add comprehensive filtering (site_id, department_id, device_type, status)
    - Add pagination (page, per_page with max 500)
    - Add sorting (sort, order)
    - _Requirements: 21.1, 21.2, 21.6, 21.7_

  - [ ] 17.9 Extend user API endpoints
    - Create routes/users.py
    - Implement GET /api/v1/users (list users)
    - Implement POST /api/v1/users (create user)
    - Implement GET /api/v1/users/{id} (user details)
    - Implement PUT /api/v1/users/{id} (update user)
    - Implement DELETE /api/v1/users/{id} (delete user)
    - Implement POST /api/v1/users/{id}/departments (assign to department)
    - Implement DELETE /api/v1/users/{id}/departments/{dept_id} (remove from department)
    - _Requirements: 19.3, 21.1_

  - [ ] 17.10 Create OpenAPI/Swagger documentation
    - Install flask-swagger-ui or similar
    - Document all API endpoints with request/response schemas
    - Add authentication documentation
    - Add rate limiting documentation
    - Add error code documentation
    - Host at /api/docs
    - _Requirements: 21.9_

  - [ ] 17.11 Add API token UI pages
    - Create templates/api_tokens.html (token list page)
    - Create templates/api_token_create.html (token creation with scope selection)
    - Add token usage statistics page
    - Display token prefix for identification (hide full token after creation)
    - _Requirements: 21.4_

  - [ ]* 17.12 Write performance tests for bulk operations
    - Test bulk operations with 100, 500, 1000 devices
    - Measure execution time and memory usage
    - Verify progress tracking accuracy
    - _Requirements: 20.4_

- [ ] 18. Checkpoint - Bulk Operations and API Complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 19. Phase 10: Mobile UI and Polish (Weeks 21-22)
  - [ ] 19.1 Implement mobile-responsive CSS
    - Update static/css/main.css with responsive breakpoints
    - Add media queries for 320px, 768px, 1024px, 2560px
    - Implement collapsible navigation menu for mobile
    - Convert device list to card layout on mobile (< 768px)
    - Optimize dashboard charts for mobile viewing
    - _Requirements: 22.1, 22.2, 22.3, 22.5_

  - [ ] 19.2 Add touch-friendly controls
    - Increase touch target size to minimum 44px
    - Add swipe gestures for alert acknowledge/dismiss
    - Add touch-friendly date pickers
    - Add pull-to-refresh on mobile
    - _Requirements: 22.4, 22.8_

  - [ ] 19.3 Optimize mobile performance
    - Implement lazy loading for images
    - Defer non-critical JavaScript
    - Minimize CSS and JavaScript bundles
    - Add service worker for offline support
    - _Requirements: 22.7_

  - [ ] 19.4 Test mobile responsiveness
    - Test on iOS Safari (iPhone)
    - Test on Android Chrome
    - Test on iPad
    - Test orientation changes
    - _Requirements: 22.6_

  - [ ] 19.5 Add configuration import/export UI
    - Create templates/configuration_import.html (import interface with validation)
    - Create templates/configuration_export.html (export interface with section selection)
    - Add configuration preview before import
    - Add validation error display
    - _Requirements: 23.7, 23.8, 23.9, 23.10_

  - [ ] 19.6 Extend configuration API endpoints
    - Modify routes/configuration.py
    - Implement GET /api/v1/configuration/export (export configuration)
    - Implement POST /api/v1/configuration/import (import configuration)
    - Implement POST /api/v1/configuration/validate (validate configuration)
    - Implement GET /api/v1/configuration/snapshots (list snapshots)
    - Implement POST /api/v1/configuration/snapshots (create snapshot)
    - Implement GET /api/v1/configuration/snapshots/{id} (get snapshot)
    - Implement DELETE /api/v1/configuration/snapshots/{id} (delete snapshot)
    - _Requirements: 23.7, 23.8_

  - [ ]* 19.7 Write property test for configuration round-trip
    - **Property 6: Configuration Round-Trip**
    - **Validates: Requirements 23.6**

- [ ] 20. Checkpoint - Mobile UI and Polish Complete
  - Ensure all tests pass, ask the user if questions arise.


- [ ] 21. Phase 11: Security Hardening (Week 23)
  - [ ] 21.1 Implement CSRF protection
    - Install Flask-WTF or similar CSRF library
    - Add CSRF tokens to all forms
    - Validate CSRF tokens on all state-changing endpoints
    - Add CSRF token to API documentation
    - _Requirements: 25.6_

  - [ ] 21.2 Implement input sanitization
    - Add input sanitization to all user input fields
    - Sanitize HTML to prevent XSS
    - Use parameterized queries to prevent SQL injection
    - Validate and sanitize file uploads
    - _Requirements: 25.7_

  - [ ] 21.3 Implement HTTPS enforcement
    - Add HTTPS redirect middleware
    - Set Secure flag on cookies
    - Set HSTS headers
    - Enforce HTTPS for polling node communications
    - _Requirements: 25.4_

  - [ ] 21.4 Implement password complexity validation
    - Add password complexity requirements (min 12 chars, uppercase, lowercase, digit, special char)
    - Add password strength meter to UI
    - Enforce password complexity on user creation and password change
    - Track password_last_changed timestamp
    - _Requirements: 25.9_

  - [ ] 21.5 Implement session timeout
    - Add configurable session timeout (default 30 minutes)
    - Track last activity timestamp
    - Invalidate sessions after timeout
    - Add session timeout warning modal
    - _Requirements: 25.10_

  - [ ] 21.6 Implement security audit logging
    - Log all authentication failures
    - Log all authorization failures
    - Log all security-relevant events (password changes, permission changes, etc.)
    - Create security_audit_log table
    - _Requirements: 25.8_

  - [ ] 21.7 Add TLS certificate validation for webhooks
    - Implement TLS certificate validation in webhook worker
    - Add option to disable validation for self-signed certificates
    - Log certificate validation failures
    - _Requirements: 25.5_

  - [ ]* 21.8 Write security tests
    - Test CSRF protection on all state-changing endpoints
    - Test input sanitization for XSS prevention
    - Test SQL injection prevention
    - Test session timeout enforcement
    - Test password complexity validation
    - _Requirements: 25.6, 25.7, 25.9, 25.10_

  - [ ] 21.9 Perform security audit
    - Review all authentication and authorization code
    - Review all input validation code
    - Review all encryption implementations
    - Check for common vulnerabilities (OWASP Top 10)
    - _Requirements: 25.1-25.10_

- [ ] 22. Checkpoint - Security Hardening Complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 23. Phase 12: Documentation and Deployment (Week 24)
  - [ ] 23.1 Create API documentation
    - Complete OpenAPI/Swagger specification
    - Add authentication examples
    - Add request/response examples for all endpoints
    - Add error code reference
    - Add rate limiting documentation
    - _Requirements: 21.9_

  - [ ] 23.2 Create administrator guide
    - Document installation and configuration
    - Document site and department setup
    - Document polling node deployment
    - Document alert escalation policy configuration
    - Document compliance report setup
    - Document SLA metric configuration
    - Document backup and restore procedures
    - _Requirements: 9.1, 10.1, 12.1, 13.1, 16.1_

  - [ ] 23.3 Create agent deployment guide
    - Document Server_Agent installation (Windows and Linux)
    - Document Tactical_Agent installation
    - Document agent configuration options
    - Document agent troubleshooting
    - Document agent version compatibility
    - _Requirements: 4.1, 4.6, 24.1, 24.2, 24.7_

  - [ ] 23.4 Create troubleshooting guide
    - Document common issues and solutions
    - Document log file locations
    - Document diagnostic commands
    - Document performance tuning
    - Document database maintenance
    - _Requirements: 11.7, 11.8_

  - [ ] 23.5 Create deployment scripts
    - Create database migration script
    - Create systemd service files for workers
    - Create nginx/Apache configuration examples
    - Create Docker Compose configuration
    - Create Kubernetes manifests
    - _Requirements: 11.1, 11.2, 11.8_

  - [ ] 23.6 Create backup and disaster recovery procedures
    - Document database backup procedures
    - Document camera frame backup procedures
    - Document configuration export/import for DR
    - Document failover procedures
    - _Requirements: 11.5, 11.7_

  - [ ] 23.7 Create monitoring and alerting setup
    - Document health check endpoints
    - Document metrics for external monitoring
    - Document recommended alerts for system health
    - _Requirements: 11.4_

  - [ ] 23.8 Create performance tuning guide
    - Document database indexing recommendations
    - Document worker scaling recommendations
    - Document caching configuration
    - Document PostgreSQL tuning parameters
    - _Requirements: 5.1-5.7_

  - [ ] 23.9 Perform final testing
    - Test all 25 requirements end-to-end
    - Test backward compatibility with agent versions 1.0+
    - Test high availability failover
    - Test distributed polling with multiple nodes
    - Test bulk operations with 1000+ devices
    - _Requirements: 24.1-24.8_

  - [ ] 23.10 Create release notes
    - Document all new features
    - Document breaking changes (if any)
    - Document upgrade procedures
    - Document known issues
    - _Requirements: 1.1-25.10_

- [ ] 24. Final Checkpoint - Documentation and Deployment Complete
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation at the end of each phase
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The implementation follows a 24-week roadmap with 12 phases
- Backward compatibility with existing agents (v1.0+) is maintained throughout
- Security is prioritized with encryption, CSRF protection, and input sanitization
- All new features include comprehensive API endpoints and UI pages
- Performance is optimized for 10,000+ devices with proper indexing and caching
