"""
Apply NMS Design System v5.0 CSS changes to dashboard.html inline styles.
This script does targeted text replacements in the inline <style> block.
"""
import re

filepath = r'd:\device_monitoring_tactical\templates\dashboard.html'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

original = content  # backup for comparison

# ── 1. KPI view icon: remove green + scale ──
content = content.replace(
    'transition: all 0.2s ease;',
    'transition: color 130ms ease;',
    1  # only first occurrence
)
content = content.replace(
    '        color: #20c997;\n        transform: scale(1.1);',
    '        color: rgba(211, 220, 230, 0.72);\n        /* v5.0: no scale, no green */',
    1
)

# ── 2. Tab buttons: remove green, pill → 4px ──
content = content.replace(
    '        border-radius: 999px;\n        font-size: 0.7rem;\n        text-transform: uppercase;\n        letter-spacing: 0.08em;\n        transition: all 150ms ease;\n    }\n\n    .tabs button.active {\n        color: #d9f5f0;\n        border-color: rgba(32, 201, 151, 0.7);\n        background: rgba(32, 201, 151, 0.12);\n    }\n\n    .tabs button:hover {\n        color: #e6fff8;\n        border-color: rgba(32, 201, 151, 0.5);',
    '        border-radius: 4px;\n        font-size: 0.7rem;\n        text-transform: uppercase;\n        letter-spacing: 0.08em;\n        transition: border-color 130ms ease, background 130ms ease;\n    }\n\n    /* v5.0: active tabs are NEVER green */\n    .tabs button.active {\n        color: #e6edf5;\n        border-color: rgba(148, 163, 184, 0.32);\n        background: rgba(148, 163, 184, 0.18);\n    }\n\n    .tabs button:hover {\n        color: #e6edf5;\n        border-color: rgba(148, 163, 184, 0.32);',
    1
)

# ── 3. Overall health card border ──
content = content.replace(
    '        border: 1px solid rgba(255, 255, 255, 0.06);\n        border-radius: 8px;\n        padding: 0;\n        background: transparent;\n        margin-bottom: 0;\n    }',
    '        border: 1px solid rgba(148, 163, 184, 0.20);\n        border-radius: 6px;\n        padding: 0;\n        background: transparent;\n        margin-bottom: 0;\n    }',
    1
)

# ── 4. KPI card baseline: remove vivid bg, green hover ──
content = content.replace(
    '        background: rgba(255, 255, 255, 0.07);\n        border: 1px solid rgba(255, 255, 255, 0.08);\n        transition: transform 150ms ease, border-color 150ms ease;\n    }\n\n    .tactical-stat-card:hover {\n        border-color: rgba(32, 201, 151, 0.6);\n        transform: translateY(-2px);',
    '        background: rgba(16, 19, 27, 0.92);\n        border: 1px solid rgba(148, 163, 184, 0.16);\n        transition: transform 130ms ease, border-color 130ms ease;\n    }\n\n    /* v5.0: max -1px, no glow, no green */\n    .tactical-stat-card:hover {\n        border-color: rgba(148, 163, 184, 0.32);\n        transform: translateY(-1px);',
    1
)

# ── 5. KPI stat-label: v5.0 typography ──
content = content.replace(
    '    .tactical-stat-card .stat-label {\n        font-size: 0.78rem;\n        letter-spacing: 0.14em;\n        text-transform: uppercase;\n        color: #8b949e;\n    }\n\n    .tactical-stat-card .stat-value {\n        font-size: 1.6rem;\n        font-weight: 700;\n    }\n\n    .tactical-stat-card .metric-sub {\n        margin-top: 6px;\n        font-size: 0.78rem;\n        opacity: 0.7;\n    }',
    '    /* v5.0: KPI label typography */\n    .tactical-stat-card .stat-label {\n        font-family: \'IBM Plex Sans\', sans-serif;\n        font-size: 0.63rem;\n        font-weight: 700;\n        letter-spacing: 0.12em;\n        text-transform: uppercase;\n        color: #8a97a6;\n    }\n\n    /* v5.0: KPI value — IBM Plex Mono, max 1.2rem */\n    .tactical-stat-card .stat-value {\n        font-family: \'IBM Plex Mono\', monospace;\n        font-size: 1.2rem;\n        font-weight: 700;\n        font-variant-numeric: tabular-nums;\n    }\n\n    .tactical-stat-card .metric-sub {\n        margin-top: 6px;\n        font-size: 0.69rem;\n        color: #8a97a6;\n    }',
    1
)

