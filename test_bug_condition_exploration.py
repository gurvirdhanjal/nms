"""
Bug Condition Exploration Test for Device Addition Database Error

**Validates: Requirements 2.1, 2.2**

CRITICAL: This test MUST FAIL on unfixed code - failure confirms the bug exists.
DO NOT attempt to fix the test or the code when it fails.

This test encodes the EXPECTED behavior - it will validate the fix when it passes after implementation.

GOAL: Surface counterexamples that demonstrate the NotNullViolation bug exists.

Property 1: Fault Condition - SNMP Configuration Creation for New Devices
For any device creation request where the device is new (not in database) and SNMP data is provided,
the fixed code SHALL create the device and associate the SNMP configuration using SQLAlchemy relationships,
allowing the ORM to automatically manage the device_id foreign key after the device INSERT completes,
without raising NotNullViolation errors.
"""

import os
import sys
from flask import Flask
from extensions import db
from config import Config
from models.device import Device
from models.snmp_config import DeviceSnmpConfig
from services.device_identity import upsert_device_from_identity
from routes.scanning import _upsert_snmp_config_for_device
import psycopg2.errors

# Initialize Flask app for testing
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)


def cleanup_test_devices():
    """Remove test devices from database"""
    with app.app_context():
        # First delete SNMP configs for test devices
        test_devices = Device.query.filter(Device.device_ip.like('192.168.99.%')).all()
        for device in test_devices:
            DeviceSnmpConfig.query.filter_by(device_id=device.device_id).delete()
        # Then delete the devices
        Device.query.filter(Device.device_ip.like('192.168.99.%')).delete()
        db.session.commit()


def test_1_single_device_with_snmp_fails():
    """
    Test Case 1: Single Device with SNMP Data
    
    EXPECTED ON UNFIXED CODE: NotNullViolation error
    EXPECTED ON FIXED CODE: Device and SNMP config created successfully
    
    This test creates a new device via upsert_device_from_identity() with SNMP data,
    then calls _upsert_snmp_config_for_device() to create the SNMP configuration.
    
    On unfixed code, device.device_id is None before flush, causing NotNullViolation
    when DeviceSnmpConfig(device_id=device.device_id) is created.
    """
    print("\n" + "="*80)
    print("TEST 1: Single Device with SNMP Data")
    print("="*80)
    
    with app.app_context():
        try:
            test_ip = "192.168.99.100"
            
            # Clean up any existing test device
            Device.query.filter_by(device_ip=test_ip).delete()
            db.session.commit()
            
            # Create new device via identity service
            device, action, _ = upsert_device_from_identity(
                ip=test_ip,
                mac="AA:BB:CC:DD:EE:01",
                hostname="test-device-1",
                manufacturer="Test Manufacturer",
                device_type="server",
                is_monitored=True
            )
            
            print(f"✓ Device created: {device.device_name} @ {device.device_ip}")
            print(f"  Action: {action}")
            print(f"  device.device_id BEFORE flush: {device.device_id}")
            
            # Verify device_id is None before flush (this is the bug condition)
            if device.device_id is None:
                print("  ⚠ CONFIRMED: device.device_id is None before flush (BUG CONDITION)")
            else:
                print(f"  ⚠ UNEXPECTED: device.device_id is {device.device_id} (already set)")
            
            # Attempt to create SNMP config (this should fail on unfixed code)
            snmp_data = {
                'snmp_working': True,
                'snmp_community': 'public',
                'snmp_version': '2c',
                'snmp_port': 161
            }
            
            print(f"  Attempting to create SNMP config...")
            _upsert_snmp_config_for_device(device, snmp_data)
            
            # Commit the transaction (this is where NotNullViolation occurs on unfixed code)
            print(f"  Attempting to commit...")
            db.session.commit()
            
            # If we reach here, the test passed (expected on FIXED code)
            print(f"✓ SUCCESS: Device and SNMP config created without errors")
            print(f"  device.device_id AFTER commit: {device.device_id}")
            
            # Verify the SNMP config was created correctly
            snmp_config = DeviceSnmpConfig.query.filter_by(device_id=device.device_id).first()
            if snmp_config:
                print(f"✓ SNMP config created: device_id={snmp_config.device_id}, community={snmp_config.community_string}")
                assert snmp_config.device_id == device.device_id, "SNMP config device_id mismatch"
            else:
                print(f"✗ FAIL: SNMP config not found in database")
                return False
            
            return True
            
        except psycopg2.errors.NotNullViolation as e:
            # This is EXPECTED on unfixed code
            print(f"✓ EXPECTED FAILURE (unfixed code): NotNullViolation error")
            print(f"  Error: {str(e)}")
            print(f"  This confirms the bug exists: device_id is None when creating DeviceSnmpConfig")
            db.session.rollback()
            return False
            
        except Exception as e:
            print(f"✗ UNEXPECTED ERROR: {type(e).__name__}: {str(e)}")
            db.session.rollback()
            raise


