# Design Document: Monitoring Phase 1 MVP

## Overview

This design extends the existing monitoring system to support multi-site enterprise deployments with agent-free printer monitoring, basic RBAC, REST API foundation, and distributed polling. The design prioritizes backward compatibility, incremental deployment, and minimal infrastructure changes suitable for a solo developer with an 8-12 week timeline.

### Design Principles

1. **Backward Compatibility**: All existing agents (server_agent.py, service.py) continue working without modification
2. **Incremental Schema**: Database changes use nullable columns to avoid breaking existing queries
3. **Reuse Existing Patterns**: Leverage poll_tasks queue and scheduler/worker architecture
4. **Minimal New Dependencies**: No TimescaleDB, Redis, or RabbitMQ in Phase 1
5. **Agent-Free Monitoring**: Windows Event Forwarding (WEF), syslog, and SNMP only for printers

### Key Constraints

- Use existing PostgreSQL database
- Extend existing poll_tasks queue pattern
- Reuse scheduler/worker architecture from services/scheduler.py and workers/snmp_worker.py
- No breaking changes to existing Device model or API endpoints
- Simple deployment with minimal new services

## Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                         Web Application                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   Flask UI   │  │  REST API    │  │  Auth/RBAC   │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Service Layer                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ Sites Svc    │  │ Departments  │  │ Print Jobs   │          │
│  │              │  │ Service      │  │ Service      │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ Polling Node │  │ WEF Parser   │  │ Syslog Parser│          │
│  │ Service      │  │              │  │              │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Scheduler & Workers                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │  Scheduler   │→ │  poll_tasks  │← │ SNMP Worker  │          │
│  │ (existing)   │  │   (queue)    │  │ (extended)   │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Data Collection                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ WEF Collector│  │Syslog Receiver│ │ SNMP Polling │          │
│  │ (Windows)    │  │  (UDP 514)   │  │  (existing)  │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
         │                    │                    │
         └────────────────────┴────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PostgreSQL Database                           │
│  Sites │ Departments │ PrintJobs │ PollingNodes │ Devices       │
└─────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

#### Existing Components (Extended)

1. **Scheduler (services/scheduler.py)**
   - Extends existing enqueue_snmp_tasks() to support printer polling
   - Adds enqueue_printer_snmp_tasks() method
   - Maintains existing 5-minute polling cycle

2. **SNMP Worker (workers/snmp_worker.py)**
   - Extends execute_task() to handle 'printer_snmp' task type
   - Adds _execute_printer_snmp() method
   - Reuses existing FOR UPDATE SKIP LOCKED pattern

3. **Device Model (models/device.py)**
   - Adds nullable columns: site_id, department_id, polling_node_id
   - Maintains backward compatibility (nulls allowed)

#### New Components

1. **WEF Event Collector (services/wef_collector.py)**
   - Receives Windows Event Forwarding events on HTTP endpoint
   - Parses Event ID 307 (print job completed)
   - Extracts: user, document_name, printer_name, page_count, timestamp
   - Creates PrintJob records

2. **Syslog Receiver (services/syslog_receiver.py)**
   - Listens on UDP port 514
   - Parses CUPS syslog messages
   - Extracts: user, document_name, printer_name, page_count, timestamp
   - Creates PrintJob records

3. **Sites Service (services/sites_service.py)**
   - CRUD operations for Site records
   - Validates Site deletion (prevents if devices assigned)
   - Provides site filtering helpers

4. **Departments Service (services/departments_service.py)**
   - CRUD operations for Department records
   - Validates Department deletion (prevents if devices/users assigned)
   - Provides department filtering helpers

5. **Print Jobs Service (services/print_jobs_service.py)**
   - Stores print job audit records
   - Provides filtering by date, user, printer, site, department
   - Handles CSV export
   - Enforces 90-day retention policy

6. **Polling Node Service (services/polling_node_service.py)**
   - Registers/deregisters polling nodes
   - Tracks heartbeat and status
   - Handles device-to-node assignment
   - Processes metric forwarding from nodes

### Data Flow

#### Print Job Collection Flow

**Windows Print Server:**
```
Print Server → WEF Event → Event Collector → Parse Event → 
  Lookup Printer Device → Create PrintJob → Store in DB
```

**Linux CUPS Server:**
```
CUPS Server → Syslog → Syslog Receiver → Parse Syslog → 
  Lookup Printer Device → Create PrintJob → Store in DB
```

#### Printer SNMP Polling Flow

```
Scheduler → Enqueue printer_snmp task → poll_tasks table →
  SNMP Worker → Poll Printer MIB → Store Metrics → 
  Update Device health → Evaluate Thresholds
```

#### Distributed Polling Flow

```
Polling Node → Collect Metrics → Forward to Central Aggregator →
  Store Metrics → Update last_heartbeat → Process Alerts
```

## Components and Interfaces

### Database Schema Extensions

#### New Tables

