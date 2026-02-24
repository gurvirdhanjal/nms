# Requirements Document

## Introduction

This document specifies requirements for Phase 1 MVP of an enterprise monitoring system expansion. The system extends existing monitoring infrastructure to support multi-site deployments, agent-free printer monitoring, basic RBAC with department isolation, REST API foundation, and basic distributed polling. This phase focuses on delivering stable, production-ready core features incrementally without breaking existing functionality, designed for a solo developer with an 8-12 week timeline.

## Glossary

- **Monitoring_System**: The existing monitoring infrastructure that tracks device health and metrics
- **Site**: A physical location where monitored devices are deployed
- **Department**: An organizational unit within the company that owns or manages devices
- **Polling_Node**: A distributed component that collects metrics from devices and forwards them to the Central_Aggregator
- **Central_Aggregator**: The central server that receives metrics from all Polling_Nodes and stores them in the database
- **Print_Server**: A Windows or Linux server that manages print queues and processes print jobs
- **Network_Printer**: A printer with direct network connectivity that supports SNMP
- **WEF**: Windows Event Forwarding - a Windows feature that forwards event logs to a collector
- **CUPS**: Common Unix Printing System - the standard printing system for Linux
- **SNMP**: Simple Network Management Protocol - used for querying device status and metrics
- **Printer_MIB**: Management Information Base for printers defined in RFC 3805
- **Print_Job**: A record of a document sent to a printer, including user, document name, printer, timestamp, and page count
- **API_Token**: An authentication credential used to access the REST API
- **RBAC**: Role-Based Access Control - a permission model based on user roles
- **Device**: Any monitored endpoint including printers, workstations, servers, firewalls, routers, and switches
- **Metric**: A measured value from a device such as CPU usage, memory, toner level, or page count
- **Heartbeat**: A periodic signal sent by a Polling_Node to indicate it is operational
- **Syslog_Receiver**: A component that receives and parses syslog messages from CUPS servers
- **Event_Collector**: A Windows component that receives forwarded events from Print_Servers via WEF

## Requirements

### Requirement 1: Multi-Site Device Organization

**User Story:** As a system administrator, I want to organize devices by physical location, so that I can manage and monitor devices based on where they are deployed.

#### Acceptance Criteria

1. THE Monitoring_System SHALL store Site records with name, address, timezone, and contact information
2. WHEN a Site is created, THE Monitoring_System SHALL assign it a unique identifier
3. THE Monitoring_System SHALL associate each Device with exactly one Site
4. WHEN a Device is created, THE Monitoring_System SHALL require a Site assignment
5. THE Monitoring_System SHALL allow updating a Device's Site assignment
6. WHEN a Site has associated Devices, THE Monitoring_System SHALL prevent deletion of the Site
7. THE Monitoring_System SHALL allow deletion of Sites with no associated Devices

### Requirement 2: Site-Level Dashboards

**User Story:** As a system administrator, I want to view site-specific dashboards, so that I can monitor the health of devices at each location.

#### Acceptance Criteria

1. THE Monitoring_System SHALL display a dashboard for each Site showing all associated Devices
2. THE Site dashboard SHALL display device count, online count, offline count, and warning count
3. THE Site dashboard SHALL display recent alerts for Devices at that Site
4. THE Site dashboard SHALL display aggregate metrics for all Devices at that Site
5. WHEN a user selects a Site, THE Monitoring_System SHALL filter all views to show only Devices from that Site

### Requirement 3: Site Filtering

**User Story:** As a system administrator, I want to filter devices by site across all views, so that I can focus on specific locations.

#### Acceptance Criteria

1. THE Monitoring_System SHALL provide a Site filter on the device list view
2. THE Monitoring_System SHALL provide a Site filter on the alerts view
3. THE Monitoring_System SHALL provide a Site filter on the metrics view
4. WHEN a Site filter is applied, THE Monitoring_System SHALL display only data for Devices at the selected Site
5. THE Monitoring_System SHALL persist Site filter selection across page navigation within a user session

### Requirement 4: Windows Print Server Monitoring via WEF

**User Story:** As a system administrator, I want to monitor Windows print servers without installing agents, so that I can track print jobs and printer usage with minimal infrastructure changes.

