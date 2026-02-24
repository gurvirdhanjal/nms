# Design Document: Enterprise Monitoring Expansion

## Overview

This design extends the Device Monitoring Tactical system with enterprise-grade capabilities for large-scale, multi-site deployments. The expansion adds printer monitoring (SNMP + print server agents), RTSP camera integration, multi-server agent deployment with centralized aggregation, distributed polling architecture, high availability, advanced alerting with escalation policies, compliance reporting, performance baselines, capacity forecasting, SLA tracking, ticketing system integration, custom dashboards, advanced RBAC with department isolation, bulk operations, comprehensive REST API, mobile-responsive UI, configuration import/export, and enterprise security enhancements.

### Design Goals

1. **Scalability**: Support thousands of devices across multiple sites with distributed polling
2. **High Availability**: Active-passive failover with PostgreSQL replication
3. **Enterprise Features**: Advanced alerting, compliance reporting, SLA tracking, capacity planning
4. **Backward Compatibility**: Existing agents (server_agent.py v1.0+, service.py v1.0+) continue working without upgrades
5. **Security**: AES-256 encryption for credentials, HTTPS enforcement, CSRF protection, input sanitization
6. **Extensibility**: Plugin architecture for new device types, webhook integrations, custom dashboards

### Architectural Principles

- **Scheduler/Worker Separation**: Scheduler enqueues tasks (no I/O), workers execute (SNMP, HTTP, database writes)
- **Device Identity Hierarchy**: UUID > MAC > Hostname > IP (existing pattern maintained)
- **Task Queue Pattern**: PollTask table with SELECT FOR UPDATE SKIP LOCKED for concurrency safety
- **Rollup Tables**: Hourly/daily aggregations for long-term metrics (existing pattern extended)
- **Strike Counters**: 3-strike rule for alert escalation (existing pattern maintained)
- **On-Premise Only**: No cloud dependencies, PostgreSQL required for production

## Architecture

### High-Level Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Flask Web Application                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │   Routes     │  │   Services   │  │  Middleware  │  │   Models    │ │
│  │  (REST API)  │  │  (Business)  │  │    (RBAC)    │  │ (SQLAlchemy)│ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
         ┌──────────▼──────────┐     ┌─────────▼──────────┐
         │   Scheduler         │     │   PostgreSQL DB    │
         │  (Task Enqueuer)    │     │  (Primary Store)   │
         └──────────┬──────────┘     └─────────┬──────────┘
                    │                           │
         ┌──────────▼──────────┐               │
         │   PollTask Queue    │◄──────────────┘
         │  (poll_tasks table) │
         └──────────┬──────────┘
                    │
      ┌─────────────┼─────────────┬─────────────┐
      │             │             │             │
┌─────▼─────┐ ┌────▼────┐  ┌─────▼─────┐ ┌────▼────┐
│SNMP Worker│ │Interface│  │  Camera   │ │Webhook  │
│  (Health) │ │ Worker  │  │  Worker   │ │ Worker  │
└─────┬─────┘ └────┬────┘  └─────┬─────┘ └────┬────┘
      │            │              │            │
      └────────────┴──────────────┴────────────┘
                    │
         ┌──────────▼──────────┐
         │  Monitored Devices  │
         │  ┌──────┐ ┌──────┐  │
         │  │SNMP  │ │Agent │  │
         │  │Device│ │Server│  │
         │  └──────┘ └──────┘  │
         │  ┌──────┐ ┌──────┐  │
         │  │Camera│ │Print │  │
         │  │(RTSP)│ │Server│  │
         │  └──────┘ └──────┘  │
         └─────────────────────┘
```


### Distributed Polling Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    Central Aggregator (HQ)                        │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────────┐   │
│  │  Web UI/API    │  │   Scheduler    │  │  PostgreSQL DB   │   │
│  └────────────────┘  └────────────────┘  └──────────────────┘   │
│                           │                        ▲              │
│                           │ Enqueue Tasks          │ Store Metrics│
│                           ▼                        │              │
│                    ┌──────────────┐                │              │
│                    │ PollTask     │                │              │
│                    │ Queue        │                │              │
│                    └──────────────┘                │              │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                ┌──────────────┼──────────────┐
                │              │              │
     ┌──────────▼────────┐ ┌──▼──────────┐ ┌─▼─────────────┐
     │ Polling Node 1    │ │Polling Node│ │Polling Node N │
     │  (Site A)         │ │  (Site B)  │ │  (Site C)     │
     │ ┌──────────────┐  │ │            │ │               │
     │ │SNMP Worker   │  │ │            │ │               │
     │ │Camera Worker │  │ │            │ │               │
     │ │Local Cache   │  │ │            │ │               │
     │ └──────────────┘  │ │            │ │               │
     │        │          │ │            │ │               │
     │        │ HTTPS    │ │   HTTPS    │ │    HTTPS      │
     │        │ Metrics  │ │   Metrics  │ │    Metrics    │
     │        └──────────┼─┴────────────┴─┴───────────────┘
     │                   │
     │  ┌────────────────▼──────────────┐
     │  │  Local Devices (Site A)       │
     │  │  Printers, Cameras, Servers   │
     │  └───────────────────────────────┘
     └───────────────────────────────────┘
```

### Multi-Site Topology

```
Site A (HQ)                    Site B (Branch)              Site C (Remote)
┌─────────────────┐           ┌─────────────────┐         ┌─────────────────┐
│ Central         │           │ Polling Node    │         │ Polling Node    │
│ Aggregator      │◄─────────►│ (Local Workers) │◄───────►│ (Local Workers) │
│ + Polling Node  │  HTTPS    │                 │ HTTPS   │                 │
└────────┬────────┘           └────────┬────────┘         └────────┬────────┘
         │                              │                           │
    ┌────┴────┐                    ┌───┴────┐                 ┌────┴────┐
    │Devices  │                    │Devices │                 │Devices  │
    │(50)     │                    │(30)    │                 │(20)     │
    └─────────┘                    └────────┘                 └─────────┘
```


## Components and Interfaces

### New Models (Database Schema)

#### 1. Printer Metrics (Requirement 1)

```python
class PrinterMetrics(db.Model):
    __tablename__ = 'printer_metrics'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False, index=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Status
    status = db.Column(db.String(50), nullable=True)  # idle, printing, error, offline
    status_code = db.Column(db.Integer, nullable=True)  # RFC 3805 hrPrinterStatus
    
    # Consumables (percentage 0-100)
    toner_black = db.Column(db.Integer, nullable=True)
    toner_cyan = db.Column(db.Integer, nullable=True)
    toner_magenta = db.Column(db.Integer, nullable=True)
    toner_yellow = db.Column(db.Integer, nullable=True)
    
    # Paper
    paper_tray_status = db.Column(db.JSON, nullable=True)  # {tray_id: status}
    
    # Counters
    page_count_total = db.Column(db.BigInteger, nullable=True)
    page_count_color = db.Column(db.BigInteger, nullable=True)
    page_count_bw = db.Column(db.BigInteger, nullable=True)
    job_queue_length = db.Column(db.Integer, nullable=True)
    
    # Relationships
    device = db.relationship('Device', backref=db.backref('printer_metrics', lazy='dynamic'))
    
    __table_args__ = (
        db.Index('idx_printer_metrics_device_timestamp', 'device_id', 'timestamp'),
    )
```

#### 2. Print Job Audit (Requirements 2, 3)

```python
class PrintJobAudit(db.Model):
    __tablename__ = 'print_job_audit'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False, index=True)
    print_server_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=True, index=True)
    
    # Job Identity
    job_id = db.Column(db.String(100), nullable=False, index=True)
    document_name = db.Column(db.String(500), nullable=True)
    
    # User Context
    user_account = db.Column(db.String(200), nullable=True, index=True)
    source_ip = db.Column(db.String(50), nullable=True, index=True)
    workstation_device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='SET NULL'), nullable=True)
    
    # Printer
    printer_name = db.Column(db.String(200), nullable=False, index=True)
    
    # Metrics
    page_count = db.Column(db.Integer, nullable=True)
    
    # Timestamps
    submission_time = db.Column(db.DateTime, nullable=False, index=True)
    completion_time = db.Column(db.DateTime, nullable=True)
    
    # Status
    status = db.Column(db.String(50), nullable=True)  # submitted, printing, completed, failed, cancelled
    
    # Relationships
    device = db.relationship('Device', foreign_keys=[device_id], backref=db.backref('print_jobs', lazy='dynamic'))
    print_server = db.relationship('Device', foreign_keys=[print_server_id])
    workstation = db.relationship('Device', foreign_keys=[workstation_device_id])
    
    __table_args__ = (
        db.Index('idx_print_job_user_time', 'user_account', 'submission_time'),
        db.Index('idx_print_job_ip_time', 'source_ip', 'submission_time'),
        db.Index('idx_print_job_printer_time', 'printer_name', 'submission_time'),
    )
```


#### 3. Camera Devices and Frame Storage (Requirements 7, 8)

```python
class CameraDevice(db.Model):
    __tablename__ = 'camera_devices'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    
    # RTSP Configuration
    rtsp_url = db.Column(db.String(500), nullable=False)  # rtsp://[user:pass@]host[:port]/path
    rtsp_username = db.Column(db.String(100), nullable=True)
    rtsp_password_encrypted = db.Column(db.LargeBinary, nullable=True)  # AES-256 encrypted
    transport_protocol = db.Column(db.String(10), default='tcp')  # tcp, udp
    
    # Frame Capture Settings
    capture_enabled = db.Column(db.Boolean, default=True)
    capture_interval_seconds = db.Column(db.Integer, default=60)
    
    # Status
    last_successful_connection = db.Column(db.DateTime, nullable=True)
    last_connection_error = db.Column(db.Text, nullable=True)
    consecutive_failures = db.Column(db.Integer, default=0)
    
    # Relationships
    device = db.relationship('Device', backref=db.backref('camera_config', uselist=False))


class CameraFrame(db.Model):
    __tablename__ = 'camera_frames'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Frame Metadata
    capture_timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    file_path = db.Column(db.String(500), nullable=False)  # static/camera_frames/{device_id}/{timestamp}.jpg
    file_size_bytes = db.Column(db.Integer, nullable=True)
    resolution = db.Column(db.String(20), nullable=True)  # "1920x1080"
    
    # Relationships
    device = db.relationship('Device', backref=db.backref('camera_frames', lazy='dynamic'))
    
    __table_args__ = (
        db.Index('idx_camera_frame_device_timestamp', 'device_id', 'capture_timestamp'),
    )
```

#### 4. Sites (Requirement 9)

```python
class Site(db.Model):
    __tablename__ = 'sites'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    site_name = db.Column(db.String(200), nullable=False, unique=True, index=True)
    site_code = db.Column(db.String(50), nullable=True, unique=True, index=True)
    
    # Location
    address = db.Column(db.Text, nullable=True)
    timezone = db.Column(db.String(50), default='UTC')
    
    # Contact
    contact_name = db.Column(db.String(200), nullable=True)
    contact_email = db.Column(db.String(200), nullable=True)
    contact_phone = db.Column(db.String(50), nullable=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    devices = db.relationship('Device', backref='site', lazy='dynamic')
    polling_nodes = db.relationship('PollingNode', backref='site', lazy='dynamic')
```


#### 5. Polling Nodes (Requirement 10)

```python
class PollingNode(db.Model):
    __tablename__ = 'polling_nodes'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    node_name = db.Column(db.String(200), nullable=False, unique=True, index=True)
    node_uuid = db.Column(db.String(36), nullable=False, unique=True, index=True)
    
    # Network
    api_endpoint = db.Column(db.String(500), nullable=False)  # https://node.site-a.local:5002
    api_token_hash = db.Column(db.String(256), nullable=False)  # bcrypt hash
    
    # Assignment
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id', ondelete='SET NULL'), nullable=True, index=True)
    subnet_scope = db.Column(db.JSON, nullable=True)  # ["10.1.0.0/24", "10.1.1.0/24"]
    
    # Status
    status = db.Column(db.String(20), default='offline', index=True)  # online, offline, degraded
    last_heartbeat = db.Column(db.DateTime, nullable=True, index=True)
    metrics_queue_depth = db.Column(db.Integer, default=0)
    error_count = db.Column(db.Integer, default=0)
    
    # Configuration
    enabled = db.Column(db.Boolean, default=True)
    priority = db.Column(db.Integer, default=5)  # Lower = higher priority
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PollingNodeAssignment(db.Model):
    __tablename__ = 'polling_node_assignments'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False, index=True)
    polling_node_id = db.Column(db.Integer, db.ForeignKey('polling_nodes.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Assignment Method
    assignment_method = db.Column(db.String(20), nullable=False)  # site, subnet, manual
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    device = db.relationship('Device', backref=db.backref('polling_assignments', lazy='dynamic'))
    polling_node = db.relationship('PollingNode', backref=db.backref('device_assignments', lazy='dynamic'))
    
    __table_args__ = (
        db.UniqueConstraint('device_id', 'polling_node_id', name='uq_device_polling_node'),
    )
```

#### 6. Alert Escalation Policies (Requirement 12)

```python
class AlertEscalationPolicy(db.Model):
    __tablename__ = 'alert_escalation_policies'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    policy_name = db.Column(db.String(200), nullable=False, unique=True, index=True)
    
    # Filters
    severity_filter = db.Column(db.JSON, nullable=True)  # ["CRITICAL", "WARNING"]
    device_type_filter = db.Column(db.JSON, nullable=True)  # ["server", "printer"]
    
    # Status
    enabled = db.Column(db.Boolean, default=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    levels = db.relationship('EscalationLevel', backref='policy', lazy='dynamic', cascade='all, delete-orphan')


class EscalationLevel(db.Model):
    __tablename__ = 'escalation_levels'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    policy_id = db.Column(db.Integer, db.ForeignKey('alert_escalation_policies.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Level Configuration
    level_number = db.Column(db.Integer, nullable=False)  # 1, 2, 3...
    delay_minutes = db.Column(db.Integer, nullable=False)  # Delay from previous level
    
    # Notification
    recipients = db.Column(db.JSON, nullable=False)  # ["admin@example.com", "oncall@example.com"]
    notification_methods = db.Column(db.JSON, nullable=False)  # ["email", "webhook"]
    webhook_ids = db.Column(db.JSON, nullable=True)  # [1, 2, 3] - references WebhookIntegration.id
    
    __table_args__ = (
        db.UniqueConstraint('policy_id', 'level_number', name='uq_policy_level'),
    )


class AlertEscalationState(db.Model):
    __tablename__ = 'alert_escalation_states'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    alert_id = db.Column(db.Integer, nullable=False, index=True)  # References alert system (not modeled here)
    policy_id = db.Column(db.Integer, db.ForeignKey('alert_escalation_policies.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Current State
    current_level = db.Column(db.Integer, default=1)
    next_escalation_time = db.Column(db.DateTime, nullable=True, index=True)
    
    # Status
    acknowledged = db.Column(db.Boolean, default=False)
    acknowledged_by = db.Column(db.String(200), nullable=True)
    acknowledged_at = db.Column(db.DateTime, nullable=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    policy = db.relationship('AlertEscalationPolicy', backref=db.backref('escalation_states', lazy='dynamic'))
```