# ── 6. Section divider ──
content = content.replace(
    '        border-top: 1px solid rgba(255, 255, 255, 0.06);\n    }',
    '        border-top: 1px solid rgba(148, 163, 184, 0.16);\n    }',
    1
)

# ── 7. Device/server card hover: remove green ──
content = content.replace(
    '        border-color: rgba(32, 201, 151, 0.6);\n        transform: translateY(-2px);\n        transition: transform 150ms ease, border-color 150ms ease;',
    '        border-color: rgba(148, 163, 184, 0.32);\n        transform: translateY(-1px);\n        transition: transform 130ms ease, border-color 130ms ease;',
    1
)

# ── 8. Alert row hover: remove glow ──
content = content.replace(
    '        box-shadow: 0 0 12px rgba(220, 53, 69, 0.25);',
    '        box-shadow: none; /* v5.0: no glow */',
    1
)

# ── 9. Breakdown panel: remove vivid bg ──
content = content.replace(
    '        background: rgba(14, 15, 22, 0.72);\n        border: 1px solid rgba(255, 255, 255, 0.07);',
    '        background: rgba(16, 19, 27, 0.92);\n        border: 1px solid rgba(148, 163, 184, 0.20);',
    1
)

# ── 10. Breakdown subcards: muted ──
content = content.replace(
    '        background: rgba(255, 255, 255, 0.02);\n        border: 1px solid rgba(255, 255, 255, 0.06);',
    '        background: rgba(16, 19, 27, 0.72);\n        border: 1px solid rgba(148, 163, 184, 0.16);',
    1
)
content = content.replace(
    '        border-bottom: 1px solid rgba(255, 255, 255, 0.06);',
    '        border-bottom: 1px solid rgba(148, 163, 184, 0.16);',
    1
)

# ── 11. Fleet overview card border: remove green ──
content = content.replace(
    '    #fleet-overview-row .tactical-stat-card {\n        border-color: rgba(32, 201, 151, 0.26);\n    }',
    '    #fleet-overview-row .tactical-stat-card {\n        border-color: rgba(148, 163, 184, 0.16);\n    }',
    1
)

# ── 12. Fleet table card: muted ──
content = content.replace(
    '        background: rgba(255, 255, 255, 0.03);',
    '        background: rgba(16, 19, 27, 0.72);',
    1
)
content = content.replace(
    '        border: 1px solid rgba(255, 255, 255, 0.06);\n        box-shadow: none;',
    '        border: 1px solid rgba(148, 163, 184, 0.16);\n        box-shadow: none;',
    1
)

# ── 13. Server filter active: remove green ──
content = content.replace(
    '        border-color: rgba(32, 201, 151, 0.35);\n        box-shadow: 0 0 0 1px rgba(32, 201, 151, 0.12) inset;',
    '        border-color: rgba(148, 163, 184, 0.32);\n        box-shadow: none;',
    1
)

# ── 14. Enterprise overrides: complete token set ──
old_tokens = """    .dashboard-enterprise {
        --e-space-1: 0.4rem;
        --e-space-2: 0.62rem;
        --e-space-3: 0.82rem;
        --e-space-4: 1.15rem;
        --e-space-5: 1.55rem;

        --e-bg-panel: rgba(16, 19, 27, 0.88);
        --e-bg-panel-soft: rgba(16, 19, 27, 0.72);
        --e-border: rgba(148, 163, 184, 0.2);
        --e-border-strong: rgba(148, 163, 184, 0.36);

        --e-text-primary: #e6edf5;
        --e-text-secondary: #c3cfdb;
        --e-text-muted: #93a0ae;
    }"""