#### Acceptance Criteria

1. THE Event_Collector SHALL receive forwarded print events from Windows Print_Servers via WEF
2. WHEN a print event is received, THE Event_Collector SHALL parse the event to extract user, document name, printer name, timestamp, and page count
3. THE Monitoring_System SHALL store each parsed print event as a Print_Job record
4. THE Monitoring_System SHALL associate each Print_Job with the corresponding Network_Printer or Print_Server
5. IF a print event cannot be parsed, THEN THE Event_Collector SHALL log the error and continue processing
6. THE Event_Collector SHALL process print events within 5 seconds of receipt

### Requirement 5: Linux CUPS Server Monitoring via Syslog

**User Story:** As a system administrator, I want to monitor Linux CUPS servers without installing agents, so that I can track print jobs from Linux environments.

#### Acceptance Criteria

1. THE Syslog_Receiver SHALL receive syslog messages from CUPS servers
2. WHEN a CUPS syslog message is received, THE Syslog_Receiver SHALL parse it to extract user, document name, printer name, timestamp, and page count
3. THE Monitoring_System SHALL store each parsed CUPS log entry as a Print_Job record
4. THE Monitoring_System SHALL associate each Print_Job with the corresponding Network_Printer
5. IF a CUPS syslog message cannot be parsed, THEN THE Syslog_Receiver SHALL log the error and continue processing
6. THE Syslog_Receiver SHALL process syslog messages within 5 seconds of receipt

### Requirement 6: Network Printer SNMP Monitoring

**User Story:** As a system administrator, I want to monitor network printers via SNMP, so that I can track toner levels, page counts, and printer status without agents.

#### Acceptance Criteria

1. THE Monitoring_System SHALL poll Network_Printers using SNMP based on Printer_MIB
2. THE Monitoring_System SHALL collect toner level percentages for each color cartridge
3. THE Monitoring_System SHALL collect total page count and pages printed since last poll
4. THE Monitoring_System SHALL collect printer status including ready, printing, paper jam, and out of paper
5. THE Monitoring_System SHALL collect print queue length
6. WHEN an SNMP poll fails, THE Monitoring_System SHALL log the error and retry on the next poll cycle
7. THE Monitoring_System SHALL poll each Network_Printer at intervals between 1 and 60 minutes as configured per printer

### Requirement 7: Print Job Audit Trail

**User Story:** As a system administrator, I want to view a complete audit trail of print jobs, so that I can track printing activity and costs.

#### Acceptance Criteria

1. THE Monitoring_System SHALL display a list of Print_Jobs with user, document name, printer, timestamp, page count, and Site
2. THE Monitoring_System SHALL allow filtering Print_Jobs by date range, user, printer, Site, and Department
3. THE Monitoring_System SHALL allow sorting Print_Jobs by timestamp, user, printer, or page count
4. THE Monitoring_System SHALL display total page count for the filtered Print_Jobs
5. THE Monitoring_System SHALL retain Print_Job records for at least 90 days
6. THE Monitoring_System SHALL allow exporting Print_Job data to CSV format

### Requirement 8: Department Hierarchy

**User Story:** As a system administrator, I want to organize users and devices into departments, so that I can provide department-level access control and reporting.

#### Acceptance Criteria

1. THE Monitoring_System SHALL store Department records with name and description
2. WHEN a Department is created, THE Monitoring_System SHALL assign it a unique identifier
3. THE Monitoring_System SHALL allow associating Devices with zero or one Department
4. THE Monitoring_System SHALL allow associating users with zero or one Department
5. WHEN a Department has associated Devices or users, THE Monitoring_System SHALL prevent deletion of the Department
6. THE Monitoring_System SHALL allow deletion of Departments with no associated Devices or users

### Requirement 9: Department-Based Device Filtering

**User Story:** As a department manager, I want to view only devices assigned to my department, so that I can focus on resources I manage.

#### Acceptance Criteria

