import os
import sys
import time
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import datetime

import requests
from apscheduler.schedulers.blocking import BlockingScheduler

# Add the project directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from config import Config
from extensions import db, redis_client
from models.tracked_device import TrackedDevice

# Configure logging for the background worker
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [TrackingWorker] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Constants
CHECK_INTERVAL_SECONDS = 15
MAX_CONCURRENT_WORKERS = 20
NETWORK_TIMEOUT_SECONDS = 2.5
REDIS_TTL_SECONDS = 60

app = create_app()


def _map_probe_error(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.Timeout):
        return 'AGENT_TIMEOUT'
    if isinstance(exc, requests.exceptions.ProxyError):
        return 'AGENT_PROXY_BLOCKED'
    if isinstance(exc, requests.exceptions.ConnectionError):
        return 'AGENT_UNREACHABLE'
    return 'AGENT_REQUEST_FAILED'


def _parse_json_payload(response: requests.Response) -> dict:
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _build_offline_result(error_code: str, method: str = 'none') -> dict:
    return {
        'status': 'offline',
        'availability_status': 'offline',
        'tracking_data': {},
        'metrics_available': False,
        'probe_error_code': error_code,
        'probe_method': method,
    }


def probe_single_device(device_id: int, ip_address: str, shared_api_key: str):
    """
    Probe one tracking agent endpoint and classify availability state.
    Returns (device_id, probe_result, timestamp_utc).
    """
    run_at = datetime.utcnow()

    if not ip_address:
        return device_id, _build_offline_result('DEVICE_NO_IP'), run_at

    base_url = f"http://{ip_address}:5002"
    identity_data = {}
    probe_error_code = None

    try:
        with requests.Session() as session:
            session.trust_env = False

            # 1) Identity probe
            try:
                response = session.get(
                    f"{base_url}/api/identity",
                    timeout=NETWORK_TIMEOUT_SECONDS,
                )
                if response.status_code == 200:
                    identity_data = _parse_json_payload(response)
                else:
                    probe_error_code = f'IDENTITY_HTTP_{response.status_code}'
            except requests.exceptions.RequestException as exc:
                probe_error_code = _map_probe_error(exc)

            # 2) Full stats probe
            try:
                headers = {'X-API-Key': shared_api_key} if shared_api_key else {}
                response = session.get(
                    f"{base_url}/api/secure/stats",
                    timeout=NETWORK_TIMEOUT_SECONDS,
                    headers=headers,
                )
                if response.status_code == 200:
                    stats_data = _parse_json_payload(response)
                    if identity_data:
                        device_info = stats_data.get('device_info')
                        if isinstance(device_info, dict):
                            for key, value in identity_data.items():
                                device_info.setdefault(key, value)
                        else:
                            stats_data['device_info'] = identity_data

                    metrics_available = bool(
                        stats_data.get('system_metrics') or
                        stats_data.get('today_stats') or
                        stats_data.get('current_activity')
                    )
                    return device_id, {
                        'status': 'online' if metrics_available else 'degraded',
                        'availability_status': 'online' if metrics_available else 'degraded',
                        'tracking_data': stats_data,
                        'metrics_available': metrics_available,
                        'probe_error_code': None if metrics_available else (probe_error_code or 'STATS_PAYLOAD_EMPTY'),
                        'probe_method': 'stats',
                    }, run_at

                if not probe_error_code:
                    probe_error_code = f'STATS_HTTP_{response.status_code}'
            except requests.exceptions.RequestException as exc:
                if not probe_error_code:
                    probe_error_code = _map_probe_error(exc)

            # 3) Identity reachable fallback -> degraded
            if identity_data:
                return device_id, {
                    'status': 'degraded',
                    'availability_status': 'degraded',
                    'tracking_data': {'device_info': identity_data},
                    'metrics_available': False,
                    'probe_error_code': probe_error_code,
                    'probe_method': 'identity',
                }, run_at

            # 4) Health fallback -> degraded
            try:
                response = session.get(
                    f"{base_url}/api/health",
                    timeout=NETWORK_TIMEOUT_SECONDS,
                )
                if response.status_code == 200:
                    return device_id, {
                        'status': 'degraded',
                        'availability_status': 'degraded',
                        'tracking_data': {},
                        'metrics_available': False,
                        'probe_error_code': probe_error_code,
                        'probe_method': 'health',
                    }, run_at
                if not probe_error_code:
                    probe_error_code = f'HEALTH_HTTP_{response.status_code}'
            except requests.exceptions.RequestException as exc:
                if not probe_error_code:
                    probe_error_code = _map_probe_error(exc)
                if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
                    raise exc

            return device_id, _build_offline_result(probe_error_code or 'AGENT_UNREACHABLE'), run_at
    except requests.exceptions.RequestException as exc:
        # Short-circuited from above
        probe_error_code = _map_probe_error(exc)
        
        # Ping fallback
        from routes.tracking import _ping_host
        host_alive = _ping_host(ip_address, timeout=1.0)
        if host_alive:
            logger.info("[TrackingWorker] Host %s is UP but Agent is DOWN/MISSING (code=%s)", ip_address, probe_error_code)
            return device_id, {
                'status': 'degraded',
                'availability_status': 'degraded',
                'tracking_data': {'device_info': identity_data},
                'metrics_available': False,
                'probe_error_code': probe_error_code,
                'probe_method': 'none',
                'agent_missing_on_host': True # Mark for UI/Reports
            }, run_at
            
        return device_id, _build_offline_result(probe_error_code), run_at
    except Exception as e:
        logger.error(f"Failed checking {ip_address} (ID {device_id}): {e}")
        return device_id, _build_offline_result('AGENT_REQUEST_FAILED'), run_at