def test_2_bulk_device_addition_with_session_pollution():
    """
    Test Case 2: Bulk Device Addition with Session Pollution
    
    EXPECTED ON UNFIXED CODE: First device fails, session polluted, subsequent devices fail
    EXPECTED ON FIXED CODE: All devices created successfully
    
    This test creates 5 new devices with SNMP data in sequence.
    On unfixed code, the first failure pollutes the session, causing all subsequent
    operations to fail with "This Session's transaction has been rolled back".
    """
    print("\n" + "="*80)
    print("TEST 2: Bulk Device Addition with Session Pollution")
    print("="*80)
    
    with app.app_context():
        success_count = 0
        failure_count = 0
        session_pollution_detected = False
        
        for i in range(1, 6):
            try:
                test_ip = f"192.168.99.{100 + i}"
                
                # Clean up any existing test device
                Device.query.filter_by(device_ip=test_ip).delete()
                db.session.commit()
                
                # Create new device
                device, action, _ = upsert_device_from_identity(
                    ip=test_ip,
                    mac=f"AA:BB:CC:DD:EE:{i:02d}",
                    hostname=f"test-device-{i}",
                    manufacturer="Test Manufacturer",
                    device_type="server",
                    is_monitored=True
                )
                
                # Create SNMP config
                snmp_data = {
                    'snmp_working': True,
                    'snmp_community': 'public',
                    'snmp_version': '2c',
                    'snmp_port': 161
                }
                _upsert_snmp_config_for_device(device, snmp_data)
                
                # Commit
                db.session.commit()
                
                print(f"✓ Device {i}/5 created successfully: {test_ip}")
                success_count += 1
                
            except psycopg2.errors.NotNullViolation as e:
                print(f"✗ Device {i}/5 failed: NotNullViolation (expected on unfixed code)")
                failure_count += 1
                db.session.rollback()
                
            except Exception as e:
                error_msg = str(e)
                if "rolled back" in error_msg.lower():
                    print(f"✗ Device {i}/5 failed: Session pollution detected!")
                    print(f"  Error: {error_msg}")
                    session_pollution_detected = True
                    failure_count += 1
                else:
                    print(f"✗ Device {i}/5 failed: {type(e).__name__}: {error_msg}")
                    failure_count += 1
                db.session.rollback()
        
        print(f"\nResults: {success_count} succeeded, {failure_count} failed")
        
        if session_pollution_detected:
            print(f"✓ EXPECTED FAILURE (unfixed code): Session pollution detected")
            print(f"  This confirms the bug: session not properly cleaned up after NotNullViolation")
            return False
        elif failure_count > 0:
            print(f"✓ EXPECTED FAILURE (unfixed code): {failure_count} devices failed with NotNullViolation")
            return False
        else:
            print(f"✓ SUCCESS: All {success_count} devices created without errors")
            return True


def test_3_api_endpoint_returns_500():
    """
    Test Case 3: API Endpoint with New Device + SNMP Data
    
    EXPECTED ON UNFIXED CODE: 500 error
    EXPECTED ON FIXED CODE: 201 success with device created
    
    This test simulates a POST request to /scanning/add_to_inventory with new device + SNMP data.
    On unfixed code, the endpoint returns 500 error due to NotNullViolation.
    """
    print("\n" + "="*80)
    print("TEST 3: API Endpoint /scanning/add_to_inventory")
    print("="*80)
    
    with app.app_context():
        try:
            test_ip = "192.168.99.200"
            
            # Clean up any existing test device
            Device.query.filter_by(device_ip=test_ip).delete()
            db.session.commit()
            
            # Simulate the add_to_inventory endpoint logic
            data = {
                'ip_address': test_ip,
                'hostname': 'test-api-device',
                'mac_address': 'AA:BB:CC:DD:EE:FF',
                'device_type': 'server',
                'snmp_working': True,
                'snmp_community': 'public',
                'snmp_version': '2c',
                'snmp_port': 161
            }
            
            device, action, _ = upsert_device_from_identity(
                ip=data['ip_address'],
                mac=data['mac_address'],
                hostname=data['hostname'],
                manufacturer='Unknown',
                device_type=data['device_type'],
                is_monitored=True,
                is_active=True
            )
            
            if action in ("created", "updated"):
                _upsert_snmp_config_for_device(device, data)
                db.session.commit()
            
            print(f"✓ SUCCESS: API endpoint logic completed without errors")
            print(f"  Device created: {device.device_name} @ {device.device_ip}")
            print(f"  device_id: {device.device_id}")
            return True
            
        except psycopg2.errors.NotNullViolation as e:
            print(f"✓ EXPECTED FAILURE (unfixed code): NotNullViolation error")
            print(f"  This would result in 500 error response from API endpoint")
            print(f"  Error: {str(e)}")
            db.session.rollback()
            return False
            
        except Exception as e:
            print(f"✗ UNEXPECTED ERROR: {type(e).__name__}: {str(e)}")
            db.session.rollback()
            raise