1. THE Monitoring_System SHALL provide a Department filter on the device list view
2. THE Monitoring_System SHALL provide a Department filter on the alerts view
3. THE Monitoring_System SHALL provide a Department filter on the Print_Job audit view
4. WHEN a Department filter is applied, THE Monitoring_System SHALL display only data for Devices assigned to that Department
5. WHERE a user has view_own_department permission, THE Monitoring_System SHALL automatically filter to show only the user's Department

### Requirement 10: Basic RBAC Permissions

**User Story:** As a system administrator, I want to control what users can view based on their department, so that I can enforce data access policies.

#### Acceptance Criteria

1. THE Monitoring_System SHALL support a view_own_department permission that restricts users to viewing only their Department's data
2. THE Monitoring_System SHALL support a view_all_departments permission that allows users to view data from all Departments
3. WHERE a user has view_own_department permission, THE Monitoring_System SHALL filter all device lists, alerts, and Print_Jobs to show only the user's Department
4. WHERE a user has view_all_departments permission, THE Monitoring_System SHALL display data from all Departments
5. WHEN a user without view_all_departments permission attempts to access another Department's data, THE Monitoring_System SHALL return an authorization error

### Requirement 11: Sites REST API

**User Story:** As an API consumer, I want to manage sites via REST API, so that I can integrate site management with other systems.

#### Acceptance Criteria

1. THE Monitoring_System SHALL provide a GET /api/sites endpoint that returns all Sites
2. THE Monitoring_System SHALL provide a GET /api/sites/{id} endpoint that returns a specific Site
3. THE Monitoring_System SHALL provide a POST /api/sites endpoint that creates a new Site
4. THE Monitoring_System SHALL provide a PUT /api/sites/{id} endpoint that updates a Site
5. THE Monitoring_System SHALL provide a DELETE /api/sites/{id} endpoint that deletes a Site with no associated Devices
6. WHEN a DELETE request targets a Site with associated Devices, THE Monitoring_System SHALL return a 409 Conflict error
7. THE Monitoring_System SHALL require API_Token authentication for all Sites API endpoints

### Requirement 12: Devices REST API Extensions

**User Story:** As an API consumer, I want to filter devices by site and department via REST API, so that I can retrieve relevant device subsets.

#### Acceptance Criteria

1. THE Monitoring_System SHALL extend GET /api/devices to accept a site_id query parameter
2. THE Monitoring_System SHALL extend GET /api/devices to accept a department_id query parameter
3. WHEN site_id is provided, THE Monitoring_System SHALL return only Devices assigned to that Site
4. WHEN department_id is provided, THE Monitoring_System SHALL return only Devices assigned to that Department
5. WHEN both site_id and department_id are provided, THE Monitoring_System SHALL return only Devices matching both criteria
6. THE Monitoring_System SHALL include site_id and department_id fields in Device API responses

### Requirement 13: Printers REST API

**User Story:** As an API consumer, I want to query printer information and metrics via REST API, so that I can build custom printer monitoring tools.

#### Acceptance Criteria

1. THE Monitoring_System SHALL provide a GET /api/printers endpoint that returns all Network_Printers and Print_Servers
2. THE Monitoring_System SHALL provide a GET /api/printers/{id} endpoint that returns details for a specific printer
3. THE Monitoring_System SHALL provide a GET /api/printers/{id}/metrics endpoint that returns current toner levels, page counts, and status
4. THE Monitoring_System SHALL provide a GET /api/printers/{id}/jobs endpoint that returns Print_Jobs for a specific printer
5. THE GET /api/printers/{id}/jobs endpoint SHALL accept start_date and end_date query parameters
6. THE Monitoring_System SHALL require API_Token authentication for all Printers API endpoints
7. THE Monitoring_System SHALL apply Department filtering to Printers API based on the authenticated user's permissions

### Requirement 14: Print Jobs REST API

**User Story:** As an API consumer, I want to query print job audit data via REST API, so that I can generate custom reports.

#### Acceptance Criteria

1. THE Monitoring_System SHALL provide a GET /api/print-jobs endpoint that returns Print_Job records
2. THE GET /api/print-jobs endpoint SHALL accept start_date, end_date, user, printer_id, site_id, and department_id query parameters
3. THE GET /api/print-jobs endpoint SHALL support pagination with page and page_size parameters
4. THE GET /api/print-jobs endpoint SHALL return total page count for the filtered results
5. THE Monitoring_System SHALL require API_Token authentication for the Print Jobs API
6. THE Monitoring_System SHALL apply Department filtering based on the authenticated user's permissions

