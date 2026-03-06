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
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="interface-poller",
            )
            self._thread.start()
            print("Interface Poller Service started.")

    def stop_polling(self, timeout=2.0):
        """Stop the background polling thread"""
        self._stop_event.set()
        thread = self._thread
        if not thread:
            return

        try:
            thread.join(timeout=max(0.0, float(timeout)))
        except KeyboardInterrupt:
            # Keep shutdown path clean even if Ctrl+C is pressed again.
            pass

        if thread.is_alive():
            print("Interface Poller stop requested; thread still exiting.")
        else:
            print("Interface Poller Service stopped.")
            self._thread = None

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
            try:
                wait_seconds = max(0.1, float(interval))
            except (TypeError, ValueError):
                wait_seconds = 10.0
            self._stop_event.wait(wait_seconds)

    def _poll_all_devices(self):
        """Polls all monitored devices (no simulation fallback)."""
        devices = Device.query.filter_by(is_monitored=True).all()
        
        for device in devices:
            if self._stop_event.is_set():
                break
            for attempt in range(3):
                if self._stop_event.is_set():
                    break
                try:
                    # TRY REAL SNMP POLL
                    # In a real scenario, we'd check if SNMP is enabled for this device.
                    # No simulation fallback is allowed.
                    success = self._poll_device_real(device)

                    # Commit per device to prevent long-running transactions (SQLite Lock Fix)
                    db.session.commit()

                    # Yield to let other threads write
                    self._stop_event.wait(0.05)
                    break
                except OperationalError as e:
                    db.session.rollback()
                    if "database is locked" in str(e).lower() and attempt < 2:
                        # Backoff and retry
                        self._stop_event.wait(0.2 * (attempt + 1))
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