**Sites Table**
```sql
CREATE TABLE sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL UNIQUE,
    address TEXT,
    timezone VARCHAR(50) DEFAULT 'UTC',
    contact_info TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**Departments Table**
```sql
CREATE TABLE departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**PrintJobs Table**
```sql
CREATE TABLE print_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user VARCHAR(100) NOT NULL,
    document_name VARCHAR(255) NOT NULL,
    printer_id INTEGER NOT NULL,
    timestamp DATETIME NOT NULL,
    page_count INTEGER NOT NULL,
    site_id INTEGER,
    department_id INTEGER,
    source VARCHAR(20) NOT NULL,  -- 'wef', 'syslog', 'snmp'
    raw_event TEXT,  -- Original event for debugging
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (printer_id) REFERENCES device(device_id) ON DELETE CASCADE,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE SET NULL,
    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE SET NULL
);

CREATE INDEX idx_print_jobs_timestamp ON print_jobs(timestamp);
CREATE INDEX idx_print_jobs_printer ON print_jobs(printer_id);
CREATE INDEX idx_print_jobs_user ON print_jobs(user);
CREATE INDEX idx_print_jobs_site ON print_jobs(site_id);
CREATE INDEX idx_print_jobs_department ON print_jobs(department_id);
```

**PollingNodes Table**
```sql
CREATE TABLE polling_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL UNIQUE,
    hostname VARCHAR(255) NOT NULL,
    site_id INTEGER,
    last_heartbeat DATETIME,
    status VARCHAR(20) DEFAULT 'offline',  -- 'online', 'offline'
    auth_token VARCHAR(255) NOT NULL,  -- For authenticating node requests
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE SET NULL
);

CREATE INDEX idx_polling_nodes_status ON polling_nodes(status);
CREATE INDEX idx_polling_nodes_site ON polling_nodes(site_id);
```

**APITokens Table**
```sql
CREATE TABLE api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(100),  -- User-friendly name for the token
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_used_at DATETIME,
    revoked_at DATETIME,
    FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
);

CREATE INDEX idx_api_tokens_hash ON api_tokens(token_hash);
CREATE INDEX idx_api_tokens_user ON api_tokens(user_id);
```

**RateLimits Table**
```sql
CREATE TABLE rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_token_id INTEGER NOT NULL,
    window_start DATETIME NOT NULL,
    request_count INTEGER DEFAULT 0,
    FOREIGN KEY (api_token_id) REFERENCES api_tokens(id) ON DELETE CASCADE
);

CREATE INDEX idx_rate_limits_token_window ON rate_limits(api_token_id, window_start);
```

#### Extended Tables

**Device Table Extensions**
```sql
ALTER TABLE device ADD COLUMN site_id INTEGER;
ALTER TABLE device ADD COLUMN department_id INTEGER;
ALTER TABLE device ADD COLUMN polling_node_id INTEGER;

CREATE INDEX idx_device_site ON device(site_id);
CREATE INDEX idx_device_department ON device(department_id);
CREATE INDEX idx_device_polling_node ON device(polling_node_id);
```

**User Table Extensions**
```sql
ALTER TABLE user ADD COLUMN department_id INTEGER;
ALTER TABLE user ADD COLUMN view_own_department BOOLEAN DEFAULT FALSE;
ALTER TABLE user ADD COLUMN view_all_departments BOOLEAN DEFAULT TRUE;

CREATE INDEX idx_user_department ON user(department_id);
```

### Service Interfaces

#### SitesService

```python
class SitesService:
    def create_site(self, name: str, address: str = None, 
                   timezone: str = 'UTC', contact_info: str = None) -> Site:
        """Create a new site."""
        
    def get_site(self, site_id: int) -> Site:
        """Get site by ID."""
        
    def list_sites(self) -> List[Site]:
        """List all sites."""
        
    def update_site(self, site_id: int, **kwargs) -> Site:
        """Update site attributes."""
        
    def delete_site(self, site_id: int) -> bool:
        """Delete site if no devices assigned. Raises ValueError if devices exist."""
        
    def get_site_devices(self, site_id: int) -> List[Device]:
        """Get all devices for a site."""
        
    def get_site_stats(self, site_id: int) -> dict:
        """Get device count, online count, offline count, warning count."""
```

#### DepartmentsService

```python
class DepartmentsService:
    def create_department(self, name: str, description: str = None) -> Department:
        """Create a new department."""
        
    def get_department(self, dept_id: int) -> Department:
        """Get department by ID."""
        
    def list_departments(self) -> List[Department]:
        """List all departments."""
        
    def update_department(self, dept_id: int, **kwargs) -> Department:
        """Update department attributes."""
        
    def delete_department(self, dept_id: int) -> bool:
        """Delete department if no devices/users assigned."""
        
    def get_department_devices(self, dept_id: int) -> List[Device]:
        """Get all devices for a department."""
```

#### PrintJobsService

```python
class PrintJobsService:
    def create_print_job(self, user: str, document_name: str, 
                        printer_id: int, timestamp: datetime,
                        page_count: int, source: str,
                        raw_event: str = None) -> PrintJob:
        """Create a print job record."""
        
    def list_print_jobs(self, start_date: datetime = None,
                       end_date: datetime = None,
                       user: str = None,
                       printer_id: int = None,
                       site_id: int = None,
                       department_id: int = None,
                       page: int = 1,
                       page_size: int = 100) -> dict:
        """List print jobs with filtering and pagination.
        Returns: {'jobs': [...], 'total': N, 'total_pages': M}"""
        
    def get_total_pages(self, filters: dict) -> int:
        """Get total page count for filtered print jobs."""
        
    def export_to_csv(self, filters: dict) -> str:
        """Export filtered print jobs to CSV format."""
        
    def cleanup_old_jobs(self, retention_days: int = 90):
        """Delete print jobs older than retention period."""
```

