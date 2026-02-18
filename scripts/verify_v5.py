"""Verify all v5.0 compliance checks in dashboard.html"""
with open(r'd:\device_monitoring_tactical\templates\dashboard.html', 'r', encoding='utf-8') as f:
    content = f.read()

checks = [
    ('v5.0 token set (--s-critical)', '--s-critical:' in content),
    ('--a-micro token', '--a-micro:' in content),
    ('--t-mono token', '--t-mono:' in content),
    ('--e-bg-base token', '--e-bg-base:' in content),
    ('border-radius 6px present', 'border-radius: 6px' in content),
    ('NO border-radius 10px', 'border-radius: 10px' not in content),
    ('badge 3px present', 'border-radius: 3px;' in content),
    ('NO pill badges (999px)', 'border-radius: 999px' not in content),
    ('NO green active tab color', 'rgba(32, 201, 151, 0.7)' not in content),
    ('IBM Plex Mono in values', "IBM Plex Mono" in content),
    ('tabular-nums present', 'font-variant-numeric: tabular-nums' in content),
    ('muted avail excellent', 'rgba(32, 201, 151, 0.18)' in content),
    ('NO vivid avail gradients', 'rgba(29, 209, 161, 0.9)' not in content),
    ('KPI accent ::after', 'tactical-stat-card::after' in content),
    ('NO scale(1.1)', 'scale(1.1)' not in content),
    ('NO backdrop-filter blur', 'backdrop-filter: blur(6px)' not in content),
    ('NO breakdownFocusPulse', '@keyframes breakdownFocusPulse' not in content),
    ('global-error v5 style', '--s-critical-bg' in content),
    ('v5.0 comment markers', 'v5.0' in content),
]

print('── v5.0 Compliance Checks ──')
for name, result in checks:
    status = '✅' if result else '❌'
    print(f'{status} {name}')

fails = sum(1 for _, v in checks if not v)
print(f'\nTotal: {len(checks)} checks, {len(checks)-fails} passed, {fails} failed')
