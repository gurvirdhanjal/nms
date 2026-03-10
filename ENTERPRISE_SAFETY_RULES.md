# Enterprise Safety Rules - Quick Reference

## Golden Rule: NO SILENT REASSIGNMENT

**NEVER auto-reassign existing devices to a different site/department.**

---

## Device Assignment Logic

```python
# ✅ CORRECT: Check existence before auto-assignment
existing_device = Device.query.filter_by(device_ip=ip_address).first()

if existing_device:
    # PRESERVE existing assignment
    assigned_site_id = existing_device.site_id
    action = 'preserved'
else:
    # NEW device - safe to auto-assign
    assigned_site_id = suggested_site_id
    action = 'auto_assigned'
```

```python
# ❌ WRONG: Always auto-assign (DANGEROUS!)
assigned_site_id = best_subnet.site_id  # Silently changes existing devices!
```

---

## Assignment Decision Matrix

| Device Status | Subnet Mapped | Action | User Notification |
|--------------|---------------|--------|-------------------|
| **New** | Yes | Auto-assign to site | ✅ "Auto-assigned to Site A based on subnet X" |
| **New** | No | Leave site_id=NULL | ⚠️ "IP not in any mapped subnet" |
| **Existing** | Yes (same site) | Preserve site_id | No notification needed |
| **Existing** | Yes (different site) | Preserve site_id | ℹ️ "Site preserved. Subnet suggests Site B." |
| **Existing** | No | Preserve site_id | No notification needed |

---

## User Notifications

### For New Devices:
```javascript
// Success with auto-assignment
{
  "success": true,
  "action": "created",
  "site_assignment_action": "auto_assigned",
  "info": "Device auto-assigned to site based on subnet 172.16.1.0/24"
}

// Warning for unmapped subnet
{
  "success": true,
  "action": "created",
  "site_assignment_action": "unassigned",
  "warning": "Device added but IP is not in any mapped subnet"
}
```

### For Existing Devices:
```javascript
// Info when site is preserved (different from subnet suggestion)
{
  "success": true,
  "action": "updated",
  "site_assignment_action": "preserved",
  "info": "Device already assigned to site ID 1. Subnet 172.16.2.0/24 suggests site ID 2. Site assignment preserved."
}
```

---

## Manual Reassignment Workflow

When admin needs to change a device's site:

1. **Navigate**: Device details page
2. **Review**: System shows current site vs suggested site
3. **Confirm**: Admin clicks "Reassign to Site B"
4. **Reason**: Admin enters reason for change
5. **Audit**: System logs: "User [admin] reassigned device from Site A to Site B. Reason: [reason]"

**API Endpoint**:
```bash
POST /api/devices/{device_id}/reassign-site
{
  "site_id": 2,
  "reason": "Device physically moved to new location"
}
```

---

## Admin Diagnostic Tools

### Check Site Alignment
```bash
GET /api/devices/site-alignment-check
```

Returns list of devices where current site ≠ suggested site (based on subnet).

**Use Case**: Periodic review to identify devices that may need manual reassignment.

### Get Site Suggestion
```bash
GET /api/devices/{device_id}/suggest-site
```

Returns:
- Current site
- Suggested site (from subnet)
- Reason for suggestion
- Whether action is needed

---

## Code Review Checklist

When reviewing device assignment code, verify:

- [ ] Checks if device exists before assigning site_id
- [ ] Preserves existing site_id for existing devices
- [ ] Only auto-assigns for NEW devices
- [ ] Provides clear user notifications
- [ ] Logs all manual reassignments to audit trail
- [ ] Never uses `UPDATE devices SET site_id = X WHERE ...` without existence check

---

## Testing Scenarios

### Test 1: New Device with Mapped Subnet
```
1. Scanner discovers 172.16.1.50 (new device)
2. Subnet 172.16.1.0/24 is mapped to Site A
3. ✅ Device auto-assigned to Site A
4. ✅ Info message shown
```

### Test 2: Existing Device Re-discovered
```
1. Device 172.16.1.50 exists, assigned to Site A
2. Admin maps 172.16.1.0/24 to Site B
3. Scanner re-discovers 172.16.1.50
4. ✅ Device remains in Site A (preserved)
5. ✅ Info message: "Site preserved. Subnet suggests Site B."
```

### Test 3: New Device with Unmapped Subnet
```
1. Scanner discovers 192.168.100.50 (new device)
2. No subnet mapping exists
3. ✅ Device added with site_id=NULL
4. ⚠️ Warning: "IP not in any mapped subnet"
```

### Test 4: Manual Reassignment
```
1. Admin opens device details
2. System shows: Current=Site A, Suggested=Site B
3. Admin clicks "Reassign to Site B"
4. Admin enters reason
5. ✅ Device reassigned to Site B
6. ✅ Audit log created
7. ✅ Department cleared (must be reassigned)
```

---

## Common Pitfalls to Avoid

### ❌ Pitfall 1: Blind Auto-Assignment
```python
# WRONG: Always assigns from subnet
device.site_id = best_subnet.site_id if best_subnet else None
```

### ❌ Pitfall 2: Bulk Update Without Check
```python
# WRONG: Updates all devices in subnet
Device.query.filter(Device.device_ip.like('172.16.1.%')).update({'site_id': 1})
```

### ❌ Pitfall 3: Silent Department Reassignment
```python
# WRONG: Changes department without user action
if device.site_id != department.site_id:
    device.department_id = None  # Silent change!
```

### ✅ Correct Approach
```python
# RIGHT: Explicit check and preserve
existing = Device.query.filter_by(device_ip=ip).first()
if existing:
    site_id = existing.site_id  # Preserve
else:
    site_id = suggested_site_id  # Auto-assign for new
```

---

## Audit Trail Requirements

All site/department changes MUST be logged:

```python
from middleware.rbac import create_audit_log

create_audit_log(
    action='reassign_site',
    entity_type='device',
    entity_id=device_id,
    entity_name=device.device_name,
    description=f'Reassigned from site {old_site_id} to {new_site_id}',
    changes={
        'old_site_id': old_site_id,
        'new_site_id': new_site_id,
        'reason': reason
    }
)
```

---

## Summary

**DO**:
- ✅ Check if device exists before auto-assignment
- ✅ Preserve existing site_id for existing devices
- ✅ Auto-assign only for NEW devices
- ✅ Provide clear user notifications
- ✅ Log all manual reassignments
- ✅ Provide admin tools for review and manual fixes

**DON'T**:
- ❌ Auto-reassign existing devices
- ❌ Bulk update without existence checks
- ❌ Silent changes without user notification
- ❌ Skip audit logging

---

**Remember**: In enterprise systems, explicit is better than implicit. Always require admin confirmation for reassignments.

---

**Document Version**: 1.0  
**Created**: 2026-02-26  
**Status**: Active Policy