#### 7. Compliance Reporting (Requirement 13)

```python
class ComplianceReport(db.Model):
    __tablename__ = 'compliance_reports'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    report_name = db.Column(db.String(200), nullable=False, index=True)
    report_type = db.Column(db.String(50), nullable=False, index=True)  # access_audit, change_log, alert_history, uptime, security_event
    
    # Report Configuration
    template_name = db.Column(db.String(100), nullable=True)  # SOC2, ISO27001, HIPAA, PCI_DSS
    report_parameters = db.Column(db.JSON, nullable=True)  # Filters, date ranges, etc.
    
    # Schedule
    schedule_enabled = db.Column(db.Boolean, default=False)
    schedule_frequency = db.Column(db.String(20), nullable=True)  # daily, weekly, monthly
    schedule_day_of_week = db.Column(db.Integer, nullable=True)  # 0-6 for weekly
    schedule_day_of_month = db.Column(db.Integer, nullable=True)  # 1-31 for monthly
    schedule_time = db.Column(db.Time, nullable=True)
    
    # Delivery
    delivery_emails = db.Column(db.JSON, nullable=True)  # ["compliance@example.com"]
    
    # Metadata
    created_by = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    executions = db.relationship('ComplianceReportExecution', backref='report', lazy='dynamic', cascade='all, delete-orphan')


class ComplianceReportExecution(db.Model):
    __tablename__ = 'compliance_report_executions'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    report_id = db.Column(db.Integer, db.ForeignKey('compliance_reports.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Execution Details
    generation_timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    report_period_start = db.Column(db.DateTime, nullable=False)
    report_period_end = db.Column(db.DateTime, nullable=False)
    generated_by = db.Column(db.String(200), nullable=True)
    
    # Output
    file_path_pdf = db.Column(db.String(500), nullable=True)
    file_path_excel = db.Column(db.String(500), nullable=True)
    file_size_bytes = db.Column(db.Integer, nullable=True)
    
    # Status
    status = db.Column(db.String(20), default='pending')  # pending, generating, completed, failed
    error_message = db.Column(db.Text, nullable=True)
    
    # Access Audit
    access_log = db.relationship('ComplianceReportAccess', backref='execution', lazy='dynamic', cascade='all, delete-orphan')


class ComplianceReportAccess(db.Model):
    __tablename__ = 'compliance_report_access'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    execution_id = db.Column(db.Integer, db.ForeignKey('compliance_report_executions.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Access Details
    accessed_by = db.Column(db.String(200), nullable=False, index=True)
    accessed_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    access_method = db.Column(db.String(50), nullable=True)  # download, view, email
    source_ip = db.Column(db.String(50), nullable=True)
```


#### 8. Performance Baselines and Anomaly Detection (Requirement 14)

```python
class PerformanceBaseline(db.Model):
    __tablename__ = 'performance_baselines'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False, index=True)
    metric_name = db.Column(db.String(100), nullable=False, index=True)  # cpu_usage, memory_usage, disk_usage
    
    # Baseline Statistics (30-day rolling window)
    mean_value = db.Column(db.Float, nullable=True)
    std_deviation = db.Column(db.Float, nullable=True)
    min_value = db.Column(db.Float, nullable=True)
    max_value = db.Column(db.Float, nullable=True)
    sample_count = db.Column(db.Integer, nullable=True)
    
    # Anomaly Detection Configuration
    sensitivity = db.Column(db.String(20), default='medium')  # low (4σ), medium (3σ), high (2σ)
    enabled = db.Column(db.Boolean, default=True)
    
    # Metadata
    last_calculated = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    calculation_period_start = db.Column(db.DateTime, nullable=True)
    calculation_period_end = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    device = db.relationship('Device', backref=db.backref('performance_baselines', lazy='dynamic'))
    
    __table_args__ = (
        db.UniqueConstraint('device_id', 'metric_name', name='uq_device_metric_baseline'),
    )


class AnomalyDetection(db.Model):
    __tablename__ = 'anomaly_detections'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False, index=True)
    baseline_id = db.Column(db.Integer, db.ForeignKey('performance_baselines.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Detection Details
    detected_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    metric_name = db.Column(db.String(100), nullable=False)
    current_value = db.Column(db.Float, nullable=False)
    baseline_mean = db.Column(db.Float, nullable=False)
    deviation_sigma = db.Column(db.Float, nullable=False)  # How many standard deviations
    
    # Status
    consecutive_anomalies = db.Column(db.Integer, default=1)
    alert_generated = db.Column(db.Boolean, default=False)
    resolved = db.Column(db.Boolean, default=False)
    resolved_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    device = db.relationship('Device', backref=db.backref('anomaly_detections', lazy='dynamic'))
    baseline = db.relationship('PerformanceBaseline', backref=db.backref('anomalies', lazy='dynamic'))
```

#### 9. Capacity Planning and Forecasting (Requirement 15)

```python
class CapacityForecast(db.Model):
    __tablename__ = 'capacity_forecasts'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False, index=True)
    resource_type = db.Column(db.String(50), nullable=False, index=True)  # disk, memory, cpu
    
    # Current State
    current_utilization = db.Column(db.Float, nullable=True)
    current_capacity = db.Column(db.Float, nullable=True)
    
    # Forecast (Linear Regression on 90-day data)
    growth_rate_per_day = db.Column(db.Float, nullable=True)  # Percentage points per day
    forecasted_exhaustion_date = db.Column(db.Date, nullable=True, index=True)
    days_until_exhaustion = db.Column(db.Integer, nullable=True)
    confidence_score = db.Column(db.Float, nullable=True)  # R² value
    
    # Thresholds
    warning_threshold = db.Column(db.Float, default=90.0)  # Alert when forecast < 30 days
    critical_threshold = db.Column(db.Float, default=95.0)  # Alert when forecast < 7 days
    
    # Status
    alert_level = db.Column(db.String(20), nullable=True)  # none, warning, critical
    
    # Metadata
    calculated_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    calculation_period_start = db.Column(db.DateTime, nullable=True)
    calculation_period_end = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    device = db.relationship('Device', backref=db.backref('capacity_forecasts', lazy='dynamic'))
    
    __table_args__ = (
        db.UniqueConstraint('device_id', 'resource_type', name='uq_device_resource_forecast'),
    )
```


#### 10. SLA Tracking (Requirement 16)

```python
class SLAMetric(db.Model):
    __tablename__ = 'sla_metrics'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    metric_name = db.Column(db.String(200), nullable=False, unique=True, index=True)
    
    # Metric Configuration
    metric_type = db.Column(db.String(50), nullable=False)  # uptime_percentage, avg_response_time, max_downtime, alert_resolution_time
    target_value = db.Column(db.Float, nullable=False)
    measurement_period = db.Column(db.String(20), nullable=False)  # monthly, quarterly
    
    # Scope
    device_ids = db.Column(db.JSON, nullable=True)  # Specific devices, or null for all
    device_types = db.Column(db.JSON, nullable=True)  # ["server", "printer"]
    site_ids = db.Column(db.JSON, nullable=True)  # Specific sites
    
    # Status
    enabled = db.Column(db.Boolean, default=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    measurements = db.relationship('SLAMeasurement', backref='metric', lazy='dynamic', cascade='all, delete-orphan')


class SLAMeasurement(db.Model):
    __tablename__ = 'sla_measurements'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    metric_id = db.Column(db.Integer, db.ForeignKey('sla_metrics.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Measurement Period
    period_start = db.Column(db.DateTime, nullable=False, index=True)
    period_end = db.Column(db.DateTime, nullable=False, index=True)
    
    # Results
    actual_value = db.Column(db.Float, nullable=False)
    target_value = db.Column(db.Float, nullable=False)
    compliance_percentage = db.Column(db.Float, nullable=False)  # (actual/target) * 100
    
    # Status
    status = db.Column(db.String(20), nullable=False, index=True)  # meeting, at_risk, breached
    breach_count = db.Column(db.Integer, default=0)
    
    # Metadata
    calculated_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.Index('idx_sla_measurement_metric_period', 'metric_id', 'period_start', 'period_end'),
    )


class SLABreach(db.Model):
    __tablename__ = 'sla_breaches'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    measurement_id = db.Column(db.Integer, db.ForeignKey('sla_measurements.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Breach Details
    breach_timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    breach_description = db.Column(db.Text, nullable=True)
    affected_devices = db.Column(db.JSON, nullable=True)  # [device_id, ...]
    
    # Impact
    downtime_minutes = db.Column(db.Integer, nullable=True)
    
    # Relationships
    measurement = db.relationship('SLAMeasurement', backref=db.backref('breaches', lazy='dynamic'))
```


#### 11. Webhook Integration (Requirement 17)

```python
class WebhookIntegration(db.Model):
    __tablename__ = 'webhook_integrations'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    webhook_name = db.Column(db.String(200), nullable=False, unique=True, index=True)
    
    # Endpoint Configuration
    webhook_url = db.Column(db.String(500), nullable=False)
    http_method = db.Column(db.String(10), default='POST')  # POST, PUT
    
    # Authentication
    auth_method = db.Column(db.String(20), default='none')  # none, basic, bearer, custom
    auth_username = db.Column(db.String(200), nullable=True)
    auth_password_encrypted = db.Column(db.LargeBinary, nullable=True)  # AES-256 encrypted
    auth_token_encrypted = db.Column(db.LargeBinary, nullable=True)  # AES-256 encrypted
    custom_headers = db.Column(db.JSON, nullable=True)  # {"X-API-Key": "value"}
    
    # Payload Configuration
    payload_template = db.Column(db.Text, nullable=True)  # Jinja2 template
    content_type = db.Column(db.String(100), default='application/json')
    
    # Trigger Configuration
    trigger_on_severities = db.Column(db.JSON, nullable=True)  # ["CRITICAL", "WARNING"]
    trigger_on_device_types = db.Column(db.JSON, nullable=True)  # ["server", "printer"]
    
    # Retry Configuration
    max_retries = db.Column(db.Integer, default=3)
    retry_backoff_seconds = db.Column(db.Integer, default=60)
    
    # TLS Configuration
    verify_tls = db.Column(db.Boolean, default=True)
    
    # Status
    enabled = db.Column(db.Boolean, default=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    deliveries = db.relationship('WebhookDelivery', backref='webhook', lazy='dynamic', cascade='all, delete-orphan')


class WebhookDelivery(db.Model):
    __tablename__ = 'webhook_deliveries'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    webhook_id = db.Column(db.Integer, db.ForeignKey('webhook_integrations.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Trigger Context
    alert_id = db.Column(db.Integer, nullable=True, index=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='SET NULL'), nullable=True, index=True)
    
    # Delivery Details
    attempt_number = db.Column(db.Integer, default=1)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Request
    request_payload = db.Column(db.Text, nullable=True)
    request_headers = db.Column(db.JSON, nullable=True)
    
    # Response
    response_status_code = db.Column(db.Integer, nullable=True)
    response_body = db.Column(db.Text, nullable=True)
    response_time_ms = db.Column(db.Integer, nullable=True)
    
    # Status
    status = db.Column(db.String(20), nullable=False, index=True)  # success, failed, retrying
    error_message = db.Column(db.Text, nullable=True)
    
    # Relationships
    device = db.relationship('Device', backref=db.backref('webhook_deliveries', lazy='dynamic'))
```


#### 12. Custom Dashboards (Requirement 18)

```python
class CustomDashboard(db.Model):
    __tablename__ = 'custom_dashboards'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    dashboard_name = db.Column(db.String(200), nullable=False, index=True)
    
    # Ownership
    owner_user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    visibility = db.Column(db.String(20), default='private')  # private, shared, public
    
    # Layout Configuration
    layout_config = db.Column(db.JSON, nullable=False)  # Grid layout: [{widget_id, x, y, w, h}, ...]
    
    # Metadata
    is_default = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    owner = db.relationship('User', backref=db.backref('dashboards', lazy='dynamic'))
    widgets = db.relationship('DashboardWidget', backref='dashboard', lazy='dynamic', cascade='all, delete-orphan')
    shares = db.relationship('DashboardShare', backref='dashboard', lazy='dynamic', cascade='all, delete-orphan')


class DashboardWidget(db.Model):
    __tablename__ = 'dashboard_widgets'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    dashboard_id = db.Column(db.Integer, db.ForeignKey('custom_dashboards.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Widget Configuration
    widget_type = db.Column(db.String(50), nullable=False)  # device_status, alert_list, performance_chart, capacity_gauge, sla_status
    widget_title = db.Column(db.String(200), nullable=True)
    
    # Data Filters
    filter_config = db.Column(db.JSON, nullable=True)  # {site_id: 1, device_type: "server", department_id: 5}
    
    # Display Configuration
    display_config = db.Column(db.JSON, nullable=True)  # Chart colors, refresh interval, etc.
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class DashboardShare(db.Model):
    __tablename__ = 'dashboard_shares'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    dashboard_id = db.Column(db.Integer, db.ForeignKey('custom_dashboards.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Share Target
    shared_with_user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=True, index=True)
    shared_with_role = db.Column(db.String(50), nullable=True)  # admin, user, manager
    
    # Permissions
    can_edit = db.Column(db.Boolean, default=False)
    
    # Metadata
    shared_at = db.Column(db.DateTime, default=datetime.utcnow)
    shared_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    
    # Relationships
    shared_with_user = db.relationship('User', foreign_keys=[shared_with_user_id], backref=db.backref('shared_dashboards', lazy='dynamic'))
    shared_by_user = db.relationship('User', foreign_keys=[shared_by])
```


#### 13. Departments and RBAC (Requirement 19)

```python
class Department(db.Model):
    __tablename__ = 'departments'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    department_name = db.Column(db.String(200), nullable=False, unique=True, index=True)
    department_code = db.Column(db.String(50), nullable=True, unique=True, index=True)
    
    # Hierarchy
    parent_department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='SET NULL'), nullable=True, index=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    parent = db.relationship('Department', remote_side=[id], backref=db.backref('child_departments', lazy='dynamic'))
    devices = db.relationship('Device', backref='department', lazy='dynamic')
    users = db.relationship('User', backref='department', lazy='dynamic')


class UserDepartmentAssignment(db.Model):
    __tablename__ = 'user_department_assignments'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Permissions
    role_in_department = db.Column(db.String(50), nullable=False)  # viewer, manager, admin
    can_view_children = db.Column(db.Boolean, default=False)  # Hierarchical access
    
    # Metadata
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    assigned_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    
    # Relationships
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('department_assignments', lazy='dynamic'))
    department = db.relationship('Department', backref=db.backref('user_assignments', lazy='dynamic'))
    assigner = db.relationship('User', foreign_keys=[assigned_by])
    
    __table_args__ = (
        db.UniqueConstraint('user_id', 'department_id', name='uq_user_department'),
    )


# Extend existing Device model with department_id
# Device.department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='SET NULL'), nullable=True, index=True)

# Extend existing User model with department_isolation flag
# User.department_isolation_enabled = db.Column(db.Boolean, default=False)
```