#### PollingNodeService

```python
class PollingNodeService:
    def register_node(self, name: str, hostname: str, 
                     site_id: int = None) -> PollingNode:
        """Register a new polling node and generate auth token."""
        
    def deregister_node(self, node_id: int) -> bool:
        """Deregister node if no devices assigned."""
        
    def update_heartbeat(self, node_id: int):
        """Update last_heartbeat and set status to 'online'."""
        
    def check_node_health(self):
        """Mark nodes offline if no heartbeat for 5 minutes."""
        
    def assign_device(self, device_id: int, node_id: int):
        """Assign device to polling node."""
        
    def unassign_device(self, device_id: int):
        """Remove device from polling node."""
        
    def auto_assign_devices(self, site_id: int):
        """Auto-assign devices to nodes based on site."""
        
    def receive_metrics(self, node_id: int, metrics: List[dict]):
        """Process metrics forwarded from polling node."""
```

#### WEFCollector

```python
class WEFCollector:
    def receive_event(self, event_xml: str) -> dict:
        """Receive and parse Windows Event Forwarding event.
        Returns parsed event dict or None if parsing fails."""
        
    def parse_print_event(self, event_xml: str) -> dict:
        """Parse Event ID 307 (print job completed).
        Returns: {
            'user': str,
            'document_name': str,
            'printer_name': str,
            'page_count': int,
            'timestamp': datetime
        }"""
        
    def process_print_event(self, event_data: dict):
        """Create PrintJob record from parsed event."""
```

#### SyslogReceiver

```python
class SyslogReceiver:
    def start_listener(self, port: int = 514):
        """Start UDP syslog listener."""
        
    def parse_cups_message(self, message: str) -> dict:
        """Parse CUPS syslog message.
        Returns: {
            'user': str,
            'document_name': str,
            'printer_name': str,
            'page_count': int,
            'timestamp': datetime
        }"""
        
    def process_cups_message(self, message_data: dict):
        """Create PrintJob record from parsed syslog."""
```

### REST API Endpoints

#### Sites API

```
GET    /api/sites              - List all sites
GET    /api/sites/{id}         - Get site details
POST   /api/sites              - Create site
PUT    /api/sites/{id}         - Update site
DELETE /api/sites/{id}         - Delete site (fails if devices exist)
```

#### Departments API

```
GET    /api/departments        - List all departments
GET    /api/departments/{id}   - Get department details
POST   /api/departments        - Create department
PUT    /api/departments/{id}   - Update department
DELETE /api/departments/{id}   - Delete department (fails if devices/users exist)
```

#### Devices API Extensions

```
GET    /api/devices?site_id={id}&department_id={id}  - Filter devices
```

Response includes site_id and department_id fields.

#### Printers API

```
GET    /api/printers                    - List all printers
GET    /api/printers/{id}               - Get printer details
GET    /api/printers/{id}/metrics       - Get current printer metrics
GET    /api/printers/{id}/jobs          - Get print jobs for printer
```

#### Print Jobs API

```
GET    /api/print-jobs?start_date={date}&end_date={date}&user={user}&printer_id={id}&site_id={id}&department_id={id}&page={n}&page_size={n}
```

Response:
```json
{
  "jobs": [...],
  "total": 1234,
  "total_pages": 13,
  "page": 1,
  "page_size": 100,
  "total_page_count": 45678
}
```

#### API Tokens

```
POST   /api/tokens              - Generate new API token
GET    /api/tokens              - List user's tokens
DELETE /api/tokens/{id}         - Revoke token
```

### Authentication & Authorization

#### API Token Authentication

1. User generates token via UI or API
2. System creates random token (32 bytes, hex-encoded)
3. Token hash stored in database (SHA-256)
4. Token returned to user once (not retrievable later)
5. Requests include: `Authorization: Bearer <token>`
6. System validates token hash and loads user permissions

#### RBAC Permissions

**Permission Flags (User model):**
- `view_own_department`: Boolean - restricts to user's department
- `view_all_departments`: Boolean - allows viewing all departments

**Filtering Logic:**
- If `view_own_department=True`: Filter all queries by user's department_id
- If `view_all_departments=True`: No filtering applied
- Default for new users: `view_all_departments=True` (backward compatible)

#### Rate Limiting

- 1000 requests per hour per API token
- Sliding window: track request count in current hour
- Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- 429 response when limit exceeded

## Data Models

### Site Model

```python
class Site(db.Model):
    __tablename__ = 'sites'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    address = db.Column(db.Text)
    timezone = db.Column(db.String(50), default='UTC')
    contact_info = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    devices = db.relationship('Device', backref='site', lazy='dynamic')
    polling_nodes = db.relationship('PollingNode', backref='site', lazy='dynamic')
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'address': self.address,
            'timezone': self.timezone,
            'contact_info': self.contact_info,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
```

