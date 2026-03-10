# Server Dashboard Troubleshooting Guide

## Data Not Populating

### Issue: Dashboard shows "Loading..." but no data appears

**Root Causes & Solutions:**

#### 1. No Server Devices with Agent Data
**Symptom**: All KPIs show "-" or "0"

**Check**:
```sql
-- Check if you have server devices
SELECT device_id, device_name, device_type, device_ip 
FROM devices 
WHERE device_type = 'server';

-- Check if you have agent health logs
SELECT COUNT(*) FROM server_health_logs WHERE source = 'agent';
```

**Solution**:
- Ensure you have devices with `device_type = 'server'`
- Ensure tactical agents are installed and reporting
- Check agent connectivity on port 5002

#### 2. API Endpoint Not Responding
**Symptom**: Browser console shows 404 or 500 errors

**Check Browser Console**:
```
F12 → Console tab
Look for: Failed to fetch server data
```

**Solution**:
```bash
# Test API directly
curl http://127.0.0.1:5000/api/server/health

# Should return JSON with servers array
```

#### 3. RBAC Permissions
**Symptom**: API returns empty servers array

**Check**:
- User must have permission to view servers
- Admin users see all servers
- Non-admin users see only their department/site servers

**Solution**:
- Login as admin user
- Or assign proper department/site to user

#### 4. JavaScript Errors
**Symptom**: Console shows JavaScript errors

**Common Errors**:
```javascript
// Error: Cannot read property 'toFixed' of null
// Fix: Added null checks in template

// Error: timeAgo is not defined
// Fix: Import timeAgo from utils.js
```

**Solution**:
- Clear browser cache
- Hard refresh (Ctrl+Shift+R)
- Check browser console for specific errors

## Verification Steps

### Step 1: Check API Response
```bash
curl http://127.0.0.1:5000/api/server/health | jq
```

Expected response:
```json
{
  "timestamp": "2026-03-09T15:45:00Z",
  "counts": {
    "total": 5,
    "healthy": 3,
    "warning": 1,
    "critical": 1,
    "offline": 0
  },
  "servers": [
    {
      "device_id": 123,
      "device_name": "web-server-01",
      "hostname": "web01",
      "ip": "192.168.1.100",
      "health": "Healthy",
      "last_seen": "2026-03-09T15:44:30Z",
      "cpu_usage": 45.2,
      "memory_usage": 62.8,
      "disk_usage": 38.5,
      "os": "Ubuntu 22.04",
      "uptime": 345600,
      "latency": 1.2,
      "packet_loss": 0.0,
      "jitter": 0.5
    }
  ]
}
```

### Step 2: Check Browser Network Tab
1. Open DevTools (F12)
2. Go to Network tab
3. Refresh page
4. Look for `/api/server/health` request
5. Check response status (should be 200)
6. Check response body (should have servers array)

### Step 3: Check JavaScript Console
1. Open DevTools (F12)
2. Go to Console tab
3. Look for errors (red text)
4. Common issues:
   - Import errors
   - Null reference errors
   - API fetch errors

### Step 4: Verify Database
```sql
-- Check server devices
SELECT COUNT(*) FROM devices WHERE device_type = 'server';

-- Check latest agent logs
SELECT d.device_name, d.device_ip, shl.timestamp, shl.cpu_usage, shl.memory_usage
FROM devices d
LEFT JOIN server_health_logs shl ON d.device_id = shl.device_id
WHERE d.device_type = 'server' AND shl.source = 'agent'
ORDER BY shl.timestamp DESC
LIMIT 10;
```

## Common Issues & Fixes

### Issue: KPIs show "NaN%"
**Cause**: Division by zero or null values

**Fix**: Already implemented null checks
```javascript
const avgCpu = servers.reduce((sum, s) => sum + (s.cpu_usage || 0), 0) / servers.length;
```

### Issue: "Offline" count is wrong
**Cause**: Health calculation logic

**Check**: `utils/server_health.py` - `compute_server_health()` function

### Issue: Charts not rendering
**Cause**: Chart.js not loaded or canvas elements missing

**Fix**:
1. Ensure Chart.js is included in base template
2. Check canvas IDs match JavaScript
3. Verify chart initialization code runs after DOM load

### Issue: Modal doesn't open
**Cause**: Modal not initialized or device_id missing

**Fix**:
```javascript
// Check if modal is initialized
console.log('Modal instance:', modalInstance);

// Check device ID
console.log('Device ID:', btn.dataset.deviceId);
```

### Issue: Auto-refresh not working
**Cause**: Interval not set or page hidden

**Fix**:
```javascript
// Verify interval is running
console.log('Refresh interval:', refreshInterval);

// Check if page is visible
console.log('Page visible:', !document.hidden);
```

## Performance Issues

### Issue: Dashboard loads slowly
**Causes**:
1. Too many servers (>100)
2. Complex queries
3. Large time ranges

**Solutions**:
1. Add pagination to server table
2. Optimize database queries
3. Add indexes on frequently queried columns
4. Cache API responses

### Issue: High memory usage
**Causes**:
1. Chart.js memory leaks
2. Too many DOM elements
3. Large datasets

**Solutions**:
1. Destroy charts before recreating
2. Use virtual scrolling for large tables
3. Limit data points in charts

## Debug Mode

Enable debug logging:
```javascript
// Add to top of script block
const DEBUG = true;

function debug(...args) {
    if (DEBUG) console.log('[ServerDashboard]', ...args);
}

// Use throughout code
debug('Fetching server data...');
debug('Received data:', data);
debug('Updated KPIs:', counts);
```

## API Testing

Test API with different scenarios:

```bash
# Test as admin
curl -H "Cookie: session=<admin_session>" http://127.0.0.1:5000/api/server/health

# Test as regular user
curl -H "Cookie: session=<user_session>" http://127.0.0.1:5000/api/server/health

# Test with no servers
# (Should return empty array, not error)

# Test with offline servers
# (Should show in offline count)
```

## Quick Fixes

### Fix 1: Clear All Caches
```bash
# Browser
Ctrl+Shift+Delete → Clear cache

# Flask
rm -rf __pycache__
rm -rf static/.webassets-cache

# Restart server
```

### Fix 2: Reset Database Connection
```python
# In Flask shell
from extensions import db
db.session.remove()
db.engine.dispose()
```

### Fix 3: Verify Imports
```javascript
// Check all imports are working
import { createServerMetricsView } from "...";  // ✓
import { initServerModal, openServerModal } from "...";  // ✓
import { timeAgo } from "...";  // ✓
```

## Still Not Working?

1. Check Flask logs for errors
2. Check database connectivity
3. Verify all migrations ran
4. Test with minimal dataset (1-2 servers)
5. Try different browser
6. Check firewall/proxy settings

## Success Indicators

When working correctly, you should see:
- ✅ KPIs populate within 1-2 seconds
- ✅ Server table shows all servers
- ✅ Filters work (All, Problems, Healthy, etc.)
- ✅ Modal opens on chart icon click
- ✅ Full page opens on external link click
- ✅ Auto-refresh updates every 30 seconds
- ✅ No errors in browser console
- ✅ API returns 200 status

## Contact Support

If issues persist:
1. Collect browser console logs
2. Collect Flask application logs
3. Export API response
4. Note browser version and OS
5. Describe exact steps to reproduce