#### 14. Bulk Operations (Requirement 20)

```python
class BulkOperation(db.Model):
    __tablename__ = 'bulk_operations'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    operation_type = db.Column(db.String(50), nullable=False, index=True)  # enable_monitoring, disable_monitoring, set_maintenance, assign_site, assign_department, delete_devices
    
    # Execution Context
    initiated_by = db.Column(db.String(200), nullable=False, index=True)
    initiated_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Target Devices
    device_ids = db.Column(db.JSON, nullable=False)  # [1, 2, 3, ...]
    device_count = db.Column(db.Integer, nullable=False)
    
    # Operation Parameters
    operation_parameters = db.Column(db.JSON, nullable=True)  # {site_id: 5, maintenance_mode: true}
    
    # Status
    status = db.Column(db.String(20), default='pending', index=True)  # pending, running, completed, failed
    progress_current = db.Column(db.Integer, default=0)
    progress_total = db.Column(db.Integer, nullable=False)
    
    # Results
    successful_count = db.Column(db.Integer, default=0)
    failed_count = db.Column(db.Integer, default=0)
    error_summary = db.Column(db.JSON, nullable=True)  # [{device_id: 1, error: "msg"}, ...]
    
    # Timing
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    results = db.relationship('BulkOperationResult', backref='operation', lazy='dynamic', cascade='all, delete-orphan')


class BulkOperationResult(db.Model):
    __tablename__ = 'bulk_operation_results'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    operation_id = db.Column(db.Integer, db.ForeignKey('bulk_operations.id', ondelete='CASCADE'), nullable=False, index=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Result
    success = db.Column(db.Boolean, nullable=False)
    error_message = db.Column(db.Text, nullable=True)
    
    # Timing
    processed_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    device = db.relationship('Device', backref=db.backref('bulk_operation_results', lazy='dynamic'))
```


#### 15. API Tokens and Rate Limiting (Requirement 21)

```python
class APIToken(db.Model):
    __tablename__ = 'api_tokens'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Token
    token_name = db.Column(db.String(200), nullable=False)
    token_hash = db.Column(db.String(256), nullable=False, unique=True, index=True)  # bcrypt hash
    token_prefix = db.Column(db.String(10), nullable=False)  # First 8 chars for display
    
    # Permissions
    scopes = db.Column(db.JSON, nullable=True)  # ["devices:read", "devices:write", "alerts:read"]
    
    # Rate Limiting
    rate_limit_per_hour = db.Column(db.Integer, default=1000)
    
    # Status
    enabled = db.Column(db.Boolean, default=True)
    last_used_at = db.Column(db.DateTime, nullable=True)
    
    # Expiration
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('api_tokens', lazy='dynamic'))
    requests = db.relationship('APIRequest', backref='token', lazy='dynamic', cascade='all, delete-orphan')


class APIRequest(db.Model):
    __tablename__ = 'api_requests'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    token_id = db.Column(db.Integer, db.ForeignKey('api_tokens.id', ondelete='CASCADE'), nullable=True, index=True)
    
    # Request Details
    endpoint = db.Column(db.String(500), nullable=False, index=True)
    http_method = db.Column(db.String(10), nullable=False)
    user_agent = db.Column(db.String(500), nullable=True)
    source_ip = db.Column(db.String(50), nullable=True, index=True)
    
    # Response
    response_status = db.Column(db.Integer, nullable=False, index=True)
    response_time_ms = db.Column(db.Integer, nullable=True)
    
    # Metadata
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    __table_args__ = (
        db.Index('idx_api_request_token_timestamp', 'token_id', 'timestamp'),
    )


class RateLimitBucket(db.Model):
    __tablename__ = 'rate_limit_buckets'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    token_id = db.Column(db.Integer, db.ForeignKey('api_tokens.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Bucket (sliding window)
    window_start = db.Column(db.DateTime, nullable=False, index=True)
    request_count = db.Column(db.Integer, default=0)
    
    # Metadata
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    token = db.relationship('APIToken', backref=db.backref('rate_limit_buckets', lazy='dynamic'))
    
    __table_args__ = (
        db.UniqueConstraint('token_id', 'window_start', name='uq_token_window'),
    )
```


#### 16. Configuration Import/Export (Requirement 23)

```python
class ConfigurationSnapshot(db.Model):
    __tablename__ = 'configuration_snapshots'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    snapshot_name = db.Column(db.String(200), nullable=False, index=True)
    
    # Snapshot Content
    configuration_json = db.Column(db.Text, nullable=False)  # Full JSON export
    configuration_hash = db.Column(db.String(64), nullable=False)  # SHA-256 hash for integrity
    
    # Scope
    includes_sites = db.Column(db.Boolean, default=True)
    includes_departments = db.Column(db.Boolean, default=True)
    includes_devices = db.Column(db.Boolean, default=True)
    includes_alert_policies = db.Column(db.Boolean, default=True)
    includes_sla_metrics = db.Column(db.Boolean, default=True)
    includes_rbac_roles = db.Column(db.Boolean, default=True)
    
    # Metadata
    created_by = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    file_size_bytes = db.Column(db.Integer, nullable=True)
    
    # Import Tracking
    import_history = db.relationship('ConfigurationImport', backref='snapshot', lazy='dynamic', cascade='all, delete-orphan')


class ConfigurationImport(db.Model):
    __tablename__ = 'configuration_imports'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    snapshot_id = db.Column(db.Integer, db.ForeignKey('configuration_snapshots.id', ondelete='CASCADE'), nullable=True, index=True)
    
    # Import Details
    imported_by = db.Column(db.String(200), nullable=False, index=True)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Import Mode
    import_mode = db.Column(db.String(20), nullable=False)  # full, partial, merge
    sections_imported = db.Column(db.JSON, nullable=True)  # ["sites", "devices"]
    
    # Validation
    validation_errors = db.Column(db.JSON, nullable=True)  # [{field: "site_name", error: "duplicate"}]
    validation_warnings = db.Column(db.JSON, nullable=True)
    
    # Results
    status = db.Column(db.String(20), nullable=False, index=True)  # success, failed, partial
    items_created = db.Column(db.Integer, default=0)
    items_updated = db.Column(db.Integer, default=0)
    items_failed = db.Column(db.Integer, default=0)
    error_summary = db.Column(db.Text, nullable=True)
```

#### 17. Device Model Extensions

```python
# Extensions to existing Device model for enterprise features

# Add to Device model:
# site_id = db.Column(db.Integer, db.ForeignKey('sites.id', ondelete='SET NULL'), nullable=True, index=True)
# department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='SET NULL'), nullable=True, index=True)
# agent_version = db.Column(db.String(20), nullable=True)  # For backward compatibility tracking
# last_agent_checkin = db.Column(db.DateTime, nullable=True, index=True)  # For agent health monitoring
```

#### 18. User Model Extensions

```python
# Extensions to existing User model for enterprise features

# Add to User model:
# department_isolation_enabled = db.Column(db.Boolean, default=False)
# default_dashboard_id = db.Column(db.Integer, db.ForeignKey('custom_dashboards.id', ondelete='SET NULL'), nullable=True)
# session_timeout_minutes = db.Column(db.Integer, default=30)
# password_last_changed = db.Column(db.DateTime, nullable=True)
# password_complexity_required = db.Column(db.Boolean, default=True)
```


## Data Models

### Entity Relationship Diagram

```
┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│    Site      │◄───────┤    Device    ├────────►│  Department  │
└──────┬───────┘         └──────┬───────┘         └──────────────┘
       │                        │
       │                        ├────────► PrinterMetrics
       │                        ├────────► PrintJobAudit
       │                        ├────────► CameraDevice ──► CameraFrame
       │                        ├────────► PerformanceBaseline ──► AnomalyDetection
       │                        ├────────► CapacityForecast
       │                        └────────► PollingNodeAssignment
       │
       ▼
┌──────────────┐
│ PollingNode  │
└──────────────┘

┌──────────────────────┐         ┌──────────────────┐
│AlertEscalationPolicy │────────►│ EscalationLevel  │
└──────────┬───────────┘         └──────────────────┘
           │
           ▼
┌──────────────────────┐
│AlertEscalationState  │
└──────────────────────┘

┌──────────────────┐         ┌────────────────────────┐
│   SLAMetric      │────────►│   SLAMeasurement       │
└──────────────────┘         └────────┬───────────────┘
                                      │
                                      ▼
                             ┌────────────────┐
                             │   SLABreach    │
                             └────────────────┘

┌──────────────────────┐         ┌──────────────────────┐
│WebhookIntegration    │────────►│  WebhookDelivery     │
└──────────────────────┘         └──────────────────────┘

┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
│      User        │────────►│CustomDashboard   │────────►│ DashboardWidget  │
└────────┬─────────┘         └────────┬─────────┘         └──────────────────┘
         │                            │
         │                            ▼
         │                   ┌──────────────────┐
         │                   │ DashboardShare   │
         │                   └──────────────────┘
         │
         ├────────► APIToken ──► APIRequest
         │                   └──► RateLimitBucket
         │
         └────────► UserDepartmentAssignment ──► Department
```

### Key Indexes

Performance-critical indexes for enterprise scale:

```sql
-- Printer Metrics
CREATE INDEX idx_printer_metrics_device_timestamp ON printer_metrics(device_id, timestamp DESC);

-- Print Job Audit (compliance queries)
CREATE INDEX idx_print_job_user_time ON print_job_audit(user_account, submission_time DESC);
CREATE INDEX idx_print_job_ip_time ON print_job_audit(source_ip, submission_time DESC);
CREATE INDEX idx_print_job_printer_time ON print_job_audit(printer_name, submission_time DESC);

-- Camera Frames (cleanup queries)
CREATE INDEX idx_camera_frame_device_timestamp ON camera_frames(device_id, capture_timestamp DESC);

-- Polling Nodes (heartbeat monitoring)
CREATE INDEX idx_polling_node_heartbeat ON polling_nodes(last_heartbeat DESC) WHERE enabled = true;

-- Alert Escalation (scheduler queries)
CREATE INDEX idx_escalation_state_next_time ON alert_escalation_states(next_escalation_time) WHERE acknowledged = false;

-- SLA Measurements (reporting queries)
CREATE INDEX idx_sla_measurement_metric_period ON sla_measurements(metric_id, period_start, period_end);

-- API Rate Limiting (sliding window)
CREATE INDEX idx_rate_limit_token_window ON rate_limit_buckets(token_id, window_start DESC);

-- Department Isolation (RBAC filtering)
CREATE INDEX idx_device_department ON device(department_id) WHERE department_id IS NOT NULL;
CREATE INDEX idx_device_site ON device(site_id) WHERE site_id IS NOT NULL;

-- Capacity Forecasting (alert generation)
CREATE INDEX idx_capacity_forecast_exhaustion ON capacity_forecasts(forecasted_exhaustion_date) WHERE alert_level IN ('warning', 'critical');
```


## Service Layer Design

### New Services

#### 1. PrinterMonitoringService (Requirements 1, 2, 3)

```python
class PrinterMonitoringService:
    """
    Handles printer monitoring via SNMP and print server agent extensions.
    """
    
    def poll_printer_snmp(self, device_id: int) -> dict:
        """
        Poll printer metrics via SNMP using RFC 3805 Printer MIB.
        
        OIDs:
        - hrPrinterStatus: 1.3.6.1.2.1.25.3.5.1.1
        - prtMarkerSuppliesLevel: 1.3.6.1.2.1.43.11.1.1.9
        - prtMarkerSuppliesMaxCapacity: 1.3.6.1.2.1.43.11.1.1.8
        - prtMarkerSuppliesType: 1.3.6.1.2.1.43.11.1.1.4
        - hrDeviceStatus: 1.3.6.1.2.1.25.3.2.1.5
        
        Returns: {status, toner_levels, page_count, job_queue_length}
        """
        pass
    
    def store_printer_metrics(self, device_id: int, metrics: dict) -> PrinterMetrics:
        """Store printer metrics and evaluate alert thresholds."""
        pass
    
    def check_printer_alerts(self, device: Device, metrics: PrinterMetrics):
        """
        Evaluate printer alert conditions:
        - Toner < 20%: WARNING
        - Status error for 3 consecutive polls: CRITICAL
        - Job queue > 50: WARNING
        """
        pass
    
    def parse_print_job_log(self, log_entry: dict) -> PrintJobAudit:
        """
        Parse print job from Windows Event Log or CUPS log.
        Correlate with workstation device if source_ip matches.
        """
        pass
    
    def get_print_audit_trail(self, filters: dict) -> List[PrintJobAudit]:
        """
        Query print audit trail with filters:
        - user_account, source_ip, printer_name, date_range
        """
        pass
```

#### 2. CameraMonitoringService (Requirements 7, 8)

```python
class CameraMonitoringService:
    """
    Handles RTSP camera monitoring and frame capture.
    """
    
    def test_rtsp_connection(self, rtsp_url: str, username: str, password: str) -> dict:
        """
        Test RTSP connection using OpenCV or ffmpeg.
        Returns: {success, error_message, resolution}
        """
        pass
    
    def capture_frame(self, device_id: int) -> CameraFrame:
        """
        Capture single frame from RTSP stream.
        - Resize to max 1920x1080
        - Compress as JPEG quality 85
        - Store in static/camera_frames/{device_id}/{timestamp}.jpg
        - Create CameraFrame record
        """
        pass
    
    def cleanup_old_frames(self, retention_days: int = 30):
        """
        Delete camera frames older than retention period.
        Update storage statistics.
        """
        pass
    
    def get_latest_frame(self, device_id: int) -> Optional[CameraFrame]:
        """Get most recent frame for device."""
        pass
    
    def get_frame_gallery(self, site_id: Optional[int] = None) -> List[dict]:
        """
        Get latest frames from all cameras (optionally filtered by site).
        Returns: [{device_id, device_name, frame_path, timestamp}, ...]
        """
        pass
    
    def encrypt_rtsp_credentials(self, password: str) -> bytes:
        """Encrypt RTSP password using AES-256."""
        pass
    
    def decrypt_rtsp_credentials(self, encrypted: bytes) -> str:
        """Decrypt RTSP password."""
        pass
```


#### 3. PollingNodeService (Requirement 10)

```python
class PollingNodeService:
    """
    Manages distributed polling nodes and metric aggregation.
    """
    
    def register_polling_node(self, node_data: dict) -> PollingNode:
        """
        Register new polling node.
        Generate API token, assign to site/subnet.
        """
        pass
    
    def process_heartbeat(self, node_uuid: str, metrics: dict):
        """
        Process heartbeat from polling node.
        Update: last_heartbeat, metrics_queue_depth, error_count, status
        """
        pass
    
    def assign_devices_to_node(self, node_id: int, device_ids: List[int], method: str):
        """
        Assign devices to polling node.
        Methods: site, subnet, manual
        """
        pass
    
    def forward_metrics(self, node_uuid: str, metrics_batch: List[dict]):
        """
        Receive metrics from polling node and store in central DB.
        Validate node authentication, decompress if needed.
        """
        pass
    
    def check_node_health(self):
        """
        Check all polling nodes for stale heartbeats.
        Generate CRITICAL alert if heartbeat > 5 minutes old.
        """
        pass
    
    def get_node_assignments(self, node_id: int) -> List[Device]:
        """Get all devices assigned to a polling node."""
        pass
```