### Department Model

```python
class Department(db.Model):
    __tablename__ = 'departments'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    devices = db.relationship('Device', backref='department', lazy='dynamic')
    users = db.relationship('User', backref='department', lazy='dynamic')
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
```

### PrintJob Model

```python
class PrintJob(db.Model):
    __tablename__ = 'print_jobs'
    
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(100), nullable=False, index=True)
    document_name = db.Column(db.String(255), nullable=False)
    printer_id = db.Column(db.Integer, db.ForeignKey('device.device_id'), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, index=True)
    page_count = db.Column(db.Integer, nullable=False)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'))
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'))
    source = db.Column(db.String(20), nullable=False)  # 'wef', 'syslog', 'snmp'
    raw_event = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    printer = db.relationship('Device', backref='print_jobs')
    
    def to_dict(self):
        return {
            'id': self.id,
            'user': self.user,
            'document_name': self.document_name,
            'printer_id': self.printer_id,
            'printer_name': self.printer.device_name if self.printer else None,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'page_count': self.page_count,
            'site_id': self.site_id,
            'department_id': self.department_id,
            'source': self.source
        }
```

### PollingNode Model

```python
class PollingNode(db.Model):
    __tablename__ = 'polling_nodes'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    hostname = db.Column(db.String(255), nullable=False)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'))
    last_heartbeat = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='offline')
    auth_token = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    devices = db.relationship('Device', backref='polling_node', lazy='dynamic')
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'hostname': self.hostname,
            'site_id': self.site_id,
            'last_heartbeat': self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
```

### APIToken Model

```python
class APIToken(db.Model):
    __tablename__ = 'api_tokens'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    token_hash = db.Column(db.String(255), nullable=False, unique=True)
    name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime)
    revoked_at = db.Column(db.DateTime)
    
    user = db.relationship('User', backref='api_tokens')
    
    def is_valid(self):
        return self.revoked_at is None
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_used_at': self.last_used_at.isoformat() if self.last_used_at else None,
            'revoked': self.revoked_at is not None
        }
```

### Printer MIB OIDs

For SNMP printer polling (RFC 3805 Printer MIB):

```python
PRINTER_MIB_OIDS = {
    # Device Info
    'hrDeviceDescr': '1.3.6.1.2.1.25.3.2.1.3.1',
    'prtGeneralSerialNumber': '1.3.6.1.2.1.43.5.1.1.17.1',
    
    # Status
    'hrPrinterStatus': '1.3.6.1.2.1.25.3.5.1.1.1',
    'hrPrinterDetectedErrorState': '1.3.6.1.2.1.25.3.5.1.2.1',
    
    # Page Counts
    'prtMarkerLifeCount': '1.3.6.1.2.1.43.10.2.1.4.1.1',
    
    # Toner Levels (per cartridge)
    'prtMarkerSuppliesLevel': '1.3.6.1.2.1.43.11.1.1.9',
    'prtMarkerSuppliesMaxCapacity': '1.3.6.1.2.1.43.11.1.1.8',
    'prtMarkerSuppliesDescription': '1.3.6.1.2.1.43.11.1.1.6',
    'prtMarkerSuppliesType': '1.3.6.1.2.1.43.11.1.1.4',
    
    # Queue Length
    'hrPrinterQueueLength': '1.3.6.1.2.1.25.3.5.1.1.1'
}
```


## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property Reflection

After analyzing all acceptance criteria, several redundant properties were identified and consolidated:

- Site/Department filtering properties (2.5, 3.4, 9.4, 12.3, 12.4) can be combined into comprehensive filtering properties
- RBAC filtering properties (9.5, 10.1, 10.3) are redundant and can be combined
- Authentication properties (11.7, 13.6, 14.5, 15.7) are redundant and can be combined
- Deletion prevention properties (1.6, 8.5) follow the same pattern
- Parsing properties (4.2, 5.2) follow the same pattern for different sources
- Error handling properties (4.5, 5.5, 6.6) follow the same pattern

### Property 1: Site CRUD Round Trip

*For any* site with valid name, address, timezone, and contact information, creating the site and then retrieving it should return an equivalent site with all fields preserved and a unique ID assigned.

**Validates: Requirements 1.1, 1.2**

### Property 2: Device Site Assignment

*For any* device, updating its site_id to a valid site should result in the device being associated with that site, and the site's device list should include that device.

**Validates: Requirements 1.3, 1.5**

### Property 3: Site Deletion Protection

*For any* site that has at least one device assigned to it, attempting to delete that site should fail with an error, and the site should remain in the database.

**Validates: Requirements 1.6**

### Property 4: Empty Site Deletion

*For any* site that has no devices assigned to it, deleting that site should succeed, and the site should no longer be retrievable.

**Validates: Requirements 1.7**

### Property 5: Site Statistics Accuracy

*For any* site, the computed statistics (device count, online count, offline count, warning count) should equal the actual counts of devices in those states assigned to that site.

**Validates: Requirements 2.2**

### Property 6: Site Alert Filtering

*For any* site, querying alerts for that site should return only alerts associated with devices assigned to that site, and should not return alerts for devices at other sites.

**Validates: Requirements 2.3**

