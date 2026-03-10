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

                # We pass worker_compute=true to bypass the O(1) fetch and force the actual calculation
                response = client.get('/api/dashboard/full_snapshot?worker_compute=true')

                if response.status_code == 200:
                    # Parse the payload from the response data to ensure we got a valid JSON
                    payload_dict = json.loads(response.data)

                    # Dump it back to a compact raw string for the DB
                    raw_json_string = json.dumps(payload_dict)

                    cache_key = 'full_snapshot_24h_active_200'

                    # Upsert into DashboardSnapshot table
                    snapshot = DashboardSnapshot.query.filter_by(cache_key=cache_key).first()
                    if not snapshot:
                        snapshot = DashboardSnapshot(cache_key=cache_key, payload=raw_json_string)
                        db.session.add(snapshot)
                    else:
                        snapshot.payload = raw_json_string
                        snapshot.updated_at = datetime.utcnow()

                    db.session.commit()
                    duration = time.time() - start_time
                    logger.info("Dashboard Snapshot successfully updated in %.2f seconds.", duration)
                else:
                    logger.error("Failed to compute dashboard snapshot. HTTP %s", response.status_code)

            except Exception as error:
                db.session.rollback()
                logger.error("Exception during snapshot computation: %s", error, exc_info=True)
            finally:
                db.session.remove()


if __name__ == '__main__':
    logger.info("Initializing Dashboard Aggregation Worker...")

    # Run once immediately on startup
    compute_and_store_snapshot()

    # Schedule to run every 25 seconds
    scheduler = BlockingScheduler()
    scheduler.add_job(compute_and_store_snapshot, 'interval', seconds=25, max_instances=1, coalesce=True)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down Dashboard Worker.")