#### 4. AlertEscalationService (Requirement 12)

```python
class AlertEscalationService:
    """
    Manages alert escalation policies and notifications.
    """
    
    def initiate_escalation(self, alert_id: int, device: Device):
        """
        Start escalation sequence for alert.
        Find matching policy, create EscalationState, schedule level 1.
        """
        pass
    
    def process_escalation_queue(self):
        """
        Check for alerts ready to escalate.
        Query: next_escalation_time <= now AND acknowledged = false
        """
        pass
    
    def escalate_to_next_level(self, state: AlertEscalationState):
        """
        Escalate alert to next level.
        Send notifications, update state, schedule next level.
        """
        pass
    
    def acknowledge_alert(self, alert_id: int, user: str):
        """
        Acknowledge alert, halt escalation.
        Update: acknowledged = true, acknowledged_by, acknowledged_at
        """
        pass
    
    def send_escalation_notifications(self, level: EscalationLevel, alert_data: dict):
        """
        Send notifications for escalation level.
        Methods: email, webhook
        """
        pass
    
    def test_escalation_policy(self, policy_id: int) -> dict:
        """
        Simulate escalation without generating real alerts.
        Returns: {levels_tested, notifications_sent, errors}
        """
        pass
```

#### 5. ComplianceReportingService (Requirement 13)

```python
class ComplianceReportingService:
    """
    Generates compliance reports for regulatory frameworks.
    """
    
    def generate_report(self, report_id: int, period_start: datetime, period_end: datetime) -> ComplianceReportExecution:
        """
        Generate compliance report.
        Query data, apply template, render PDF/Excel.
        """
        pass
    
    def get_report_template(self, template_name: str) -> dict:
        """
        Load report template (SOC2, ISO27001, HIPAA, PCI_DSS).
        Returns: {sections, queries, formatting}
        """
        pass
    
    def render_pdf(self, report_data: dict, template: dict) -> str:
        """Render report as PDF, return file path."""
        pass
    
    def render_excel(self, report_data: dict, template: dict) -> str:
        """Render report as Excel, return file path."""
        pass
    
    def schedule_report(self, report: ComplianceReport):
        """
        Schedule report generation.
        Calculate next run time based on frequency.
        """
        pass
    
    def deliver_report(self, execution: ComplianceReportExecution):
        """
        Deliver report via email to configured recipients.
        Attach PDF and Excel files.
        """
        pass
    
    def log_report_access(self, execution_id: int, user: str, method: str, source_ip: str):
        """Log report access for audit trail."""
        pass
```


#### 6. BaselineAnomalyService (Requirement 14)

```python
class BaselineAnomalyService:
    """
    Calculates performance baselines and detects anomalies.
    """
    
    def calculate_baseline(self, device_id: int, metric_name: str) -> PerformanceBaseline:
        """
        Calculate 30-day rolling baseline.
        Query metrics, compute: mean, std_dev, min, max
        """
        pass
    
    def detect_anomaly(self, device_id: int, metric_name: str, current_value: float) -> Optional[AnomalyDetection]:
        """
        Check if current value is anomalous.
        Compare to baseline: |value - mean| > sensitivity * std_dev
        """
        pass
    
    def check_consecutive_anomalies(self, device_id: int, metric_name: str):
        """
        Check for 3 consecutive anomalies.
        Generate WARNING alert if threshold met.
        """
        pass
    
    def reset_baseline(self, device_id: int, metric_name: str):
        """
        Reset baseline after configuration change or maintenance.
        Delete existing baseline, trigger recalculation.
        """
        pass
    
    def get_sensitivity_threshold(self, sensitivity: str) -> float:
        """
        Map sensitivity to standard deviations.
        low: 4σ, medium: 3σ, high: 2σ
        """
        pass
```

#### 7. CapacityPlanningService (Requirement 15)

```python
class CapacityPlanningService:
    """
    Forecasts resource exhaustion using linear regression.
    """
    
    def calculate_forecast(self, device_id: int, resource_type: str) -> CapacityForecast:
        """
        Calculate capacity forecast using 90-day historical data.
        Linear regression: y = mx + b
        Forecast when utilization reaches 90%
        """
        pass
    
    def get_historical_data(self, device_id: int, resource_type: str, days: int = 90) -> List[tuple]:
        """
        Query historical utilization data.
        Returns: [(timestamp, utilization), ...]
        """
        pass
    
    def linear_regression(self, data: List[tuple]) -> dict:
        """
        Perform linear regression.
        Returns: {slope, intercept, r_squared}
        """
        pass
    
    def forecast_exhaustion_date(self, current_util: float, growth_rate: float, threshold: float = 90.0) -> Optional[date]:
        """
        Calculate date when utilization reaches threshold.
        Returns None if growth_rate <= 0 (not growing)
        """
        pass
    
    def check_forecast_alerts(self, forecast: CapacityForecast):
        """
        Generate alerts based on forecast.
        WARNING: < 30 days, CRITICAL: < 7 days
        """
        pass
    
    def get_capacity_dashboard(self, site_id: Optional[int] = None) -> List[dict]:
        """
        Get capacity forecasts requiring attention.
        Sort by days_until_exhaustion ASC
        """
        pass
```

#### 8. SLATrackingService (Requirement 16)

```python
class SLATrackingService:
    """
    Tracks SLA metrics and compliance.
    """
    
    def calculate_sla_measurement(self, metric_id: int, period_start: datetime, period_end: datetime) -> SLAMeasurement:
        """
        Calculate SLA metric for period.
        Query relevant data, compute actual value, compare to target.
        """
        pass
    
    def calculate_uptime_percentage(self, device_ids: List[int], period_start: datetime, period_end: datetime) -> float:
        """
        Calculate uptime percentage for devices.
        Exclude maintenance windows.
        """
        pass
    
    def calculate_avg_response_time(self, device_ids: List[int], period_start: datetime, period_end: datetime) -> float:
        """Calculate average response time (ping latency)."""
        pass
    
    def calculate_alert_resolution_time(self, device_ids: List[int], period_start: datetime, period_end: datetime) -> float:
        """Calculate average time from alert generation to acknowledgment."""
        pass
    
    def check_sla_breach(self, measurement: SLAMeasurement):
        """
        Check if SLA is breached.
        Generate CRITICAL alert if breached.
        """
        pass
    
    def get_sla_dashboard(self) -> List[dict]:
        """
        Get current SLA status for all metrics.
        Returns: [{metric_name, status, actual, target, compliance_pct}, ...]
        """
        pass
```


#### 9. WebhookIntegrationService (Requirement 17)

```python
class WebhookIntegrationService:
    """
    Manages webhook integrations for ticketing systems.
    """
    
    def send_webhook(self, webhook_id: int, alert_data: dict) -> WebhookDelivery:
        """
        Send webhook notification.
        Render payload template, authenticate, POST request.
        """
        pass
    
    def render_payload(self, template: str, context: dict) -> str:
        """
        Render Jinja2 payload template.
        Context: {alert_severity, device_name, device_ip, alert_message, timestamp, alert_id}
        """
        pass
    
    def authenticate_request(self, webhook: WebhookIntegration) -> dict:
        """
        Build authentication headers/credentials.
        Methods: basic, bearer, custom headers
        """
        pass
    
    def retry_failed_delivery(self, delivery_id: int):
        """
        Retry failed webhook delivery.
        Exponential backoff, max 3 attempts.
        """
        pass
    
    def test_webhook(self, webhook_id: int) -> dict:
        """
        Test webhook configuration.
        Send test payload, return response.
        """
        pass
    
    def encrypt_webhook_credentials(self, password: str) -> bytes:
        """Encrypt webhook credentials using AES-256."""
        pass
    
    def decrypt_webhook_credentials(self, encrypted: bytes) -> str:
        """Decrypt webhook credentials."""
        pass
```

#### 10. DashboardService (Requirement 18)

```python
class DashboardService:
    """
    Manages custom dashboards and widgets.
    """
    
    def create_dashboard(self, user_id: int, dashboard_data: dict) -> CustomDashboard:
        """Create new custom dashboard."""
        pass
    
    def add_widget(self, dashboard_id: int, widget_data: dict) -> DashboardWidget:
        """Add widget to dashboard."""
        pass
    
    def get_widget_data(self, widget: DashboardWidget, user: User) -> dict:
        """
        Fetch data for widget.
        Apply department isolation filters if enabled.
        """
        pass
    
    def share_dashboard(self, dashboard_id: int, target_user_id: int, can_edit: bool):
        """Share dashboard with another user."""
        pass
    
    def export_dashboard(self, dashboard_id: int) -> dict:
        """Export dashboard configuration as JSON."""
        pass
    
    def import_dashboard(self, user_id: int, config: dict) -> CustomDashboard:
        """Import dashboard from JSON configuration."""
        pass
    
    def get_dashboard_templates(self) -> List[dict]:
        """
        Get predefined dashboard templates.
        Templates: network_admin, security_admin, department_manager, executive
        """
        pass
```

#### 11. DepartmentIsolationService (Requirement 19)

```python
class DepartmentIsolationService:
    """
    Enforces department-based access isolation.
    """
    
    def get_accessible_devices(self, user: User) -> Query:
        """
        Get devices accessible to user based on department assignments.
        Apply hierarchical access if can_view_children = true.
        """
        pass
    
    def get_user_departments(self, user_id: int, include_children: bool = False) -> List[Department]:
        """
        Get departments accessible to user.
        Optionally include child departments.
        """
        pass
    
    def check_device_access(self, user: User, device_id: int) -> bool:
        """Check if user can access device."""
        pass
    
    def filter_query_by_department(self, query: Query, user: User) -> Query:
        """
        Apply department filter to SQLAlchemy query.
        Used in API endpoints and reports.
        """
        pass
    
    def assign_user_to_department(self, user_id: int, department_id: int, role: str, can_view_children: bool):
        """Assign user to department with role."""
        pass
```


#### 12. BulkOperationsService (Requirement 20)

```python
class BulkOperationsService:
    """
    Executes bulk operations on devices asynchronously.
    """
    
    def initiate_bulk_operation(self, operation_type: str, device_ids: List[int], parameters: dict, user: str) -> BulkOperation:
        """
        Create bulk operation record.
        Validate permissions, enqueue for async execution.
        """
        pass
    
    def execute_bulk_operation(self, operation_id: int):
        """
        Execute bulk operation asynchronously.
        Process devices in batches, update progress.
        """
        pass
    
    def execute_single_device_operation(self, operation_type: str, device_id: int, parameters: dict) -> dict:
        """
        Execute operation on single device.
        Returns: {success, error_message}
        """
        pass
    
    def get_operation_status(self, operation_id: int) -> dict:
        """
        Get bulk operation status.
        Returns: {status, progress_current, progress_total, successful_count, failed_count}
        """
        pass
    
    def import_devices_csv(self, csv_file, user: str) -> BulkOperation:
        """
        Import devices from CSV file.
        Validate, preview, create bulk operation.
        """
        pass
    
    def export_devices_csv(self, device_ids: List[int]) -> str:
        """
        Export devices to CSV file.
        Returns: file path
        """
        pass
```

#### 13. ConfigurationService (Requirement 23)

```python
class ConfigurationService:
    """
    Handles configuration import/export with validation.
    """
    
    def export_configuration(self, sections: List[str]) -> dict:
        """
        Export system configuration as JSON.
        Sections: sites, departments, devices, alert_policies, sla_metrics, rbac_roles
        """
        pass
    
    def validate_configuration(self, config: dict) -> dict:
        """
        Validate configuration JSON.
        Returns: {valid, errors, warnings}
        """
        pass
    
    def import_configuration(self, config: dict, mode: str, sections: List[str], user: str) -> ConfigurationImport:
        """
        Import configuration.
        Modes: full (replace all), partial (selected sections), merge (update existing)
        """
        pass
    
    def parse_configuration(self, json_str: str) -> dict:
        """
        Parse JSON configuration string.
        Validate JSON syntax, schema.
        """
        pass
    
    def pretty_print_configuration(self, config: dict) -> str:
        """
        Format configuration as pretty-printed JSON.
        2-space indentation, sorted keys.
        """
        pass
    
    def calculate_config_hash(self, config: dict) -> str:
        """Calculate SHA-256 hash for integrity verification."""
        pass
```

#### 14. SecurityService (Requirement 25)

```python
class SecurityService:
    """
    Handles encryption, authentication, and security features.
    """
    
    def encrypt_aes256(self, plaintext: str, key: bytes) -> bytes:
        """Encrypt string using AES-256-CBC."""
        pass
    
    def decrypt_aes256(self, ciphertext: bytes, key: bytes) -> str:
        """Decrypt AES-256-CBC ciphertext."""
        pass
    
    def get_encryption_key(self) -> bytes:
        """
        Get encryption key from environment or generate.
        Store in ENCRYPTION_KEY env var.
        """
        pass
    
    def hash_api_token(self, token: str) -> str:
        """Hash API token using bcrypt."""
        pass
    
    def verify_api_token(self, token: str, token_hash: str) -> bool:
        """Verify API token against hash."""
        pass
    
    def generate_api_token(self) -> str:
        """Generate secure random API token (32 bytes, hex)."""
        pass
    
    def sanitize_input(self, user_input: str) -> str:
        """Sanitize user input to prevent XSS/SQL injection."""
        pass
    
    def validate_password_complexity(self, password: str) -> dict:
        """
        Validate password complexity.
        Requirements: min 12 chars, uppercase, lowercase, digit, special char
        Returns: {valid, errors}
        """
        pass
```


## Worker Processes

### New Workers

#### 1. Camera Worker (`workers/camera_worker.py`)

```python
"""
Camera Worker — Captures frames from RTSP cameras.

Architecture:
    Scheduler → PollTask(task_type='camera_capture') → Camera Worker → Capture Frame

Concurrency:
    Uses SELECT FOR UPDATE SKIP LOCKED (same pattern as SNMP worker)
    
Configuration:
    BATCH_SIZE = 10
    MAX_WORKERS = 5  # Parallel RTSP connections
    CAPTURE_TIMEOUT = 10  # seconds
"""

def execute_camera_capture(app, task_id):
    """
    Execute camera frame capture task.
    1. Get CameraDevice config
    2. Connect to RTSP stream
    3. Capture frame
    4. Resize and compress
    5. Save to disk
    6. Create CameraFrame record
    7. Check consecutive failures for alert
    """
    pass
```

#### 2. Webhook Worker (`workers/webhook_worker.py`)

