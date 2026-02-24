# Implementation Plan: Monitoring Phase 1 MVP

## Overview

This implementation plan extends the existing monitoring system to support multi-site enterprise deployments with agent-free printer monitoring, basic RBAC, REST API foundation, and distributed polling. The plan is structured in 6 phases over 8-12 weeks for a solo developer, prioritizing backward compatibility and incremental deployment.

## Timeline

- Phase 1: Foundation (Week 1-2)
- Phase 2: Printer Monitoring (Week 3-4)
- Phase 3: REST API (Week 5-6)
- Phase 4: RBAC & Filtering (Week 7-8)
- Phase 5: Distributed Polling (Week 9-10)
- Phase 6: UI & Polish (Week 11-12)

## Tasks

### Phase 1: Foundation (Week 1-2)

- [ ] 1. Create database migration for new tables and columns
  - [ ] 1.1 Create Sites table with schema from design
    - Add columns: id, name, address, timezone, contact_info, created_at
    - Add unique constraint on name
    - _Requirements: 1.1, 1.2, 23.1_
  
  - [ ] 1.2 Create Departments table with schema from design
    - Add columns: id, name, description, created_at
    - Add unique constraint on name
    - _Requirements: 8.1, 8.2, 23.2_
  
  - [ ] 1.3 Create PrintJobs table with schema from design
    - Add columns: id, user, document_name, printer_id, timestamp, page_count, site_id, department_id, source, raw_event, created_at
    - Add foreign keys and indexes as specified in design
    - _Requirements: 4.3, 5.3, 23.5_
  
  - [ ] 1.4 Create PollingNodes table with schema from design
    - Add columns: id, name, hostname, site_id, last_heartbeat, status, auth_token, created_at
    - Add indexes as specified in design
    - _Requirements: 18.1, 18.2, 23.6_
  
  - [ ] 1.5 Create APITokens table with schema from design
    - Add columns: id, user_id, token_hash, name, created_at, last_used_at, revoked_at
    - Add indexes as specified in design
    - _Requirements: 16.1, 16.7, 23.7_
  
  - [ ] 1.6 Create RateLimits table with schema from design
    - Add columns: id, api_token_id, window_start, request_count
    - Add indexes as specified in design
    - _Requirements: 17.1, 17.4_
  
  - [ ] 1.7 Extend Device table with nullable columns
    - Add site_id, department_id, polling_node_id as nullable foreign keys
    - Add indexes as specified in design
    - _Requirements: 1.3, 8.3, 19.1, 23.3, 23.4, 23.8_
  
  - [ ] 1.8 Extend User table with nullable columns
    - Add department_id, view_own_department, view_all_departments columns
    - Set view_all_departments default to TRUE for backward compatibility
    - Add indexes as specified in design
    - _Requirements: 8.4, 10.1, 10.2, 23.7_

- [ ] 2. Create data models for new entities
  - [ ] 2.1 Implement Site model (models/site.py)
    - Create Site class with SQLAlchemy mappings
    - Implement to_dict() method
    - Add relationships to Device and PollingNode
    - _Requirements: 1.1, 1.2_
  
  - [ ] 2.2 Implement Department model (models/department.py)
    - Create Department class with SQLAlchemy mappings
    - Implement to_dict() method
    - Add relationships to Device and User
    - _Requirements: 8.1, 8.2_
  
  - [ ] 2.3 Implement PrintJob model (models/print_job.py)
    - Create PrintJob class with SQLAlchemy mappings
    - Implement to_dict() method
    - Add relationship to Device (printer)
    - _Requirements: 4.3, 5.3_
  
  - [ ] 2.4 Implement PollingNode model (models/polling_node.py)
    - Create PollingNode class with SQLAlchemy mappings
    - Implement to_dict() method
    - Add relationships to Site and Device
    - _Requirements: 18.1, 18.2, 18.3_
  
  - [ ] 2.5 Implement APIToken model (models/api_token.py)
    - Create APIToken class with SQLAlchemy mappings
    - Implement is_valid() and to_dict() methods
    - Add relationship to User
    - _Requirements: 16.1, 16.7_
  
  - [ ] 2.6 Implement RateLimit model (models/rate_limit.py)
    - Create RateLimit class with SQLAlchemy mappings
    - Add relationship to APIToken
    - _Requirements: 17.1_

