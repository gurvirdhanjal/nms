"""
Report Generator for Phase 1 MVP Verification

Generates comprehensive markdown reports showing:
- Component implementation status
- Property test status
- Implementation and coverage percentages
- Gap analysis with prioritized recommendations
"""

import sys
import os
from datetime import datetime
from typing import Dict, Any

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from tests.verification.verify_schema import verify_schema
from tests.verification.verify_models import verify_models
from tests.verification.verify_services import verify_services
from tests.verification.gap_analyzer import analyze_gaps


class ReportGenerator:
    """Generates verification reports in markdown format."""
    
    def generate_verification_report(self, verification_results: Dict,
                                    test_results: Dict = None) -> str:
        """
        Generate markdown verification report.
        
        Args:
            verification_results: Results from all verifiers
            test_results: Results from test execution (optional)
            
        Returns:
            Markdown formatted report string
        """
        report = []
        
        # Header
        report.append("# Phase 1 MVP Verification Report")
        report.append(f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # Summary
        impl_pct = self.calculate_implementation_percentage(verification_results)
        report.append("## Summary\n")
        report.append(f"**Implementation Progress:** {impl_pct:.2f}%\n")
        
        if test_results:
            coverage_pct = self.calculate_coverage_percentage(test_results)
            report.append(f"**Test Coverage:** {coverage_pct:.2f}% (of 60 properties)\n")
        
        # Component Status
        report.append("\n## Component Status\n")
        
        # Schema status
        if 'schema' in verification_results:
            report.append("\n### Database Schema\n")
            schema_results = verification_results['schema']
            
            if schema_results.get('tables'):
                report.append("\n| Table | Status |")
                report.append("|-------|--------|")
                for table, info in schema_results['tables'].items():
                    status = info['status']
                    emoji = '✅' if status == 'Implemented' else '⚠️' if status == 'Partial' else '❌'
                    report.append(f"| {table} | {emoji} {status} |")
            
            if schema_results.get('missing_tables'):
                report.append("\n**Missing Tables:**")
                for table in schema_results['missing_tables']:
                    report.append(f"- {table}")
            
            if schema_results.get('missing_columns'):
                report.append("\n**Missing Columns:**")
                for col_info in schema_results['missing_columns']:
                    report.append(f"- {col_info['table']}.{col_info['column']}")
        
        # Model status
        if 'models' in verification_results:
            report.append("\n### Data Models\n")
            model_results = verification_results['models']
            
            if model_results.get('models'):
                report.append("\n| Model | Status |")
                report.append("|-------|--------|")
                for model, info in model_results['models'].items():
                    status = info['status']
                    emoji = '✅' if status == 'Implemented' else '⚠️' if status == 'Partial' else '❌'
                    report.append(f"| {model} | {emoji} {status} |")
            
            if model_results.get('missing_models'):
                report.append("\n**Missing Models:**")
                for model_info in model_results['missing_models']:
                    report.append(f"- {model_info['name']} ({model_info['module']})")
            
            if model_results.get('missing_methods'):
                report.append("\n**Missing Methods:**")
                for method_info in model_results['missing_methods']:
                    report.append(f"- {method_info['model']}.{method_info['method']}")
        
        # Service status
        if 'services' in verification_results:
            report.append("\n### Service Layer\n")
            service_results = verification_results['services']
            
            if service_results.get('services'):
                report.append("\n| Service | Status |")
                report.append("|---------|--------|")
                for service, info in service_results['services'].items():
                    status = info['status']
                    emoji = '✅' if status == 'Implemented' else '⚠️' if status == 'Partial' else '❌'
                    report.append(f"| {service} | {emoji} {status} |")
            
            if service_results.get('missing_services'):
                report.append("\n**Missing Services:**")
                for service_info in service_results['missing_services']:
                    report.append(f"- {service_info['name']} ({service_info['module']})")
            
            if service_results.get('missing_methods'):
                report.append("\n**Missing Methods:**")
                for method_info in service_results['missing_methods']:
                    report.append(f"- {method_info['service']}.{method_info['method']}")
        
        # API status
        if 'api' in verification_results:
            report.append("\n### API Endpoints\n")
            api_results = verification_results['api']
            
            if api_results.get('endpoints'):
                report.append("\n| Endpoint | Status |")
                report.append("|----------|--------|")
                for endpoint, info in api_results['endpoints'].items():
                    status = info['status']
                    emoji = '✅' if status == 'Implemented' else '❌'
                    report.append(f"| {endpoint} | {emoji} {status} |")
            
            if api_results.get('missing_endpoints'):
                report.append("\n**Missing Endpoints:**")
                for endpoint_info in api_results['missing_endpoints']:
                    report.append(f"- {endpoint_info['method']} {endpoint_info['path']}")
        
        # Gap Analysis
        if 'gaps' in verification_results:
            report.append("\n## Gap Analysis\n")
            gaps = verification_results['gaps']
            
            if gaps.get('missing_components'):
                report.append(f"\n**Missing Components:** {len(gaps['missing_components'])}")
            
            if gaps.get('partial_components'):
                report.append(f"\n**Partial Components:** {len(gaps['partial_components'])}")
            
            if gaps.get('priority_order'):
                report.append("\n### Priority Order (by dependencies)\n")
                for i, component in enumerate(gaps['priority_order'][:10], 1):
                    report.append(f"{i}. {component}")
                
                if len(gaps['priority_order']) > 10:
                    report.append(f"\n... and {len(gaps['priority_order']) - 10} more")
            
            # Recommendations
            if 'recommendations' in gaps:
                report.append("\n### Recommendations\n")
                for rec in gaps['recommendations'][:5]:
                    report.append(f"- {rec}")
                
                if len(gaps['recommendations']) > 5:
                    report.append(f"\n... and {len(gaps['recommendations']) - 5} more recommendations")
        
        # Test Results
        if test_results:
            report.append("\n## Test Results\n")
            
            if 'property_tests' in test_results:
                report.append("\n### Property Tests\n")
                prop_tests = test_results['property_tests']
                report.append(f"- **Total Properties:** {prop_tests.get('total', 0)}")
                report.append(f"- **Tested:** {prop_tests.get('tested', 0)}")
                report.append(f"- **Passed:** {prop_tests.get('passed', 0)}")
                report.append(f"- **Failed:** {prop_tests.get('failed', 0)}")
            
            if 'unit_tests' in test_results:
                report.append("\n### Unit Tests\n")
                unit_tests = test_results['unit_tests']
                report.append(f"- **Total:** {unit_tests.get('total', 0)}")
                report.append(f"- **Passed:** {unit_tests.get('passed', 0)}")
                report.append(f"- **Failed:** {unit_tests.get('failed', 0)}")
        
        return '\n'.join(report)
    
    def calculate_implementation_percentage(self, results: Dict) -> float:
        """
        Calculate percentage of implemented components.
        
        Args:
            results: Verification results dictionary
            
        Returns:
            Implementation percentage (0-100)
        """
        total_components = 0
        implemented_components = 0
        
        # Count schema components
        if 'schema' in results and 'tables' in results['schema']:
            for table, info in results['schema']['tables'].items():
                total_components += 1
                if info['status'] == 'Implemented':
                    implemented_components += 1
        
        # Count model components
        if 'models' in results and 'models' in results['models']:
            for model, info in results['models']['models'].items():
                total_components += 1
                if info['status'] == 'Implemented':
                    implemented_components += 1
        
        # Count service components
        if 'services' in results and 'services' in results['services']:
            for service, info in results['services']['services'].items():
                total_components += 1
                if info['status'] == 'Implemented':
                    implemented_components += 1
        
        # Count API components
        if 'api' in results and 'endpoints' in results['api']:
            for endpoint, info in results['api']['endpoints'].items():
                total_components += 1
                if info['status'] == 'Implemented':
                    implemented_components += 1
        
        if total_components == 0:
            return 0.0
        
        return (implemented_components / total_components) * 100
    
    def calculate_coverage_percentage(self, test_results: Dict) -> float:
        """
        Calculate percentage of tested properties (out of 60).
        
        Args:
            test_results: Test execution results
            
        Returns:
            Coverage percentage (0-100)
        """
        total_properties = 60  # As specified in Phase 1
        
        if 'property_tests' in test_results:
            tested = test_results['property_tests'].get('tested', 0)
            return (tested / total_properties) * 100
        
        return 0.0
    
    def format_as_markdown(self, report_data: Dict) -> str:
        """
        Format report data as markdown.
        
        Args:
            report_data: Structured report data
            
        Returns:
            Markdown formatted string
        """
        return self.generate_verification_report(report_data)


def main():
    """Main entry point for report generation."""
    print("Phase 1 MVP Verification System")
    print("=" * 50)
    print()
    
    # Run verifications
    print("Running schema verification...")
    schema_results = verify_schema()
    
    print("Running model verification...")
    model_results = verify_models()
    
    print("Running service verification...")
    service_results = verify_services()
    
    # Note: API verification requires Flask app instance
    # This would be done in a test context
    api_results = {
        'endpoints': {},
        'missing_endpoints': []
    }
    
    # Combine results
    verification_results = {
        'schema': schema_results,
        'models': model_results,
        'services': service_results,
        'api': api_results
    }
    
    # Analyze gaps
    print("Analyzing gaps...")
    gap_results = analyze_gaps(verification_results)
    verification_results['gaps'] = gap_results
    
    # Generate recommendations
    from tests.verification.gap_analyzer import GapAnalyzer
    analyzer = GapAnalyzer()
    all_gaps = gap_results['missing_components'] + gap_results['partial_components']
    recommendations = analyzer.generate_recommendations(all_gaps)
    verification_results['gaps']['recommendations'] = recommendations
    
    # Generate report
    print("Generating report...")
    generator = ReportGenerator()
    report = generator.generate_verification_report(verification_results)
    
    # Save report
    report_path = 'phase1_verification_report.md'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\nReport saved to: {report_path}")
    print()
    
    # Print summary
    impl_pct = generator.calculate_implementation_percentage(verification_results)
    print(f"Implementation Progress: {impl_pct:.2f}%")
    print()
    
    # Print report to console
    print(report)


if __name__ == '__main__':
    main()