```python
"""
Webhook Worker — Sends webhook notifications with retry logic.

Architecture:
    Alert Generated → PollTask(task_type='webhook_delivery') → Webhook Worker → HTTP POST

Retry Logic:
    Exponential backoff: 60s, 120s, 240s (max 3 attempts)
"""

def execute_webhook_delivery(app, task_id):
    """
    Execute webhook delivery task.
    1. Get WebhookIntegration config
    2. Render payload template
    3. Build authentication
    4. Send HTTP request
    5. Log delivery result
    6. Retry on failure
    """
    pass
```

#### 3. Baseline Calculator Worker (`workers/baseline_worker.py`)

```python
"""
Baseline Calculator Worker — Calculates performance baselines.

Schedule:
    Runs daily at 04:00 UTC
    Processes all devices with baseline tracking enabled
    
Processing:
    - Query 30-day metrics
    - Calculate mean, std_dev, min, max
    - Update PerformanceBaseline records
"""

def calculate_all_baselines(app):
    """
    Calculate baselines for all devices.
    Metrics: cpu_usage, memory_usage, disk_usage, network_in_bps, network_out_bps
    """
    pass
```

#### 4. Capacity Forecast Worker (`workers/capacity_worker.py`)

```python
"""
Capacity Forecast Worker — Calculates capacity forecasts.

Schedule:
    Runs daily at 05:00 UTC
    Processes all devices with capacity planning enabled
    
Processing:
    - Query 90-day metrics
    - Perform linear regression
    - Calculate exhaustion date
    - Update CapacityForecast records
    - Generate alerts if needed
"""

def calculate_all_forecasts(app):
    """
    Calculate capacity forecasts for all devices.
    Resources: disk, memory
    """
    pass
```

#### 5. SLA Calculator Worker (`workers/sla_worker.py`)

```python
"""
SLA Calculator Worker — Calculates SLA measurements.

Schedule:
    Runs at end of each measurement period:
    - Monthly: 1st day of month at 00:00 UTC
    - Quarterly: 1st day of quarter at 00:00 UTC
    
Processing:
    - Query metrics for period
    - Calculate actual values
    - Compare to targets
    - Create SLAMeasurement records
    - Check for breaches
"""

def calculate_sla_measurements(app, period_type: str):
    """
    Calculate SLA measurements for period.
    period_type: monthly, quarterly
    """
    pass
```

#### 6. Compliance Report Worker (`workers/compliance_worker.py`)

```python
"""
Compliance Report Worker — Generates scheduled compliance reports.

Schedule:
    Runs based on report schedule configuration
    
Processing:
    - Query report data
    - Apply template
    - Render PDF and Excel
    - Deliver via email
"""

def generate_scheduled_reports(app):
    """
    Generate all scheduled compliance reports due now.
    """
    pass
```


### Scheduler Extensions

Add to `services/scheduler.py`:

```python
def enqueue_printer_tasks(self):
    """
    Enqueue printer SNMP polling tasks.
    Query devices with device_type='printer' and SNMP enabled.
    """
    pass

def enqueue_camera_tasks(self):
    """
    Enqueue camera frame capture tasks.
    Query CameraDevice records with capture_enabled=true.
    """
    pass

def check_polling_node_health(self):
    """
    Check polling node heartbeats.
    Generate alerts for nodes with stale heartbeats (> 5 minutes).
    """
    pass

def process_alert_escalations(self):
    """
    Process alert escalation queue.
    Query AlertEscalationState records with next_escalation_time <= now.
    """
    pass

def calculate_baselines(self):
    """
    Trigger baseline calculation worker.
    Schedule: daily at 04:00 UTC
    """
    pass

def calculate_capacity_forecasts(self):
    """
    Trigger capacity forecast worker.
    Schedule: daily at 05:00 UTC
    """
    pass

def calculate_sla_measurements(self):
    """
    Trigger SLA calculation worker.
    Schedule: monthly/quarterly based on metric configuration
    """
    pass

def generate_compliance_reports(self):
    """
    Trigger compliance report generation.
    Schedule: based on report configuration
    """
    pass
```

### Task Types

New task types for `PollTask.task_type`:

```python
TASK_TYPES = {
    'snmp_health': 'SNMP health metrics polling',
    'interface': 'Interface counter polling',
    'discovery': 'SNMP discovery enrichment',
    'printer_snmp': 'Printer SNMP polling',  # NEW
    'camera_capture': 'Camera frame capture',  # NEW
    'webhook_delivery': 'Webhook notification delivery',  # NEW
    'baseline_calculation': 'Performance baseline calculation',  # NEW
    'capacity_forecast': 'Capacity forecast calculation',  # NEW
    'sla_calculation': 'SLA measurement calculation',  # NEW
}
```


## API Endpoints

### REST API Design (Requirement 21)

#### Authentication

All API endpoints require authentication via:
- API Token: `X-API-Key` header
- Session Cookie: For web UI requests

Rate limiting: 1000 requests/hour per token (configurable per token)

#### Base URL

```
/api/v1/
```

#### Endpoint Categories

### 1. Sites API

```
GET    /api/v1/sites                    # List all sites
POST   /api/v1/sites                    # Create site
GET    /api/v1/sites/{id}               # Get site details
PUT    /api/v1/sites/{id}               # Update site
DELETE /api/v1/sites/{id}               # Delete site
GET    /api/v1/sites/{id}/devices       # List devices in site
GET    /api/v1/sites/{id}/dashboard     # Site dashboard metrics
```

### 2. Departments API

```
GET    /api/v1/departments              # List all departments
POST   /api/v1/departments              # Create department
GET    /api/v1/departments/{id}         # Get department details
PUT    /api/v1/departments/{id}         # Update department
DELETE /api/v1/departments/{id}         # Delete department
GET    /api/v1/departments/{id}/devices # List devices in department
GET    /api/v1/departments/{id}/users   # List users in department
```

### 3. Devices API (Extended)

```
GET    /api/v1/devices                  # List devices (with filters)
POST   /api/v1/devices                  # Create device
GET    /api/v1/devices/{id}             # Get device details
PUT    /api/v1/devices/{id}             # Update device
DELETE /api/v1/devices/{id}             # Delete device
GET    /api/v1/devices/{id}/metrics     # Get device metrics
GET    /api/v1/devices/{id}/alerts      # Get device alerts
POST   /api/v1/devices/bulk             # Bulk operations
POST   /api/v1/devices/import           # Import from CSV
GET    /api/v1/devices/export           # Export to CSV
```

Query Parameters for GET /api/v1/devices:
- `site_id`: Filter by site
- `department_id`: Filter by department
- `device_type`: Filter by type (server, printer, camera, etc.)
- `status`: Filter by status (online, offline, maintenance)
- `page`: Page number (default: 1)
- `per_page`: Items per page (default: 50, max: 500)
- `sort`: Sort field (default: device_name)
- `order`: Sort order (asc, desc)

### 4. Printers API

```
GET    /api/v1/printers                 # List all printers
GET    /api/v1/printers/{id}            # Get printer details
GET    /api/v1/printers/{id}/metrics    # Get printer metrics
GET    /api/v1/printers/{id}/jobs       # Get print jobs
GET    /api/v1/print-jobs               # List all print jobs (with filters)
GET    /api/v1/print-jobs/{id}          # Get print job details
GET    /api/v1/print-audit              # Print audit trail
```

Query Parameters for GET /api/v1/print-audit:
- `user_account`: Filter by user
- `source_ip`: Filter by IP
- `printer_name`: Filter by printer
- `start_date`: Start date (ISO 8601)
- `end_date`: End date (ISO 8601)
- `page`, `per_page`, `sort`, `order`

### 5. Cameras API

```
GET    /api/v1/cameras                  # List all cameras
POST   /api/v1/cameras                  # Add camera
GET    /api/v1/cameras/{id}             # Get camera details
PUT    /api/v1/cameras/{id}             # Update camera
DELETE /api/v1/cameras/{id}             # Delete camera
GET    /api/v1/cameras/{id}/frames      # Get camera frames
POST   /api/v1/cameras/{id}/capture     # Capture frame on-demand
GET    /api/v1/cameras/{id}/latest      # Get latest frame
GET    /api/v1/camera-gallery           # Get gallery view (all cameras)
```

### 6. Polling Nodes API

```
GET    /api/v1/polling-nodes            # List all polling nodes
POST   /api/v1/polling-nodes            # Register polling node
GET    /api/v1/polling-nodes/{id}       # Get node details
PUT    /api/v1/polling-nodes/{id}       # Update node
DELETE /api/v1/polling-nodes/{id}       # Delete node
POST   /api/v1/polling-nodes/{id}/heartbeat  # Send heartbeat
POST   /api/v1/polling-nodes/{id}/metrics    # Forward metrics batch
GET    /api/v1/polling-nodes/{id}/assignments # Get device assignments
POST   /api/v1/polling-nodes/{id}/assign     # Assign devices
```

### 7. Alerts API (Extended)

```
GET    /api/v1/alerts                   # List alerts (with filters)
GET    /api/v1/alerts/{id}              # Get alert details
POST   /api/v1/alerts/{id}/acknowledge  # Acknowledge alert
POST   /api/v1/alerts/{id}/resolve      # Resolve alert
GET    /api/v1/alerts/{id}/escalation   # Get escalation status
```

### 8. Alert Escalation Policies API

```
GET    /api/v1/escalation-policies      # List policies
POST   /api/v1/escalation-policies      # Create policy
GET    /api/v1/escalation-policies/{id} # Get policy details
PUT    /api/v1/escalation-policies/{id} # Update policy
DELETE /api/v1/escalation-policies/{id} # Delete policy
POST   /api/v1/escalation-policies/{id}/test  # Test policy
```


### 9. Compliance Reports API

```
GET    /api/v1/compliance-reports       # List reports
POST   /api/v1/compliance-reports       # Create report
GET    /api/v1/compliance-reports/{id}  # Get report details
PUT    /api/v1/compliance-reports/{id}  # Update report
DELETE /api/v1/compliance-reports/{id}  # Delete report
POST   /api/v1/compliance-reports/{id}/generate  # Generate report
GET    /api/v1/compliance-reports/{id}/executions  # List executions
GET    /api/v1/compliance-reports/executions/{id}  # Get execution details
GET    /api/v1/compliance-reports/executions/{id}/download  # Download report
GET    /api/v1/compliance-reports/templates  # List templates
```

### 10. SLA Metrics API

```
GET    /api/v1/sla-metrics              # List SLA metrics
POST   /api/v1/sla-metrics              # Create SLA metric
GET    /api/v1/sla-metrics/{id}         # Get metric details
PUT    /api/v1/sla-metrics/{id}         # Update metric
DELETE /api/v1/sla-metrics/{id}         # Delete metric
GET    /api/v1/sla-metrics/{id}/measurements  # List measurements
GET    /api/v1/sla-metrics/{id}/breaches      # List breaches
GET    /api/v1/sla-dashboard            # SLA dashboard
```

### 11. Capacity Planning API

```
GET    /api/v1/capacity-forecasts       # List forecasts
GET    /api/v1/capacity-forecasts/{id}  # Get forecast details
POST   /api/v1/capacity-forecasts/{id}/recalculate  # Recalculate
GET    /api/v1/capacity-dashboard       # Capacity dashboard
```

### 12. Performance Baselines API

```
GET    /api/v1/baselines                # List baselines
GET    /api/v1/baselines/{id}           # Get baseline details
POST   /api/v1/baselines/{id}/reset     # Reset baseline
GET    /api/v1/baselines/{id}/anomalies # List anomalies
```

### 13. Webhooks API

```
GET    /api/v1/webhooks                 # List webhooks
POST   /api/v1/webhooks                 # Create webhook
GET    /api/v1/webhooks/{id}            # Get webhook details
PUT    /api/v1/webhooks/{id}            # Update webhook
DELETE /api/v1/webhooks/{id}            # Delete webhook
POST   /api/v1/webhooks/{id}/test       # Test webhook
GET    /api/v1/webhooks/{id}/deliveries # List deliveries
POST   /api/v1/webhooks/deliveries/{id}/retry  # Retry delivery
```

### 14. Dashboards API

```
GET    /api/v1/dashboards               # List dashboards
POST   /api/v1/dashboards               # Create dashboard
GET    /api/v1/dashboards/{id}          # Get dashboard details
PUT    /api/v1/dashboards/{id}          # Update dashboard
DELETE /api/v1/dashboards/{id}          # Delete dashboard
POST   /api/v1/dashboards/{id}/share    # Share dashboard
GET    /api/v1/dashboards/{id}/data     # Get dashboard data
POST   /api/v1/dashboards/{id}/widgets  # Add widget
PUT    /api/v1/dashboards/widgets/{id}  # Update widget
DELETE /api/v1/dashboards/widgets/{id}  # Delete widget
GET    /api/v1/dashboard-templates      # List templates
```

### 15. Bulk Operations API

```
POST   /api/v1/bulk-operations          # Create bulk operation
GET    /api/v1/bulk-operations/{id}     # Get operation status
GET    /api/v1/bulk-operations/{id}/results  # Get operation results
DELETE /api/v1/bulk-operations/{id}     # Cancel operation
```

### 16. Configuration API

```
GET    /api/v1/configuration/export     # Export configuration
POST   /api/v1/configuration/import     # Import configuration
POST   /api/v1/configuration/validate   # Validate configuration
GET    /api/v1/configuration/snapshots  # List snapshots
POST   /api/v1/configuration/snapshots  # Create snapshot
GET    /api/v1/configuration/snapshots/{id}  # Get snapshot
DELETE /api/v1/configuration/snapshots/{id}  # Delete snapshot
```

### 17. API Tokens API

```
GET    /api/v1/api-tokens               # List user's tokens
POST   /api/v1/api-tokens               # Create token
GET    /api/v1/api-tokens/{id}          # Get token details
PUT    /api/v1/api-tokens/{id}          # Update token
DELETE /api/v1/api-tokens/{id}          # Revoke token
GET    /api/v1/api-tokens/{id}/usage    # Get token usage stats
```

### 18. Users API (Extended)

```
GET    /api/v1/users                    # List users
POST   /api/v1/users                    # Create user
GET    /api/v1/users/{id}               # Get user details
PUT    /api/v1/users/{id}               # Update user
DELETE /api/v1/users/{id}               # Delete user
POST   /api/v1/users/{id}/departments   # Assign to department
DELETE /api/v1/users/{id}/departments/{dept_id}  # Remove from department
```

### API Response Format

#### Success Response

```json
{
  "success": true,
  "data": { ... },
  "meta": {
    "page": 1,
    "per_page": 50,
    "total": 150,
    "total_pages": 3
  }
}
```

