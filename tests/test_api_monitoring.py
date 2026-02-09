import requests
import time


class ApiMonitor:
    def __init__(self, base_url, token=None, timeout=5):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self):
        headers = {
            "Accept": "application/json"
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get(self, endpoint):
        url = f"{self.base_url}{endpoint}"
        resp = requests.get(
            url,
            headers=self._headers(),
            timeout=self.timeout,
            verify=False  # disable for lab testing
        )
        resp.raise_for_status()
        return resp.json()

    def collect_health(self):
        """
        Normalize API data into NMS-friendly format
        """
        data = self.get("/health")

        return {
            "cpu_percent": data.get("cpu"),
            "memory_percent": data.get("memory"),
            "uptime_seconds": data.get("uptime"),
            "status": "up" if data.get("status") == "ok" else "down"
        }


if __name__ == "__main__":
    # Example API device
    DEVICE_API = "http://127.0.0.1:9000"
    TOKEN = None  # set if required

    monitor = ApiMonitor(DEVICE_API, token=TOKEN)

    print("=== API Monitoring Test ===")

    try:
        metrics = monitor.collect_health()
        print("Metrics received:")
        for k, v in metrics.items():
            print(f"  {k}: {v}")
    except Exception as e:
        print(f"API monitoring failed: {e}")
