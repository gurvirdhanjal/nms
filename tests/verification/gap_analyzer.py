"""
Gap Analyzer for Phase 1 MVP

Analyzes gaps between specification and implementation:
- Identifies missing components
- Prioritizes by task dependencies
- Generates actionable recommendations
"""

from typing import Dict, List, Any
from .phase1_spec import PHASE1_SPEC


class GapAnalyzer:
    """Analyzes implementation gaps and prioritizes by dependencies."""
    
    def __init__(self, phase1_spec: Dict = None):
        """
        Initialize gap analyzer.
        
        Args:
            phase1_spec: Phase 1 specification dictionary (defaults to PHASE1_SPEC)
        """
        self.spec = phase1_spec or PHASE1_SPEC
    
    def analyze_gaps(self, verification_results: Dict) -> Dict[str, Any]:
        """
        Analyze gaps between spec and implementation.
        
        Args:
            verification_results: Combined results from all verifiers
            
        Returns:
            Dictionary with gap analysis:
            {
                'missing_components': List[dict],
                'partial_components': List[dict],
                'priority_order': List[str]
            }
        """
        missing_components = []
        partial_components = []
        
        # Analyze schema gaps
        if 'schema' in verification_results:
            schema_results = verification_results['schema']
            
            for table in schema_results.get('missing_tables', []):
                missing_components.append({
                    'type': 'table',
                    'name': table,
                    'task': self._find_task_for_component('table', table)
                })
            
            for col_info in schema_results.get('missing_columns', []):
                missing_components.append({
                    'type': 'column',
                    'name': f"{col_info['table']}.{col_info['column']}",
                    'task': self._find_task_for_component('column', col_info['table'])
                })
        
        # Analyze model gaps
        if 'models' in verification_results:
            model_results = verification_results['models']
            
            for model_info in model_results.get('missing_models', []):
                missing_components.append({
                    'type': 'model',
                    'name': model_info['name'],
                    'task': self._find_task_for_component('model', model_info['name'])
                })
            
            for method_info in model_results.get('missing_methods', []):
                partial_components.append({
                    'type': 'model_method',
                    'name': f"{method_info['model']}.{method_info['method']}",
                    'task': self._find_task_for_component('model', method_info['model'])
                })
        
        # Analyze service gaps
        if 'services' in verification_results:
            service_results = verification_results['services']
            
            for service_info in service_results.get('missing_services', []):
                missing_components.append({
                    'type': 'service',
                    'name': service_info['name'],
                    'task': self._find_task_for_component('service', service_info['name'])
                })
            
            for method_info in service_results.get('missing_methods', []):
                partial_components.append({
                    'type': 'service_method',
                    'name': f"{method_info['service']}.{method_info['method']}",
                    'task': self._find_task_for_component('service', method_info['service'])
                })
        
        # Analyze API gaps
        if 'api' in verification_results:
            api_results = verification_results['api']
            
            for endpoint_info in api_results.get('missing_endpoints', []):
                missing_components.append({
                    'type': 'endpoint',
                    'name': f"{endpoint_info['method']} {endpoint_info['path']}",
                    'task': self._find_task_for_component('endpoint', endpoint_info['path'])
                })
        
        # Prioritize gaps by dependencies
        priority_order = self.prioritize_by_dependencies(missing_components + partial_components)
        
        return {
            'missing_components': missing_components,
            'partial_components': partial_components,
            'priority_order': priority_order
        }
    
    def _find_task_for_component(self, component_type: str, component_name: str) -> str:
        """
        Find the task ID responsible for a component.
        
        Args:
            component_type: Type of component (table, model, service, endpoint)
            component_name: Name of component
            
        Returns:
            Task ID string (e.g., '1.2')
        """
        # Simple mapping based on component names
        task_mapping = {
            'sites': '1.2',
            'Site': '1.2',
            'SitesService': '1.2',
            'departments': '1.3',
            'Department': '1.3',
            'DepartmentsService': '1.3',
            'print_job_audit': '2.3',
            'PrintJobAudit': '2.3',
            'PrintJobsService': '2.3',
            'PrintLogCollector': '2.1',
            'printer_metrics': '3.1',
            'PrinterMetrics': '3.1',
            'polling_nodes': '6.1',
            'PollingNode': '6.1',
            'PollingNodeService': '6.1',
            'api_tokens': '4.1',
            'APIToken': '4.1',
            'rate_limits': '4.2',
        }
        
        # Check for direct match
        if component_name in task_mapping:
            return task_mapping[component_name]
        
        # Check for partial match
        for key, task_id in task_mapping.items():
            if key.lower() in component_name.lower():
                return task_id
        
        # Default to schema task
        return '1.1'
    
    def prioritize_by_dependencies(self, gaps: List[Dict]) -> List[str]:
        """
        Order gaps by task dependencies from Phase 1.
        
        Args:
            gaps: List of gap dictionaries with task IDs
            
        Returns:
            List of gap names in priority order
        """
        # Build task dependency graph
        task_graph = {}
        for task in self.spec['tasks']:
            task_id = task['id']
            dependencies = task.get('depends_on', [])
            task_graph[task_id] = dependencies
        
        # Topological sort
        sorted_tasks = self._topological_sort(task_graph)
        
        # Order gaps by sorted tasks
        prioritized_gaps = []
        for task_id in sorted_tasks:
            for gap in gaps:
                if gap.get('task') == task_id and gap['name'] not in prioritized_gaps:
                    prioritized_gaps.append(gap['name'])
        
        # Add any remaining gaps not matched to tasks
        for gap in gaps:
            if gap['name'] not in prioritized_gaps:
                prioritized_gaps.append(gap['name'])
        
        return prioritized_gaps
    
    def _topological_sort(self, graph: Dict[str, List[str]]) -> List[str]:
        """
        Perform topological sort on dependency graph.
        
        Args:
            graph: Dictionary mapping task IDs to their dependencies
            
        Returns:
            List of task IDs in dependency order
        """
        # Calculate in-degree for each node
        in_degree = {node: 0 for node in graph}
        for node in graph:
            for dep in graph[node]:
                if dep in in_degree:
                    in_degree[dep] += 1
        
        # Queue of nodes with no dependencies
        queue = [node for node in in_degree if in_degree[node] == 0]
        result = []
        
        while queue:
            node = queue.pop(0)
            result.append(node)
            
            # Reduce in-degree for dependent nodes
            for other_node in graph:
                if node in graph[other_node]:
                    in_degree[other_node] -= 1
                    if in_degree[other_node] == 0:
                        queue.append(other_node)
        
        return result
    
    def generate_recommendations(self, gaps: List[Dict]) -> List[str]:
        """
        Generate actionable recommendations for each gap.
        
        Args:
            gaps: List of gap dictionaries
            
        Returns:
            List of recommendation strings
        """
        recommendations = []
        
        for gap in gaps:
            gap_type = gap['type']
            gap_name = gap['name']
            task_id = gap.get('task', 'Unknown')
            
            if gap_type == 'table':
                recommendations.append(
                    f"Create table '{gap_name}' (Task {task_id}): "
                    f"Add migration script to create the table with required columns."
                )
            elif gap_type == 'column':
                recommendations.append(
                    f"Add column '{gap_name}' (Task {task_id}): "
                    f"Add migration script to alter the table."
                )
            elif gap_type == 'model':
                recommendations.append(
                    f"Implement model '{gap_name}' (Task {task_id}): "
                    f"Create model class with to_dict() method and relationships."
                )
            elif gap_type == 'service':
                recommendations.append(
                    f"Implement service '{gap_name}' (Task {task_id}): "
                    f"Create service class with required CRUD methods."
                )
            elif gap_type == 'endpoint':
                recommendations.append(
                    f"Implement endpoint '{gap_name}' (Task {task_id}): "
                    f"Add route handler with authentication and RBAC."
                )
            elif gap_type in ['model_method', 'service_method']:
                recommendations.append(
                    f"Add method '{gap_name}' (Task {task_id}): "
                    f"Implement the missing method in the class."
                )
        
        return recommendations


def analyze_gaps(verification_results: Dict) -> Dict[str, Any]:
    """
    Convenience function to analyze gaps.
    
    Args:
        verification_results: Combined verification results
        
    Returns:
        Gap analysis dictionary
    """
    analyzer = GapAnalyzer()
    return analyzer.analyze_gaps(verification_results)
