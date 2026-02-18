"""
PollTask — DB-backed task queue for SNMP polling.

Decouples scheduling (enqueue) from execution (worker).
Designed for horizontal scaling: SELECT FOR UPDATE SKIP LOCKED
prevents duplicate execution across multiple worker instances.
"""
from datetime import datetime, timedelta
from extensions import db


class PollTask(db.Model):
    __tablename__ = 'poll_tasks'

    # ── Identity ───────────────────────────────────────────────
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(
        db.Integer,
        db.ForeignKey('device.device_id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    task_type = db.Column(
        db.String(30),
        nullable=False,
        index=True
    )  # snmp_health | interface | discovery

    # ── State ──────────────────────────────────────────────────
    status = db.Column(
        db.String(20),
        nullable=False,
        default='pending',
        index=True
    )  # pending | running | done | failed
    priority = db.Column(
        db.Integer,
        nullable=False,
        default=5,
        index=True
    )  # 1 = critical, 5 = standard, 9 = low

    # ── Scheduling ─────────────────────────────────────────────
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    next_run_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    # ── Retry ──────────────────────────────────────────────────
    retry_count = db.Column(db.Integer, default=0)
    MAX_RETRIES = 3

    # ── Error Tracking ─────────────────────────────────────────
    error_code = db.Column(db.String(50), nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    # ── Relationships ──────────────────────────────────────────
    device = db.relationship(
        'Device',
        backref=db.backref('poll_tasks', lazy='dynamic'),
        foreign_keys=[device_id]
    )

    # ── Composite Index (idempotency queries) ──────────────────
    __table_args__ = (
        db.Index(
            'idx_poll_task_device_type_status',
            'device_id', 'task_type', 'status'
        ),
        db.Index(
            'idx_poll_task_pending_queue',
            'status', 'next_run_at', 'priority', 'created_at'
        ),
    )

    # ── Task Lifecycle Methods ─────────────────────────────────

    @property
    def is_retriable(self):
        """Can this task be retried (under max retry limit)?"""
        return self.retry_count < self.MAX_RETRIES

    def mark_running(self):
        """Transition: pending → running."""
        self.status = 'running'
        self.started_at = datetime.utcnow()
        self.error_code = None
        self.error_message = None

    def mark_done(self):
        """Transition: running → done."""
        self.status = 'done'
        self.finished_at = datetime.utcnow()
        self.error_code = None
        self.error_message = None

    def mark_failed(self, error_code: str, error_message: str):
        """
        Transition: running → failed or running → pending (retry).

        Exponential backoff: next_run_at = now + 2^retry_count seconds.
        After MAX_RETRIES, status stays 'failed' permanently.
        """
        self.retry_count += 1
        self.error_code = error_code
        self.error_message = error_message[:500] if error_message else None

        if self.is_retriable:
            # Re-queue with exponential backoff
            backoff_seconds = 2 ** self.retry_count
            self.status = 'pending'
            self.next_run_at = datetime.utcnow() + timedelta(seconds=backoff_seconds)
            self.started_at = None
        else:
            # Exhausted retries — mark permanently failed
            self.status = 'failed'
            self.finished_at = datetime.utcnow()

    # ── Class Methods (Querying) ───────────────────────────────

    @classmethod
    def has_pending_or_running(cls, device_id: int, task_type: str) -> bool:
        """Check if a task already exists for this device+type that isn't complete."""
        return db.session.query(
            cls.query.filter(
                cls.device_id == device_id,
                cls.task_type == task_type,
                cls.status.in_(['pending', 'running'])
            ).exists()
        ).scalar()

    @classmethod
    def enqueue(cls, device_id: int, task_type: str, priority: int = 5):
        """
        Create a new PollTask if no pending/running task exists.
        Returns the task if created, None if duplicate.
        """
        if cls.has_pending_or_running(device_id, task_type):
            return None

        task = cls(
            device_id=device_id,
            task_type=task_type,
            priority=priority,
            status='pending',
            next_run_at=datetime.utcnow(),
        )
        db.session.add(task)
        return task

    def __repr__(self):
        return (
            f'<PollTask {self.id} device={self.device_id} '
            f'type={self.task_type} status={self.status} '
            f'retries={self.retry_count}>'
        )
