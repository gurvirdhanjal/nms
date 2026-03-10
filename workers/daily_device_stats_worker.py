import logging
from datetime import datetime, timedelta, date
from sqlalchemy import func

from extensions import db
from models.device import Device
from models.scan_history import DeviceScanHistory
from models.dashboard import DailyDeviceStats

logger = logging.getLogger(__name__)

class DailyDeviceStatsWorker:
    def __init__(self, app):
        self.app = app

    def run_once(self):
        """
        Calculates and upserts DailyDeviceStats from DeviceScanHistory for the previous day.
        If DailyDeviceStats is entirely empty, it performs a full historical backfill.
        """
        with self.app.app_context():
            logger.info("[DailyDeviceStatsWorker] Starting run_once")
            try:
                self._run_job()
                logger.info("[DailyDeviceStatsWorker] Completed run_once")
            except Exception as e:
                logger.exception(f"[DailyDeviceStatsWorker] Error running job: {e}")

    def _run_job(self):
        # 1. Determine time ranges
        has_stats = db.session.query(DailyDeviceStats.id).first() is not None

        target_dates = []
        if not has_stats:
            # Full backfill: find min and max dates in DeviceScanHistory
            logger.info("[DailyDeviceStatsWorker] No stats found, performing full backfill")
            min_date, max_date = db.session.query(
                func.min(func.date(DeviceScanHistory.scan_timestamp)),
                func.max(func.date(DeviceScanHistory.scan_timestamp))
            ).first()

            if not min_date or not max_date:
                logger.info("[DailyDeviceStatsWorker] No scan history found to backfill.")
                return

            # SQLite returns strings for func.date, PG might return date objects
            if isinstance(min_date, str):
                min_date = datetime.strptime(min_date[:10], '%Y-%m-%d').date()
            elif isinstance(min_date, datetime):
                min_date = min_date.date()

            if isinstance(max_date, str):
                max_date = datetime.strptime(max_date[:10], '%Y-%m-%d').date()
            elif isinstance(max_date, datetime):
                max_date = max_date.date()

            curr = min_date
            while curr <= max_date:
                target_dates.append(curr)
                curr += timedelta(days=1)
        else:
            # Daily run: process yesterday and today just to be safe
            today = datetime.utcnow().date()
            yesterday = today - timedelta(days=1)
            target_dates = [yesterday, today]

        # Map IP to Device ID for fast lookup
        devices = db.session.query(Device.device_id, Device.device_ip).filter(Device.device_ip.isnot(None)).all()
        ip_to_device_id = {d.device_ip: d.device_id for d in devices if d.device_ip}

        if not ip_to_device_id:
            logger.info("[DailyDeviceStatsWorker] No devices with IP addresses found.")
            return

        backend = db.engine.url.get_backend_name()

        for target_date in target_dates:
            logger.info(f"[DailyDeviceStatsWorker] Processing date: {target_date}")

            start_ts = datetime(target_date.year, target_date.month, target_date.day)
            end_ts = start_ts + timedelta(days=1)

            # Fetch daily aggregates by IP
            if backend == 'sqlite':
                status_cond = "status = 'Online'"
            else:
                status_cond = "status ILIKE 'online'"

            # We group in python to avoid complex cross-dialect SQL for conditional counts
            scans_for_day = db.session.query(
                DeviceScanHistory.device_ip,
                DeviceScanHistory.status,
                DeviceScanHistory.ping_time_ms,
                DeviceScanHistory.packet_loss
            ).filter(
                DeviceScanHistory.scan_timestamp >= start_ts,
                DeviceScanHistory.scan_timestamp < end_ts,
                DeviceScanHistory.device_ip.isnot(None)
            ).all()

            if not scans_for_day:
                continue

            # Grouping stats
            daily_stats_by_ip = {}
            for ip, status, latency, pkt_loss in scans_for_day:
                if ip not in daily_stats_by_ip:
                    daily_stats_by_ip[ip] = {
                        'total_scans': 0,
                        'online_scans': 0,
                        'latencies': [],
                        'packet_losses': []
                    }

                stats = daily_stats_by_ip[ip]
                stats['total_scans'] += 1
                if str(status).strip().lower() == 'online':
                    stats['online_scans'] += 1
                if latency is not None:
                    stats['latencies'].append(latency)
                if pkt_loss is not None:
                    stats['packet_losses'].append(pkt_loss)

            # Upsert into DailyDeviceStats
            for ip, stats in daily_stats_by_ip.items():
                device_id = ip_to_device_id.get(ip)
                if not device_id:
                    continue

                total_scans = stats['total_scans']
                online_scans = stats['online_scans']
                uptime_pct = (online_scans / total_scans * 100.0) if total_scans > 0 else 0.0

                lats = stats['latencies']
                avg_latency = sum(lats) / len(lats) if lats else None
                max_latency = max(lats) if lats else None
                min_latency = min(lats) if lats else None

                pkts = stats['packet_losses']
                avg_packet_loss = sum(pkts) / len(pkts) if pkts else 0.0

                # Fetch existing record to update or create new
                existing = db.session.query(DailyDeviceStats).filter_by(
                    device_id=device_id,
                    date=target_date
                ).first()

                if existing:
                    existing.uptime_percent = uptime_pct
                    existing.avg_latency_ms = avg_latency
                    existing.max_latency_ms = max_latency
                    existing.min_latency_ms = min_latency
                    existing.avg_packet_loss_pct = avg_packet_loss
                    existing.total_scans = total_scans
                    existing.online_scans = online_scans
                else:
                    new_stat = DailyDeviceStats(
                        device_id=device_id,
                        date=target_date,
                        uptime_percent=uptime_pct,
                        avg_latency_ms=avg_latency,
                        max_latency_ms=max_latency,
                        min_latency_ms=min_latency,
                        avg_packet_loss_pct=avg_packet_loss,
                        total_scans=total_scans,
                        online_scans=online_scans,
                        total_alerts=0 # Keeping this default for now
                    )
                    db.session.add(new_stat)

            db.session.commit()