- [ ]* 2.7 Write unit tests for all data models
  - Test model creation, relationships, and to_dict() methods
  - Test foreign key constraints
  - _Requirements: 1.1, 8.1, 18.1_

- [x] 3. Implement core service classes
  - [x] 3.1 Implement SitesService (services/sites_service.py)
    - Implement create_site(), get_site(), list_sites(), update_site()
    - Implement delete_site() with device check
    - Implement get_site_devices() and get_site_stats()
    - _Requirements: 1.1, 1.2, 1.5, 1.6, 1.7, 2.2_
  
  - [ ]* 3.2 Write property test for Site CRUD round trip
    - **Property 1: Site CRUD Round Trip**
    - **Validates: Requirements 1.1, 1.2**
  
  - [ ]* 3.3 Write property test for site deletion protection
    - **Property 3: Site Deletion Protection**
    - **Validates: Requirements 1.6**
  
  - [ ]* 3.4 Write property test for empty site deletion
    - **Property 4: Empty Site Deletion**
    - **Validates: Requirements 1.7**
  
  - [x] 3.5 Implement DepartmentsService (services/departments_service.py)
    - Implement create_department(), get_department(), list_departments(), update_department()
    - Implement delete_department() with device/user check
    - Implement get_department_devices()
    - _Requirements: 8.1, 8.2, 8.5, 8.6_
  
  - [ ]* 3.6 Write property test for Department CRUD round trip
    - **Property 21: Department CRUD Round Trip**
    - **Validates: Requirements 8.1, 8.2**
  
  - [ ]* 3.7 Write property test for department deletion protection
    - **Property 24: Department Deletion Protection**
    - **Validates: Requirements 8.5**
  
  - [ ]* 3.8 Write property test for empty department deletion
    - **Property 25: Empty Department Deletion**
    - **Validates: Requirements 8.6**
  
  - [x] 3.9 Implement PrintJobsService (services/print_jobs_service.py)
    - Implement create_print_job() with printer lookup
    - Implement list_print_jobs() with filtering and pagination
    - Implement get_total_pages() for aggregation
    - Implement export_to_csv()
    - Implement cleanup_old_jobs() for 90-day retention
    - _Requirements: 4.3, 4.4, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_
  
  - [ ]* 3.10 Write property test for print job filtering correctness
    - **Property 16: Print Job Filtering Correctness**
    - **Validates: Requirements 7.2, 12.4, 12.5**
  
  - [ ]* 3.11 Write property test for print job page count aggregation
    - **Property 18: Print Job Page Count Aggregation**
    - **Validates: Requirements 7.4, 14.4**

- [ ] 4. Checkpoint - Ensure all tests pass
  - Run database migrations
  - Run model unit tests
  - Run service property tests
  - Ensure all tests pass, ask the user if questions arise.


### Phase 2: Printer Monitoring (Week 3-4)

- [ ] 5. Implement Windows Event Forwarding (WEF) collector
  - [ ] 5.1 Create WEFCollector class (services/wef_collector.py)
    - Implement receive_event() HTTP endpoint handler
    - Implement parse_print_event() for Event ID 307
    - Extract user, document_name, printer_name, page_count, timestamp from XML
    - Implement process_print_event() to create PrintJob records
    - Handle printer lookup and orphaned jobs (printer_id=null)
    - _Requirements: 4.1, 4.2, 4.3, 4.4_
  
  - [ ]* 5.2 Write property test for WEF event parsing completeness
    - **Property 9: WEF Event Parsing Completeness**
    - **Validates: Requirements 4.2**
  
  - [ ]* 5.3 Write property test for print event error resilience
    - **Property 11: Print Event Error Resilience**
    - **Validates: Requirements 4.5, 5.5**
  
  - [ ]* 5.4 Write unit tests for WEF collector
    - Test valid Event ID 307 parsing
    - Test invalid XML handling
    - Test missing field handling
    - Test printer not found scenario
    - _Requirements: 4.2, 4.5_