### Property 7: Site Metric Aggregation

*For any* site and metric type, the aggregated metric value for that site should equal the sum (or appropriate aggregation function) of that metric across all devices assigned to that site.

**Validates: Requirements 2.4**

### Property 8: Site Filtering Correctness

*For any* site filter applied to device lists, alert lists, or metric views, the results should contain only data for devices assigned to the specified site.

**Validates: Requirements 2.5, 3.4, 12.3**

### Property 9: WEF Event Parsing Completeness

*For any* valid Windows Event Forwarding print event (Event ID 307), parsing the event should extract all required fields (user, document_name, printer_name, timestamp, page_count) with no fields missing or null.

**Validates: Requirements 4.2**

### Property 10: Print Job Persistence

*For any* parsed print event (from WEF or syslog), storing it as a PrintJob should result in a retrievable record with all fields matching the parsed data and a valid printer_id foreign key.

**Validates: Requirements 4.3, 4.4, 5.3, 5.4**

### Property 11: Print Event Error Resilience

*For any* invalid or malformed print event (WEF or syslog), the collector should log the error, continue processing subsequent events, and not crash or stop the service.

**Validates: Requirements 4.5, 5.5**

### Property 12: CUPS Syslog Parsing Completeness

*For any* valid CUPS syslog message containing print job information, parsing the message should extract all required fields (user, document_name, printer_name, timestamp, page_count) with no fields missing or null.

**Validates: Requirements 5.2**

### Property 13: Printer SNMP Data Collection

*For any* network printer polled via SNMP, the collected data should include toner levels for all cartridges, total page count, printer status, and queue length, with each field having a valid value or explicit null if unavailable.

**Validates: Requirements 6.2, 6.3, 6.4, 6.5**

### Property 14: SNMP Poll Error Handling

*For any* SNMP poll that fails (timeout, authentication error, etc.), the system should log the error with error code and message, and the device should remain in the poll queue for the next cycle without crashing the worker.

**Validates: Requirements 6.6**

### Property 15: Printer Poll Interval Enforcement

*For any* printer with a configured poll interval between 1 and 60 minutes, the time between consecutive polls should be within the configured interval ± 10% tolerance.

**Validates: Requirements 6.7**

### Property 16: Print Job Filtering Correctness

*For any* combination of filters (date range, user, printer, site, department) applied to print job queries, the results should contain only print jobs matching all specified filter criteria.

**Validates: Requirements 7.2, 12.4, 12.5**

### Property 17: Print Job Sorting Correctness

*For any* print job list sorted by a field (timestamp, user, printer, page_count), the results should be in correct ascending or descending order according to that field's values.

**Validates: Requirements 7.3**

### Property 18: Print Job Page Count Aggregation

*For any* filtered set of print jobs, the total page count should equal the sum of the page_count field across all jobs in the filtered result set.

**Validates: Requirements 7.4, 14.4**

### Property 19: Print Job Retention Policy

*For any* print job with a timestamp older than 90 days, running the cleanup process should delete that job, while print jobs newer than 90 days should be retained.

**Validates: Requirements 7.5**

### Property 20: Print Job CSV Export Completeness

*For any* set of print jobs exported to CSV, the CSV should contain all required fields (user, document_name, printer, timestamp, page_count, site, department) for each job, with proper escaping and valid CSV format.

**Validates: Requirements 7.6**

### Property 21: Department CRUD Round Trip

*For any* department with valid name and description, creating the department and then retrieving it should return an equivalent department with all fields preserved and a unique ID assigned.

**Validates: Requirements 8.1, 8.2**

### Property 22: Device Department Association

*For any* device, the device should have either null department_id (no department) or a valid department_id referencing an existing department, but never an invalid foreign key.

**Validates: Requirements 8.3**

### Property 23: User Department Association

*For any* user, the user should have either null department_id (no department) or a valid department_id referencing an existing department, but never an invalid foreign key.

**Validates: Requirements 8.4**

### Property 24: Department Deletion Protection

*For any* department that has at least one device or user assigned to it, attempting to delete that department should fail with an error, and the department should remain in the database.

**Validates: Requirements 8.5**

### Property 25: Empty Department Deletion

*For any* department that has no devices or users assigned to it, deleting that department should succeed, and the department should no longer be retrievable.

**Validates: Requirements 8.6**

### Property 26: Department Filtering Correctness

*For any* department filter applied to device lists, alert lists, or print job lists, the results should contain only data for devices/jobs assigned to the specified department.

**Validates: Requirements 9.4**

### Property 27: RBAC Department Auto-Filtering

*For any* user with view_own_department permission set to true, all queries for devices, alerts, and print jobs should automatically filter to show only data from the user's assigned department, regardless of explicit filter parameters.

**Validates: Requirements 9.5, 10.1, 10.3**

### Property 28: RBAC All Departments Access

*For any* user with view_all_departments permission set to true, queries for devices, alerts, and print jobs should return data from all departments without filtering.

**Validates: Requirements 10.2, 10.4**

### Property 29: RBAC Cross-Department Access Denial

*For any* user with view_own_department=true attempting to access data from a department other than their assigned department, the system should return a 403 Forbidden or filter out the unauthorized data.

