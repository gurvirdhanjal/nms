"""
Sites Service for Multi-Site Device Organization.
Handles CRUD operations for sites and site-related queries.
"""
from typing import List, Dict, Optional
from extensions import db
from models.site import Site
from models.device import Device


class SitesService:
    """Service for managing sites and site-related operations."""

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
        Delete site if no devices assigned.
        
        Args:
            site_id: Site ID
            
        Returns:
            True if deleted successfully
            
        Raises:
            ValueError: If site not found or has devices assigned
        """
        try:
            site = Site.query.get(site_id)
            if not site:
                raise ValueError(f"Site with ID {site_id} not found")
            
            # Check for associated devices
            device_count = Device.query.filter_by(site_id=site_id).count()
            if device_count > 0:
                raise ValueError(
                    f"Cannot delete site '{site.site_name}': "
                    f"{device_count} device(s) are assigned to this site"
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
        return Device.query.filter_by(site_id=site_id).order_by(Device.device_name).all()

    def get_site_stats(self, site_id: int) -> Dict:
        """
        Get device statistics for a site.
        
        Args:
            site_id: Site ID
            
        Returns:
            Dict with device_count, online_count, offline_count, warning_count
        """
        from models.server_health import ServerHealthLog
        from datetime import datetime, timedelta
        
        # Get all devices for site
        devices = Device.query.filter_by(site_id=site_id).all()
        device_count = len(devices)
        
        if device_count == 0:
            return {
                'device_count': 0,
                'online_count': 0,
                'offline_count': 0,
                'warning_count': 0
            }
        
        # Get device IDs
        device_ids = [d.device_id for d in devices]
        
        # Check health status (devices with recent health logs are online)
        cutoff = datetime.utcnow() - timedelta(minutes=10)
        online_device_ids = db.session.query(ServerHealthLog.device_id).filter(
            ServerHealthLog.device_id.in_(device_ids),
            ServerHealthLog.timestamp >= cutoff
        ).distinct().all()
        online_device_ids = [d[0] for d in online_device_ids]
        
        online_count = len(online_device_ids)
        offline_count = device_count - online_count
        
        # Count devices with warnings (high strikes)
        warning_count = Device.query.filter(
            Device.site_id == site_id,
            db.or_(
                Device.health_alert_strikes >= 2,
                Device.latency_strikes >= 2,
                Device.packet_loss_strikes >= 2
            )
        ).count()
        
        return {
            'device_count': device_count,
            'online_count': online_count,
            'offline_count': offline_count,
            'warning_count': warning_count
        }