### Requirement 15: Departments REST API

**User Story:** As an API consumer, I want to manage departments via REST API, so that I can integrate department management with HR systems.

#### Acceptance Criteria

1. THE Monitoring_System SHALL provide a GET /api/departments endpoint that returns all Departments
2. THE Monitoring_System SHALL provide a GET /api/departments/{id} endpoint that returns a specific Department
3. THE Monitoring_System SHALL provide a POST /api/departments endpoint that creates a new Department
4. THE Monitoring_System SHALL provide a PUT /api/departments/{id} endpoint that updates a Department
5. THE Monitoring_System SHALL provide a DELETE /api/departments/{id} endpoint that deletes a Department with no associated Devices or users
6. WHEN a DELETE request targets a Department with associated Devices or users, THE Monitoring_System SHALL return a 409 Conflict error
7. THE Monitoring_System SHALL require API_Token authentication for all Departments API endpoints

### Requirement 16: API Token Authentication

**User Story:** As an API consumer, I want to authenticate using API tokens, so that I can securely access the REST API.

#### Acceptance Criteria

1. THE Monitoring_System SHALL generate API_Tokens for authenticated users
2. THE Monitoring_System SHALL accept API_Tokens in the Authorization header using Bearer scheme
3. WHEN a valid API_Token is provided, THE Monitoring_System SHALL authenticate the request and apply the user's permissions
4. WHEN an invalid API_Token is provided, THE Monitoring_System SHALL return a 401 Unauthorized error
5. WHEN no API_Token is provided for a protected endpoint, THE Monitoring_System SHALL return a 401 Unauthorized error
6. THE Monitoring_System SHALL allow users to revoke their API_Tokens
7. THE Monitoring_System SHALL store API_Tokens securely using one-way hashing

### Requirement 17: API Rate Limiting

**User Story:** As a system administrator, I want to rate limit API requests, so that I can prevent abuse and ensure system stability.

#### Acceptance Criteria

1. THE Monitoring_System SHALL limit each API_Token to 1000 requests per hour
2. WHEN an API_Token exceeds the rate limit, THE Monitoring_System SHALL return a 429 Too Many Requests error
3. THE Monitoring_System SHALL include X-RateLimit-Limit, X-RateLimit-Remaining, and X-RateLimit-Reset headers in API responses
4. THE Monitoring_System SHALL reset rate limit counters every hour
5. THE Monitoring_System SHALL track rate limits per API_Token independently

### Requirement 18: Polling Node Registration

**User Story:** As a system administrator, I want to register distributed polling nodes, so that I can scale monitoring across multiple locations.

#### Acceptance Criteria

1. THE Monitoring_System SHALL allow registering Polling_Nodes with name, hostname, and Site assignment
2. WHEN a Polling_Node is registered, THE Monitoring_System SHALL assign it a unique identifier and authentication credential
3. THE Monitoring_System SHALL store Polling_Node status including last_heartbeat timestamp and operational state
4. THE Monitoring_System SHALL display a list of all registered Polling_Nodes with their status
5. THE Monitoring_System SHALL allow deregistering Polling_Nodes that have no assigned Devices

### Requirement 19: Device-to-Node Assignment

**User Story:** As a system administrator, I want to assign devices to polling nodes, so that I can distribute monitoring load.

#### Acceptance Criteria

1. THE Monitoring_System SHALL allow assigning each Device to exactly one Polling_Node
2. THE Monitoring_System SHALL support automatic assignment of Devices to Polling_Nodes based on Site
3. THE Monitoring_System SHALL support manual assignment of Devices to specific Polling_Nodes
4. WHEN a Device is assigned to a Polling_Node, THE Monitoring_System SHALL notify the Polling_Node of the assignment
5. THE Monitoring_System SHALL allow reassigning Devices to different Polling_Nodes
6. WHEN a Polling_Node is deregistered, THE Monitoring_System SHALL unassign all its Devices

### Requirement 20: Metric Forwarding from Nodes to Central Aggregator