**Validates: Requirements 10.5**

### Property 30: API Site Deletion Conflict Response

*For any* DELETE request to /api/sites/{id} where the site has associated devices, the API should return HTTP 409 Conflict status code and the site should not be deleted.

**Validates: Requirements 11.6**

### Property 31: API Authentication Requirement

*For any* protected API endpoint (sites, departments, printers, print-jobs), a request without a valid API token should return HTTP 401 Unauthorized status code.

**Validates: Requirements 11.7, 13.6, 14.5, 15.7**

### Property 32: Device API Response Fields

*For any* device returned by the /api/devices endpoint, the response should include site_id and department_id fields (which may be null for backward compatibility).

**Validates: Requirements 12.6**

### Property 33: API Department Filtering Application

*For any* API request from a user with view_own_department permission, the printers and print-jobs endpoints should automatically apply department filtering based on the user's assigned department.

**Validates: Requirements 13.7, 14.6**

### Property 34: Print Jobs API Pagination

*For any* print jobs query with page and page_size parameters, the response should contain exactly page_size jobs (or fewer on the last page), and requesting all pages should return all matching jobs exactly once.

**Validates: Requirements 14.3**

### Property 35: API Department Deletion Conflict Response

*For any* DELETE request to /api/departments/{id} where the department has associated devices or users, the API should return HTTP 409 Conflict status code and the department should not be deleted.

**Validates: Requirements 15.6**

### Property 36: API Token Authentication Flow

*For any* valid API token provided in the Authorization header with Bearer scheme, the system should authenticate the request, load the associated user's permissions, and apply those permissions to the request.

**Validates: Requirements 16.3**

### Property 37: API Token Rejection

*For any* invalid API token (expired, revoked, malformed, or non-existent), the system should return HTTP 401 Unauthorized and not process the request.

**Validates: Requirements 16.4, 16.5**

### Property 38: API Token Revocation

*For any* API token that has been revoked, subsequent requests using that token should return HTTP 401 Unauthorized, even if the token was previously valid.

**Validates: Requirements 16.6**

### Property 39: API Token Storage Security

*For any* API token stored in the database, the stored value should be a one-way hash (SHA-256 or stronger), not the plaintext token, making it impossible to retrieve the original token from the database.

**Validates: Requirements 16.7**

### Property 40: Rate Limit Enforcement

*For any* API token, after making 1000 requests within a one-hour window, the next request should return HTTP 429 Too Many Requests status code.

**Validates: Requirements 17.1, 17.2**

### Property 41: Rate Limit Headers

*For any* API response, the response should include X-RateLimit-Limit, X-RateLimit-Remaining, and X-RateLimit-Reset headers with valid values.

**Validates: Requirements 17.3**

### Property 42: Rate Limit Reset

*For any* API token that has reached its rate limit, after the one-hour window expires, the request count should reset to 0 and requests should be allowed again.

**Validates: Requirements 17.4**

### Property 43: Rate Limit Independence

*For any* two different API tokens, the rate limit counter for one token should not affect the rate limit counter for the other token.

**Validates: Requirements 17.5**

### Property 44: Polling Node Registration

*For any* polling node registered with name, hostname, and optional site_id, the system should create a node record with a unique ID and a unique authentication token.

**Validates: Requirements 18.1, 18.2**

### Property 45: Polling Node Status Tracking

*For any* polling node, the system should store and update its last_heartbeat timestamp and status (online/offline) fields.

**Validates: Requirements 18.3**

### Property 46: Polling Node Deletion Protection

*For any* polling node that has at least one device assigned to it, attempting to deregister that node should fail with an error, and the node should remain in the database.

**Validates: Requirements 18.5**

### Property 47: Device Polling Node Assignment

*For any* device, the device should have either null polling_node_id (no node assigned) or a valid polling_node_id referencing an existing polling node, but never an invalid foreign key.

**Validates: Requirements 19.1**

### Property 48: Site-Based Auto-Assignment

*For any* device at a site where polling nodes exist, running auto-assignment should assign the device to a polling node at the same site.

**Validates: Requirements 19.2**

### Property 49: Device Reassignment

*For any* device assigned to a polling node, reassigning it to a different polling node should update the polling_node_id and the device should appear in the new node's device list.

**Validates: Requirements 19.5**

### Property 50: Polling Node Deregistration Cleanup

*For any* polling node that is deregistered, all devices previously assigned to that node should have their polling_node_id set to null.

**Validates: Requirements 19.6**

### Property 51: Metric Submission Authentication

*For any* metric submission from a polling node, the system should verify the node's authentication token before accepting the metrics.

**Validates: Requirements 20.2**

### Property 52: Metric Storage Completeness

*For any* metric submitted by an authenticated polling node, the system should store the metric with timestamp, device_id, metric_name, and value fields all populated.

**Validates: Requirements 20.3**

### Property 53: Heartbeat Timestamp Update

*For any* heartbeat received from a polling node, the system should update the node's last_heartbeat field to the current timestamp.

**Validates: Requirements 21.2**

### Property 54: Polling Node Offline Detection

*For any* polling node whose last_heartbeat is older than 5 minutes, the system should mark the node's status as 'offline'.