- [ ] 6. Implement CUPS syslog receiver
  - [ ] 6.1 Create SyslogReceiver class (services/syslog_receiver.py)
    - Implement start_listener() for UDP port 514
    - Implement parse_cups_message() for CUPS syslog format
    - Extract user, document_name, printer_name, page_count, timestamp
    - Implement process_cups_message() to create PrintJob records
    - Handle printer lookup and orphaned jobs
    - _Requirements: 5.1, 5.2, 5.3, 5.4_
  
  - [ ]* 6.2 Write property test for CUPS syslog parsing completeness
    - **Property 12: CUPS Syslog Parsing Completeness**
    - **Validates: Requirements 5.2**
  
  - [ ]* 6.3 Write unit tests for syslog receiver
    - Test valid CUPS message parsing
    - Test invalid syslog format handling
    - Test missing field handling
    - Test printer not found scenario
    - _Requirements: 5.2, 5.5_

- [ ] 7. Extend SNMP worker for printer polling
  - [ ] 7.1 Extend SNMPWorker class (workers/snmp_worker.py)
    - Add _execute_printer_snmp() method
    - Implement Printer MIB OID queries (RFC 3805)
    - Query toner levels, page counts, printer status, queue length
    - Store metrics in database
    - Handle SNMP errors with retry logic
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_
  
  - [ ]* 7.2 Write property test for printer SNMP data collection
    - **Property 13: Printer SNMP Data Collection**
    - **Validates: Requirements 6.2, 6.3, 6.4, 6.5**
  
  - [ ]* 7.3 Write property test for SNMP poll error handling
    - **Property 14: SNMP Poll Error Handling**
    - **Validates: Requirements 6.6**
  
  - [ ] 7.4 Extend Scheduler class (services/scheduler.py)
    - Add enqueue_printer_snmp_tasks() method
    - Query devices with device_type='printer'
    - Enqueue 'printer_snmp' tasks to poll_tasks table
    - Respect per-printer poll intervals (1-60 minutes)
    - _Requirements: 6.1, 6.7_
  
  - [ ]* 7.5 Write property test for printer poll interval enforcement
    - **Property 15: Printer Poll Interval Enforcement**
    - **Validates: Requirements 6.7**

  
  - [ ]* 7.6 Write integration tests for printer SNMP polling
    - Test enqueue_printer_snmp_tasks()
    - Test _execute_printer_snmp() with mock SNMP responses
    - Test metric storage
    - _Requirements: 6.1, 6.2, 6.3_

- [ ] 8. Checkpoint - Ensure all tests pass
  - Test WEF collector with sample events
  - Test syslog receiver with sample messages
  - Test SNMP printer polling
  - Ensure all tests pass, ask the user if questions arise.

### Phase 3: REST API (Week 5-6)

- [ ] 9. Implement API token authentication
  - [ ] 9.1 Create API token generation endpoint (routes/api_tokens.py)
    - Implement POST /api/tokens to generate new token
    - Generate random 32-byte token, hash with SHA-256
    - Store token_hash in database
    - Return plaintext token once
    - _Requirements: 16.1, 16.7_
  
  - [ ] 9.2 Implement API token listing and revocation
    - Implement GET /api/tokens to list user's tokens
    - Implement DELETE /api/tokens/{id} to revoke token
    - _Requirements: 16.6_
  
  - [ ] 9.3 Create API authentication middleware (middleware/api_auth.py)
    - Parse Authorization header (Bearer scheme)
    - Validate token hash against database
    - Load user and permissions
    - Return 401 for invalid/missing tokens
    - _Requirements: 16.2, 16.3, 16.4, 16.5_
  
  - [ ]* 9.4 Write property test for API token authentication flow
    - **Property 36: API Token Authentication Flow**
    - **Validates: Requirements 16.3**
  
  - [ ]* 9.5 Write property test for API token rejection
    - **Property 37: API Token Rejection**
    - **Validates: Requirements 16.4, 16.5**
  
  - [ ]* 9.6 Write property test for API token storage security
    - **Property 39: API Token Storage Security**
    - **Validates: Requirements 16.7**