def _normalize_probe_result(result: dict) -> dict:
    availability_status = str(result.get('availability_status') or result.get('status') or 'offline').lower()
    if availability_status not in ('online', 'degraded', 'offline'):
        availability_status = 'offline'

    tracking_payload = result.get('tracking_data')
    if not isinstance(tracking_payload, dict):
        tracking_payload = {}

    return {
        'status': availability_status,
        'availability_status': availability_status,
        'tracking_data': tracking_payload,
        'metrics_available': bool(result.get('metrics_available')),
        'probe_error_code': result.get('probe_error_code'),
        'probe_method': result.get('probe_method') or ('stats' if availability_status != 'offline' else 'none'),
    }

def tracking_sync_job():
    """
    Main background job function executed by APScheduler.
    Fetches all devices, pings them concurrently, and commits the results in one batch.
    """
    start_time = time.time()
    
    with app.app_context():
        devices = TrackedDevice.query.all()
        if not devices:
            logger.info("No tracked devices found. Skipping cycle.")
            return

        device_count = len(devices)
        logger.info(f"Starting tracking sync cycle for {device_count} devices...")
        shared_api_key = Config.API_KEY
        results_map = {}

        # Concurrently ping devices
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS) as executor:
            future_to_device = {
                executor.submit(probe_single_device, device.id, device.ip_address, shared_api_key): device.id
                for device in devices
            }

            wait_timeout = max(5, int(len(future_to_device) * NETWORK_TIMEOUT_SECONDS * 2))
            try:
                for future in as_completed(future_to_device, timeout=wait_timeout):
                    try:
                        dev_id, result, run_time = future.result()
                        results_map[dev_id] = (_normalize_probe_result(result), run_time)
                    except Exception as exc:
                        dev_id = future_to_device[future]
                        logger.error(f"Device {dev_id} generated an exception: {exc}")
                        results_map[dev_id] = (_build_offline_result('AGENT_REQUEST_FAILED'), datetime.utcnow())
            except FuturesTimeoutError:
                logger.warning("Tracking sync timed out waiting for one or more probe futures.")

            for future, dev_id in future_to_device.items():
                if dev_id in results_map:
                    continue
                if future.done():
                    try:
                        _, result, run_time = future.result()
                        results_map[dev_id] = (_normalize_probe_result(result), run_time)
                        continue
                    except Exception:
                        pass
                results_map[dev_id] = (_build_offline_result('AGENT_TIMEOUT'), datetime.utcnow())

        # Update database models with results
        updates = 0
        for device in devices:
            if device.id in results_map:
                result, run_time = results_map[device.id]

                availability_status = result.get('availability_status', 'offline')
                tracking_data = result.get('tracking_data') if isinstance(result.get('tracking_data'), dict) else {}
                metrics_available = bool(result.get('metrics_available', False))

                device.availability_status = availability_status
                device.last_probe_at = run_time
                device.probe_error_code = result.get('probe_error_code')
                device.probe_method = result.get('probe_method')
                device.metrics_available = metrics_available

                if tracking_data:
                    device.tracking_data = json.dumps(tracking_data)
                else:
                    device.tracking_data = None

                if availability_status in ('online', 'degraded'):
                    device.last_seen = run_time

                if redis_client and device.mac_address:
                    try:
                        redis_client.setex(
                            f"tracking:probe:{device.mac_address}",
                            REDIS_TTL_SECONDS,
                            json.dumps({
                                'status': availability_status,
                                'availability_status': availability_status,
                                'metrics_available': metrics_available,
                                'probe_error_code': result.get('probe_error_code'),
                                'probe_method': result.get('probe_method'),
                                'last_probe_at': run_time.isoformat(),
                                'tracking_data': tracking_data,
                            }),
                        )
                    except Exception as e:
                        logger.debug(f"Failed to sync probe to Redis: {e}")

                updates += 1

        # Commit all changes at once
        try:
            db.session.commit()
            duration = time.time() - start_time
            logger.info(f"Completed tracking sync cycle for {updates} devices in {duration:.2f}s.")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Database commit failed during sync cycle: {e}")

if __name__ == '__main__':
    scheduler = BlockingScheduler()
    scheduler.add_job(
        id='tracking_sync_job',
        func=tracking_sync_job,
        trigger='interval',
        seconds=CHECK_INTERVAL_SECONDS,
        max_instances=1,  # Prevent overlapping cycles if DB locks
        coalesce=True
    )
    
    logger.info(f"Starting APScheduler... Interval: {CHECK_INTERVAL_SECONDS}s, Concurrency: {MAX_CONCURRENT_WORKERS}")
    
    # Run the first job immediately upon startup
    try:
        tracking_sync_job()
    except Exception as e:
        logger.error(f"Initial job run failed: {e}")
        
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shutting down...")
