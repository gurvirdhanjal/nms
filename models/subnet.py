from extensions import db
from datetime import datetime
import ipaddress


class Subnet(db.Model):
    __tablename__ = 'subnets'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    cidr = db.Column(db.String(50), nullable=False)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False)
    description = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Prevent identical CIDRs from being mapped to the exact same site duplicate times
    __table_args__ = (
        db.UniqueConstraint('site_id', 'cidr', name='uq_site_cidr'),
    )

    def to_dict(self):
        normalized_cidr = self.validate_cidr(self.cidr) or self.cidr
        return {
            'id': self.id,
            'cidr': normalized_cidr,
            'site_id': self.site_id,
            'site_name': self.site.site_name if getattr(self, 'site', None) else None,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    @staticmethod
    def validate_cidr(cidr_str):
        """Validates and normalizes a CIDR string. Returns None if invalid."""
        try:
            # strict=False allows host bits to be set, it zeros them out to the network address
            network = ipaddress.ip_network(cidr_str, strict=False)
            return str(network)
        except ValueError:
            return None

    @staticmethod
    def get_best_match(ip_address):
        """
        Find the most specific subnet that contains the given IP address.
        
        Returns the Subnet object with the smallest prefix length (most specific)
        that contains the IP, or None if no match found.
        
        Args:
            ip_address (str): IP address to match (e.g., "172.16.1.50")
        
        Returns:
            Subnet object or None
        """
        try:
            ip_obj = ipaddress.ip_address(ip_address)
        except ValueError:
            return None
        
        # Get all subnets from database
        all_subnets = Subnet.query.all()
        
        # Find matching subnets
        matches = []
        for subnet in all_subnets:
            try:
                network = ipaddress.ip_network(subnet.cidr, strict=False)
                if ip_obj in network:
                    matches.append((subnet, network.prefixlen))
            except ValueError:
                continue
        
        # Return the most specific match (highest prefix length)
        if matches:
            matches.sort(key=lambda x: x[1], reverse=True)
            return matches[0][0]
        
        return None

    @staticmethod
    def get_subnets_for_site(site_id):
        """
        Get all subnets mapped to a specific site.
        
        Args:
            site_id (int): Site ID
        
        Returns:
            List of Subnet objects
        """
        return Subnet.query.filter_by(site_id=site_id).all()

    @staticmethod
    def is_ip_in_site_subnets(ip_address, site_id):
        """
        Check if an IP address belongs to any subnet mapped to a site.
        
        Args:
            ip_address (str): IP address to check
            site_id (int): Site ID
        
        Returns:
            bool: True if IP is in any of the site's subnets
        """
        try:
            ip_obj = ipaddress.ip_address(ip_address)
        except ValueError:
            return False
        
        subnets = Subnet.get_subnets_for_site(site_id)
        for subnet in subnets:
            try:
                network = ipaddress.ip_network(subnet.cidr, strict=False)
                if ip_obj in network:
                    return True
            except ValueError:
                continue
        
        return False

    def contains_ip(self, ip_address):
        """
        Check if this subnet contains the given IP address.
        
        Args:
            ip_address (str): IP address to check
        
        Returns:
            bool: True if IP is in this subnet
        """
        try:
            ip_obj = ipaddress.ip_address(ip_address)
            network = ipaddress.ip_network(self.cidr, strict=False)
            return ip_obj in network
        except ValueError:
            return False

    def __repr__(self):
        return f'<Subnet {self.cidr} (Site: {self.site_id})>'
