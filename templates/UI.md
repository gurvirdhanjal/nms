Version 4.0 — Network Monitoring Edition

Scope: Every template, component, modal, table, JS-rendered element, and real-time monitoring surface

This document is the operational ground truth.
The dashboard is the visual reference.
If any new page conflicts with this file — the new page is wrong.

0. Mental Model — Monitoring First

This is a live Network Monitoring System (NMS).

It is not a SaaS marketing UI.
It is not decorative.
It is not expressive.

It is a control surface for anomaly detection under operational stress.

Design must optimize for:

Rapid anomaly detection

Peripheral vision degradation detection

Stable layout under live updates

Zero decorative distraction

Numeric clarity over visual flourish

Core Philosophy

Healthy systems must visually recede.
Warning systems must be detectable within 1 second.
Critical systems must be detectable in peripheral vision.

If a design change improves aesthetics but reduces anomaly clarity — it is rejected.

1. Operational UI Principles
1.1 Status Hierarchy

Visual dominance order:

Critical

Warning

Degraded

Healthy

Unknown

Green is informational, not celebratory.

Healthy states must never visually dominate the layout.

1.2 Monitoring Stability Rule

The interface must remain visually stable during polling updates.

No layout shift during refresh

No resizing based on value change

No animated emphasis on state change

No flashing, blinking, or pulsing

Status updates must be instant and calm.

Motion is allowed only for structural transitions (panel open/close, tab switch).

1.3 Time Context Rule

Every state must include temporal context.

Examples:

Last seen

Last polled

Updated X seconds ago

Downtime duration

Consecutive failure count

Status without time context is incomplete.

2. CSS Architecture
Class Hierarchy — Never Bypass
body
└── .tactical-theme
    └── .dashboard-enterprise
        ├── .overall-health-card
        ├── .tactical-stat-card
        │   └── .enterprise-kpi
        ├── #device-breakdown
        │   └── .breakdown-panel
        │       ├── .breakdown-grid
        │       ├── .breakdown-subcard
        │       └── .breakdown-section-heading
        ├── .fleet-table-card
        ├── .alerts-container
        └── .enterprise-panel

Hard Rules

❌ No inline style="" (except canvas height)

❌ No unscoped CSS

❌ No hardcoded hex values in JS

❌ No !important outside .dashboard-enterprise

❌ No decorative gradients in enterprise mode

❌ No glass blur effects for aesthetic purposes

❌ No infinite animations

❌ No bounce easing

❌ No layout shift on data update

❌ No display: none toggling without animated wrapper

❌ No chart.destroy() on refresh

❌ No center-aligned numeric metrics

❌ No green active tabs

3. Design Tokens (Enterprise Mode)

Defined on .dashboard-enterprise.

.dashboard-enterprise {
    --e-space-1: 0.4rem;
    --e-space-2: 0.62rem;
    --e-space-3: 0.82rem;
    --e-space-4: 1.15rem;
    --e-space-5: 1.55rem;

    --e-bg-panel:      rgba(16, 19, 27, 0.88);
    --e-bg-panel-soft: rgba(16, 19, 27, 0.72);

    --e-border:        rgba(148, 163, 184, 0.20);
    --e-border-strong: rgba(148, 163, 184, 0.36);

    --e-text-primary:   #e6edf5;
    --e-text-secondary: #c3cfdb;
    --e-text-muted:     #93a0ae;
}


No raw values outside this system.

4. Status Colors (Monitoring-Tuned)
Status	Color	Notes
Healthy	#20c997 (muted use only)	Must not dominate layout
Warning	#ffc107	Must contrast clearly with green
Critical	#dc3545	Must be immediately visible
Offline	#6c757d	Muted neutral
Unknown	#adb5bd	Informational

Healthy state must visually recede compared to warning/critical.

5. Layout Hierarchy — 4 Operational Layers
Layer 1 — Global Status Strip
Layer 2 — KPI Grid
Layer 3 — Breakdown Intelligence
Layer 4 — Alerts Command

