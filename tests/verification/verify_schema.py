"""
Schema Verifier for Phase 1 MVP

Verifies database schema matches Phase 1 specification by checking:
- Table existence
- Column definitions
- Indexes
- Foreign key constraints
"""

import sqlite3
from typing import Dict, List, Any
from .phase1_spec import PHASE1_SPEC


class SchemaVerifier:
    """Verifies database schema against Phase 1 specification."""
    
    def __init__(self, db_path: str = 'secure_employee_monitor.db'):
        """
        Initialize schema verifier.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.connection = None
        
    def connect(self) -> bool:
        """
        Connect to database with retry logic.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.connection = sqlite3.connect(self.db_path)
            self.connection.row_factory = sqlite3.Row
            return True
        except sqlite3.Error as e:
            print(f"Database connection error: {e}")
            return False
    
    def close(self):
        """Close database connection."""
        if self.connection:
            self.connection.close()
            self.connection = None
    
    def verify_table_exists(self, table_name: str, columns: List[str]) -> Dict[str, Any]:
        """
        Verify table exists with specified columns.
        
        Args:
            table_name: Name of table to check
            columns: List of expected column names
            
        Returns:
            Dictionary with verification results:
            {
                'exists': bool,
                'missing_columns': List[str],
                'extra_columns': List[str]
            }
        """
        cursor = self.connection.cursor()
        
        # Check if table exists
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name=?
        """, (table_name,))
        
        table_exists = cursor.fetchone() is not None
        
        if not table_exists:
            return {
                'exists': False,
                'missing_columns': columns,
                'extra_columns': []
            }
        
        # Get actual columns
        cursor.execute(f"PRAGMA table_info({table_name})")
        actual_columns = [row['name'] for row in cursor.fetchall()]
        
        # Compare columns
        missing_columns = [col for col in columns if col not in actual_columns]
        extra_columns = [col for col in actual_columns if col not in columns]
        
        return {
            'exists': True,
            'missing_columns': missing_columns,
            'extra_columns': extra_columns
        }
    
    def verify_indexes(self, table_name: str, indexes: List[str]) -> Dict[str, Any]:
        """
        Verify indexes exist on table.
        
        Args:
            table_name: Name of table
            indexes: List of expected index names
            
        Returns:
            Dictionary with verification results:
            {
                'exists': bool,
                'missing_indexes': List[str]
            }
        """
        cursor = self.connection.cursor()
        
        # Get actual indexes
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='index' AND tbl_name=?
        """, (table_name,))
        
        actual_indexes = [row['name'] for row in cursor.fetchall()]
        
        # Compare indexes
        missing_indexes = [idx for idx in indexes if idx not in actual_indexes]
        
        return {
            'exists': len(missing_indexes) == 0,
            'missing_indexes': missing_indexes
        }
    
    def verify_foreign_keys(self, table_name: str, fks: List[Dict]) -> Dict[str, Any]:
        """
        Verify foreign key constraints.
        
        Args:
            table_name: Name of table
            fks: List of foreign key specifications
            
        Returns:
            Dictionary with verification results:
            {
                'exists': bool,
                'missing_fks': List[dict]
            }
        """
        cursor = self.connection.cursor()
        
        # Get actual foreign keys
        cursor.execute(f"PRAGMA foreign_key_list({table_name})")
        actual_fks = cursor.fetchall()
        
        missing_fks = []
        for fk_spec in fks:
            column = fk_spec['column']
            references = fk_spec['references'].split('.')
            ref_table = references[0]
            ref_column = references[1] if len(references) > 1 else 'id'
            
            # Check if this FK exists
            fk_exists = any(
                fk['from'] == column and fk['table'] == ref_table
                for fk in actual_fks
            )
            
            if not fk_exists:
                missing_fks.append(fk_spec)
        
        return {
            'exists': len(missing_fks) == 0,
            'missing_fks': missing_fks
        }
    
    def generate_schema_report(self) -> Dict[str, Any]:
        """
        Generate complete schema verification report.
        
        Returns:
            Dictionary with complete verification results
        """
        if not self.connect():
            return {
                'error': 'Failed to connect to database',
                'tables': {},
                'missing_tables': [],
                'missing_columns': [],
                'missing_indexes': [],
                'missing_foreign_keys': []
            }
        
        try:
            results = {
                'tables': {},
                'missing_tables': [],
                'missing_columns': [],
                'missing_indexes': [],
                'missing_foreign_keys': []
            }
            
            # Check each table from spec
            for table_spec in PHASE1_SPEC['tables']:
                table_name = table_spec['name']
                columns = table_spec['columns']
                indexes = table_spec.get('indexes', [])
                fks = table_spec.get('foreign_keys', [])
                
                # Verify table and columns
                table_result = self.verify_table_exists(table_name, columns)
                
                if not table_result['exists']:
                    results['missing_tables'].append(table_name)
                    results['tables'][table_name] = {'status': 'Missing'}
                    continue
                
                # Track missing columns
                if table_result['missing_columns']:
                    for col in table_result['missing_columns']:
                        results['missing_columns'].append({
                            'table': table_name,
                            'column': col
                        })
                
                # Verify indexes
                index_result = self.verify_indexes(table_name, indexes)
                if index_result['missing_indexes']:
                    for idx in index_result['missing_indexes']:
                        results['missing_indexes'].append({
                            'table': table_name,
                            'index': idx
                        })
                
                # Verify foreign keys
                fk_result = self.verify_foreign_keys(table_name, fks)
                if fk_result['missing_fks']:
                    for fk in fk_result['missing_fks']:
                        results['missing_foreign_keys'].append({
                            'table': table_name,
                            'fk': fk
                        })
                
                # Determine overall status
                if (table_result['missing_columns'] or 
                    index_result['missing_indexes'] or 
                    fk_result['missing_fks']):
                    results['tables'][table_name] = {'status': 'Partial'}
                else:
                    results['tables'][table_name] = {'status': 'Implemented'}
            
            # Check for additional columns in existing tables
            for table_col_spec in PHASE1_SPEC.get('table_columns', []):
                table_name = table_col_spec['table']
                columns = table_col_spec['columns']
                
                table_result = self.verify_table_exists(table_name, columns)
                
                if table_result['exists'] and table_result['missing_columns']:
                    for col in table_result['missing_columns']:
                        results['missing_columns'].append({
                            'table': table_name,
                            'column': col
                        })
            
            return results
            
        finally:
            self.close()


def verify_schema(db_path: str = 'secure_employee_monitor.db') -> Dict[str, Any]:
    """
    Convenience function to verify schema.
    
    Args:
        db_path: Path to database file
        
    Returns:
        Verification results dictionary
    """
    verifier = SchemaVerifier(db_path)
    return verifier.generate_schema_report()
