import atexit
import logging
import os
import sys
import threading
import webbrowser

# When frozen by PyInstaller, make the extracted bundle importable explicitly.
if getattr(sys, 'frozen', False):
    _bundle_root = getattr(sys, '_MEIPASS', None)
    if _bundle_root and _bundle_root not in sys.path:
        sys.path.insert(0, _bundle_root)

from app import create_app
from waitress import serve

app = create_app()

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

from services.scheduler import MonitoringScheduler
from services.interface_poller import interface_poller


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", os.getenv("PORT", "5000")))
WEB_DEBUG = _env_bool("WEB_DEBUG", True)
WEB_OPEN_BROWSER = _env_bool("WEB_OPEN_BROWSER", True)

def open_browser():
    webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")

if __name__ == "__main__":
    # 1. Hydrate collector with DB history
    try:
        from routes.monitoring import monitor
        monitor.hydrate_collector(app)
    except Exception as e:
        print(f"Error hydrating collector: {e}")

    # 2. Start Background Monitoring (Device Pings)
    try:
        scheduler = MonitoringScheduler(app)
        scheduler.start_scheduled_monitoring()
        scheduler.prewarm_report_cache()       # warm reports now; don't wait 8 min
        scheduler.warm_dashboard_snapshot()    # warm dashboard snapshot immediately
        print("[OK] Monitoring Scheduler started.")
    except Exception as e:
        print(f"Error starting scheduler: {e}")

    # 3. Start Interface Polling (SNMP/Traffic)
    try:
        interface_poller.start_polling(app)
        print("[OK] Interface Poller started.")
    except Exception as e:
        print(f"Error starting poller: {e}")

    if WEB_OPEN_BROWSER:
        t = threading.Timer(1.5, open_browser)
        t.daemon = True
        t.start()
    
    _bg_scheduler = None

    def _shutdown():
        """Stop background threads before interpreter teardown to avoid
        'could not acquire lock for stdout' fatal errors from daemon threads
        that are still running during atexit."""
        if _bg_scheduler is not None:
            try:
                _bg_scheduler.stop_scheduled_monitoring()
            except Exception:
                pass
        try:
            interface_poller.stop_polling(timeout=3.0)
        except Exception:
            pass

    atexit.register(_shutdown)

    try:
        _bg_scheduler = scheduler
    except NameError:
        pass

    try:
        print(f"[WEB] Starting web_main on {WEB_HOST}:{WEB_PORT} (Waitress, 16 threads)")
        # Waitress: bounded thread pool prevents Flask dev server's unlimited-thread
        # per-request model from exhausting the DB connection pool (size 20+overflow).
        serve(app, host=WEB_HOST, port=WEB_PORT, threads=16)
    finally:
        _shutdown()