**User Story:** As a system administrator, I want polling nodes to forward metrics to the central aggregator, so that all data is centrally stored and accessible.

#### Acceptance Criteria

1. WHEN a Polling_Node collects a Metric, THE Polling_Node SHALL forward it to the Central_Aggregator
2. THE Central_Aggregator SHALL accept Metric submissions from authenticated Polling_Nodes
3. THE Central_Aggregator SHALL store received Metrics in the database with timestamp, device_id, metric_name, and value
4. IF metric forwarding fails, THEN THE Polling_Node SHALL queue the Metric and retry forwarding
5. THE Polling_Node SHALL retain queued Metrics for up to 24 hours before discarding
6. THE Central_Aggregator SHALL process received Metrics within 10 seconds

### Requirement 21: Polling Node Heartbeat Monitoring

**User Story:** As a system administrator, I want to monitor polling node health, so that I can detect and respond to node failures.

#### Acceptance Criteria

1. THE Polling_Node SHALL send a Heartbeat to the Central_Aggregator every 60 seconds
2. WHEN a Heartbeat is received, THE Central_Aggregator SHALL update the Polling_Node's last_heartbeat timestamp
3. WHEN a Polling_Node has not sent a Heartbeat for 5 minutes, THE Central_Aggregator SHALL mark it as offline
4. WHEN a Polling_Node is marked offline, THE Central_Aggregator SHALL generate an alert
5. WHEN an offline Polling_Node sends a Heartbeat, THE Central_Aggregator SHALL mark it as online and clear the alert
6. THE Monitoring_System SHALL display Polling_Node status on a dedicated dashboard

### Requirement 22: Backward Compatibility with Existing Agents

**User Story:** As a system administrator, I want Phase 1 changes to be backward compatible with existing agents, so that I don't need to update all agents immediately.

#### Acceptance Criteria

1. THE Monitoring_System SHALL accept metrics from server_agent.py version 1.0 and later
2. THE Monitoring_System SHALL accept metrics from service.py version 1.0 and later
3. WHEN an existing agent submits metrics without site_id or department_id, THE Monitoring_System SHALL accept the metrics and assign default values
4. THE Monitoring_System SHALL maintain existing API endpoints without breaking changes
5. THE Monitoring_System SHALL maintain existing database schema for Device and Metric tables while adding new optional columns

### Requirement 23: Database Schema Extensions

**User Story:** As a system administrator, I want database schema changes to be non-breaking, so that the system remains operational during deployment.

#### Acceptance Criteria

1. THE Monitoring_System SHALL add Sites table with columns: id, name, address, timezone, contact_info, created_at
2. THE Monitoring_System SHALL add Departments table with columns: id, name, description, created_at
3. THE Monitoring_System SHALL add site_id column to Devices table as nullable foreign key
4. THE Monitoring_System SHALL add department_id column to Devices table as nullable foreign key
5. THE Monitoring_System SHALL add PrintJobs table with columns: id, user, document_name, printer_id, timestamp, page_count, site_id, department_id
6. THE Monitoring_System SHALL add PollingNodes table with columns: id, name, hostname, site_id, last_heartbeat, status, created_at
7. THE Monitoring_System SHALL add department_id column to Users table as nullable foreign key
8. THE Monitoring_System SHALL add polling_node_id column to Devices table as nullable foreign key

### Requirement 24: Existing Functionality Preservation

**User Story:** As a system administrator, I want all existing monitoring functionality to continue working, so that Phase 1 deployment doesn't disrupt operations.

#### Acceptance Criteria

1. THE Monitoring_System SHALL continue to collect metrics from all existing Device types
2. THE Monitoring_System SHALL continue to generate alerts based on existing alert rules
3. THE Monitoring_System SHALL continue to display existing dashboards
4. THE Monitoring_System SHALL continue to support existing user authentication and authorization
5. THE Monitoring_System SHALL continue to process metrics using the existing poll_tasks queue pattern
6. FOR ALL existing API endpoints, THE Monitoring_System SHALL maintain the same request and response formats
7. FOR ALL existing database queries, THE Monitoring_System SHALL return the same results when site_id and department_id are null
