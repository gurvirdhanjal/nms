from app import create_app
import webbrowser
import threading
import os

app = create_app()

import logging
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
    
    try:
        print(f"[WEB] Starting web_main on {WEB_HOST}:{WEB_PORT} (debug={WEB_DEBUG})")
        app.run(host=WEB_HOST, port=WEB_PORT, debug=WEB_DEBUG, use_reloader=False)
    finally:
        if 'scheduler' in locals():
            try:
                scheduler.stop_scheduled_monitoring()
            except KeyboardInterrupt:
                pass
            except Exception as e:
                print(f"Error stopping scheduler: {e}")
        if 'interface_poller' in locals():
            try:
                interface_poller.stop_polling(timeout=1.0)
            except KeyboardInterrupt:
                pass
            except Exception as e:
                print(f"Error stopping interface poller: {e}")