5.1 Alerts Escalation Rule

If active critical alerts > 0:

Alerts container visually elevates above Fleet Overview

Divider spacing reduces

Header border strengthens (--e-border-strong)

No flashing. No animation. Only priority shift.

6. KPI Philosophy (Monitoring Order)

KPI order must reflect operational urgency:

Health

Availability

Critical Count

Resource Saturation

Performance Metrics

Reordering for visual symmetry is forbidden.

KPI Hover Rules

Enterprise hover:

transform: translateY(-1px);


Never scale beyond 1.02.

7. Tables — Monitoring Optimized
Hard Rules

Right-align numeric columns

Freeze column widths

Use font-variant-numeric: tabular-nums

No dynamic resizing on value change

.metric {
    text-align: right;
}

8. Availability Cells — Context Required

Availability color alone is insufficient.

Hover must display:

Last failure time

Total downtime

Consecutive failure count

Enterprise mode must use muted backgrounds — no vivid gradients.

9. Animation System — Operational Constraints
Durations
Type	Duration
Micro	120–150ms
Transition	150–220ms
Panel Expand	260–360ms
Maximum	400ms
Forbidden

Infinite animation

Pulse loops

Glow effects

Bounce easing

Scale > 1.02

Flashing

Neon shadows

Animated state change emphasis

State changes must be instant.

10. Charts — Diagnostic Only

Charts support metrics.
Numbers dominate.

Rules

Update with .update('none')

No destroy/recreate

Subtle grid lines

Muted color palette

No dominant gradients

Sparkline opacity ≤ 0.6

Charts are secondary.

11. Monitoring Load Visibility

Global status strip must include:

Poll interval

Last poll duration

Task queue backlog (if present)

Sync time

Operators must know if monitoring itself is degraded.

12. Error Handling Visibility

All API errors must appear in:

#global-error


Errors must not silently fail inside components.

13. Typography Rules (Unchanged from v3 but Operationally Framed)

KPI value: 1.2rem

KPI label: 0.63rem uppercase

Table headers: 0.62rem uppercase

Numeric fields: tabular-nums

No oversized KPI text

No decorative type scaling

Clarity > Expression.

14. Monitoring Metric Formatting Rules
Metric	Format
Percent	82.4%
Bytes	KB / MB / GB
Network rate	KB/s or MB/s
Time	UTC or relative
Uptime	14d 6h 32m
Load avg	1.24

Never raw bytes or seconds.

15. Prohibited Patterns
Pattern	Reason
Neon glow shadows	Distracting
Infinite pulse	Stress-inducing
Decorative blur	Non-operational
Green active tabs	Misleading emphasis
Centered metrics	Slower scan speed
Destroy/recreate charts	Flicker
Layout shift on refresh	Operator confusion
16. Monitoring-Specific Rules

• Healthy systems fade into background
• Degraded systems detectable within 1 second
• Critical systems visible in peripheral vision
• No animation on state change
• Right-align all metrics
• Always show time context
• No decorative gradients in enterprise mode
• Operational clarity overrides visual symmetry

17. Component Checklist (Before Shipping)
Tokens

 No hardcoded hex

 Scoped CSS only

 No !important abuse

Typography

 KPI sizes correct

 Numeric alignment correct

 Tabular numbers enabled

Animation

 Under 400ms

 No bounce

 No infinite loops

Monitoring

 Time context visible

 Loading placeholder present

 Error banner wired

 Charts update without flicker

18. Final Principle

This interface is not designed to impress.

It is designed to:

Reduce cognitive load

Surface operational risk

Remain stable under live polling

Support fast, confident decisions

If a visual decision increases drama, it is wrong.

If a visual decision increases clarity, it is correct.

Version 4.0 — Network Monitoring Edition
Update this file whenever dashboard tokens change.
The dashboard remains the reference implementation.