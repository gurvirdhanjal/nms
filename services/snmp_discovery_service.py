import threading
import uuid
from datetime import datetime
from typing import Dict, Optional

from extensions import db
from services.snmp_discovery import SnmpDiscovery

# Global singleton
_snmp_discovery_service = None


def get_snmp_discovery_service():
    global _snmp_discovery_service
    if _snmp_discovery_service is None:
        _snmp_discovery_service = SnmpDiscoveryService()
    return _snmp_discovery_service


class SnmpDiscoveryService:
    def __init__(self):
        self.jobs: Dict[str, Dict] = {}
        self.jobs_lock = threading.Lock()

    def start_job(
        self,
        seed_ip: str,
        app,
        community: str = "public",
        version: str = "2c",
        max_depth: int = 3,
        max_switches: int = 50,
        persist: bool = True,
        timeout: int = 2,
        retries: int = 1,
        username: str = "system",
    ) -> str:
        job_id = str(uuid.uuid4())

        with self.jobs_lock:
            self.jobs[job_id] = {
                "id": job_id,
                "seed_ip": seed_ip,
                "status": "running",
                "started_at": datetime.utcnow().isoformat(),
                "finished_at": None,
                "error": None,
                "switch_count": 0,
                "device_count": 0,
                "last_switch": None,
                "username": username,
                "options": {
                    "community": community,
                    "version": version,
                    "max_depth": max_depth,
                    "max_switches": max_switches,
                    "persist": persist,
                    "timeout": timeout,
                    "retries": retries,
                },
            }

        thread = threading.Thread(
            target=self._run_job,
            args=(
                job_id,
                app,
                seed_ip,
                community,
                version,
                max_depth,
                max_switches,
                persist,
                timeout,
                retries,
            ),
            daemon=True,
        )
        thread.start()
        return job_id

    def _run_job(
        self,
        job_id: str,
        app,
        seed_ip: str,
        community: str,
        version: str,
        max_depth: int,
        max_switches: int,
        persist: bool,
        timeout: int,
        retries: int,
    ):
        with app.app_context():
            try:
                discovery = SnmpDiscovery(
                    community=community,
                    version=version,
                    timeout=timeout,
                    retries=retries,
                )

                def on_switch(progress):
                    with self.jobs_lock:
                        job = self.jobs.get(job_id)
                        if job:
                            job["switch_count"] = progress.get("visited", job["switch_count"])
                            job["last_switch"] = progress.get("ip") or job["last_switch"]

                switches = discovery.discover(
                    seed_ip,
                    max_depth=max_depth,
                    max_switches=max_switches,
                    on_switch=on_switch,
                )

                device_count = sum(len(sw.get("devices", [])) for sw in switches)

                inserted = updated = 0
                if persist:
                    inserted, updated = self._persist_devices(switches)

                with self.jobs_lock:
                    job = self.jobs.get(job_id)
                    if job:
                        job["status"] = "completed"
                        job["finished_at"] = datetime.utcnow().isoformat()
                        job["switch_count"] = len(switches)
                        job["device_count"] = device_count
                        job["switches"] = switches
                        job["persisted_inserted"] = inserted
                        job["persisted_updated"] = updated

            except Exception as e:
                with self.jobs_lock:
                    job = self.jobs.get(job_id)
                    if job:
                        job["status"] = "error"
                        job["finished_at"] = datetime.utcnow().isoformat()
                        job["error"] = str(e)

    def get_job(self, job_id: str) -> Optional[Dict]:
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None

    def get_active_job(self, username: str = "system") -> Optional[Dict]:
        with self.jobs_lock:
            for job in self.jobs.values():
                if job.get("username") == username and job.get("status") == "running":
                    return dict(job)
        return None

    def _persist_devices(self, switches):
        from models.device import Device

        inserted = 0
        updated = 0
        seen = set()

        for sw in switches:
            for dev in sw.get("devices", []):
                ip = dev.get("ip")
                mac = dev.get("mac")
                if not ip and not mac:
                    continue

                key = (ip or "", mac or "")
                if key in seen:
                    continue
                seen.add(key)

                existing = None
                if ip:
                    existing = Device.query.filter_by(device_ip=ip).first()
                if not existing and mac:
                    existing = Device.query.filter_by(macaddress=mac).first()

                if existing:
                    if mac and (not existing.macaddress or existing.macaddress == "N/A"):
                        existing.macaddress = mac
                    if ip and existing.device_ip != ip:
                        existing.device_ip = ip
                    if dev.get("interface"):
                        existing.port = dev.get("interface")
                    if not existing.device_type:
                        existing.device_type = "switch"
                    if not existing.device_name or existing.device_name.startswith("Device-"):
                        existing.device_name = f"Device-{existing.device_ip}"
                    updated += 1
                else:
                    if not ip:
                        # Skip MAC-only entries to avoid cluttering inventory
                        continue
                    device = Device(
                        device_name=f"Device-{ip}",
                        device_ip=ip,
                        device_type="switch",
                        port=dev.get("interface") or "",
                        macaddress=mac or "N/A",
                        hostname="Unknown",
                        manufacturer="Unknown",
                        is_monitored=False,
                        is_active=True,
                    )
                    db.session.add(device)
                    inserted += 1

        db.session.commit()
        return inserted, updated
