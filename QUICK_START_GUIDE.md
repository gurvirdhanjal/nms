# Server Monitoring - Quick Start Guide

## 🚀 What's New

Your server monitoring interface has been upgraded with enterprise-grade features:

1. **Fullscreen Modal** - Quick triage with maximum visibility
2. **Dedicated Full Page** - Deep investigation without distractions  
3. **Split Actions** - Choose between quick view or full page
4. **Auto-Refresh** - Metrics update every 30 seconds
5. **Enterprise Design** - Dense, subtle, professional appearance

## 📍 How to Access

### Option 1: From Dashboard (Quick View)
1. Navigate to Dashboard
2. Scroll to "Server Health" section
3. Find your server in the table
4. Click the **chart icon (📊)** button
5. Modal opens fullscreen with all metrics

### Option 2: From Dashboard (Full Page)
1. Navigate to Dashboard
2. Scroll to "Server Health" section
3. Find your server in the table
4. Click the **external link icon (🔗)** button
5. Opens dedicated monitoring page

### Option 3: From Device Details
1. Navigate to Devices → Click on a server
2. Click "Open Server Modal" button
3. In modal, click "Full Page" button
4. Opens dedicated monitoring page

### Option 4: Direct URL
Navigate to: `http://127.0.0.1:5000/devices/<device_id>/server-monitoring`

Example: `http://127.0.0.1:5000/devices/7230/server-monitoring`

## 🎯 Key Features

### Fullscreen Modal
- **Purpose**: Quick operational triage
- **Access**: Chart icon (📊) in dashboard table
- **Features**:
  - All core metrics visible
  - Time range selection (15m, 1h, 6h, 24h, 7d)
  - Connection snapshot
  - Top processes
  - Load average, swap, disk I/O
  - "Full Page" button for deeper investigation

### Dedicated Full Page
- **Purpose**: Deep investigation and monitoring
- **Access**: External link icon (🔗) or "Full Page" button
- **Features**:
  - Sticky header (always visible)
  - Extended metrics
  - Threshold editor (admins only)
  - Per-interface network stats
  - Top CPU processes
  - CPU internals
  - File descriptor usage
  - Manual refresh button
  - Auto-refresh every 30 seconds

## 🎨 Visual Guide

### Dashboard Table Actions
```
Server Name        Actions
web-server-01      [📊] [🔗]
                    ↓    ↓
                  Modal  Full Page
```

### Modal Header
```
[🖥️] web-server-01              [Full Page] [×]
    192.168.1.100 • Active
```

### Full Page Header
```
[🖥️] web-server-01
    192.168.1.100 • Active
    
    [← Back] [⚙️ Config] [🔄 Refresh]
```

## ⚡ Quick Actions

### View Server Metrics
1. Dashboard → Server table → Click 📊
2. View metrics in fullscreen modal

### Deep Investigation
1. Dashboard → Server table → Click 🔗
2. Full page opens with extended metrics

### Refresh Metrics
- **Auto**: Happens every 30 seconds
- **Manual**: Click "Refresh" button

### Change Time Range
1. Click time range buttons: [15M] [1H] [6H] [24H] [7D]
2. Metrics reload for selected range

### View Connections
1. Scroll to "Connection Snapshot" section
2. Click "Refresh Now" button
3. See live connection data

## 🔧 Troubleshooting

### Modal Not Opening
- Check browser console for errors
- Ensure device is a server type
- Verify JavaScript is enabled

### Full Page Shows 400 Error
- Device must be type "server"
- Check device type in device details

### Metrics Not Loading
- Check network connectivity
- Verify agent is running on server
- Check server health logs

### Auto-Refresh Not Working
- Check browser console
- Ensure page is visible (not in background tab)
- Verify no JavaScript errors

## 📱 Mobile Support

### Responsive Design
- Modal adapts to screen size
- Full page has mobile layout
- Touch-friendly buttons
- Optimized spacing

### Mobile Actions
- Tap chart icon for modal
- Tap external link for full page
- Swipe to scroll metrics
- Pinch to zoom charts

## 🎓 Best Practices

### For Quick Checks
- Use modal (📊) for fast triage
- Check health status at a glance
- View current metrics quickly

### For Investigation
- Use full page (🔗) for deep dive
- Analyze historical trends
- Compare multiple metrics
- Review connection patterns

### For Monitoring
- Keep full page open in separate tab
- Auto-refresh keeps data current
- Use time range to analyze trends
- Export data if needed (future feature)

## 🔐 Permissions

### All Users
- View server metrics
- Open modal
- Access full page
- View connection data

### Admins Only
- Edit threshold profiles
- Modify alert settings
- Configure monitoring

## 📊 Metrics Explained

### Health Status
- **Healthy**: All metrics within thresholds
- **Warning**: One or more metrics elevated
- **Critical**: One or more metrics in danger zone

### CPU Usage
- Current percentage
- Historical trend chart
- Peak values

### Memory Usage
- Used vs Total
- Percentage utilized
- Swap usage

### Disk Usage
- Used vs Total
- Percentage full
- I/O rates

### Network
- Throughput (in/out)
- Connection count
- Top remote IPs

### System
- Load average (1/5/15 min)
- Process count
- Zombie processes
- File descriptors

## 🆘 Support

### Documentation
- `SERVER_MONITORING_IMPROVEMENTS.md` - Technical details
- `SERVER_MONITORING_UI_GUIDE.md` - Visual guide
- `IMPLEMENTATION_CHECKLIST.md` - Feature checklist

### Common Issues
1. **Modal blank**: Refresh page, check console
2. **Metrics not updating**: Check agent status
3. **Full page 404**: Verify URL format
4. **Slow loading**: Check network, reduce time range

## 🎉 Tips & Tricks

### Keyboard Shortcuts
- `Esc` - Close modal
- `Tab` - Navigate buttons
- `Enter` - Activate focused button

### Time Range Selection
- **15M**: Real-time monitoring
- **1H**: Recent activity
- **6H**: Short-term trends
- **24H**: Daily patterns (default)
- **7D**: Weekly trends

### Connection Snapshot
- Shows top 20 remote IPs
- Click "Refresh Now" for latest data
- Resolved devices show name/type
- Unknown devices show IP only

### Performance
- Modal loads faster than full page
- Full page has more metrics
- Auto-refresh is efficient
- Manual refresh forces update

## 🌟 What's Next

The server monitoring interface is now production-ready with:
- ✅ Enterprise-grade design
- ✅ Fast operational triage
- ✅ Deep investigation capabilities
- ✅ Real-time updates
- ✅ Mobile support

Enjoy your enhanced monitoring experience!
