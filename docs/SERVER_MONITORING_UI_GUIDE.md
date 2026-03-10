# Server Monitoring UI Guide

## Visual Hierarchy

### 1. Enhanced Modal (Fullscreen)
```
┌─────────────────────────────────────────────────────────────────┐
│ [🖥️] Server Name                    [Full Page] [×]             │
│     192.168.1.100 • Active                                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Health Status: Healthy (Score 95)    Last Seen: 2m ago         │
│  Boot Time: 4d 6h 36m                 Uptime: 4d 6h 36m         │
│                                                                  │
│  [15M] [1H] [6H] [24H] [7D]  ← Time Range Selection            │
│                                                                  │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐           │
│  │ CPU Usage    │ │ Memory       │ │ Disk         │           │
│  │ 45.2%        │ │ 62.8%        │ │ 38.5%        │           │
│  │ [Chart]      │ │ [Chart]      │ │ [Chart]      │           │
│  └──────────────┘ └──────────────┘ └──────────────┘           │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐│
│  │ Network Throughput                                          ││
│  │ [Chart showing In/Out traffic]                              ││
│  └────────────────────────────────────────────────────────────┘│
│                                                                  │
│  ┌──────────────────────┐ ┌──────────────────────┐            │
│  │ Agent Connections    │ │ Connection Snapshot  │            │
│  │ Top 20 IPs           │ │ [Refresh Now]        │            │
│  │ [Table]              │ │ [Table]              │            │
│  └──────────────────────┘ └──────────────────────┘            │
│                                                                  │
│  [Load Average] [Swap Usage] [System Telemetry]                │
│  [Disk I/O Rates] [Top Processes]                              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2. Dedicated Full Page (`/devices/<id>/server-monitoring`)
```
┌─────────────────────────────────────────────────────────────────┐
│ STICKY HEADER (Always Visible)                                  │
│ ┌─────────────────────────────────────────────────────────────┐│
│ │ [🖥️] Server Name                                            ││
│ │     192.168.1.100 • Active                                  ││
│ │                                                              ││
│ │     [← Back] [⚙️ Config] [🔄 Refresh]                       ││
│ └─────────────────────────────────────────────────────────────┘│
├─────────────────────────────────────────────────────────────────┤
│ SCROLLABLE CONTENT                                               │
│                                                                  │
│  [Same telemetry panels as modal, but with extended metrics]    │
│  [Additional sections for admins: Threshold Editor]             │
│  [Per-Interface Network Stats]                                  │
│  [Top 5 CPU Processes]                                          │
│  [CPU Internals] [File Descriptor Usage]                        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3. Dashboard Server Table
```
┌─────────────────────────────────────────────────────────────────┐
│ Server Name        Health   CPU   Mem   Disk   Actions          │
├─────────────────────────────────────────────────────────────────┤
│ ● web-server-01    Healthy  45%   62%   38%   [📊] [🔗]        │
│   192.168.1.100                                                  │
│                                                                  │
│ ● db-server-02     Warning  78%   85%   42%   [📊] [🔗]        │
│   192.168.1.101                                                  │
└─────────────────────────────────────────────────────────────────┘

Legend:
[📊] = Quick View Modal
[🔗] = Full Page Monitoring
```

## Color Scheme

### Health Status
- **Healthy**: Green (#00e5a0)
- **Warning**: Amber (#ffc107)
- **Critical**: Red (#ef4444)
- **Unknown**: Gray (#6b7280)

### Backgrounds
- **Primary**: rgba(16, 19, 27, 0.92)
- **Surface**: rgba(24, 30, 42, 0.95)
- **Card**: rgba(24, 30, 42, 0.95)

### Borders
- **Default**: rgba(148, 163, 184, 0.08)
- **Accent**: rgba(148, 163, 184, 0.12)

### Text
- **Primary**: #e6edf5
- **Secondary**: #8a97a6
- **Muted**: #556070

## Typography

### Fonts
- **Monospace**: 'IBM Plex Mono', monospace (for IPs, metrics, technical data)
- **Sans-serif**: System default (for labels, descriptions)

### Sizes
- **Page Title**: 18px
- **Modal Title**: 13px
- **Section Headers**: 11px (uppercase, letter-spacing: 0.05em)
- **Metric Values**: 13px
- **Labels**: 10-12px

## Spacing

### Padding
- **Page Container**: 1.5rem 2rem
- **Modal Body**: 1.5rem 2rem
- **Card Body**: 12px 16px
- **Card Header**: 12px 16px

### Gaps
- **Grid Gaps**: 0.5rem - 1rem
- **Button Groups**: 0.5rem
- **Flex Gaps**: 0.5rem - 1rem

## Responsive Breakpoints

### Desktop (≥1200px)
- Full 10-column table layout
- All metrics visible
- Side-by-side panels

### Tablet (768px - 1199px)
- Stacked metric panels
- Hidden latency/jitter columns
- Reduced padding

### Mobile (<768px)
- Single column layout
- Minimal table columns
- Stacked action buttons

## Interaction States

### Buttons
- **Default**: rgba(255,255,255,0.04) background
- **Hover**: rgba(255,255,255,0.08) background
- **Active**: rgba(255,255,255,0.12) background

### Links
- **Default**: var(--text-secondary)
- **Hover**: var(--text-primary)
- **Transition**: all 0.2s

### Modal
- **Backdrop**: rgba(0,0,0,0.5)
- **Animation**: fade + slide
- **Close**: Opacity transition on hover

## Accessibility

### ARIA Labels
- Modal: `aria-hidden="true"` when closed
- Buttons: `aria-label` for icon-only buttons
- Tables: Proper `<th>` headers

### Keyboard Navigation
- Tab order: Header → Actions → Content
- Escape key: Closes modal
- Enter key: Activates focused button

### Screen Readers
- Semantic HTML structure
- Descriptive button text
- Status announcements for dynamic updates

## Performance Considerations

### Auto-Refresh
- Interval: 30 seconds
- Paused when: Modal closed, page hidden
- Manual trigger: Available via refresh button

### Chart Rendering
- Canvas-based (Chart.js)
- Debounced resize handlers
- Destroyed on cleanup

### Data Loading
- Progressive enhancement
- Loading states shown
- Error boundaries implemented

## Browser Support

### Minimum Requirements
- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+

### Features Used
- CSS Grid
- Flexbox
- ES6 Modules
- Fetch API
- Backdrop Filter
- CSS Custom Properties