new_tokens = """    /* ── NMS Design System v5.0 — Full Token Set ── */
    .dashboard-enterprise {
        /* Spacing */
        --e-space-1: 0.4rem;
        --e-space-2: 0.62rem;
        --e-space-3: 0.82rem;
        --e-space-4: 1.15rem;
        --e-space-5: 1.55rem;

        /* Surfaces */
        --e-bg-base:       #070a10;
        --e-bg-panel:      rgba(16, 19, 27, 0.92);
        --e-bg-panel-soft: rgba(16, 19, 27, 0.72);
        --e-bg-row-alt:    rgba(255, 255, 255, 0.018);
        --e-bg-row-hover:  rgba(148, 163, 184, 0.10);

        /* Borders */
        --e-border:        rgba(148, 163, 184, 0.16);
        --e-border-strong: rgba(148, 163, 184, 0.32);
        --e-border-panel:  rgba(148, 163, 184, 0.20);

        /* Typography */
        --e-text-primary:   #e6edf5;
        --e-text-secondary: #c3cfdb;
        --e-text-muted:     #8a97a6;
        --e-text-dim:       #5e6b78;

        /* Status Colors */
        --s-critical:  #dc3545;
        --s-warning:   #ffc107;
        --s-degraded:  #fd7e14;
        --s-healthy:   #20c997;
        --s-offline:   #6c757d;
        --s-unknown:   #adb5bd;

        /* Status Backgrounds (muted) */
        --s-critical-bg:  rgba(220, 53, 69, 0.12);
        --s-critical-bd:  rgba(220, 53, 69, 0.30);
        --s-warning-bg:   rgba(255, 193, 7, 0.10);
        --s-warning-bd:   rgba(255, 193, 7, 0.28);
        --s-healthy-bg:   rgba(32, 201, 151, 0.10);
        --s-healthy-bd:   rgba(32, 201, 151, 0.24);
        --s-offline-bg:   rgba(108, 117, 125, 0.12);
        --s-offline-bd:   rgba(108, 117, 125, 0.28);

        /* Animation Durations */
        --a-micro:      130ms;
        --a-transition: 180ms;
        --a-panel:      300ms;
        --a-max:        400ms;

        /* Typography Scale */
        --t-kpi-value:    1.2rem;
        --t-kpi-label:    0.63rem;
        --t-table-header: 0.62rem;
        --t-body:         0.82rem;
        --t-mono:         'IBM Plex Mono', monospace;
        --t-sans:         'IBM Plex Sans', sans-serif;
    }"""

content = content.replace(old_tokens, new_tokens, 1)

# ── 15. Panel border-radius: 10px → 6px ──
content = content.replace(
    '        border-radius: 10px !important;',
    '        border-radius: 6px !important;',
)  # replace all occurrences

# ── 16. Badge radius: 999px → 3px ──
content = content.replace(
    '        border-radius: 999px;',
    '        border-radius: 3px;',
)  # replace all in dashboard

# ── 17. Tab active enterprise: remove green ──
content = content.replace(
    '    .dashboard-enterprise .tabs button.active {\n        color: var(--e-text-primary);\n        border-color: rgba(173, 186, 201, 0.55);\n        background: rgba(148, 163, 184, 0.18);\n    }',
    '    /* v5.0: active tabs are NEVER green */\n    .dashboard-enterprise .tabs button.active {\n        color: var(--e-text-primary);\n        border-color: var(--e-border-strong);\n        background: rgba(148, 163, 184, 0.18);\n    }',
    1
)

# ── 18. Enterprise hover: remove box-shadow expansion ──
content = content.replace(
    '        border-color: var(--e-border-strong) !important;\n        transform: translateY(-1px);\n        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.22) !important;',
    '        border-color: var(--e-border-strong) !important;\n        transform: translateY(-1px);\n        /* v5.0: no shadow expansion on hover */\n        box-shadow: none !important;',
    1
)

# ── 19. Update #global-error to v5.0 spec ──
content = content.replace(
    '    <div id="global-error" class="alert alert-danger initially-hidden"></div>',
    '    <div id="global-error" class="initially-hidden" style="display:none;background:var(--s-critical-bg);border:1px solid var(--s-critical-bd);border-left:3px solid var(--s-critical);border-radius:4px;padding:var(--e-space-3) var(--e-space-4);font-family:var(--t-mono);font-size:0.72rem;color:#f5a0a8;margin-bottom:var(--e-space-3);"></div>',
    1
)

