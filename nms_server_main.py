"""
nms_server_main.py — Production entry point for the NMSAdminServer EXE.

Combines Waitress (production WSGI, 6 threads) with the monitoring scheduler
and interface poller. config.py already handles sys.frozen so .env is loaded
from the folder that contains this EXE.
"""
import os
import sys
import logging

# ------------------------------------------------------------------
# Frozen-path guard: when running as a PyInstaller EXE, sys._MEIPASS
# is the _internal/ folder. Make sure Python can find all bundled
# packages from there.
# ------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS
    if _base not in sys.path:
        sys.path.insert(0, _base)

from app import create_app

app = create_app()

# Silence Werkzeug request logs in production (NSSM handles log files).
logging.getLogger('werkzeug').setLevel(logging.ERROR)


if __name__ == '__main__':
    from waitress import serve

    port = int(os.environ.get('PORT', 5001))
    host = os.environ.get('WEB_HOST', '0.0.0.0')
    public_host = os.environ.get('PUBLIC_HOST')
    if not public_host:
        public_host = 'localhost' if host in {'0.0.0.0', '::'} else host

    # 1. Hydrate monitoring collector with DB history
    try:
        from routes.monitoring import monitor
        monitor.hydrate_collector(app)
        print('[OK] Monitor collector hydrated.')
    except Exception as _e:
        print(f'[WARN] Could not hydrate collector: {_e}')

    # 2. Start background monitoring scheduler (pings, rollups, alerts)
    try:
        from services.scheduler import MonitoringScheduler
        _scheduler = MonitoringScheduler(app)
        _scheduler.start_scheduled_monitoring()
        print('[OK] Monitoring scheduler started.')
    except Exception as _e:
        print(f'[WARN] Could not start scheduler: {_e}')

    # 3. Start SNMP interface poller
    try:
        from services.interface_poller import interface_poller
        interface_poller.start_polling(app)
        print('[OK] Interface poller started.')
    except Exception as _e:
        print(f'[WARN] Could not start interface poller: {_e}')

    print(f'[NMS] Admin server running on http://{host}:{port}')
    print(f'[NMS] Accessible at: http://{public_host}:{port}')

    try:
        serve(app, host=host, port=port, threads=6)
    finally:
        if '_scheduler' in dir():
            try:
                _scheduler.stop_scheduled_monitoring()
            except Exception:
                pass
