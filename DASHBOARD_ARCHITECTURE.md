# Dashboard Architecture - Two-Level Model

## Overview

The monitoring system uses a **two-level dashboard model** for optimal operational clarity:

1. **Main Dashboard** (`/dashboard`) - NOC Overview
2. **Server Dashboard** (`/dashboard/servers`) - Server Operations

This separation prevents dashboard bloat while providing specialized views for different operational contexts.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                      MAIN DASHBOARD                              │
│                     (NOC Overview)                               │
│                    /dashboard                                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Purpose: Whole-platform situational awareness                  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Top Section: Overall Health Strip                        │  │
│  │ - Core KPIs                                              │  │
│  │ - Availability (Online/Offline/Maintenance)              │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Middle: Fleet Overview                                   │  │
│  │ - Top servers summary (4 KPI cards)                      │  │
│  │ - Server health table (top 10)                           │  │
│  │ - [View Server Dashboard] button ←─────────────┐         │  │
│  │ - Subnet health                                 │         │  │
│  │ - Network intelligence                          │         │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                     │         │  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Bottom: Alerts and Intelligence                          │  │
│  │ - Recent alerts                                          │  │
│  │ - Network topology                                       │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                     │         │  │
│  Answers:                                          │         │  │
│  • What is broken right now?                       │         │  │
│  • Is the estate healthy overall?                  │         │  │
│  • Which domain needs attention first?             │         │  │
│                                                     │         │  │
└─────────────────────────────────────────────────────┼─────────┘  │
                                                      │            │
                                                      ▼            │
┌─────────────────────────────────────────────────────────────────┐
│                    SERVER DASHBOARD                              │
│                  (Server Operations)                             │
│                 /dashboard/servers                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Purpose: Server-only fleet operations                          │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Row 1: Server Fleet Health KPIs                         │  │
│  │ [Total] [Healthy] [Warning] [Critical] [Offline] [>Thr] │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Row 2: Resource Pressure                                 │  │
│  │ [Avg CPU] [Avg Memory] [Avg Disk] [Fleet Uptime]        │  │
│  │ With P95 values and gauges                               │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Row 3: Priority Panels                                   │  │
│  │ [Top Problematic Servers] [Recent Server Alerts]         │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Row 4: Resource Trends (24h)                             │  │
│  │ [CPU Trend] [Memory Trend] [Disk Trend]                  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Row 5: Operational Table                                 │  │
│  │ Full server fleet with filters                           │  │
│  │ [All] [Problems] [Healthy] [Warning] [Critical]          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Answers:                                                        │
│  • Which servers are under pressure?                             │
│  • Which server is the highest priority incident?                │
│  • Is memory pressure systemic or isolated?                      │
│  • Are server alerts increasing?                                 │
│  • Which servers are trending toward failure?                    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │   Server Modal        │
              │   (Quick Triage)      │
              └───────────────────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │ Full Server Detail    │
              │ /devices/<id>/        │
              │ server-monitoring     │
              └───────────────────────┘
```

## Navigation Flow

```
Main Dashboard (NOC)
    ↓
    ├─→ [View Server Dashboard] button
    │       ↓
    │   Server Dashboard
    │       ↓
    │       ├─→ [Chart Icon] → Server Modal (Quick View)
    │       │                       ↓
    │       │                   [Full Page] button
    │       │                       ↓
    │       └─→ [External Link] → Full Server Detail Page
    │
    └─→ Fleet Overview Table
            ↓
            ├─→ [Chart Icon] → Server Modal
            └─→ [External Link] → Full Server Detail Page
