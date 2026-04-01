import sys
import os
import asyncio
sys.path.append(os.getcwd())
from app import create_app
from services.device_monitor import DeviceMonitor

app = create_app()

async def run_monitor():
    with app.app_context():
        monitor = DeviceMonitor()
        print("Starting manual monitoring of devices...")
        await monitor.monitor_stored_devices()
        print("Monitoring cycle completed.")

if __name__ == "__main__":
    asyncio.run(run_monitor())
