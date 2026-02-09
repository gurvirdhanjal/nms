import time
import threading
from extensions import db
from models.device import Device
from sqlalchemy.exc import OperationalError

class InterfacePoller:
    """
    Service to poll interface statistics from devices and store history.
    Simulation disabled: only real backend data should be stored.
    """
    def __init__(self):
        self._stop_event = threading.Event()
        self._thread = None
        self._app = None

    def start_polling(self, app):
        """Start the background polling thread"""
        self._app = app
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            print("Interface Poller Service started.")

    def stop_polling(self):
        """Stop the background polling thread"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
            print("Interface Poller Service stopped.")

    def _run_loop(self):
        """Main polling loop"""
        while not self._stop_event.is_set():
            try:
                with self._app.app_context():
                    try:
                        self._poll_all_devices()
                    finally:
                        db.session.remove()
            except Exception as e:
                print(f"Error in interface poller loop: {e}")

            
            # Sleep for 10 seconds (aggressive polling for real-time feel)
            # In production, this should be configurable
            interval = 10
            if self._app:
                interval = self._app.config.get('INTERFACE_POLL_INTERVAL', 10)
            time.sleep(interval)

    def _poll_all_devices(self):
        """Polls all monitored devices (no simulation fallback)."""
        devices = Device.query.filter_by(is_monitored=True).all()
        
        for device in devices:
            for attempt in range(3):
                try:
                    # TRY REAL SNMP POLL
                    # In a real scenario, we'd check if SNMP is enabled for this device.
                    # No simulation fallback is allowed.
                    success = self._poll_device_real(device)

                    # Commit per device to prevent long-running transactions (SQLite Lock Fix)
                    db.session.commit()

                    # Yield to let other threads write
                    time.sleep(0.05)
                    break
                except OperationalError as e:
                    db.session.rollback()
                    if "database is locked" in str(e).lower() and attempt < 2:
                        # Backoff and retry
                        time.sleep(0.2 * (attempt + 1))
                        continue
                    print(f"Error polling device {device.device_id}: {e}")
                    break
                except Exception as e:
                    db.session.rollback()
                    print(f"Error polling device {device.device_id}: {e}")
                    break
            # Ensure connections are returned to pool per device
            db.session.remove()

    def _poll_device_real(self, device) -> bool:
        """
        Attempt to poll real SNMP data.
        Returns True if successful, False if failed/no-response.
        """
        # Placeholder for real SNMP retrieval logic:
        # 1. Get credentials (snmp_config)
        # 2. snmp_service.get_interface_counters(...)
        # 3. Update DB
        
        # To enable real polling, implement the credential lookup here.
        return False

# Singleton
interface_poller = InterfacePoller()