**Validates: Requirements 21.3**

### Property 55: Polling Node Offline Alert

*For any* polling node that transitions from 'online' to 'offline' status, the system should generate an alert for that node.

**Validates: Requirements 21.4**

### Property 56: Polling Node Recovery

*For any* polling node with status 'offline', receiving a heartbeat should update the status to 'online' and clear any offline alerts.

**Validates: Requirements 21.5**

### Property 57: Legacy Agent Compatibility

*For any* metric submission from server_agent.py or service.py version 1.0 or later, the system should accept the metrics even if site_id and department_id fields are missing, treating them as null.

**Validates: Requirements 22.1, 22.2, 22.3**

### Property 58: API Backward Compatibility

*For any* existing API endpoint that existed before Phase 1, the request and response formats should remain unchanged, with new fields added as optional.

**Validates: Requirements 22.4, 24.6**

### Property 59: Database Query Compatibility

*For any* existing database query that filters or retrieves devices, the query should return the same results when site_id and department_id are null as it did before these columns were added.

**Validates: Requirements 22.5, 24.7**

### Property 60: Existing Functionality Preservation

*For any* existing device type, alert rule, authentication method, or poll_tasks queue operation, the functionality should continue to work exactly as it did before Phase 1 changes.

**Validates: Requirements 24.1, 24.2, 24.4, 24.5**

## Error Handling

### Print Event Parsing Errors

**WEF Event Parsing:**
- Invalid XML: Log error with raw event, continue processing
- Missing required fields: Log warning, skip event, continue processing
- Unknown Event ID: Ignore silently (not a print event)
- Printer not found: Log warning with printer name, create orphaned PrintJob with printer_id=null

**CUPS Syslog Parsing:**
- Invalid syslog format: Log error with raw message, continue processing
- Missing required fields: Log warning, skip message, continue processing
- Printer not found: Log warning with printer name, create orphaned PrintJob with printer_id=null

### SNMP Polling Errors

**Error Codes:**
- `SNMP_TIMEOUT`: Device not responding, retry on next cycle
- `SNMP_AUTH_FAILED`: Invalid community string or credentials, mark in config
- `SNMP_NO_DATA`: Device responded but no metrics available
- `SNMP_PARSE_ERROR`: Response received but couldn't parse OID values
- `SNMP_UNKNOWN_ERROR`: Unexpected error, log full exception

**Error Handling:**
- Log error with code and message
- Update DeviceSnmpConfig.last_poll_error field
- Mark task as failed with retry (up to 3 retries with exponential backoff)
- Don't crash worker process
- Continue processing other devices

### API Errors

**Authentication Errors:**
- 401 Unauthorized: Missing, invalid, or revoked API token
- 403 Forbidden: Valid token but insufficient permissions (RBAC)

**Validation Errors:**
- 400 Bad Request: Invalid parameters, missing required fields
- 409 Conflict: Deletion prevented by foreign key constraints
- 422 Unprocessable Entity: Valid format but business logic violation

**Rate Limiting:**
- 429 Too Many Requests: Rate limit exceeded, include Retry-After header

**Server Errors:**
- 500 Internal Server Error: Unexpected exception, log full stack trace
- 503 Service Unavailable: Database connection failed, temporary issue

### Database Errors

**Foreign Key Violations:**
- Site deletion with devices: Raise ValueError, return 409 to API
- Department deletion with devices/users: Raise ValueError, return 409 to API
- Polling node deletion with devices: Raise ValueError, return 409 to API

**Unique Constraint Violations:**
- Duplicate site name: Raise ValueError, return 400 to API
- Duplicate department name: Raise ValueError, return 400 to API
- Duplicate polling node name: Raise ValueError, return 400 to API

**Transaction Handling:**
- Wrap all multi-step operations in transactions
- Rollback on any error
- Log error and return appropriate HTTP status code
- Clean up database session after error

### Polling Node Errors

**Heartbeat Timeout:**
- Mark node as offline after 5 minutes without heartbeat
- Generate alert for offline node
- Don't delete node or unassign devices automatically

**Metric Submission Errors:**
- Invalid authentication: Return 401, log attempt
- Invalid metric format: Return 400, log error
- Database error: Return 500, log error, don't lose metrics

**Node Deregistration:**
- Prevent if devices assigned: Return 409
- Unassign all devices before allowing deletion
- Revoke authentication token

## Testing Strategy

### Dual Testing Approach

This feature requires both unit tests and property-based tests for comprehensive coverage:

**Unit Tests:**
- Specific examples of print event parsing (WEF Event ID 307, CUPS syslog formats)
- API endpoint existence and basic functionality
- Database schema validation (table and column existence)
- Error handling for specific edge cases (empty strings, null values)
- Integration tests for WEF collector and syslog receiver

**Property-Based Tests:**
- Universal properties that hold for all inputs (see Correctness Properties section)
- Randomized testing of filtering, sorting, and aggregation logic
- RBAC permission enforcement across all endpoints
- Rate limiting behavior with random request patterns
- Backward compatibility with random legacy data

### Property-Based Testing Configuration

**Library:** Use `hypothesis` for Python property-based testing

