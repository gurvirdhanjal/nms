"""
Departments Service for Organizational Unit Management.
Handles CRUD operations for departments and department-related queries.
"""
from typing import List, Optional
from extensions import db
from models.department import Department
from models.device import Device


class DepartmentsService:
    """Service for managing departments and department-related operations."""

    def create_department(self, name: str, description: str = None, site_id: int = None) -> Department:
        """
        Create a new department.
        
        Args:
            name: Department name (required, unique)
            description: Department description
            site_id: Optional site association
            
        Returns:
            Created Department object
            
        Raises:
            ValueError: If department with name already exists
        """
        try:
            # Check if department already exists
            existing = Department.query.filter_by(name=name).first()
            if existing:
                raise ValueError(f"Department with name '{name}' already exists")
            
            # Create department
            department = Department(
                name=name,
                description=description,
                site_id=site_id
            )
            
            db.session.add(department)
            db.session.commit()
            
            return department
            
        except Exception as e:
            db.session.rollback()
            raise

    def get_department(self, department_id: int) -> Optional[Department]:
        """
        Get department by ID.
        
        Args:
            department_id: Department ID
            
        Returns:
            Department object or None if not found
        """
        return Department.query.get(department_id)

    def list_departments(self) -> List[Department]:
        """
        List all departments.
        
        Returns:
            List of Department objects
        """
        return Department.query.order_by(Department.name).all()

    def update_department(self, department_id: int, **kwargs) -> Department:
        """
        Update department attributes.
        
        Args:
            department_id: Department ID
            **kwargs: Fields to update (name, description, site_id)
            
        Returns:
            Updated Department object
            
        Raises:
            ValueError: If department not found or name conflict
        """
        try:
            department = Department.query.get(department_id)
            if not department:
                raise ValueError(f"Department with ID {department_id} not found")
            
            # Update fields
            if 'name' in kwargs:
                # Check for name conflict
                existing = Department.query.filter(
                    Department.name == kwargs['name'],
                    Department.id != department_id
                ).first()
                if existing:
                    raise ValueError(f"Department with name '{kwargs['name']}' already exists")
                department.name = kwargs['name']
            
            if 'description' in kwargs:
                department.description = kwargs['description']
            
            if 'site_id' in kwargs:
                department.site_id = kwargs['site_id']
            
            db.session.commit()
            return department
            
        except Exception as e:
            db.session.rollback()
            raise

    def delete_department(self, department_id: int) -> bool:
        """
        Delete department and unassign devices/users automatically.
        
        Args:
            department_id: Department ID
            
        Returns:
            True if deleted successfully
            
        Raises:
            ValueError: If department not found
        """
        try:
            department = Department.query.get(department_id)
            if not department:
                raise ValueError(f"Department with ID {department_id} not found")
            
            device_count = Device.query.filter_by(department_id=department_id).count()
            if device_count:
                Device.query.filter_by(department_id=department_id).update(
                    {'department_id': None}, synchronize_session='fetch'
                )
            
            from models.user import User
            user_count = User.query.filter_by(department_id=department_id).count()
            if user_count:
                User.query.filter_by(department_id=department_id).update(
                    {'department_id': None}, synchronize_session='fetch'
                )
            
            db.session.delete(department)
            db.session.commit()
            return True
            
        except Exception as e:
            db.session.rollback()
            raise

    def get_department_devices(self, department_id: int) -> List[Device]:
        """
        Get all devices for a department.
        
        Args:
            department_id: Department ID
            
        Returns:
            List of Device objects
        """
        return Device.query.filter_by(department_id=department_id).order_by(Device.device_name).all()
