import asyncio
import logging
from datetime import datetime, timedelta
from extensions import db
from sqlalchemy.orm.exc import StaleDataError, ObjectDeletedError
from services.network_scanner import NetworkScanner
import statistics

logger = logging.getLogger(__name__)


def _build_latency_spike_payload(
    device_id: int,
    device_ip: str,
    device_name: str,
    latency_ms: float,
    icmp_thresholds: dict,
) -> dict:
    severity = (
        'critical'
        if latency_ms >= icmp_thresholds['latency_critical_ms']
        else 'warning'
    )
    threshold_ms = (
        icmp_thresholds['latency_critical_ms']
        if severity == 'critical'
        else icmp_thresholds['latency_warning_ms']
    )
    return {
        'device_id': device_id,
        'ip': device_ip,
        'name': device_name,
        'latency_ms': round(latency_ms, 2),
        'threshold_ms': threshold_ms,
        'severity': severity,
    }


class DeviceMonitor:
    def __init__(self):
        self.scanner = NetworkScanner()
        
        # Initialize Metrics Collector
        from metrics.collector import MetricCollector
        self.collector = MetricCollector()
        
        # Initialize Event System
        from extensions import event_manager
        from thresholds.evaluator import ThresholdEvaluator
        from thresholds.rules import ThresholdRule, ThresholdOperator
        
        # Define Default Rules
        # 1. High Latency Rule (> 100ms warning, > 200ms critical)
        latency_rule = ThresholdRule(
            metric_name="network_latency_ms",
            operator=ThresholdOperator.GT,
            warning_threshold=100.0,
            critical_threshold=200.0,
            samples_required=1  # Fast reaction for demo
        )
        
        # 2. Availability Rule (Normal=1, Offline=0)
        # We want to alert if it is 0. So < 1 is bad.
        availability_rule = ThresholdRule(
            metric_name="device_availability",
            operator=ThresholdOperator.LT,
            warning_threshold=None,
            critical_threshold=0.5, # < 0.5 means 0 (Offline)
            samples_required=1
        )
        
        self.evaluator = ThresholdEvaluator(rules=[latency_rule, availability_rule])
        self.event_manager = event_manager

    def hydrate_collector(self, app):
        """
        Public method to hydrate collector with DB history.
        Must be called with app context.
        """
        logger.info("Hydrating MetricCollector from database...")
        with app.app_context():
            try:
                from models.device import Device
                from models.scan_history import DeviceScanHistory
                from metrics.normalizer import MetricNormalizer
                
                # Get all devices (User requested to monitor everything)
                devices = Device.query.all()
                total_loaded = 0
                
                for device in devices:
                    # Get last 50 scans for this device
                    scans = DeviceScanHistory.query.filter_by(device_ip=device.device_ip)\
                        .order_by(DeviceScanHistory.scan_timestamp.desc())\
                        .limit(50).all()
                    
                    # Add to collector (reverse to keep chronological order in deque)
                    for scan in reversed(scans):
                        metrics = MetricNormalizer.normalize_ping(
                            scan.device_ip, 
                            scan.status, 
                            scan.ping_time_ms,
                            scan.scan_timestamp # Use timestamp from DB
                        )
                        self.collector.add_metrics(metrics)
                        total_loaded += 1
                        
                logger.info("Hydration complete. Loaded %d metrics.", total_loaded)
                
            except Exception as e:
                logger.exception("Error hydrating collector: %s", e)
    
    async def monitor_stored_devices(self):
        """Monitor all stored devices and save results concurrently"""
        from models.device import Device
        from models.scan_history import DeviceScanHistory
        from metrics.normalizer import MetricNormalizer
        from services.alert_manager import AlertManager
        
        # Get active device data (IDs and IPs) — copy to plain tuples so we can
        # release the DB connection before the long async ping phase.
        devices_query = db.session.query(Device.device_id, Device.device_ip, Device.device_name, Device.maintenance_mode).all()
        active_devices = [
            (d.device_id, d.device_ip, d.device_name, d.maintenance_mode)
            for d in devices_query
            if not getattr(d, 'maintenance_mode', False) and d.device_ip
        ]

        # Release the connection back to the pool NOW — asyncio.gather() below
        # can hold the event loop for 5-30 s (239 concurrent ICMP timeouts).
        # Holding a DB connection idle for that long exhausts the pool and
        # blocks login / other requests.
        db.session.remove()

        logger.debug("Monitoring %d stored devices...", len(active_devices))

        async def fetch_status(device_info):
            device_id, device_ip, device_name, _ = device_info

            # 1. Try Standard Ping
            status, latency, packet_loss, jitter, _ttl = await self.scanner.ping_device(device_ip)

            # 2. Try Tactical Agent Port (5002) if Ping fails or timeout
            if status == 'Offline':
                try:
                    agent_info = await self.scanner.check_tactical_agent(device_ip)
                    if agent_info:
                        status = 'Online'
                        if latency is None:
                            latency = 1.0  # Assumed healthy latency if agent replies
                except:
                    pass

            return {
                'id': device_id,
                'ip': device_ip,
                'name': device_name,
                'status': status,
                'latency': latency,
                'packet_loss': packet_loss,
                'jitter': jitter
            }

        # Concurrently perform network I/O (no DB connection held here)
        tasks = [fetch_status(device_info) for device_info in active_devices]

        try:
            results = await asyncio.gather(*tasks)
        except Exception as e:
            logger.error("[DeviceMonitor] Failed during concurrent ping gather: %s", e)
            results = []

        scan_results = []
        sse_update_batch = []
        
        # Process database inserts sequentially
        for res in results:
            device_id = res['id']
            device_ip = res['ip']
            device_name = res['name']
            status = res['status']
            latency = res['latency']
            packet_loss = res['packet_loss']
            jitter = res['jitter']

            # We fetch a fresh object solely for AlertManager rules processing.
            live_device = db.session.get(Device, device_id)
            if not live_device:
                db.session.rollback()
                continue
            
            # Save scan history
            scan_record = DeviceScanHistory(
                device_ip=device_ip,
                device_name=device_name,
                ping_time_ms=latency,
                status=status,
                scan_type='scheduled',
                packet_loss=packet_loss,
                jitter=jitter
            )
            
            metrics = MetricNormalizer.normalize_ping(device_ip, status, latency, packet_loss=packet_loss, jitter=jitter)
            self.collector.add_metrics(metrics)
            
            is_online = (status == 'Online')
            try:
                AlertManager.process_scan_result(live_device, is_online, latency, packet_loss, commit=False)
                # Fire immediate latency_spike SSE event on first breach (UI flash signal).
                # AlertManager handles persistent 3-strike alerts separately.
                if is_online and latency is not None:
                    try:
                        icmp = AlertManager.get_icmp_thresholds(live_device)
                        if latency >= icmp['latency_warning_ms']:
                            from services.sse_broadcaster import broadcast_event
                            broadcast_event('latency_spike', _build_latency_spike_payload(
                                device_id, device_ip, device_name, latency, icmp
                            ))
                    except Exception as _sse_err:
                        logger.warning(
                            "[DeviceMonitor] latency_spike broadcast error for %s: %s",
                            device_ip, _sse_err
                        )
            except (StaleDataError, ObjectDeletedError) as e:
                logger.warning("[DeviceMonitor] Device became stale during alert processing for %s: %s", device_ip, e)
                db.session.rollback()
                continue

            if not is_online or (latency and latency > 100) or (packet_loss and packet_loss > 5):
                try:
                    sse_update_batch.append({
                        'device_id': device_id,
                        'ip': device_ip,
                        'status': status,
                        'latency': latency,
                        'packet_loss': packet_loss,
                        'jitter': jitter
                    })
                except Exception as e:
                    logger.warning("[DeviceMonitor] Batch accumulation error: %s", e)

            # Savepoint per device — a single bad row cannot roll back the whole batch.
            try:
                sp = db.session.begin_nested()
                db.session.add(scan_record)
                sp.commit()
            except (StaleDataError, ObjectDeletedError) as e:
                logger.warning(
                    "[DeviceMonitor] Device disappeared during savepoint for %s: %s",
                    device_ip, e,
                )
                try:
                    sp.rollback()
                except Exception:
                    pass
                continue
            except Exception as e:
                logger.error(
                    "[DeviceMonitor] Savepoint failed for %s: %s", device_ip, e
                )
                try:
                    sp.rollback()
                except Exception:
                    pass
                continue

            scan_results.append({
                'device_name': device_name,
                'device_ip': device_ip,
                'status': status,
                'latency': latency,
                'packet_loss': packet_loss,
                'jitter': jitter,
                'timestamp': datetime.utcnow(),
            })

        # ── Single batch commit for the entire scan cycle ─────────────────────
        try:
            db.session.commit()
        except Exception as _batch_err:
            logger.error(
                "[DeviceMonitor] Batch commit failed (%d results); retrying per record: %s",
                len(scan_results), _batch_err,
            )
            db.session.rollback()
            # Fallback: recommit scan records individually.
            # Alert mutations (latency_strikes etc.) are in-memory and reconcile next cycle.
            for sr in scan_results:
                try:
                    fallback_record = DeviceScanHistory(
                        device_ip=sr['device_ip'],
                        device_name=sr['device_name'],
                        ping_time_ms=sr['latency'],
                        status=sr['status'],
                        scan_type='scheduled',
                        packet_loss=sr['packet_loss'],
                        jitter=sr.get('jitter'),
                    )
                    db.session.add(fallback_record)
                    db.session.commit()
                except Exception as _fb_err:
                    logger.error(
                        "[DeviceMonitor] Fallback commit failed for %s: %s",
                        sr['device_ip'], _fb_err,
                    )
                    db.session.rollback()

        # Fire one single broadcast for all troubled devices
        if sse_update_batch:
            try:
                from services.sse_broadcaster import broadcast_event
                broadcast_event('device_update_batch', {'devices': sse_update_batch})
            except Exception as e:
                logger.error("[DeviceMonitor] Bulk SSE broadcast error: %s", e)

        return scan_results
    
    def get_device_statistics(self, device_ip, hours=24, start_time=None, end_time=None):
        """Get statistics for a device over specified hours OR time range"""
        from models.scan_history import DeviceScanHistory
        
        if start_time and end_time:
            # Use explicit time range
            scans = DeviceScanHistory.query.filter(
                DeviceScanHistory.device_ip == device_ip,
                DeviceScanHistory.scan_timestamp.between(start_time, end_time)
            ).order_by(DeviceScanHistory.scan_timestamp).all()
        else:
            # Use relative hours
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            scans = DeviceScanHistory.query.filter(
                DeviceScanHistory.device_ip == device_ip,
                DeviceScanHistory.scan_timestamp >= cutoff_time
            ).order_by(DeviceScanHistory.scan_timestamp).all()
        
        if not scans:
            return None
        
        online_scans = [scan for scan in scans if scan.status == 'Online']
        offline_scans = [scan for scan in scans if scan.status == 'Offline']
        
        latencies = [scan.ping_time_ms for scan in online_scans if scan.ping_time_ms is not None]
        packet_losses = [scan.packet_loss for scan in scans if scan.packet_loss is not None]
        
        stats = {
            'total_scans': len(scans),
            'online_count': len(online_scans),
            'offline_count': len(offline_scans),
            'no_response_count': sum(
                1 for s in offline_scans
                if s.ping_time_ms is None
            ),
            'uptime_percentage': (len(online_scans) / len(scans)) * 100 if scans else 0,
            'downtime_percentage': (len(offline_scans) / len(scans)) * 100 if scans else 0,
        }
        
        if latencies:
            stats.update({
                'avg_latency': statistics.mean(latencies),
                'min_latency': min(latencies),
                'max_latency': max(latencies),
                'latency_std_dev': statistics.stdev(latencies) if len(latencies) > 1 else 0
            })
            
        if packet_losses:
            stats.update({
                'avg_packet_loss': statistics.mean(packet_losses),
                'max_packet_loss': max(packet_losses)
            })
        
        return stats
    
    def get_daily_report(self, date=None):
        """Generate daily report for all monitored devices"""
        from models.device import Device
        from models.scan_history import DeviceScanHistory
        
        if date is None:
            date = datetime.utcnow().date()
        
        start_time = datetime.combine(date, datetime.min.time())
        end_time = datetime.combine(date, datetime.max.time())
        
        devices = Device.query.all()
        report = {
            'date': date.isoformat(),
            'total_monitored_devices': len(devices),
            'devices': []
        }
        
        for device in devices:
            # Pass strict time range to get stats for the specific day
            stats = self.get_device_statistics(
                device.device_ip, 
                start_time=start_time, 
                end_time=end_time
            )
            
            if stats:
                report['devices'].append({
                    'device_name': device.device_name,
                    'device_ip': device.device_ip,
                    'stats': stats
                })
        
        return report
