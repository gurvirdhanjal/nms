"""
Phase 1 MVP Specification Data Structure

This module defines the complete Phase 1 specification including:
- Database tables with columns, indexes, and foreign keys
- Model classes with methods and relationships
- Service classes with methods and parameters
- API endpoints with paths and HTTP methods
- Task dependencies for gap prioritization
"""

PHASE1_SPEC = {
    'tables': [
        {
            'name': 'sites',
            'columns': ['id', 'name', 'address', 'timezone', 'contact_info', 'created_at'],
            'indexes': ['idx_sites_name'],
            'foreign_keys': []
        },
        {
            'name': 'departments',
            'columns': ['id', 'name', 'description', 'created_at'],
            'indexes': ['idx_departments_name'],
            'foreign_keys': []
        },
        {
            'name': 'print_job_audit',
            'columns': [
                'id', 'device_id', 'print_server_id', 'job_id', 'document_name',
                'user_account', 'source_ip', 'printer_name', 'page_count', 'size_bytes',
                'submission_time', 'completion_time', 'status', 'collection_source'
            ],
            'indexes': ['idx_print_jobs_device', 'idx_print_jobs_timestamp', 'idx_print_jobs_user'],
            'foreign_keys': [
                {'column': 'device_id', 'references': 'devices.id'}
            ]
        },
        {
            'name': 'printer_metrics',
            'columns': [
                'id', 'device_id', 'timestamp', 'status', 'status_code',
                'toner_black', 'toner_cyan', 'toner_magenta', 'toner_yellow',
                'paper_tray_status', 'page_count_total', 'page_count_color',
                'page_count_bw', 'job_queue_length'
            ],
            'indexes': ['idx_printer_metrics_device', 'idx_printer_metrics_timestamp'],
            'foreign_keys': [
                {'column': 'device_id', 'references': 'devices.id'}
            ]
        },
        {
            'name': 'polling_nodes',
            'columns': [
                'id', 'name', 'hostname', 'status', 'last_heartbeat',
                'auth_token', 'created_at'
            ],
            'indexes': ['idx_polling_nodes_name', 'idx_polling_nodes_status'],
            'foreign_keys': []
        },
        {
            'name': 'api_tokens',
            'columns': [
                'id', 'user_id', 'token_hash', 'name', 'created_at',
                'last_used', 'expires_at'
            ],
            'indexes': ['idx_api_tokens_user', 'idx_api_tokens_hash'],
            'foreign_keys': [
                {'column': 'user_id', 'references': 'users.id'}
            ]
        },
        {
            'name': 'rate_limits',
            'columns': [
                'id', 'api_token_id', 'window_start', 'request_count', 'created_at'
            ],
            'indexes': ['idx_rate_limits_token', 'idx_rate_limits_window'],
            'foreign_keys': [
                {'column': 'api_token_id', 'references': 'api_tokens.id'}
            ]
        }
    ],
    
    'table_columns': [
        {
            'table': 'devices',
            'columns': ['site_id', 'department_id', 'polling_node_id']
        },
        {
            'table': 'users',
            'columns': ['department_id', 'view_own_department', 'view_all_departments']
        }
    ],
    
    'models': [
        {
            'name': 'Site',
            'module': 'models.site',
            'methods': ['to_dict'],
            'relationships': ['devices']
        },
        {
            'name': 'Department',
            'module': 'models.department',
            'methods': ['to_dict'],
            'relationships': ['users', 'devices']
        },
        {
            'name': 'PrintJobAudit',
            'module': 'models.printer',
            'methods': ['to_dict'],
            'relationships': ['device']
        },
        {
            'name': 'PrinterMetrics',
            'module': 'models.printer',
            'methods': ['to_dict'],
            'relationships': ['device']
        },
        {
            'name': 'PollingNode',
            'module': 'models.polling_node',
            'methods': ['to_dict'],
            'relationships': ['devices']
        },
        {
            'name': 'APIToken',
            'module': 'models.api_token',
            'methods': ['to_dict', 'verify_token'],
            'relationships': ['user']
        }
    ],
    
    'services': [
        {
            'name': 'SitesService',
            'module': 'services.sites_service',
            'methods': [
                {'name': 'create_site', 'params': ['name', 'address', 'timezone', 'contact_info']},
                {'name': 'get_site', 'params': ['site_id']},
                {'name': 'list_sites', 'params': []},
                {'name': 'update_site', 'params': ['site_id', 'data']},
                {'name': 'delete_site', 'params': ['site_id']}
            ]
        },
        {
            'name': 'DepartmentsService',
            'module': 'services.departments_service',
            'methods': [
                {'name': 'create_department', 'params': ['name', 'description']},
                {'name': 'get_department', 'params': ['department_id']},
                {'name': 'list_departments', 'params': []},
                {'name': 'update_department', 'params': ['department_id', 'data']},
                {'name': 'delete_department', 'params': ['department_id']}
            ]
        },
        {
            'name': 'PrintJobsService',
            'module': 'services.print_jobs_service',
            'methods': [
                {'name': 'create_print_job', 'params': ['data']},
                {'name': 'list_print_jobs', 'params': ['filters']},
                {'name': 'get_total_pages', 'params': ['filters']},
                {'name': 'export_to_csv', 'params': ['filters']},
                {'name': 'cleanup_old_jobs', 'params': ['days']}
            ]
        },
        {
            'name': 'PrintLogCollector',
            'module': 'services.print_log_collector',
            'methods': [
                {'name': 'collect_from_windows_events', 'params': ['event_data']},
                {'name': 'collect_from_syslog', 'params': ['syslog_message']}
            ]
        },
        {
            'name': 'PollingNodeService',
            'module': 'services.polling_node_service',
            'methods': [
                {'name': 'register_node', 'params': ['name', 'hostname']},
                {'name': 'deregister_node', 'params': ['node_id']},
                {'name': 'update_heartbeat', 'params': ['node_id']},
                {'name': 'check_node_health', 'params': ['node_id']},
                {'name': 'assign_device', 'params': ['device_id', 'node_id']}
            ]
        }
    ],
    
    'endpoints': [
        # Sites endpoints
        {'path': '/api/sites', 'method': 'GET'},
        {'path': '/api/sites', 'method': 'POST'},
        {'path': '/api/sites/<int:id>', 'method': 'GET'},
        {'path': '/api/sites/<int:id>', 'method': 'PUT'},
        {'path': '/api/sites/<int:id>', 'method': 'DELETE'},
        
        # Departments endpoints
        {'path': '/api/departments', 'method': 'GET'},
        {'path': '/api/departments', 'method': 'POST'},
        {'path': '/api/departments/<int:id>', 'method': 'GET'},
        {'path': '/api/departments/<int:id>', 'method': 'PUT'},
        {'path': '/api/departments/<int:id>', 'method': 'DELETE'},
        
        # Devices endpoints (with query params)
        {'path': '/api/devices', 'method': 'GET', 'query_params': ['site_id', 'department_id']},
        
        # Printers endpoints
        {'path': '/api/printers', 'method': 'GET'},
        {'path': '/api/printers/<int:id>', 'method': 'GET'},
        {'path': '/api/printers/<int:id>/metrics', 'method': 'GET'},
        {'path': '/api/printers/<int:id>/jobs', 'method': 'GET'},
        
        # Print jobs endpoints
        {'path': '/api/print-jobs', 'method': 'GET', 'query_params': ['start_date', 'end_date', 'user', 'printer_id', 'site_id', 'department_id']},
        
        # API tokens endpoints
        {'path': '/api/tokens', 'method': 'POST'},
        {'path': '/api/tokens', 'method': 'GET'},
        {'path': '/api/tokens/<int:id>', 'method': 'DELETE'}
    ],
    
    'tasks': [
        {'id': '1.1', 'name': 'Database Schema Extensions', 'depends_on': []},
        {'id': '1.2', 'name': 'Site Model and Service', 'depends_on': ['1.1']},
        {'id': '1.3', 'name': 'Department Model and Service', 'depends_on': ['1.1']},
        {'id': '2.1', 'name': 'WEF Event Collector', 'depends_on': ['1.1']},
        {'id': '2.2', 'name': 'Syslog Receiver', 'depends_on': ['1.1']},
        {'id': '2.3', 'name': 'Print Jobs Service', 'depends_on': ['2.1', '2.2']},
        {'id': '3.1', 'name': 'SNMP Printer Polling', 'depends_on': ['1.1']},
        {'id': '4.1', 'name': 'API Token Model', 'depends_on': ['1.1']},
        {'id': '4.2', 'name': 'Rate Limiting', 'depends_on': ['4.1']},
        {'id': '4.3', 'name': 'REST API Endpoints', 'depends_on': ['1.2', '1.3', '2.3']},
        {'id': '5.1', 'name': 'RBAC Middleware', 'depends_on': ['1.3']},
        {'id': '6.1', 'name': 'Polling Node Service', 'depends_on': ['1.1']},
    ]
}
