"""
Sites Service for Multi-Site Device Organization.
Handles CRUD operations for sites and site-related queries.
"""
from typing import List, Dict, Optional

from extensions import db
from models.site import Site
from models.device import Device
from models.department import Department
from services.dashboard_availability import build_device_availability_snapshot


class SitesService:
    """Service for managing sites and site-related operations."""

    def _site_devices_query(self, site_id: int):
        site = Site.query.get(site_id)
        if not site:
            return Device.query.filter(False)

        departments = site.departments.all() if hasattr(site, "departments") else []
        dept_ids = [d.id for d in departments]

        if dept_ids:
            return Device.query.filter(
                db.or_(
                    Device.site_id == site_id,
                    Device.department_id.in_(dept_ids),
                )
            )

        return Device.query.filter_by(site_id=site_id)

    def create_site(self, name: str, address: str = None, 
                   timezone: str = 'UTC', contact_info: Dict = None) -> Site:
        """
        Create a new site.
        
        Args:
            name: Site name (required, unique)
            address: Physical address
            timezone: Timezone (default: UTC)
            contact_info: Dict with contact_name, contact_email, contact_phone
            
        Returns:
            Created Site object
            
        Raises:
            ValueError: If site with name already exists
        """
        try:
            # Check if site already exists
            existing = Site.query.filter_by(site_name=name).first()
            if existing:
                raise ValueError(f"Site with name '{name}' already exists")
            
            # Create site
            site = Site(
                site_name=name,
                address=address,
                timezone=timezone or 'UTC'
            )
            
            # Add contact info if provided
            if contact_info:
                site.contact_name = contact_info.get('contact_name')
                site.contact_email = contact_info.get('contact_email')
                site.contact_phone = contact_info.get('contact_phone')
            
            db.session.add(site)
            db.session.commit()
            
            return site
            
        except Exception as e:
            db.session.rollback()
            raise

    def get_site(self, site_id: int) -> Optional[Site]:
        """
        Get site by ID.
        
        Args:
            site_id: Site ID
            
        Returns:
            Site object or None if not found
        """
        return Site.query.get(site_id)

    def list_sites(self) -> List[Site]:
        """
        List all sites.
        
        Returns:
            List of Site objects
        """
        return Site.query.order_by(Site.site_name).all()

    def update_site(self, site_id: int, **kwargs) -> Site:
        """
        Update site attributes.
        
        Args:
            site_id: Site ID
            **kwargs: Fields to update (name, address, timezone, contact_info)
            
        Returns:
            Updated Site object
            
        Raises:
            ValueError: If site not found or name conflict
        """
        try:
            site = Site.query.get(site_id)
            if not site:
                raise ValueError(f"Site with ID {site_id} not found")
            
            # Update fields
            if 'name' in kwargs:
                # Check for name conflict
                existing = Site.query.filter(
                    Site.site_name == kwargs['name'],
                    Site.id != site_id
                ).first()
                if existing:
                    raise ValueError(f"Site with name '{kwargs['name']}' already exists")
                site.site_name = kwargs['name']
            
            if 'address' in kwargs:
                site.address = kwargs['address']
            
            if 'timezone' in kwargs:
                site.timezone = kwargs['timezone']
            
            if 'contact_info' in kwargs:
                contact_info = kwargs['contact_info']
                if isinstance(contact_info, dict):
                    site.contact_name = contact_info.get('contact_name')
                    site.contact_email = contact_info.get('contact_email')
                    site.contact_phone = contact_info.get('contact_phone')
            
            db.session.commit()
            return site
            
        except Exception as e:
            db.session.rollback()
            raise

    def delete_site(self, site_id: int) -> bool:
        """
        Delete site and clean up related departments/devices.
        
        Args:
            site_id: Site ID
            
        Returns:
            True if deleted successfully
            
        Raises:
            ValueError: If site not found
        """
        try:
            site = Site.query.get(site_id)
            if not site:
                raise ValueError(f"Site with ID {site_id} not found")

            dept_ids = [
                row[0]
                for row in db.session.query(Department.id).filter_by(site_id=site_id).all()
            ]
            if dept_ids:
                from models.user import User
                User.query.filter(User.department_id.in_(dept_ids)).update(
                    {'department_id': None}, synchronize_session='fetch'
                )
                Device.query.filter(Device.department_id.in_(dept_ids)).update(
                    {'department_id': None}, synchronize_session='fetch'
                )
                Department.query.filter(Department.id.in_(dept_ids)).delete(synchronize_session='fetch')
            
            Device.query.filter_by(site_id=site_id).update(
                {'site_id': None}, synchronize_session='fetch'
            )
            
            db.session.delete(site)
            db.session.commit()
            return True
            
        except Exception as e:
            db.session.rollback()
            raise

    def get_site_devices(self, site_id: int) -> List[Device]:
        """
        Get all devices for a site.
        
        Args:
            site_id: Site ID
            
        Returns:
            List of Device objects
        """
        return self._site_devices_query(site_id).order_by(Device.device_name).all()

    def get_site_stats(
        self,
        site_id: int,
        devices: Optional[List[Device]] = None,
        availability_snapshot: Optional[Dict] = None,
    ) -> Dict:
        """
        Get device statistics for a site.
        
        Args:
            site_id: Site ID
            
        Returns:
            Dict with device_count, online_count, offline_count, warning_count
        """
        site_devices = list(devices or self._site_devices_query(site_id).all())
        device_count = len(site_devices)

        if device_count == 0:
            return {
                "device_count": 0,
                "online_count": 0,
                "offline_count": 0,
                "warning_count": 0,
            }

        snapshot = availability_snapshot or build_device_availability_snapshot(site_devices)
        counts = snapshot.get("counts") or {}
        online_count = int(counts.get("online_total") or 0)
        offline_count = max(device_count - online_count, 0)

        warning_count = sum(
            1
            for device in site_devices
            if (device.health_alert_strikes or 0) >= 2
            or (device.latency_strikes or 0) >= 2
            or (device.packet_loss_strikes or 0) >= 2
        )

        return {
            "device_count": device_count,
            "online_count": online_count,
            "offline_count": offline_count,
            "warning_count": warning_count,
        }
