import json
import logging
import os
import sys
import time
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

# Add the project directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from extensions import db
from models.dashboard import DashboardSnapshot

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('dashboard_worker')

app = create_app()

DEFAULT_SCOPE_FRAGMENT = 'admin__global'
DEFAULT_TIME_RANGE = '24h'
DEFAULT_ALERT_STATUS = 'active'
DEFAULT_ALERT_LIMIT = '200'
# extend WORKER_SCOPES env var to warm more combos
raw = os.environ.get(
    "WORKER_SCOPES",
    "admin__global:24h:active:200"
)
SCOPES = []
for scope_entry in raw.split(","):
    parts = [part.strip() for part in scope_entry.strip().split(":")]
    if len(parts) != 4 or any(not part for part in parts):
        logger.warning("Skipping invalid WORKER_SCOPES entry: %r", scope_entry)
        continue
    SCOPES.append(parts)

if not SCOPES:
    SCOPES = [[DEFAULT_SCOPE_FRAGMENT, DEFAULT_TIME_RANGE, DEFAULT_ALERT_STATUS, DEFAULT_ALERT_LIMIT]]


def _snapshot_cache_key(
    scope_fragment: str = DEFAULT_SCOPE_FRAGMENT,
    time_range: str = DEFAULT_TIME_RANGE,
    alert_status: str = DEFAULT_ALERT_STATUS,
    alert_limit: str = DEFAULT_ALERT_LIMIT,
) -> str:
    return f"full_snapshot_{scope_fragment}_{time_range}_{alert_status}_{alert_limit}"


def compute_and_store_snapshot():
    """
    Computes the full dashboard snapshot and stores it in the database natively.
    Runs globally exactly once per interval.
    """
    start_time = time.time()
    logger.info("Starting Dashboard Snapshot aggregation cycle...")

    with app.app_context():
        # Using the test client to invoke the internal routing logic cleanly
        # and letting it assemble all sub-components using existing handlers.
        with app.test_client() as client:
            try:
                with client.session_transaction() as sess:
                    sess['logged_in'] = True
                    sess['role'] = 'admin'
                    sess['user_id'] = 'dashboard-worker'

                for scope_fragment, time_range, alert_status, alert_limit in SCOPES:
                    scope_start_time = time.time()
                    response = client.get(
                        '/api/dashboard/full_snapshot'
                        f'?worker_compute=true&range={time_range}&status={alert_status}&limit={alert_limit}'
                    )

                    if response.status_code == 200:
                        # Parse the payload from the response data to ensure we got a valid JSON
                        payload_dict = json.loads(response.data)

                        # Dump it back to a compact raw string for the DB
                        raw_json_string = json.dumps(payload_dict)

                        cache_key = _snapshot_cache_key(
                            scope_fragment=scope_fragment,
                            time_range=time_range,
                            alert_status=alert_status,
                            alert_limit=alert_limit,
                        )

                        # Upsert into DashboardSnapshot table
                        snapshot = DashboardSnapshot.query.filter_by(cache_key=cache_key).first()
                        if not snapshot:
                            snapshot = DashboardSnapshot(cache_key=cache_key, payload=raw_json_string)
                            db.session.add(snapshot)
                        else:
                            snapshot.payload = raw_json_string
                            snapshot.updated_at = datetime.utcnow()

                        db.session.commit()
                        logger.info(
                            "Dashboard Snapshot warmed for %s in %.2f seconds.",
                            cache_key,
                            time.time() - scope_start_time,
                        )
                    else:
                        logger.error(
                            "Failed to compute dashboard snapshot for %s. HTTP %s",
                            _snapshot_cache_key(
                                scope_fragment=scope_fragment,
                                time_range=time_range,
                                alert_status=alert_status,
                                alert_limit=alert_limit,
                            ),
                            response.status_code,
                        )

                logger.info(
                    "Dashboard Snapshot aggregation cycle completed in %.2f seconds.",
                    time.time() - start_time,
                )

            except Exception as error:
                db.session.rollback()
                logger.error("Exception during snapshot computation: %s", error, exc_info=True)
            finally:
                db.session.remove()


if __name__ == '__main__':
    logger.info("Initializing Dashboard Aggregation Worker...")
    logger.info(
        "Warming dashboard scopes: %s",
        ", ".join(":".join(scope_parts) for scope_parts in SCOPES),
    )

    # Run once immediately on startup
    compute_and_store_snapshot()

    # Schedule to run every 25 seconds
    scheduler = BlockingScheduler()
    scheduler.add_job(compute_and_store_snapshot, 'interval', seconds=25, max_instances=1, coalesce=True)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down Dashboard Worker.")