- [ ] 10. Implement rate limiting middleware
  - [ ] 10.1 Create rate limiting middleware (middleware/rate_limit.py)
    - Track request count per API token per hour
    - Return 429 when limit (1000) exceeded
    - Add X-RateLimit-* headers to all responses
    - Reset counters every hour
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5_
  
  - [ ]* 10.2 Write property test for rate limit enforcement
    - **Property 40: Rate Limit Enforcement**
    - **Validates: Requirements 17.1, 17.2**
  
  - [ ]* 10.3 Write property test for rate limit headers
    - **Property 41: Rate Limit Headers**
    - **Validates: Requirements 17.3**
  
  - [ ]* 10.4 Write property test for rate limit independence
    - **Property 43: Rate Limit Independence**
    - **Validates: Requirements 17.5**


- [x] 11. Implement Sites REST API
  - [ ] 11.1 Create Sites API endpoints (routes/api_sites.py)
    - Implement GET /api/sites (list all sites)
    - Implement GET /api/sites/{id} (get site details)
    - Implement POST /api/sites (create site)
    - Implement PUT /api/sites/{id} (update site)
    - Implement DELETE /api/sites/{id} (delete site, return 409 if devices exist)
    - Apply API token authentication to all endpoints
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7_
  
  - [ ]* 11.2 Write property test for API site deletion conflict response
    - **Property 30: API Site Deletion Conflict Response**
    - **Validates: Requirements 11.6**
  
  - [ ]* 11.3 Write unit tests for Sites API endpoints
    - Test GET /api/sites returns list
    - Test POST /api/sites creates site
    - Test DELETE with devices returns 409
    - Test authentication requirement
    - _Requirements: 11.1, 11.3, 11.6, 11.7_

- [ ] 12. Implement Departments REST API
  - [ ] 12.1 Create Departments API endpoints (routes/api_departments.py)
    - Implement GET /api/departments (list all departments)
    - Implement GET /api/departments/{id} (get department details)
    - Implement POST /api/departments (create department)
    - Implement PUT /api/departments/{id} (update department)
    - Implement DELETE /api/departments/{id} (delete, return 409 if devices/users exist)
    - Apply API token authentication to all endpoints
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7_
  
  - [ ]* 12.2 Write property test for API department deletion conflict response
    - **Property 35: API Department Deletion Conflict Response**
    - **Validates: Requirements 15.6**
  
  - [ ]* 12.3 Write unit tests for Departments API endpoints
    - Test GET /api/departments returns list
    - Test POST /api/departments creates department
    - Test DELETE with devices/users returns 409
    - Test authentication requirement
    - _Requirements: 15.1, 15.3, 15.6, 15.7_

- [ ] 13. Extend Devices REST API
  - [ ] 13.1 Extend Devices API endpoints (routes/api_devices.py)
    - Add site_id and department_id query parameters to GET /api/devices
    - Add site_id and department_id fields to response
    - Implement filtering logic for both parameters
    - Maintain backward compatibility (existing requests unchanged)
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_
  
  - [ ]* 13.2 Write property test for device API response fields
    - **Property 32: Device API Response Fields**
    - **Validates: Requirements 12.6**
  
  - [ ]* 13.3 Write property test for API backward compatibility
    - **Property 58: API Backward Compatibility**
    - **Validates: Requirements 22.4, 24.6**


- [ ] 14. Implement Printers REST API
  - [ ] 14.1 Create Printers API endpoints (routes/api_printers.py)
    - Implement GET /api/printers (list all printers)
    - Implement GET /api/printers/{id} (get printer details)
    - Implement GET /api/printers/{id}/metrics (get current metrics)
    - Implement GET /api/printers/{id}/jobs (get print jobs with date filtering)
    - Apply API token authentication to all endpoints
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_
  
  - [ ]* 14.2 Write unit tests for Printers API endpoints
    - Test GET /api/printers returns list
    - Test GET /api/printers/{id}/metrics returns toner levels and status
    - Test GET /api/printers/{id}/jobs with date filters
    - Test authentication requirement
    - _Requirements: 13.1, 13.3, 13.4, 13.6_