# ── 20. Availability cells: muted backgrounds per v5.0 ──
# Excellent: vivid gradient → muted rgba
content = content.replace(
    "        background: linear-gradient(135deg, rgba(29, 209, 161, 0.9), rgba(16, 172, 132, 0.9));\n        border-color: rgba(29, 209, 161, 0.85);\n        box-shadow: inset 0 0 8px rgba(29, 209, 161, 0.45);",
    "        background: rgba(32, 201, 151, 0.18);\n        border-color: rgba(32, 201, 151, 0.24);\n        box-shadow: none;",
    1
)
# Good
content = content.replace(
    "        background: linear-gradient(135deg, rgba(72, 219, 251, 0.85), rgba(10, 189, 227, 0.85));\n        border-color: rgba(72, 219, 251, 0.75);\n        box-shadow: inset 0 0 8px rgba(72, 219, 251, 0.35);",
    "        background: rgba(56, 139, 192, 0.18);\n        border-color: rgba(56, 139, 192, 0.24);\n        box-shadow: none;",
    1
)
# Warning
content = content.replace(
    "        background: linear-gradient(135deg, rgba(254, 202, 87, 0.9), rgba(255, 159, 67, 0.9));\n        border-color: rgba(255, 159, 67, 0.85);\n        box-shadow: inset 0 0 8px rgba(255, 159, 67, 0.35);",
    "        background: rgba(255, 193, 7, 0.18);\n        border-color: rgba(255, 193, 7, 0.28);\n        box-shadow: none;",
    1
)
# Bad
content = content.replace(
    "        background: linear-gradient(135deg, rgba(255, 107, 107, 0.9), rgba(238, 82, 83, 0.9));\n        border-color: rgba(238, 82, 83, 0.85);\n        box-shadow: inset 0 0 8px rgba(238, 82, 83, 0.35);",
    "        background: rgba(220, 53, 69, 0.20);\n        border-color: rgba(220, 53, 69, 0.30);\n        box-shadow: none;",
    1
)

# ── 21. KPI top accent line (::after) ──
# Add after the enterprise-kpi stat-value rule
kpi_accent = """
    /* v5.0: KPI card top accent line by status */
    .dashboard-enterprise .tactical-stat-card::after {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 2px;
    }
    .dashboard-enterprise .tactical-stat-card.critical::after { background: var(--s-critical); }
    .dashboard-enterprise .tactical-stat-card.warning::after  { background: var(--s-warning); }
    .dashboard-enterprise .tactical-stat-card.healthy::after  { background: rgba(32, 201, 151, 0.35); }

"""
# Insert before the enterprise table rules
content = content.replace(
    '    .dashboard-enterprise .tactical-table {\n        font-size: 0.79rem;\n    }',
    kpi_accent + '    .dashboard-enterprise .tactical-table {\n        font-size: 0.79rem;\n    }',
    1
)

# ── 22. KPI breakdown FocusPulse: remove ──
content = content.replace(
    """        #device-breakdown .breakdown-panel.scroll-focus {
            animation: breakdownFocusPulse 700ms ease;
        }

        @keyframes breakdownFocusPulse {
            0% {
                box-shadow: 0 0 0 0 rgba(148, 163, 184, 0.34), 0 10px 26px rgba(0, 0, 0, 0.26);
            }

            100% {
                box-shadow: 0 0 0 10px rgba(148, 163, 184, 0), 0 10px 26px rgba(0, 0, 0, 0.26);
            }
        }""",
    """        /* v5.0: focus pulse removed — state changes are instant */
        #device-breakdown .breakdown-panel.scroll-focus {
            /* no animation */
        }""",
    1
)

# ── 23. Breakdown panel: remove backdrop-filter ──
content = content.replace(
    '            backdrop-filter: blur(6px);\n            -webkit-backdrop-filter: blur(6px);',
    '            /* v5.0: no backdrop-filter blur */\n',
    1
)

# ── 24. Breakdown panel gradient → solid ──
content = content.replace(
    '            background: linear-gradient(180deg, rgba(16, 19, 27, 0.92) 0%, rgba(12, 16, 22, 0.92) 100%);',
    '            background: var(--e-bg-panel);',
    1
)

# ── 25. Availability cell inset shadow → clean ──
content = content.replace(
    '            background: rgba(18, 24, 32, 0.85);\n            box-shadow: inset 0 0 10px rgba(0, 0, 0, 0.55);\n            transition: transform 0.22s ease, box-shadow 0.22s ease;\n            will-change: transform, box-shadow;',
    '            background: rgba(18, 24, 32, 0.85);\n            box-shadow: none;\n            transition: border-color 130ms ease;',
    1
)
content = content.replace(
    '            transform: translateY(-1px);\n            box-shadow: inset 0 0 10px rgba(0, 0, 0, 0.55), 0 0 10px rgba(0, 0, 0, 0.35);',
    '            /* v5.0: no transform on availability hover */\n            transform: none;\n            box-shadow: none;',
    1
)

# Verify changes
changes = 0
for i, (a, b) in enumerate(zip(original, content)):
    if a != b:
        changes += 1
print(f"Characters changed: {changes}")
print(f"Original length: {len(original)}, New length: {len(content)}")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ Dashboard v5.0 CSS applied successfully.")
