"""
API Verifier for Phase 1 MVP

Verifies API endpoints match Phase 1 specification by checking:
- Endpoint registration
- HTTP methods
- Query parameter support
"""

from typing import Dict, List, Any
from .phase1_spec import PHASE1_SPEC


class APIVerifier:
    """Verifies API endpoints against Phase 1 specification."""
    
    def __init__(self, flask_app):
        """
        Initialize API verifier.
        
        Args:
            flask_app: Flask application instance
        """
        self.app = flask_app
    
    def verify_endpoint_exists(self, path: str, method: str) -> bool:
        """
        Check if endpoint is registered.
        
        Args:
            path: URL path (e.g., '/api/sites')
            method: HTTP method (e.g., 'GET')
            
        Returns:
            True if endpoint exists, False otherwise
        """
        # Normalize path for comparison
        normalized_path = path.replace('<int:id>', '<id>')
        
        for rule in self.app.url_map.iter_rules():
            rule_path = str(rule.rule).replace('<int:id>', '<id>')
            if rule_path == normalized_path and method in rule.methods:
                return True
        return False
    
    def verify_query_params(self, path: str, params: List[str]) -> Dict[str, Any]:
        """
        Verify endpoint accepts query parameters.
        
        Note: This is informational only as Flask doesn't enforce query params.
        
        Args:
            path: URL path
            params: Expected query parameter names
            
        Returns:
            Dictionary with verification results:
            {
                'supported': bool,
                'missing_params': List[str]
            }
        """
        # For Flask, query params are not enforced at route level
        # This would require inspecting the view function code
        # For now, we assume they're supported if the endpoint exists
        return {
            'supported': True,
            'missing_params': []
        }
    
    def list_all_endpoints(self) -> List[Dict[str, str]]:
        """
        List all registered endpoints with methods.
        
        Returns:
            List of endpoint dictionaries with path and method
        """
        endpoints = []
        for rule in self.app.url_map.iter_rules():
            for method in rule.methods:
                if method not in ['HEAD', 'OPTIONS']:
                    endpoints.append({
                        'path': str(rule.rule),
                        'method': method
                    })
        return endpoints
    
    def generate_api_report(self) -> Dict[str, Any]:
        """
        Generate complete API verification report.
        
        Returns:
            Dictionary with complete verification results
        """
        results = {
            'endpoints': {},
            'missing_endpoints': []
        }
        
        for endpoint_spec in PHASE1_SPEC['endpoints']:
            path = endpoint_spec['path']
            method = endpoint_spec['method']
            
            # Check if endpoint exists
            endpoint_exists = self.verify_endpoint_exists(path, method)
            
            endpoint_key = f"{method} {path}"
            
            if not endpoint_exists:
                results['missing_endpoints'].append({
                    'path': path,
                    'method': method
                })
                results['endpoints'][endpoint_key] = {'status': 'Missing'}
            else:
                results['endpoints'][endpoint_key] = {'status': 'Implemented'}
        
        return results


def verify_api(flask_app) -> Dict[str, Any]:
    """
    Convenience function to verify API endpoints.
    
    Args:
        flask_app: Flask application instance
        
    Returns:
        Verification results dictionary
    """
    verifier = APIVerifier(flask_app)
    return verifier.generate_api_report()
