"""
Model Verifier for Phase 1 MVP

Verifies model classes match Phase 1 specification by checking:
- Model class existence
- Required methods
- SQLAlchemy relationships
"""

import importlib
import inspect
from typing import Dict, List, Any
from .phase1_spec import PHASE1_SPEC


class ModelVerifier:
    """Verifies model classes against Phase 1 specification."""
    
    def verify_model_exists(self, model_name: str, module_path: str) -> bool:
        """
        Check if model class exists.
        
        Args:
            model_name: Name of model class
            module_path: Python module path
            
        Returns:
            True if model exists, False otherwise
        """
        try:
            module = importlib.import_module(module_path)
            return hasattr(module, model_name)
        except (ImportError, AttributeError):
            return False
    
    def verify_model_methods(self, model_name: str, module_path: str, 
                            methods: List[str]) -> Dict[str, Any]:
        """
        Verify model has required methods.
        
        Args:
            model_name: Name of model class
            module_path: Python module path
            methods: List of required method names
            
        Returns:
            Dictionary with verification results:
            {
                'exists': bool,
                'missing_methods': List[str]
            }
        """
        try:
            module = importlib.import_module(module_path)
            model_class = getattr(module, model_name)
        except (ImportError, AttributeError) as e:
            return {
                'exists': False,
                'missing_methods': methods,
                'error': str(e)
            }
        
        missing_methods = []
        for method_name in methods:
            if not hasattr(model_class, method_name):
                missing_methods.append(method_name)
        
        return {
            'exists': True,
            'missing_methods': missing_methods
        }
    
    def verify_relationships(self, model_name: str, module_path: str,
                           relationships: List[str]) -> Dict[str, Any]:
        """
        Verify SQLAlchemy relationships exist.
        
        Args:
            model_name: Name of model class
            module_path: Python module path
            relationships: List of relationship names
            
        Returns:
            Dictionary with verification results:
            {
                'exists': bool,
                'missing_relationships': List[str]
            }
        """
        try:
            module = importlib.import_module(module_path)
            model_class = getattr(module, model_name)
        except (ImportError, AttributeError) as e:
            return {
                'exists': False,
                'missing_relationships': relationships,
                'error': str(e)
            }
        
        missing_relationships = []
        for rel_name in relationships:
            if not hasattr(model_class, rel_name):
                missing_relationships.append(rel_name)
        
        return {
            'exists': True,
            'missing_relationships': missing_relationships
        }
    
    def generate_model_report(self) -> Dict[str, Any]:
        """
        Generate complete model verification report.
        
        Returns:
            Dictionary with complete verification results
        """
        results = {
            'models': {},
            'missing_models': [],
            'missing_methods': [],
            'missing_relationships': []
        }
        
        for model_spec in PHASE1_SPEC['models']:
            model_name = model_spec['name']
            module_path = model_spec['module']
            methods = model_spec.get('methods', [])
            relationships = model_spec.get('relationships', [])
            
            # Check if model exists
            if not self.verify_model_exists(model_name, module_path):
                results['missing_models'].append({
                    'name': model_name,
                    'module': module_path
                })
                results['models'][model_name] = {'status': 'Missing'}
                continue
            
            # Verify methods
            methods_result = self.verify_model_methods(model_name, module_path, methods)
            if methods_result['missing_methods']:
                for method in methods_result['missing_methods']:
                    results['missing_methods'].append({
                        'model': model_name,
                        'method': method
                    })
            
            # Verify relationships
            rels_result = self.verify_relationships(model_name, module_path, relationships)
            if rels_result['missing_relationships']:
                for rel in rels_result['missing_relationships']:
                    results['missing_relationships'].append({
                        'model': model_name,
                        'relationship': rel
                    })
            
            # Determine overall status
            if (methods_result['missing_methods'] or 
                rels_result['missing_relationships']):
                results['models'][model_name] = {'status': 'Partial'}
            else:
                results['models'][model_name] = {'status': 'Implemented'}
        
        return results


def verify_models() -> Dict[str, Any]:
    """
    Convenience function to verify models.
    
    Returns:
        Verification results dictionary
    """
    verifier = ModelVerifier()
    return verifier.generate_model_report()
