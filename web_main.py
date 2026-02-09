from app import create_app
import webbrowser
import threading

app = create_app()

from services.scheduler import MonitoringScheduler
from services.interface_poller import interface_poller

def open_browser():
    webbrowser.open("http://127.0.0.1:5000")

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

    threading.Timer(1.5, open_browser).start()
    
    try:
        app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
    finally:
        if 'scheduler' in locals():
            scheduler.stop_scheduled_monitoring()
        if 'interface_poller' in locals():
            interface_poller.stop_polling()