- [ ] 15. Implement Print Jobs REST API
  - [ ] 15.1 Create Print Jobs API endpoints (routes/api_print_jobs.py)
    - Implement GET /api/print-jobs with filtering parameters
    - Support start_date, end_date, user, printer_id, site_id, department_id filters
    - Implement pagination with page and page_size parameters
    - Return total page count in response
    - Apply API token authentication
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5_
  
  - [ ]* 15.2 Write property test for print jobs API pagination
    - **Property 34: Print Jobs API Pagination**
    - **Validates: Requirements 14.3**
  
  - [ ]* 15.3 Write unit tests for Print Jobs API
    - Test GET /api/print-jobs with various filters
    - Test pagination correctness
    - Test total page count calculation
    - Test authentication requirement
    - _Requirements: 14.1, 14.2, 14.3, 14.5_

- [ ] 16. Checkpoint - Ensure all tests pass
  - Test all API endpoints with authentication
  - Test rate limiting behavior
  - Test filtering and pagination
  - Ensure all tests pass, ask the user if questions arise.

### Phase 4: RBAC & Filtering (Week 7-8)

- [ ] 17. Implement RBAC permission checks
  - [ ] 17.1 Create RBAC helper functions (utils/rbac.py)
    - Implement get_user_department_filter() to return department_id or None
    - Implement check_department_access() to validate access
    - Handle view_own_department and view_all_departments flags
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_
  
  - [ ]* 17.2 Write property test for RBAC department auto-filtering
    - **Property 27: RBAC Department Auto-Filtering**
    - **Validates: Requirements 9.5, 10.1, 10.3**
  
  - [ ]* 17.3 Write property test for RBAC all departments access
    - **Property 28: RBAC All Departments Access**
    - **Validates: Requirements 10.2, 10.4**
  
  - [ ]* 17.4 Write property test for RBAC cross-department access denial
    - **Property 29: RBAC Cross-Department Access Denial**
    - **Validates: Requirements 10.5**


- [ ] 18. Apply department filtering to device queries
  - [ ] 18.1 Update device list queries to apply department filtering
    - Modify device list view to check user permissions
    - Apply department filter when view_own_department=True
    - Update device API endpoints to apply filtering
    - _Requirements: 9.1, 9.2, 9.5_
  
  - [ ]* 18.2 Write property test for department filtering correctness
    - **Property 26: Department Filtering Correctness**
    - **Validates: Requirements 9.4**
  
  - [ ] 18.3 Update alert queries to apply department filtering
    - Modify alert list view to check user permissions
    - Apply department filter when view_own_department=True
    - Update alert API endpoints to apply filtering
    - _Requirements: 9.2, 9.3, 9.5_

- [ ] 19. Apply site filtering to all views
  - [ ] 19.1 Add site filter to device list view
    - Add site dropdown to UI
    - Apply site filter to device queries
    - Persist filter selection in session
    - _Requirements: 3.1, 3.5_
  
  - [ ] 19.2 Add site filter to alerts view
    - Add site dropdown to UI
    - Apply site filter to alert queries
    - Persist filter selection in session
    - _Requirements: 3.2, 3.5_
  
  - [ ] 19.3 Add site filter to metrics view
    - Add site dropdown to UI
    - Apply site filter to metric queries
    - Persist filter selection in session
    - _Requirements: 3.3, 3.5_
  
  - [ ]* 19.4 Write property test for site filtering correctness
    - **Property 8: Site Filtering Correctness**
    - **Validates: Requirements 2.5, 3.4, 12.3**

- [ ] 20. Apply RBAC to API endpoints
  - [ ] 20.1 Update Printers API to apply department filtering
    - Check user permissions in all printer endpoints
    - Apply department filter when view_own_department=True
    - Return 403 for unauthorized access
    - _Requirements: 13.7_
  
  - [ ] 20.2 Update Print Jobs API to apply department filtering
    - Check user permissions in print jobs endpoint
    - Apply department filter when view_own_department=True
    - Return 403 for unauthorized access
    - _Requirements: 14.6_
  
  - [ ]* 20.3 Write property test for API department filtering application
    - **Property 33: API Department Filtering Application**
    - **Validates: Requirements 13.7, 14.6**
  
  - [ ]* 20.4 Write property test for API authentication requirement
    - **Property 31: API Authentication Requirement**
    - **Validates: Requirements 11.7, 13.6, 14.5, 15.7**

- [ ] 21. Checkpoint - Ensure all tests pass
  - Test RBAC filtering on all views
  - Test site filtering on all views
  - Test API RBAC enforcement
  - Ensure all tests pass, ask the user if questions arise.