#### Error Response

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid site_id",
    "details": {
      "field": "site_id",
      "value": "invalid"
    }
  }
}
```

### Rate Limiting Headers

```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 950
X-RateLimit-Reset: 1640000000
```


## Error Handling

### Error Classification

#### 1. Network Errors
- **SNMP_TIMEOUT**: SNMP request timeout (device unreachable)
- **SNMP_AUTH_FAILED**: SNMP authentication failure
- **RTSP_CONNECTION_FAILED**: RTSP stream connection failure
- **WEBHOOK_DELIVERY_FAILED**: Webhook HTTP request failure
- **POLLING_NODE_OFFLINE**: Polling node heartbeat timeout

#### 2. Data Validation Errors
- **VALIDATION_ERROR**: Input validation failure
- **DUPLICATE_ENTRY**: Unique constraint violation
- **INVALID_CONFIGURATION**: Configuration validation failure
- **MISSING_REQUIRED_FIELD**: Required field missing

#### 3. Permission Errors
- **UNAUTHORIZED**: Authentication required
- **FORBIDDEN**: Insufficient permissions
- **DEPARTMENT_ACCESS_DENIED**: Department isolation violation
- **RATE_LIMIT_EXCEEDED**: API rate limit exceeded

#### 4. Resource Errors
- **DEVICE_NOT_FOUND**: Device ID not found
- **SITE_NOT_FOUND**: Site ID not found
- **DEPARTMENT_NOT_FOUND**: Department ID not found
- **STORAGE_LIMIT_EXCEEDED**: Camera frame storage limit exceeded

#### 5. Processing Errors
- **BASELINE_CALCULATION_FAILED**: Insufficient data for baseline
- **FORECAST_CALCULATION_FAILED**: Linear regression failed
- **REPORT_GENERATION_FAILED**: Compliance report generation error
- **BULK_OPERATION_FAILED**: Bulk operation execution error

### Error Handling Patterns

#### 1. Worker Error Handling

```python
try:
    result = execute_task(task_id)
    task.mark_done()
except NetworkError as e:
    task.mark_failed('NETWORK_ERROR', str(e))
    # Retry with exponential backoff
except ValidationError as e:
    task.mark_failed('VALIDATION_ERROR', str(e))
    # No retry (permanent failure)
except Exception as e:
    task.mark_failed('UNKNOWN_ERROR', str(e))
    # Retry with exponential backoff
```

#### 2. API Error Handling

```python
@app.errorhandler(ValidationError)
def handle_validation_error(e):
    return jsonify({
        'success': False,
        'error': {
            'code': 'VALIDATION_ERROR',
            'message': str(e),
            'details': e.details
        }
    }), 400

@app.errorhandler(PermissionError)
def handle_permission_error(e):
    return jsonify({
        'success': False,
        'error': {
            'code': 'FORBIDDEN',
            'message': str(e)
        }
    }), 403
```

#### 3. Graceful Degradation

- **SNMP Fallback**: If SNMP fails, attempt WMI (Windows) or SSH (Linux)
- **Camera Capture**: If capture fails, log error but don't alert until 3 consecutive failures
- **Polling Node Failover**: If polling node offline, central aggregator polls devices directly
- **Webhook Retry**: Retry failed webhooks with exponential backoff (max 3 attempts)

### Logging Strategy

#### Log Levels

- **DEBUG**: Detailed diagnostic information (disabled in production)
- **INFO**: General informational messages (task execution, API requests)
- **WARNING**: Warning messages (anomaly detected, approaching threshold)
- **ERROR**: Error messages (task failure, API error)
- **CRITICAL**: Critical errors (database connection failure, worker crash)

#### Log Format

```
[2024-01-15 10:30:45] [INFO] [PrinterMonitoringService] Polled printer device_id=123: toner_black=45%, status=idle
[2024-01-15 10:30:50] [ERROR] [CameraWorker] Frame capture failed device_id=456: RTSP_CONNECTION_FAILED - Connection timeout
[2024-01-15 10:31:00] [WARNING] [CapacityPlanningService] Disk exhaustion forecast device_id=789: 15 days remaining
```

#### Log Destinations

- **Console**: stdout (development)
- **File**: `/var/log/monitoring/app.log` (production)
- **Database**: Critical errors logged to `system_events` table
- **External**: Optionally forward to syslog/ELK stack


## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Printer Device Classification

*For any* SNMP discovery result containing Printer MIB OIDs, the device SHALL be classified with device_type="printer"

**Validates: Requirements 1.1**

### Property 2: Printer Metrics Completeness

*For any* printer device after SNMP polling, the stored PrinterMetrics record SHALL contain all required fields: status, page_count, toner levels (black, cyan, magenta, yellow), paper_tray_status, and job_queue_length

**Validates: Requirements 1.3**

### Property 3: Toner Alert Threshold

*For any* printer device with any toner level below 20%, a WARNING alert SHALL be generated

**Validates: Requirements 1.4**

### Property 4: Print Job Retention

*For any* print job audit record, it SHALL NOT be deleted before the configured retention period (minimum 90 days) has elapsed

**Validates: Requirements 2.3**

### Property 5: Print Job Correlation

*For any* print job with source_ip matching a monitored workstation device, the workstation_device_id field SHALL be populated with the matching device

**Validates: Requirements 3.2**

### Property 6: Agent UUID Stability

*For any* Server_Agent installation, the UUID generated on first run SHALL remain unchanged across agent restarts

**Validates: Requirements 4.1**

### Property 7: Device Identity IP Update

*For any* device with a UUID, when the IP address changes, the system SHALL update the existing device record rather than creating a duplicate

**Validates: Requirements 4.8**

### Property 8: Device Deduplication by UUID

*For any* two device records with matching UUID, the system SHALL merge them into a single device entry

**Validates: Requirements 6.1**

### Property 9: Device Deduplication by MAC

*For any* two device records with matching MAC address (and no UUID conflict), the system SHALL merge them into a single device entry

**Validates: Requirements 6.1**

### Property 10: Device Identity Priority

*For any* device identity conflict, UUID SHALL take precedence over MAC, MAC over hostname, and hostname over IP address

**Validates: Requirements 6.2**

### Property 11: Camera Three-Strike Alert

*For any* camera device with 3 consecutive failed connection attempts, a CRITICAL alert SHALL be generated

**Validates: Requirements 7.4**

### Property 12: Camera Frame Resolution Limit

*For any* captured camera frame, the resolution SHALL NOT exceed 1920x1080 pixels

**Validates: Requirements 8.2**

### Property 13: Polling Node Metric Attribution

*For any* metrics batch received from a polling node, all stored metrics SHALL be attributed to the correct polling_node_id

**Validates: Requirements 10.4**

### Property 14: Polling Node Offline Caching

*For any* metrics generated while the Central_Aggregator is unreachable, the Polling_Node SHALL cache them locally and forward them when connectivity is restored, with no data loss

**Validates: Requirements 10.8**

### Property 15: Alert Escalation Timing

*For any* unacknowledged alert with an active escalation policy, when the level delay period elapses, the alert SHALL escalate to the next level and notifications SHALL be sent

**Validates: Requirements 12.4**

### Property 16: Alert Escalation Halt on Acknowledgment

*For any* alert with active escalation, when acknowledged by any user, escalation SHALL halt immediately and no further notifications SHALL be sent

**Validates: Requirements 12.5**

### Property 17: Anomaly Detection Threshold

*For any* metric value that exceeds the baseline mean by more than 3 standard deviations (configurable sensitivity), the system SHALL flag it as an anomaly

**Validates: Requirements 14.2**

### Property 18: Capacity Forecast Calculation

*For any* device with 90 days of historical disk utilization data, the system SHALL calculate a linear regression forecast predicting when utilization will reach 90%

**Validates: Requirements 15.2**

### Property 19: SLA Maintenance Window Exclusion

*For any* SLA uptime calculation, time periods marked as maintenance windows SHALL be excluded from both uptime and downtime calculations

**Validates: Requirements 16.9**

### Property 20: Webhook Retry with Exponential Backoff

*For any* failed webhook delivery, the system SHALL retry up to 3 times with exponential backoff delays (60s, 120s, 240s)

**Validates: Requirements 17.5**

### Property 21: Bulk Operation Progress Tracking

*For any* bulk operation, the progress_current field SHALL be incremented after each device is processed, and SHALL equal progress_total when the operation completes

**Validates: Requirements 20.4**

### Property 22: API Rate Limiting

*For any* API token, when the number of requests in a sliding 1-hour window exceeds the configured rate_limit_per_hour, subsequent requests SHALL be rejected with HTTP 429

**Validates: Requirements 21.10**

### Property 23: Configuration Round-Trip

*For any* valid Configuration object, serializing (pretty-printing) then deserializing (parsing) SHALL produce an equivalent Configuration object

**Validates: Requirements 23.6**

### Property 24: Backward Compatible Metrics Ingestion

*For any* metrics payload from an older agent version missing optional fields, the system SHALL populate default values and successfully store the metrics without error

**Validates: Requirements 24.3**

### Property 25: Credential Encryption at Rest

*For any* RTSP camera credentials stored in the database, the password field SHALL be encrypted using AES-256 (not stored as plaintext)

**Validates: Requirements 25.1**

### Property 26: CSRF Protection on State-Changing Endpoints

*For any* state-changing API request (POST, PUT, DELETE) without a valid CSRF token, the system SHALL reject the request with HTTP 403

**Validates: Requirements 25.6**


## Testing Strategy

### Dual Testing Approach

The testing strategy employs both unit tests and property-based tests as complementary approaches:

- **Unit Tests**: Verify specific examples, edge cases, error conditions, and integration points
- **Property Tests**: Verify universal properties across all inputs through randomization

Together, these approaches provide comprehensive coverage: unit tests catch concrete bugs in specific scenarios, while property tests verify general correctness across the input space.

### Property-Based Testing Configuration

**Library Selection**: 
- Python: `hypothesis` (recommended for Flask/SQLAlchemy integration)
- Alternative: `pytest-quickcheck`

**Configuration**:
- Minimum 100 iterations per property test (due to randomization)
- Each property test MUST reference its design document property via comment tag
- Tag format: `# Feature: enterprise-monitoring-expansion, Property {number}: {property_text}`

**Example Property Test**:

```python
from hypothesis import given, strategies as st
import pytest

# Feature: enterprise-monitoring-expansion, Property 1: Printer Device Classification
@given(st.builds(SNMPDiscoveryResult, has_printer_mib=st.just(True)))
def test_printer_classification_property(discovery_result):
    """For any SNMP discovery result with Printer MIB, device type should be 'printer'"""
    device = classify_device(discovery_result)
    assert device.device_type == 'printer'
```

### Unit Testing Strategy

#### 1. Service Layer Tests

Test each service method with specific examples:

```python
def test_printer_monitoring_service_poll():
    """Test printer SNMP polling with known OID responses"""
    service = PrinterMonitoringService()
    metrics = service.poll_printer_snmp(device_id=1)
    assert metrics['status'] == 'idle'
    assert metrics['toner_black'] == 45

def test_camera_service_capture_frame():
    """Test camera frame capture and storage"""
    service = CameraMonitoringService()
    frame = service.capture_frame(device_id=1)
    assert frame.file_path.startswith('static/camera_frames/')
    assert frame.resolution == '1920x1080'
```

#### 2. API Endpoint Tests

Test API endpoints with specific requests:

```python
def test_api_sites_list(client, auth_headers):
    """Test GET /api/v1/sites returns site list"""
    response = client.get('/api/v1/sites', headers=auth_headers)
    assert response.status_code == 200
    assert 'data' in response.json

def test_api_rate_limiting(client, api_token):
    """Test API rate limiting after 1000 requests"""
    for i in range(1001):
        response = client.get('/api/v1/devices', headers={'X-API-Key': api_token})
    assert response.status_code == 429
```

#### 3. Worker Tests

Test worker task execution:

```python
def test_camera_worker_capture_success(app):
    """Test camera worker successfully captures frame"""
    with app.app_context():
        task = PollTask.enqueue(device_id=1, task_type='camera_capture')
        db.session.commit()
        
        result = execute_camera_capture(app, task.id)
        assert result[1] == True  # success
        
        task = PollTask.query.get(task.id)
        assert task.status == 'done'

def test_webhook_worker_retry_on_failure(app):
    """Test webhook worker retries failed deliveries"""
    with app.app_context():
        # Mock HTTP failure
        with patch('requests.post', side_effect=ConnectionError):
            task = PollTask.enqueue(device_id=1, task_type='webhook_delivery')
            db.session.commit()
            
            result = execute_webhook_delivery(app, task.id)
            
            task = PollTask.query.get(task.id)
            assert task.status == 'pending'  # Retrying
            assert task.retry_count == 1
```

#### 4. Model Tests

Test database models and relationships:

```python
def test_site_device_relationship():
    """Test Site-Device relationship"""
    site = Site(site_name='HQ', site_code='HQ01')
    device = Device(device_name='Server1', device_type='server', device_ip='10.0.0.1')
    device.site = site
    db.session.add(site)
    db.session.commit()
    
    assert device.site_id == site.id
    assert site.devices.count() == 1

def test_department_hierarchy():
    """Test Department parent-child hierarchy"""
    parent = Department(department_name='Engineering')
    child = Department(department_name='Backend', parent=parent)
    db.session.add_all([parent, child])
    db.session.commit()
    
    assert child.parent_department_id == parent.id
    assert parent.child_departments.count() == 1
```

### Integration Tests

Test end-to-end workflows:

```python
def test_printer_monitoring_workflow(app, client):
    """Test complete printer monitoring workflow"""
    # 1. Add printer device
    response = client.post('/api/v1/devices', json={
        'device_name': 'Printer1',
        'device_type': 'printer',
        'device_ip': '10.0.0.100'
    })
    device_id = response.json['data']['device_id']
    
    # 2. Enable SNMP monitoring
    client.post(f'/api/v1/devices/{device_id}/snmp', json={
        'enabled': True,
        'community': 'public'
    })
    
    # 3. Trigger poll
    with app.app_context():
        scheduler = MonitoringScheduler(app)
        scheduler.enqueue_printer_tasks()
        
        # Execute worker
        task = PollTask.query.filter_by(device_id=device_id, task_type='printer_snmp').first()
        execute_task(app, task.id)
    
    # 4. Verify metrics stored
    response = client.get(f'/api/v1/printers/{device_id}/metrics')
    assert response.status_code == 200
    assert 'toner_black' in response.json['data']

def test_alert_escalation_workflow(app):
    """Test alert escalation from generation to acknowledgment"""
    with app.app_context():
        # 1. Create escalation policy
        policy = AlertEscalationPolicy(policy_name='Critical Escalation')
        level1 = EscalationLevel(policy=policy, level_number=1, delay_minutes=5, recipients=['admin@example.com'])
        level2 = EscalationLevel(policy=policy, level_number=2, delay_minutes=10, recipients=['manager@example.com'])
        db.session.add_all([policy, level1, level2])
        db.session.commit()
        
        # 2. Generate alert
        alert_id = 123
        service = AlertEscalationService()
        service.initiate_escalation(alert_id, device)
        
        # 3. Verify level 1 scheduled
        state = AlertEscalationState.query.filter_by(alert_id=alert_id).first()
        assert state.current_level == 1
        assert state.next_escalation_time is not None
        
        # 4. Simulate time passing
        state.next_escalation_time = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        
        # 5. Process escalation
        service.process_escalation_queue()
        
        # 6. Verify escalated to level 2
        state = AlertEscalationState.query.filter_by(alert_id=alert_id).first()
        assert state.current_level == 2
        
        # 7. Acknowledge alert
        service.acknowledge_alert(alert_id, 'admin')
        
        # 8. Verify escalation halted
        state = AlertEscalationState.query.filter_by(alert_id=alert_id).first()
        assert state.acknowledged == True
        assert state.acknowledged_by == 'admin'
```