def test_4_verify_device_id_none_before_flush():
    """
    Test Case 4: Verify device.device_id is None Before Flush
    
    This test explicitly verifies that device.device_id is None before flush,
    which is the root cause of the NotNullViolation bug.
    """
    print("\n" + "="*80)
    print("TEST 4: Verify device.device_id is None Before Flush")
    print("="*80)
    
    with app.app_context():
        try:
            test_ip = "192.168.99.250"
            
            # Clean up any existing test device
            Device.query.filter_by(device_ip=test_ip).delete()
            db.session.commit()
            
            # Create device without committing
            device = Device(
                device_name="Test Device",
                device_ip=test_ip,
                device_type="server",
                macaddress="AA:BB:CC:DD:EE:AA",
                hostname="test-device-flush",
                manufacturer="Test Manufacturer"
            )
            db.session.add(device)
            
            print(f"✓ Device added to session")
            print(f"  device.device_id BEFORE flush: {device.device_id}")
            
            if device.device_id is None:
                print(f"✓ CONFIRMED: device.device_id is None before flush")
                print(f"  This is the root cause: manual FK assignment would pass None to DeviceSnmpConfig")
            else:
                print(f"✗ UNEXPECTED: device.device_id is {device.device_id} (should be None)")
            
            # Now flush to get the ID
            db.session.flush()
            print(f"  device.device_id AFTER flush: {device.device_id}")
            
            if device.device_id is not None:
                print(f"✓ CONFIRMED: device.device_id is populated after flush")
                print(f"  This proves that flush is required before manual FK assignment")
            else:
                print(f"✗ UNEXPECTED: device.device_id is still None after flush")
            
            db.session.rollback()
            return True
            
        except Exception as e:
            print(f"✗ UNEXPECTED ERROR: {type(e).__name__}: {str(e)}")
            db.session.rollback()
            raise


def run_all_tests():
    """Run all bug condition exploration tests"""
    print("\n" + "="*80)
    print("BUG CONDITION EXPLORATION TEST SUITE")
    print("Device Addition Database Error Fix")
    print("="*80)
    print("\nCRITICAL: These tests MUST FAIL on unfixed code")
    print("Failure confirms the NotNullViolation bug exists")
    print("="*80)
    
    # Clean up before tests
    cleanup_test_devices()
    
    results = []
    
    # Run all tests
    results.append(("Test 1: Single Device with SNMP", test_1_single_device_with_snmp_fails()))
    results.append(("Test 2: Bulk Device Addition", test_2_bulk_device_addition_with_session_pollution()))
    results.append(("Test 3: API Endpoint", test_3_api_endpoint_returns_500()))
    results.append(("Test 4: Device ID Before Flush", test_4_verify_device_id_none_before_flush()))
    
    # Clean up after tests
    cleanup_test_devices()
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    passed = sum(1 for _, result in results if result)
    failed = sum(1 for _, result in results if not result)
    
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed} passed, {failed} failed out of {len(results)} tests")
    
    if failed > 0:
        print("\n" + "="*80)
        print("EXPECTED OUTCOME ON UNFIXED CODE:")
        print("Tests FAIL with NotNullViolation errors")
        print("This confirms the bug exists and needs to be fixed")
        print("="*80)
    else:
        print("\n" + "="*80)
        print("EXPECTED OUTCOME ON FIXED CODE:")
        print("All tests PASS - bug has been fixed!")
        print("="*80)
    
    return failed == 0


if __name__ == "__main__":
    try:
        all_passed = run_all_tests()
        sys.exit(0 if all_passed else 1)
    except Exception as e:
        print(f"\n✗ FATAL ERROR: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