### Phase 5: Distributed Polling (Week 9-10)

- [ ] 22. Implement polling node service
  - [ ] 22.1 Implement PollingNodeService (services/polling_node_service.py)
    - Implement register_node() with auth token generation
    - Implement deregister_node() with device check
    - Implement update_heartbeat() to update timestamp and status
    - Implement check_node_health() to mark nodes offline after 5 minutes
    - Implement assign_device() and unassign_device()
    - Implement auto_assign_devices() based on site
    - Implement receive_metrics() to process forwarded metrics
    - _Requirements: 18.1, 18.2, 18.5, 19.1, 19.2, 19.5, 19.6, 20.2, 20.3, 21.2, 21.3_
  
  - [ ]* 22.2 Write property test for polling node registration
    - **Property 44: Polling Node Registration**
    - **Validates: Requirements 18.1, 18.2**
  
  - [ ]* 22.3 Write property test for polling node deletion protection
    - **Property 46: Polling Node Deletion Protection**
    - **Validates: Requirements 18.5**
  
  - [ ]* 22.4 Write property test for device polling node assignment
    - **Property 47: Device Polling Node Assignment**
    - **Validates: Requirements 19.1**
  
  - [ ]* 22.5 Write property test for site-based auto-assignment
    - **Property 48: Site-Based Auto-Assignment**
    - **Validates: Requirements 19.2**
  
  - [ ]* 22.6 Write property test for polling node deregistration cleanup
    - **Property 50: Polling Node Deregistration Cleanup**
    - **Validates: Requirements 19.6**

- [ ] 23. Implement polling node heartbeat monitoring
  - [ ] 23.1 Create heartbeat endpoint (routes/api_polling_nodes.py)
    - Implement POST /api/polling-nodes/{id}/heartbeat
    - Authenticate polling node using auth_token
    - Call update_heartbeat() service method
    - _Requirements: 21.1, 21.2_
  
  - [ ] 23.2 Create scheduled task for node health checks
    - Add check_node_health() to scheduler
    - Run every 1 minute
    - Mark nodes offline if no heartbeat for 5 minutes
    - Generate alerts for offline nodes
    - _Requirements: 21.3, 21.4_
  
  - [ ]* 23.3 Write property test for heartbeat timestamp update
    - **Property 53: Heartbeat Timestamp Update**
    - **Validates: Requirements 21.2**
  
  - [ ]* 23.4 Write property test for polling node offline detection
    - **Property 54: Polling Node Offline Detection**
    - **Validates: Requirements 21.3**
  
  - [ ]* 23.5 Write property test for polling node recovery
    - **Property 56: Polling Node Recovery**
    - **Validates: Requirements 21.5**

- [ ] 24. Implement metric forwarding from nodes
  - [ ] 24.1 Create metric submission endpoint (routes/api_polling_nodes.py)
    - Implement POST /api/polling-nodes/{id}/metrics
    - Authenticate polling node using auth_token
    - Accept batch of metrics in request body
    - Call receive_metrics() service method
    - Store metrics in database
    - _Requirements: 20.1, 20.2, 20.3_
  
  - [ ]* 24.2 Write property test for metric submission authentication
    - **Property 51: Metric Submission Authentication**
    - **Validates: Requirements 20.2**
  
  - [ ]* 24.3 Write property test for metric storage completeness
    - **Property 52: Metric Storage Completeness**
    - **Validates: Requirements 20.3**

  
  - [ ]* 24.4 Write integration tests for metric forwarding
    - Test metric submission with authentication
    - Test batch metric processing
    - Test metric storage
    - _Requirements: 20.1, 20.2, 20.3_

- [ ] 25. Implement polling node management API
  - [ ] 25.1 Create polling node management endpoints (routes/api_polling_nodes.py)
    - Implement GET /api/polling-nodes (list all nodes)
    - Implement GET /api/polling-nodes/{id} (get node details)
    - Implement POST /api/polling-nodes (register node)
    - Implement DELETE /api/polling-nodes/{id} (deregister node)
    - Implement POST /api/polling-nodes/{id}/assign (assign device to node)
    - Implement POST /api/polling-nodes/auto-assign (auto-assign by site)
    - Apply API token authentication to all endpoints
    - _Requirements: 18.1, 18.4, 18.5, 19.1, 19.2, 19.3, 19.4_
  
  - [ ]* 25.2 Write unit tests for polling node API
    - Test node registration
    - Test node listing
    - Test device assignment
    - Test auto-assignment
    - Test authentication requirement
    - _Requirements: 18.1, 19.1, 19.2_

