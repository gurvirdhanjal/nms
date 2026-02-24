# Bugfix Requirements Document

## Introduction

This document addresses a critical database error that prevents users from adding discovered devices from the network scanner to the inventory. The bug manifests as a `psycopg2.errors.NotNullViolation` error when attempting to insert SNMP configuration records with null device_id values, followed by session pollution that causes subsequent requests to fail.

The root cause is manual foreign key assignment where device_id is passed explicitly to SNMP configuration creation functions before the device's auto-generated ID is available from PostgreSQL. The proper solution is to use SQLAlchemy relationships, which handle foreign key assignment automatically and atomically within the transaction.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a device is created via `upsert_device_from_identity()` and `_upsert_snmp_config_for_device(device, data)` is called immediately after THEN the system manually passes device.device_id to create DeviceSnmpConfig, which is None before the session flushes, causing a NotNullViolation error

1.2 WHEN a device is created and `_upsert_device_snmp_config(device_id=device.device_id, ...)` is called in bulk operations THEN the system manually passes device_id as a parameter before it's available from PostgreSQL, resulting in None being inserted

1.3 WHEN the NotNullViolation error occurs THEN the system rolls back the transaction but leaves the session in a polluted state, causing subsequent requests to fail with "This Session's transaction has been rolled back due to a previous exception during flush"

### Expected Behavior (Correct)

2.1 WHEN a device is created and SNMP configuration needs to be associated THEN the system SHALL use SQLAlchemy relationships (device.snmp_config = DeviceSnmpConfig(...)) to automatically handle foreign key assignment without manual device_id passing

2.2 WHEN SNMP configuration is created or updated THEN the system SHALL assign it via the device's relationship property, allowing SQLAlchemy to manage the device_id foreign key atomically within the transaction

2.3 WHEN any database error occurs in device addition endpoints THEN the system SHALL properly clean up the session state with db.session.rollback() to prevent session pollution

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a device already exists in the database and is being updated THEN the system SHALL CONTINUE TO update the device record and its SNMP configuration without errors

3.2 WHEN SNMP configuration already exists for a device THEN the system SHALL CONTINUE TO update the existing configuration record rather than creating a new one

3.3 WHEN device creation succeeds without SNMP data THEN the system SHALL CONTINUE TO create the device without requiring SNMP configuration

3.4 WHEN multiple devices are added in bulk operations THEN the system SHALL CONTINUE TO process all devices and report individual successes/failures

3.5 WHEN device identity matching occurs (by MAC, hostname, or IP) THEN the system SHALL CONTINUE TO correctly identify and merge duplicate devices
