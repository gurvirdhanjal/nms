"""
Service Verifier for Phase 1 MVP

Verifies service classes match Phase 1 specification by checking:
- Service class existence
- Required methods
- Method signatures
"""

import importlib
import inspect
from typing import Dict, List, Any
from .phase1_spec import PHASE1_SPEC


class ServiceVerifier:
    """Verifies service classes against Phase 1 specification."""
    
    def verify_service_exists(self, service_name: str, module_path: str) -> bool:
        """
        Check if service class exists.
        
        Args:
            service_name: Name of service class
            module_path: Python module path
            
        Returns:
            True if service exists, False otherwise
        """
        try:
            module = importlib.import_module(module_path)
            return hasattr(module, service_name)
        except (ImportError, AttributeError):
            return False
    
    def verify_service_methods(self, service_name: str, module_path: str,
                               methods: List[Dict]) -> Dict[str, Any]:
        """
        Verify service has required methods.
        
        Args:
            service_name: Name of service class
            module_path: Python module path
            methods: List of method specifications with name and params
            
        Returns:
            Dictionary with verification results:
            {
                'exists': bool,
                'missing_methods': List[str]
            }
        """
        try:
            module = importlib.import_module(module_path)
            service_class = getattr(module, service_name)
        except (ImportError, AttributeError) as e:
            return {
                'exists': False,
                'missing_methods': [m['name'] for m in methods],
                'error': str(e)
            }
        
        missing_methods = []
        for method_spec in methods:
            method_name = method_spec['name']
            if not hasattr(service_class, method_name):
                missing_methods.append(method_name)
        
        return {
            'exists': True,
            'missing_methods': missing_methods
        }
    
    def verify_method_signature(self, service_name: str, module_path: str,
                                method_name: str, params: List[str]) -> bool:
        """
        Verify method has expected parameters.
        
        Args:
            service_name: Name of service class
            module_path: Python module path
            method_name: Name of method
            params: Expected parameter names
            
        Returns:
            True if signature matches, False otherwise
        """
        try:
            module = importlib.import_module(module_path)
            service_class = getattr(module, service_name)
            method = getattr(service_class, method_name)
            
            # Get method signature
            sig = inspect.signature(method)
            actual_params = list(sig.parameters.keys())
            
            # Remove 'self' or 'cls' from actual params
            if actual_params and actual_params[0] in ['self', 'cls']:
                actual_params = actual_params[1:]
            
            # Check if all expected params are present
            # (allow extra params for flexibility)
            return all(param in actual_params for param in params)
            
        except (ImportError, AttributeError, ValueError):
            return False
    
    def generate_service_report(self) -> Dict[str, Any]:
        """
        Generate complete service verification report.
        
        Returns:
            Dictionary with complete verification results
        """
        results = {
            'services': {},
            'missing_services': [],
            'missing_methods': []
        }
        
        for service_spec in PHASE1_SPEC['services']:
            service_name = service_spec['name']
            module_path = service_spec['module']
            methods = service_spec.get('methods', [])
            
            # Check if service exists
            if not self.verify_service_exists(service_name, module_path):
                results['missing_services'].append({
                    'name': service_name,
                    'module': module_path
                })
                results['services'][service_name] = {'status': 'Missing'}
                continue
            
            # Verify methods
            methods_result = self.verify_service_methods(service_name, module_path, methods)
            if methods_result['missing_methods']:
                for method in methods_result['missing_methods']:
                    results['missing_methods'].append({
                        'service': service_name,
                        'method': method
                    })
            
            # Determine overall status
            if methods_result['missing_methods']:
                results['services'][service_name] = {'status': 'Partial'}
            else:
                results['services'][service_name] = {'status': 'Implemented'}
        
        return results


def verify_services() -> Dict[str, Any]:
    """
    Convenience function to verify services.
    
    Returns:
        Verification results dictionary
    """
    verifier = ServiceVerifier()
    return verifier.generate_service_report()