- [ ] 26. Checkpoint - Ensure all tests pass
  - Test polling node registration and heartbeat
  - Test device assignment and auto-assignment
  - Test metric forwarding
  - Test node health monitoring
  - Ensure all tests pass, ask the user if questions arise.

### Phase 6: UI & Polish (Week 11-12)

- [ ] 27. Create site management UI
  - [ ] 27.1 Create site list page (templates/sites/list.html)
    - Display all sites with device counts
    - Add create site button
    - Add edit and delete actions
    - _Requirements: 1.1, 1.2_
  
  - [ ] 27.2 Create site form page (templates/sites/form.html)
    - Form for creating/editing sites
    - Fields: name, address, timezone, contact_info
    - Validation for required fields
    - _Requirements: 1.1, 1.2_
  
  - [x] 27.3 Create site dashboard page (templates/sites/dashboard.html)
    - Display site statistics (device counts, online/offline/warning)
    - Display recent alerts for site
    - Display aggregate metrics for site
    - List all devices at site
    - _Requirements: 2.1, 2.2, 2.3, 2.4_
  
  - [ ]* 27.4 Write property test for site statistics accuracy
    - **Property 5: Site Statistics Accuracy**
    - **Validates: Requirements 2.2**
  
  - [ ]* 27.5 Write property test for site alert filtering
    - **Property 6: Site Alert Filtering**
    - **Validates: Requirements 2.3**

- [ ] 28. Create department management UI
  - [ ] 28.1 Create department list page (templates/departments/list.html)
    - Display all departments with device/user counts
    - Add create department button
    - Add edit and delete actions
    - _Requirements: 8.1, 8.2_
  
  - [ ] 28.2 Create department form page (templates/departments/form.html)
    - Form for creating/editing departments
    - Fields: name, description
    - Validation for required fields
    - _Requirements: 8.1, 8.2_


- [ ] 29. Create print audit trail UI
  - [x] 29.1 Create print jobs list page (templates/print_jobs/list.html)
    - Display print jobs with all fields (user, document, printer, timestamp, pages)
    - Add filters for date range, user, printer, site, department
    - Add sorting by timestamp, user, printer, page count
    - Display total page count for filtered results
    - Add CSV export button
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.6_
  
  - [ ]* 29.2 Write property test for print job sorting correctness
    - **Property 17: Print Job Sorting Correctness**
    - **Validates: Requirements 7.3**
  
  - [ ]* 29.3 Write property test for print job CSV export completeness
    - **Property 20: Print Job CSV Export Completeness**
    - **Validates: Requirements 7.6**

- [ ] 30. Create printer monitoring UI
  - [x] 30.1 Create printer list page (templates/printers/list.html)
    - Display all printers with current status
    - Show toner levels with color indicators
    - Show page counts and queue length
    - Add site and department filters
    - _Requirements: 6.2, 6.3, 6.4, 6.5_
  
  - [x] 30.2 Create printer detail page (templates/printers/detail.html)
    - Display printer details and current metrics
    - Display toner level charts
    - Display page count history
    - Display recent print jobs for printer
    - _Requirements: 6.2, 6.3, 6.4, 6.5, 13.3, 13.4_

- [ ] 31. Create polling node dashboard
  - [ ] 31.1 Create polling node list page (templates/polling_nodes/list.html)
    - Display all polling nodes with status
    - Show last heartbeat timestamp
    - Show device count per node
    - Add register node button
    - Add deregister action
    - _Requirements: 18.3, 18.4, 21.6_
  
  - [ ] 31.2 Create polling node detail page (templates/polling_nodes/detail.html)
    - Display node details and status
    - Display assigned devices
    - Display heartbeat history
    - Add device assignment interface
    - _Requirements: 18.3, 19.1, 19.3, 21.6_