**Test Configuration:**
- Minimum 100 iterations per property test (due to randomization)
- Each property test must reference its design document property
- Tag format: `# Feature: monitoring-phase-1-mvp, Property {number}: {property_text}`

**Example Property Test Structure:**
```python
from hypothesis import given, strategies as st

# Feature: monitoring-phase-1-mvp, Property 1: Site CRUD Round Trip
@given(
    name=st.text(min_size=1, max_size=100),
    address=st.text(max_size=500),
    timezone=st.text(min_size=1, max_size=50),
    contact_info=st.text(max_size=500)
)
def test_site_crud_round_trip(name, address, timezone, contact_info):
    """For any site with valid fields, create and retrieve should preserve all fields."""
    site = sites_service.create_site(name, address, timezone, contact_info)
    retrieved = sites_service.get_site(site.id)
    
    assert retrieved.id == site.id
    assert retrieved.name == name
    assert retrieved.address == address
    assert retrieved.timezone == timezone
    assert retrieved.contact_info == contact_info
```

### Unit Test Focus Areas

1. **Print Event Parsing:**
   - Valid WEF Event ID 307 XML parsing
   - Valid CUPS syslog message parsing
   - Invalid/malformed event handling
   - Missing field handling

2. **API Endpoints:**
   - GET /api/sites returns list
   - POST /api/sites creates site
   - DELETE /api/sites/{id} with devices returns 409
   - API token authentication on all endpoints

3. **Database Schema:**
   - Sites table exists with correct columns
   - Departments table exists with correct columns
   - PrintJobs table exists with correct columns
   - PollingNodes table exists with correct columns
   - Device table has site_id, department_id, polling_node_id columns

4. **SNMP Printer Polling:**
   - Printer MIB OID queries
   - Toner level calculation
   - Page count extraction
   - Status interpretation

5. **RBAC:**
   - view_own_department filters correctly
   - view_all_departments shows all data
   - Cross-department access denied

6. **Rate Limiting:**
   - 1000 requests allowed
   - 1001st request returns 429
   - Rate limit headers present
   - Counter resets after 1 hour

### Integration Tests

1. **WEF Collector:**
   - Start HTTP endpoint
   - Send test WEF event
   - Verify PrintJob created

2. **Syslog Receiver:**
   - Start UDP listener on port 514
   - Send test CUPS syslog message
   - Verify PrintJob created

3. **SNMP Worker:**
   - Enqueue printer_snmp task
   - Run worker
   - Verify metrics stored

4. **Polling Node:**
   - Register node
   - Send heartbeat
   - Submit metrics
   - Verify storage and status update

### Backward Compatibility Tests

1. **Legacy Agent Metrics:**
   - Submit metrics without site_id/department_id
   - Verify acceptance and null values

2. **Existing API Endpoints:**
   - Call existing endpoints with old request format
   - Verify response format unchanged (new fields optional)

3. **Existing Database Queries:**
   - Run queries with null site_id/department_id
   - Verify same results as before schema changes

4. **Poll Tasks Queue:**
   - Enqueue existing task types (snmp_health, interface)
   - Verify worker processes them correctly

### Performance Tests

While not part of unit/property tests, these should be validated manually:

1. **Print Event Processing:**
   - WEF collector processes events within 5 seconds
   - Syslog receiver processes messages within 5 seconds

2. **SNMP Polling:**
   - Worker processes 20 devices concurrently
   - Poll cycle completes within configured interval

3. **API Response Times:**
   - Endpoints respond within 200ms for typical queries
   - Pagination handles large result sets efficiently

4. **Rate Limiting:**
   - Rate limit check adds < 10ms overhead per request

### Test Data Generators

For property-based tests, use these generators:

```python
# Site generator
sites = st.builds(
    Site,
    name=st.text(min_size=1, max_size=100),
    address=st.text(max_size=500),
    timezone=st.sampled_from(['UTC', 'America/New_York', 'Europe/London']),
    contact_info=st.text(max_size=500)
)

# Department generator
departments = st.builds(
    Department,
    name=st.text(min_size=1, max_size=100),
    description=st.text(max_size=500)
)

# PrintJob generator
print_jobs = st.builds(
    PrintJob,
    user=st.text(min_size=1, max_size=100),
    document_name=st.text(min_size=1, max_size=255),
    printer_id=st.integers(min_value=1),
    timestamp=st.datetimes(),
    page_count=st.integers(min_value=1, max_value=10000),
    source=st.sampled_from(['wef', 'syslog', 'snmp'])
)

# API Token generator
api_tokens = st.builds(
    APIToken,
    user_id=st.integers(min_value=1),
    token_hash=st.text(min_size=64, max_size=64, alphabet='0123456789abcdef'),
    name=st.text(max_size=100)
)
```

### Test Coverage Goals

- Unit test coverage: > 80% of service layer code
- Property test coverage: All 60 correctness properties implemented
- Integration test coverage: All new components (WEF, syslog, SNMP printer)
- Backward compatibility: All existing functionality verified

### Continuous Integration

- Run unit tests on every commit
- Run property tests (100 iterations) on every commit
- Run integration tests on pull requests
- Run full property tests (1000 iterations) nightly
- Run backward compatibility tests before release