### Performance Tests

Test system performance under load:

```python
def test_bulk_operation_performance():
    """Test bulk operation on 1000 devices completes within 60 seconds"""
    device_ids = list(range(1, 1001))
    start_time = time.time()
    
    operation = BulkOperationsService().initiate_bulk_operation(
        operation_type='set_maintenance',
        device_ids=device_ids,
        parameters={'maintenance_mode': True},
        user='admin'
    )
    
    BulkOperationsService().execute_bulk_operation(operation.id)
    
    elapsed = time.time() - start_time
    assert elapsed < 60
    assert operation.successful_count == 1000

def test_api_response_time():
    """Test API endpoints respond within 200ms"""
    response_times = []
    for i in range(100):
        start = time.time()
        client.get('/api/v1/devices')
        response_times.append(time.time() - start)
    
    avg_response_time = sum(response_times) / len(response_times)
    assert avg_response_time < 0.2  # 200ms
```

### Security Tests

Test security features:

```python
def test_csrf_protection():
    """Test CSRF protection on state-changing endpoints"""
    response = client.post('/api/v1/devices', json={'device_name': 'Test'})
    assert response.status_code == 403

def test_department_isolation():
    """Test department isolation prevents unauthorized access"""
    user = User(username='dept_user', department_isolation_enabled=True)
    UserDepartmentAssignment(user=user, department_id=1)
    
    # Try to access device in different department
    device = Device(device_id=100, department_id=2)
    assert not DepartmentIsolationService().check_device_access(user, device.device_id)

def test_credential_encryption():
    """Test credentials are encrypted at rest"""
    camera = CameraDevice(device_id=1, rtsp_username='admin', rtsp_password_encrypted=b'...')
    db.session.add(camera)
    db.session.commit()
    
    # Verify password is not plaintext
    raw_query = db.session.execute(text('SELECT rtsp_password_encrypted FROM camera_devices WHERE id = 1'))
    encrypted = raw_query.scalar()
    assert encrypted != b'admin'  # Not plaintext
```

### Test Coverage Goals

- **Unit Tests**: 80% code coverage minimum
- **Property Tests**: All 26 correctness properties implemented
- **Integration Tests**: All major workflows covered
- **API Tests**: All endpoints tested (200+ endpoints)
- **Security Tests**: All security requirements validated


## Migration Strategy

### Database Migration Plan

#### Phase 1: Schema Creation (Zero Downtime)

```python
# migrations/001_enterprise_expansion.py

def upgrade():
    # 1. Create new tables
    op.create_table('sites', ...)
    op.create_table('departments', ...)
    op.create_table('printer_metrics', ...)
    op.create_table('print_job_audit', ...)
    op.create_table('camera_devices', ...)
    op.create_table('camera_frames', ...)
    op.create_table('polling_nodes', ...)
    op.create_table('alert_escalation_policies', ...)
    op.create_table('compliance_reports', ...)
    op.create_table('sla_metrics', ...)
    op.create_table('performance_baselines', ...)
    op.create_table('capacity_forecasts', ...)
    op.create_table('webhook_integrations', ...)
    op.create_table('custom_dashboards', ...)
    op.create_table('bulk_operations', ...)
    op.create_table('api_tokens', ...)
    op.create_table('configuration_snapshots', ...)
    
    # 2. Add columns to existing tables (nullable initially)
    op.add_column('device', sa.Column('site_id', sa.Integer(), nullable=True))
    op.add_column('device', sa.Column('department_id', sa.Integer(), nullable=True))
    op.add_column('device', sa.Column('agent_version', sa.String(20), nullable=True))
    op.add_column('device', sa.Column('last_agent_checkin', sa.DateTime(), nullable=True))
    
    op.add_column('user', sa.Column('department_isolation_enabled', sa.Boolean(), default=False))
    op.add_column('user', sa.Column('default_dashboard_id', sa.Integer(), nullable=True))
    op.add_column('user', sa.Column('session_timeout_minutes', sa.Integer(), default=30))
    
    # 3. Create indexes
    op.create_index('idx_device_site', 'device', ['site_id'])
    op.create_index('idx_device_department', 'device', ['department_id'])
    op.create_index('idx_printer_metrics_device_timestamp', 'printer_metrics', ['device_id', 'timestamp'])
    # ... (all indexes from schema design)
    
    # 4. Create foreign keys
    op.create_foreign_key('fk_device_site', 'device', 'sites', ['site_id'], ['id'])
    op.create_foreign_key('fk_device_department', 'device', 'departments', ['department_id'], ['id'])
    # ... (all foreign keys)

def downgrade():
    # Reverse all changes
    pass
```

#### Phase 2: Data Migration (Background Job)

```python
# scripts/migrate_enterprise_data.py

def migrate_existing_data():
    """
    Migrate existing data to new schema.
    Run as background job to avoid blocking.
    """
    
    # 1. Create default site for existing devices
    default_site = Site(site_name='Default Site', site_code='DEFAULT')
    db.session.add(default_site)
    db.session.commit()
    
    # 2. Assign all existing devices to default site
    Device.query.update({'site_id': default_site.id})
    db.session.commit()
    
    # 3. Create default department
    default_dept = Department(department_name='Default Department', department_code='DEFAULT')
    db.session.add(default_dept)
    db.session.commit()
    
    # 4. Identify printer devices and create CameraDevice records for cameras
    printers = Device.query.filter_by(device_type='printer').all()
    for printer in printers:
        # Printer devices already classified, no additional migration needed
        pass
    
    # 5. Create default escalation policy
    default_policy = AlertEscalationPolicy(policy_name='Default Escalation')
    level1 = EscalationLevel(policy=default_policy, level_number=1, delay_minutes=15, recipients=['admin@example.com'])
    db.session.add_all([default_policy, level1])
    db.session.commit()
    
    print("Data migration complete")
```

#### Phase 3: Agent Updates (Gradual Rollout)

```python
# server_agent.py v2.0 changes

def send_metrics_with_version():
    """
    Enhanced metrics payload with version info.
    Backward compatible: old fields still sent.
    """
    payload = {
        'agent_version': '2.0.0',  # NEW
        'client_id': get_client_uuid(),
        'hostname': socket.gethostname(),
        'ip_address': get_local_ip(),
        'mac_address': get_mac_address(),
        'metrics': {
            'cpu_usage': get_cpu_usage(),
            'memory_usage': get_memory_usage(),
            # ... existing metrics
        },
        'print_jobs': get_print_jobs() if is_print_server() else None,  # NEW
    }
    
    response = requests.post(f'{server_url}/api/agent/metrics', json=payload)
    return response.status_code == 200
```

### Deployment Strategy

#### 1. Pre-Deployment Checklist

- [ ] Database backup created
- [ ] PostgreSQL version >= 12 (for SKIP LOCKED support)
- [ ] Encryption key generated and stored in environment
- [ ] SMTP server configured for email notifications
- [ ] SSL certificates installed for HTTPS
- [ ] Redis instance available (optional, for session storage)

#### 2. Deployment Steps

```bash
# 1. Stop application
systemctl stop monitoring-app

# 2. Backup database
pg_dump monitoring_db > backup_$(date +%Y%m%d).sql

# 3. Pull latest code
git pull origin main

# 4. Install dependencies
pip install -r requirements.txt

# 5. Run migrations
flask db upgrade

# 6. Run data migration script
python scripts/migrate_enterprise_data.py

# 7. Start application
systemctl start monitoring-app

# 8. Start new workers
systemctl start camera-worker
systemctl start webhook-worker
systemctl start baseline-worker
systemctl start capacity-worker
systemctl start sla-worker
systemctl start compliance-worker

# 9. Verify health
curl http://localhost:5001/health
```

#### 3. Rollback Plan

```bash
# If issues occur, rollback:

# 1. Stop application and workers
systemctl stop monitoring-app camera-worker webhook-worker

# 2. Restore database
psql monitoring_db < backup_YYYYMMDD.sql

# 3. Revert code
git checkout previous-version

# 4. Restart application
systemctl start monitoring-app
```

### Agent Upgrade Strategy

#### Gradual Rollout

1. **Week 1**: Deploy v2.0 agents to 10% of servers (test group)
2. **Week 2**: Monitor for issues, deploy to 50% if stable
3. **Week 3**: Deploy to remaining 50%

#### Version Detection

```python
# routes/agent.py

@agent_bp.route('/api/agent/metrics', methods=['POST'])
def receive_metrics():
    data = request.json
    agent_version = data.get('agent_version', '1.0.0')  # Default to 1.0.0 if missing
    
    # Update device record
    device = Device.query.filter_by(agent_token=data['client_id']).first()
    if device:
        device.agent_version = agent_version
        device.last_agent_checkin = datetime.utcnow()
    
    # Handle version-specific fields
    if version.parse(agent_version) >= version.parse('2.0.0'):
        # Process new fields (print_jobs, etc.)
        if data.get('print_jobs'):
            process_print_jobs(device, data['print_jobs'])
    
    # Process standard metrics (backward compatible)
    process_metrics(device, data['metrics'])
    
    return jsonify({'success': True})
```


## Security Considerations

### 1. Encryption at Rest

#### AES-256 Encryption for Credentials

```python
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import os

class SecurityService:
    def __init__(self):
        # Get encryption key from environment (32 bytes for AES-256)
        self.key = os.environ.get('ENCRYPTION_KEY', '').encode()
        if len(self.key) != 32:
            raise ValueError('ENCRYPTION_KEY must be 32 bytes')
    
    def encrypt_aes256(self, plaintext: str) -> bytes:
        """Encrypt using AES-256-CBC"""
        iv = os.urandom(16)  # Random IV
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        
        # Pad plaintext to 16-byte boundary
        padded = self._pad(plaintext.encode())
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        
        # Return IV + ciphertext
        return iv + ciphertext
    
    def decrypt_aes256(self, ciphertext: bytes) -> str:
        """Decrypt AES-256-CBC"""
        iv = ciphertext[:16]
        encrypted = ciphertext[16:]
        
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        
        padded = decryptor.update(encrypted) + decryptor.finalize()
        plaintext = self._unpad(padded)
        
        return plaintext.decode()
```

#### Encrypted Fields

- `CameraDevice.rtsp_password_encrypted`: RTSP camera passwords
- `WebhookIntegration.auth_password_encrypted`: Webhook basic auth passwords
- `WebhookIntegration.auth_token_encrypted`: Webhook bearer tokens

### 2. Authentication and Authorization

#### API Token Authentication

```python
def verify_api_token(token: str) -> Optional[User]:
    """Verify API token and return associated user"""
    token_hash = bcrypt.generate_password_hash(token).decode('utf-8')
    
    api_token = APIToken.query.filter_by(token_hash=token_hash, enabled=True).first()
    if not api_token:
        return None
    
    # Check expiration
    if api_token.expires_at and api_token.expires_at < datetime.utcnow():
        return None
    
    # Update last used
    api_token.last_used_at = datetime.utcnow()
    db.session.commit()
    
    return api_token.user
```

#### Department Isolation Enforcement

```python
def enforce_department_isolation(user: User, query: Query) -> Query:
    """Apply department filter to query if isolation enabled"""
    if not user.department_isolation_enabled:
        return query
    
    # Get user's accessible departments (including children if permitted)
    accessible_dept_ids = DepartmentIsolationService().get_accessible_departments(user.id)
    
    # Filter query
    return query.filter(Device.department_id.in_(accessible_dept_ids))
```

### 3. CSRF Protection

```python
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect(app)

# Exempt API endpoints using token auth
@csrf.exempt
@require_api_token
def api_endpoint():
    pass

# Enforce CSRF on web UI endpoints
@app.route('/devices/delete/<int:id>', methods=['POST'])
@csrf.protect
def delete_device(id):
    pass
```

### 4. Input Sanitization

```python
import bleach
from sqlalchemy import text

def sanitize_input(user_input: str) -> str:
    """Sanitize user input to prevent XSS"""
    return bleach.clean(user_input, tags=[], strip=True)

def safe_query(query_template: str, params: dict):
    """Use parameterized queries to prevent SQL injection"""
    # GOOD: Parameterized query
    result = db.session.execute(text(query_template), params)
    
    # BAD: String concatenation (never do this)
    # result = db.session.execute(f"SELECT * FROM devices WHERE name = '{user_input}'")
```

### 5. HTTPS Enforcement

```python
# config.py
FORCE_HTTPS = os.environ.get('FORCE_HTTPS', 'true').lower() == 'true'

# app.py
@app.before_request
def enforce_https():
    if app.config['FORCE_HTTPS'] and not request.is_secure:
        if request.url.startswith('http://'):
            url = request.url.replace('http://', 'https://', 1)
            return redirect(url, code=301)
```

### 6. Password Complexity

```python
def validate_password_complexity(password: str) -> dict:
    """Validate password meets complexity requirements"""
    errors = []
    
    if len(password) < 12:
        errors.append('Password must be at least 12 characters')
    
    if not re.search(r'[A-Z]', password):
        errors.append('Password must contain uppercase letter')
    
    if not re.search(r'[a-z]', password):
        errors.append('Password must contain lowercase letter')
    
    if not re.search(r'[0-9]', password):
        errors.append('Password must contain digit')
    
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        errors.append('Password must contain special character')
    
    return {'valid': len(errors) == 0, 'errors': errors}
```

### 7. Session Security

```python
# config.py
SESSION_COOKIE_SECURE = True  # HTTPS only
SESSION_COOKIE_HTTPONLY = True  # No JavaScript access
SESSION_COOKIE_SAMESITE = 'Lax'  # CSRF protection
PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)  # Auto-logout

# Session timeout enforcement
@app.before_request
def check_session_timeout():
    if 'last_activity' in session:
        timeout = app.config.get('PERMANENT_SESSION_LIFETIME')
        if datetime.utcnow() - session['last_activity'] > timeout:
            session.clear()
            return redirect(url_for('auth_bp.login'))
    
    session['last_activity'] = datetime.utcnow()
```

### 8. Audit Logging

```python
def log_security_event(event_type: str, user: str, details: dict):
    """Log security-relevant events"""
    log_entry = {
        'timestamp': datetime.utcnow().isoformat(),
        'event_type': event_type,
        'user': user,
        'source_ip': request.remote_addr,
        'user_agent': request.user_agent.string,
        'details': details
    }
    
    # Log to database
    db.session.execute(
        text("INSERT INTO security_audit_log (event_type, user, source_ip, details, timestamp) VALUES (:type, :user, :ip, :details, :ts)"),
        {'type': event_type, 'user': user, 'ip': request.remote_addr, 'details': json.dumps(details), 'ts': datetime.utcnow()}
    )
    db.session.commit()
    
    # Log to file
    logger.warning(f"SECURITY: {event_type} by {user} from {request.remote_addr}: {details}")

# Usage
log_security_event('UNAUTHORIZED_ACCESS', user.username, {'resource': 'device', 'device_id': 123})
log_security_event('PASSWORD_CHANGE', user.username, {'success': True})
log_security_event('API_TOKEN_CREATED', user.username, {'token_name': 'Integration Token'})
```