- [ ] 32. Implement backward compatibility validation
  - [ ] 32.1 Test legacy agent metric submission
    - Submit metrics from server_agent.py without site_id/department_id
    - Verify metrics accepted and stored with null values
    - _Requirements: 22.1, 22.2, 22.3_
  
  - [ ]* 32.2 Write property test for legacy agent compatibility
    - **Property 57: Legacy Agent Compatibility**
    - **Validates: Requirements 22.1, 22.2, 22.3**
  
  - [ ] 32.3 Test existing API endpoint compatibility
    - Call existing endpoints with old request formats
    - Verify response formats unchanged
    - Verify new fields are optional
    - _Requirements: 22.4, 24.6_
  
  - [ ] 32.4 Test existing database query compatibility
    - Run existing queries with null site_id/department_id
    - Verify same results as before schema changes
    - _Requirements: 22.5, 24.7_
  
  - [ ]* 32.5 Write property test for database query compatibility
    - **Property 59: Database Query Compatibility**
    - **Validates: Requirements 22.5, 24.7**

  
  - [ ]* 32.6 Write property test for existing functionality preservation
    - **Property 60: Existing Functionality Preservation**
    - **Validates: Requirements 24.1, 24.2, 24.4, 24.5**

- [ ] 33. Implement remaining correctness properties
  - [ ]* 33.1 Write property test for device site assignment
    - **Property 2: Device Site Assignment**
    - **Validates: Requirements 1.3, 1.5**
  
  - [ ]* 33.2 Write property test for site metric aggregation
    - **Property 7: Site Metric Aggregation**
    - **Validates: Requirements 2.4**
  
  - [ ]* 33.3 Write property test for print job persistence
    - **Property 10: Print Job Persistence**
    - **Validates: Requirements 4.3, 4.4, 5.3, 5.4**
  
  - [ ]* 33.4 Write property test for print job retention policy
    - **Property 19: Print Job Retention Policy**
    - **Validates: Requirements 7.5**
  
  - [ ]* 33.5 Write property test for device department association
    - **Property 22: Device Department Association**
    - **Validates: Requirements 8.3**
  
  - [ ]* 33.6 Write property test for user department association
    - **Property 23: User Department Association**
    - **Validates: Requirements 8.4**
  
  - [ ]* 33.7 Write property test for API token revocation
    - **Property 38: API Token Revocation**
    - **Validates: Requirements 16.6**
  
  - [ ]* 33.8 Write property test for rate limit reset
    - **Property 42: Rate Limit Reset**
    - **Validates: Requirements 17.4**
  
  - [ ]* 33.9 Write property test for polling node status tracking
    - **Property 45: Polling Node Status Tracking**
    - **Validates: Requirements 18.3**
  
  - [ ]* 33.10 Write property test for device reassignment
    - **Property 49: Device Reassignment**
    - **Validates: Requirements 19.5**
  
  - [ ]* 33.11 Write property test for polling node offline alert
    - **Property 55: Polling Node Offline Alert**
    - **Validates: Requirements 21.4**

- [ ] 34. Create documentation
  - [ ] 34.1 Write API documentation
    - Document all API endpoints with request/response examples
    - Document authentication and rate limiting
    - Document RBAC permissions
    - _Requirements: All API requirements_
  
  - [ ] 34.2 Write deployment guide
    - Document database migration steps
    - Document WEF and syslog configuration
    - Document polling node setup
    - Document backward compatibility considerations
    - _Requirements: 22.1, 22.2, 22.3, 22.4, 22.5_
  
  - [ ] 34.3 Write user guide
    - Document site and department management
    - Document print audit trail usage
    - Document printer monitoring
    - Document polling node management
    - _Requirements: All user-facing requirements_

- [ ] 35. Final checkpoint - Ensure all tests pass
  - Run all unit tests
  - Run all property-based tests (60 properties)
  - Run all integration tests
  - Run backward compatibility tests
  - Verify all 24 requirements validated
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests validate universal correctness properties from design document
- Unit tests validate specific examples and edge cases
- Integration tests validate end-to-end flows
- Backward compatibility is critical - all existing functionality must continue working
- Total of 60 correctness properties to be implemented as property-based tests
- Use `hypothesis` library for property-based testing with minimum 100 iterations per test