```

## Dashboard Comparison

### Main Dashboard (NOC Overview)

**URL**: `/dashboard`

**Purpose**: Control tower for entire platform

**Content**:
- Overall health strip
- Core KPIs (all devices)
- Fleet overview (summary)
- Top 10 servers
- Subnet health
- Network intelligence
- Alerts and events
- Discovery status

**Target Users**:
- NOC operators
- Platform administrators
- Multi-domain managers

**Questions Answered**:
- What's broken right now?
- Is the estate healthy?
- Which domain needs attention?

**Server Content** (Summary Only):
- Fleet health percentage
- Average CPU/Memory/Disk
- Top 5 problematic servers
- Critical alert banner
- Quick "View" actions

**What NOT to Include**:
- Detailed process tables
- Long server trend panels
- Per-server resource charts
- Heavy server-only widgets
- Server-specific operations

### Server Dashboard (Server Operations)

**URL**: `/dashboard/servers`

**Purpose**: Dedicated server fleet operations

**Content**:
- Server fleet health KPIs
- Resource pressure metrics
- Top problematic servers
- Recent server alerts
- Server status distribution
- CPU/Memory/Disk trends
- Full operational table
- Process pressure indicators
- Connection analytics
- Threshold breach tracking

**Target Users**:
- Server administrators
- System operators
- DevOps engineers
- SRE teams

**Questions Answered**:
- Which servers are under pressure?
- Which server is highest priority?
- Is memory pressure systemic or isolated?
- Are server alerts increasing?
- Which servers are trending toward failure?

**Unique Features**:
- P95 resource metrics
- Fleet-wide uptime
- Resource trend charts
- Filter by health status
- Direct drill-down to single server

## Why This Separation Works

### 1. Prevents Dashboard Bloat
- Main dashboard stays focused on platform-wide view
- Server dashboard can expand without affecting NOC view
- Each dashboard has clear, distinct purpose

### 2. Operational Context
- NOC operators see cross-domain visibility
- Server admins see server-specific operations
- No confusion about which view to use

### 3. Performance
- Main dashboard loads faster (less data)
- Server dashboard can be more detailed
- Independent refresh cycles

### 4. Maintainability
- Clear separation of concerns
- Easier to update each dashboard
- No risk of breaking NOC view when adding server features

### 5. Scalability
- Can add more specialized dashboards (network, security, etc.)
- Pattern is repeatable
- Doesn't clutter main navigation

## Implementation Details

### Backend Routes

```python
# routes/monitoring.py

@monitoring_bp.route('/dashboard')
def dashboard():
    """Main NOC dashboard - platform-wide overview"""
    # Returns summary data for all domains
    return render_template('dashboard.html')

@monitoring_bp.route('/dashboard/servers')
def server_dashboard():
    """Dedicated server operations dashboard"""
    # Returns detailed server fleet data
    return render_template('server_dashboard.html')
```

### API Endpoints (Shared)

Both dashboards use the same backend APIs:
- `/api/dashboard/server_health` - Server metrics
- `/api/dashboard/alerts` - Alert data
- `/api/dashboard/statistics` - Platform stats

**Key Principle**: Don't duplicate logic. Use same services, different UI composition.

### Data Flow

```
Backend Services
    ↓
API Endpoints (shared)
    ↓
    ├─→ Main Dashboard (summarized slice)
    └─→ Server Dashboard (expanded slice)
```

## Best Practices

### Main Dashboard
1. Keep server content summarized
2. Show only top 5-10 servers
3. Use KPI cards for quick metrics
4. Provide clear link to Server Dashboard
5. Focus on cross-domain visibility

### Server Dashboard
1. Show full server fleet
2. Include detailed metrics and trends
3. Provide filtering and sorting
4. Enable drill-down to single server
5. Focus on server-specific operations

### Navigation
1. Make Server Dashboard easily accessible from Main Dashboard
2. Provide breadcrumb navigation
3. Keep modal for quick triage
4. Keep full page for deep investigation
5. Maintain consistent action buttons

## Future Expansion

This architecture supports adding more specialized dashboards:

```
Main Dashboard (NOC)
    ├─→ Server Dashboard
    ├─→ Network Dashboard (future)
    ├─→ Security Dashboard (future)
    └─→ Application Dashboard (future)
```

Each specialized dashboard:
- Has its own URL
- Uses shared backend services
- Provides domain-specific operations
- Links back to Main Dashboard
- Maintains consistent design language

## Summary

The two-level dashboard model provides:
- ✅ Clear separation of concerns
- ✅ Optimal operational context
- ✅ Prevents dashboard bloat
- ✅ Better performance
- ✅ Easier maintenance
- ✅ Scalable architecture
- ✅ Enterprise-grade UX

This is the correct enterprise setup for infrastructure monitoring platforms.