## Performance Optimizations

### 1. Database Query Optimization

#### Index Strategy

```sql
-- Composite indexes for common query patterns
CREATE INDEX idx_device_site_type ON device(site_id, device_type) WHERE is_monitored = true;
CREATE INDEX idx_device_dept_type ON device(department_id, device_type) WHERE is_monitored = true;

-- Partial indexes for active records
CREATE INDEX idx_polling_node_active ON polling_nodes(last_heartbeat DESC) WHERE enabled = true AND status = 'online';
CREATE INDEX idx_escalation_pending ON alert_escalation_states(next_escalation_time) WHERE acknowledged = false;

-- Covering indexes for dashboard queries
CREATE INDEX idx_printer_metrics_latest ON printer_metrics(device_id, timestamp DESC) INCLUDE (toner_black, toner_cyan, toner_magenta, toner_yellow, status);
```

#### Query Optimization Patterns

```python
# BAD: N+1 query problem
devices = Device.query.all()
for device in devices:
    metrics = device.printer_metrics.order_by(PrinterMetrics.timestamp.desc()).first()  # N queries

# GOOD: Eager loading with subquery
from sqlalchemy.orm import joinedload

latest_metrics = db.session.query(
    PrinterMetrics.device_id,
    func.max(PrinterMetrics.timestamp).label('max_timestamp')
).group_by(PrinterMetrics.device_id).subquery()

devices = Device.query.join(
    latest_metrics, Device.device_id == latest_metrics.c.device_id
).join(
    PrinterMetrics,
    and_(
        PrinterMetrics.device_id == latest_metrics.c.device_id,
        PrinterMetrics.timestamp == latest_metrics.c.max_timestamp
    )
).all()  # Single query
```

### 2. Caching Strategy

#### Redis Caching for Dashboard Data

```python
import redis
import json

redis_client = redis.Redis(host='localhost', port=6379, db=0)

def get_dashboard_data(user_id: int, cache_ttl: int = 300):
    """Get dashboard data with Redis caching"""
    cache_key = f'dashboard:user:{user_id}'
    
    # Try cache first
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    
    # Compute dashboard data
    data = {
        'device_count': Device.query.filter_by(is_monitored=True).count(),
        'alert_count': get_active_alert_count(),
        'site_health': get_site_health_summary(),
        'sla_status': get_sla_dashboard(),
    }
    
    # Cache for 5 minutes
    redis_client.setex(cache_key, cache_ttl, json.dumps(data))
    
    return data

def invalidate_dashboard_cache(user_id: int):
    """Invalidate cache when data changes"""
    redis_client.delete(f'dashboard:user:{user_id}')
```

#### Application-Level Caching

```python
from functools import lru_cache
from datetime import datetime, timedelta

@lru_cache(maxsize=128)
def get_department_hierarchy(department_id: int) -> List[int]:
    """Cache department hierarchy lookups"""
    dept = Department.query.get(department_id)
    if not dept:
        return []
    
    hierarchy = [department_id]
    for child in dept.child_departments:
        hierarchy.extend(get_department_hierarchy(child.id))
    
    return hierarchy

# Invalidate cache when departments change
def on_department_update(department_id: int):
    get_department_hierarchy.cache_clear()
```

### 3. Batch Processing

#### Bulk Metric Insertion

```python
def store_metrics_batch(metrics_list: List[dict]):
    """Bulk insert metrics for performance"""
    # BAD: Individual inserts
    # for metrics in metrics_list:
    #     db.session.add(ServerHealthLog(**metrics))
    #     db.session.commit()  # N commits
    
    # GOOD: Bulk insert
    db.session.bulk_insert_mappings(ServerHealthLog, metrics_list)
    db.session.commit()  # Single commit
```

#### Batch Alert Processing

```python
def process_alerts_batch(device_ids: List[int]):
    """Process alerts for multiple devices in batch"""
    # Fetch all devices and latest metrics in single query
    devices = Device.query.filter(Device.device_id.in_(device_ids)).all()
    
    latest_metrics = db.session.query(
        ServerHealthLog.device_id,
        func.max(ServerHealthLog.timestamp).label('max_ts')
    ).filter(
        ServerHealthLog.device_id.in_(device_ids)
    ).group_by(ServerHealthLog.device_id).subquery()
    
    metrics = db.session.query(ServerHealthLog).join(
        latest_metrics,
        and_(
            ServerHealthLog.device_id == latest_metrics.c.device_id,
            ServerHealthLog.timestamp == latest_metrics.c.max_ts
        )
    ).all()
    
    # Process all alerts in memory
    alerts_to_create = []
    for device, metric in zip(devices, metrics):
        if metric.cpu_usage > 90:
            alerts_to_create.append({
                'device_id': device.device_id,
                'severity': 'CRITICAL',
                'message': f'CPU usage {metric.cpu_usage}%'
            })
    
    # Bulk insert alerts
    if alerts_to_create:
        db.session.bulk_insert_mappings(Alert, alerts_to_create)
        db.session.commit()
```

### 4. Connection Pooling

```python
# config.py
SQLALCHEMY_ENGINE_OPTIONS = {
    'pool_size': 20,  # Max connections in pool
    'max_overflow': 10,  # Additional connections when pool full
    'pool_timeout': 30,  # Seconds to wait for connection
    'pool_recycle': 3600,  # Recycle connections after 1 hour
    'pool_pre_ping': True,  # Test connections before use
}
```

### 5. Asynchronous Processing

#### Background Job Queue

```python
from celery import Celery

celery = Celery('monitoring', broker='redis://localhost:6379/0')

@celery.task
def generate_compliance_report_async(report_id: int):
    """Generate compliance report asynchronously"""
    with app.app_context():
        service = ComplianceReportingService()
        execution = service.generate_report(report_id, period_start, period_end)
        return execution.id

# Usage
task = generate_compliance_report_async.delay(report_id=1)
task_id = task.id  # Return to user for status polling
```

### 6. Pagination and Lazy Loading

```python
@app.route('/api/v1/devices')
def list_devices():
    """List devices with pagination"""
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 500)  # Max 500
    
    # Use pagination to avoid loading all records
    pagination = Device.query.paginate(page=page, per_page=per_page, error_out=False)
    
    return jsonify({
        'success': True,
        'data': [d.to_dict() for d in pagination.items],
        'meta': {
            'page': page,
            'per_page': per_page,
            'total': pagination.total,
            'total_pages': pagination.pages
        }
    })
```

### 7. Database Partitioning

```sql
-- Partition large tables by time for performance
CREATE TABLE printer_metrics (
    id SERIAL,
    device_id INTEGER NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    -- ... other columns
) PARTITION BY RANGE (timestamp);

-- Create monthly partitions
CREATE TABLE printer_metrics_2024_01 PARTITION OF printer_metrics
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');

CREATE TABLE printer_metrics_2024_02 PARTITION OF printer_metrics
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');

-- Automatic partition creation via cron job
CREATE OR REPLACE FUNCTION create_next_month_partition()
RETURNS void AS $$
DECLARE
    next_month DATE := date_trunc('month', CURRENT_DATE + INTERVAL '1 month');
    partition_name TEXT := 'printer_metrics_' || to_char(next_month, 'YYYY_MM');
BEGIN
    EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF printer_metrics FOR VALUES FROM (%L) TO (%L)',
        partition_name,
        next_month,
        next_month + INTERVAL '1 month'
    );
END;
$$ LANGUAGE plpgsql;
```

### 8. Monitoring and Profiling

```python
from flask import g
import time

@app.before_request
def before_request():
    g.start_time = time.time()

@app.after_request
def after_request(response):
    if hasattr(g, 'start_time'):
        elapsed = time.time() - g.start_time
        
        # Log slow requests
        if elapsed > 1.0:  # > 1 second
            logger.warning(f"SLOW REQUEST: {request.path} took {elapsed:.2f}s")
        
        # Add timing header
        response.headers['X-Response-Time'] = f'{elapsed:.3f}s'
    
    return response

# Database query profiling
from sqlalchemy import event
from sqlalchemy.engine import Engine

@event.listens_for(Engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault('query_start_time', []).append(time.time())

@event.listens_for(Engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    total = time.time() - conn.info['query_start_time'].pop(-1)
    if total > 0.5:  # > 500ms
        logger.warning(f"SLOW QUERY ({total:.2f}s): {statement[:200]}")
```

### Performance Targets

- **API Response Time**: < 200ms (p95)
- **Dashboard Load Time**: < 1 second
- **Bulk Operation**: 1000 devices in < 60 seconds
- **Report Generation**: < 30 seconds for 90-day period
- **Database Queries**: < 100ms (p95)
- **Worker Task Processing**: < 5 seconds per task
- **Concurrent Users**: Support 100+ simultaneous users
- **Device Scale**: Support 10,000+ monitored devices


## Implementation Roadmap

### Phase 1: Foundation (Weeks 1-2)

**Database Schema**
- Create all new tables and indexes
- Add columns to existing Device and User models
- Run migrations on development environment

**Core Services**
- SecurityService (encryption, authentication)
- DepartmentIsolationService (RBAC foundation)
- ConfigurationService (import/export)

**Testing**
- Unit tests for new models
- Property tests for encryption round-trip
- Integration tests for department isolation

### Phase 2: Printer Monitoring (Weeks 3-4)

**Implementation**
- PrinterMonitoringService
- Printer SNMP polling (RFC 3805 MIB)
- Print server agent extensions (Windows Event Log, CUPS)
- Print job audit trail

**Workers**
- Extend SNMP worker for printer task type
- Add printer alert thresholds

**API**
- Printer endpoints (/api/v1/printers)
- Print audit trail endpoint

**Testing**
- Property tests for printer classification
- Property tests for toner alert thresholds
- Integration tests for print job correlation

### Phase 3: Camera Integration (Weeks 5-6)

**Implementation**
- CameraMonitoringService
- RTSP connection handling (OpenCV)
- Frame capture and storage
- Frame cleanup worker

**Workers**
- Camera worker (frame capture)
- Scheduled cleanup job

**API**
- Camera endpoints (/api/v1/cameras)
- Camera gallery endpoint

**Testing**
- Property tests for frame resolution limits
- Property tests for 3-strike camera alerts
- Integration tests for RTSP connection

### Phase 4: Multi-Site and Distributed Polling (Weeks 7-9)

**Implementation**
- Site management
- PollingNodeService
- Polling node registration and heartbeat
- Metric forwarding and aggregation
- Device-to-node assignment

**Workers**
- Polling node health checks
- Metric cache and forward

**API**
- Sites endpoints (/api/v1/sites)
- Polling nodes endpoints (/api/v1/polling-nodes)

**Testing**
- Property tests for metric attribution
- Property tests for offline caching
- Integration tests for distributed polling

### Phase 5: Advanced Alerting (Weeks 10-11)

**Implementation**
- AlertEscalationService
- Escalation policies and levels
- Escalation state machine
- Notification delivery

**Workers**
- Escalation queue processor
- Webhook worker

**API**
- Escalation policy endpoints
- Webhook integration endpoints

**Testing**
- Property tests for escalation timing
- Property tests for acknowledgment halt
- Property tests for webhook retry
- Integration tests for end-to-end escalation

### Phase 6: Compliance and Reporting (Weeks 12-13)

**Implementation**
- ComplianceReportingService
- Report templates (SOC2, ISO27001, HIPAA, PCI DSS)
- PDF and Excel rendering
- Scheduled report generation

**Workers**
- Compliance report worker

**API**
- Compliance report endpoints

**Testing**
- Unit tests for report generation
- Integration tests for scheduled reports

### Phase 7: Performance Analytics (Weeks 14-16)

**Implementation**
- BaselineAnomalyService (30-day rolling baselines)
- CapacityPlanningService (linear regression forecasting)
- SLATrackingService (uptime, response time, resolution time)

**Workers**
- Baseline calculator worker
- Capacity forecast worker
- SLA calculator worker

**API**
- Baselines endpoints
- Capacity forecasts endpoints
- SLA metrics endpoints

**Testing**
- Property tests for anomaly detection
- Property tests for capacity forecasting
- Property tests for SLA maintenance window exclusion
- Integration tests for baseline calculation

### Phase 8: Custom Dashboards and RBAC (Weeks 17-18)

**Implementation**
- DashboardService
- Dashboard widget framework
- Department hierarchy
- Department isolation enforcement

**API**
- Dashboard endpoints
- Department endpoints

**Testing**
- Unit tests for widget data fetching
- Property tests for department isolation
- Integration tests for dashboard sharing

### Phase 9: Bulk Operations and API (Weeks 19-20)

**Implementation**
- BulkOperationsService
- Async bulk operation execution
- CSV import/export
- API token management
- Rate limiting

**API**
- Bulk operations endpoints
- API token endpoints
- Complete REST API documentation (OpenAPI)

**Testing**
- Property tests for bulk operation progress
- Property tests for API rate limiting
- Performance tests for bulk operations

### Phase 10: Mobile UI and Polish (Weeks 21-22)

**Implementation**
- Mobile-responsive CSS
- Touch-friendly controls
- Lazy loading and optimization
- Configuration import/export UI

**Testing**
- Mobile browser testing
- Property tests for configuration round-trip
- End-to-end UI tests

### Phase 11: Security Hardening (Week 23)

**Implementation**
- CSRF protection
- Input sanitization
- HTTPS enforcement
- Password complexity validation
- Security audit logging

**Testing**
- Security tests for all requirements
- Penetration testing
- Vulnerability scanning

### Phase 12: Documentation and Deployment (Week 24)

**Documentation**
- API documentation (OpenAPI/Swagger)
- Administrator guide
- Agent deployment guide
- Troubleshooting guide

**Deployment**
- Production deployment scripts
- Monitoring and alerting setup
- Backup and disaster recovery procedures
- Performance tuning

## Summary

This design document specifies the architecture for expanding the Device Monitoring Tactical system with enterprise-grade capabilities. The expansion adds 18 new database models, 14 new services, 6 new worker processes, and 200+ REST API endpoints while maintaining backward compatibility with existing agents.

Key architectural decisions:
- **Scheduler/Worker Separation**: Maintains existing pattern of task enqueueing (scheduler) vs execution (workers)
- **Device Identity Hierarchy**: Preserves UUID > MAC > Hostname > IP priority
- **Distributed Polling**: Enables multi-site deployments with local polling nodes
- **Property-Based Testing**: 26 correctness properties ensure system reliability
- **Security-First**: AES-256 encryption, CSRF protection, department isolation
- **Performance**: Optimized for 10,000+ devices with sub-200ms API response times

The implementation follows a 24-week roadmap with incremental delivery of features, comprehensive testing at each phase, and gradual agent rollout to minimize risk.

