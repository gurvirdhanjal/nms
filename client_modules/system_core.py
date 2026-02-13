import psutil
import time

class SystemMonitor:
    """Core system metrics monitor (CPU, Memory)"""
    def get_core_metrics(self):
        """Get current CPU and Memory usage"""
        mem = psutil.virtual_memory()
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": mem.percent,
            "used_gb": round(mem.used / (1024**3), 2),
            "total_gb": round(mem.total / (1024**3), 2),
            "disk_usage": psutil.disk_usage('/').percent
        }

class NetworkMonitor:
    """
    Network Monitor with delta-based calculation and warm-up.
    Tracks Upload/Download speeds in KB/s.
    """
    def __init__(self):
        self.last_io = None
        self.last_time = None
        
    def get_network_metrics(self):
        """
        Calculate network speed based on delta from last call.
        Returns:
            dict: { 'upload_speed_kbps': float, 'download_speed_kbps': float }
            (Or 0.0 if first call/warm-up)
        """
        current_io = psutil.net_io_counters()
        current_time = time.time()
        
        # Warm-up / First run
        if self.last_io is None or self.last_time is None:
            self.last_io = current_io
            self.last_time = current_time
            return {
                "upload_speed_kbps": 0.0,
                "download_speed_kbps": 0.0
            }
            
        # Calculate deltas
        time_delta = current_time - self.last_time
        
        # Avoid division by zero or extremely small deltas
        if time_delta < 0.1:
            return {
                "upload_speed_kbps": 0.0,
                "download_speed_kbps": 0.0
            }
            
        bytes_sent_delta = current_io.bytes_sent - self.last_io.bytes_sent
        bytes_recv_delta = current_io.bytes_recv - self.last_io.bytes_recv
        
        # Handle interface resets (negative delta)
        if bytes_sent_delta < 0: bytes_sent_delta = 0
        if bytes_recv_delta < 0: bytes_recv_delta = 0
        
        # Convert to KB/s
        upload_speed = (bytes_sent_delta / 1024) / time_delta
        download_speed = (bytes_recv_delta / 1024) / time_delta
        
        # Update state
        self.last_io = current_io
        self.last_time = current_time
        
        return {
            "upload_speed_kbps": round(upload_speed, 2),
            "download_speed_kbps": round(download_speed, 2)
        }
