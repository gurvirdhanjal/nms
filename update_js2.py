mport re

js_path = r'd:\device_monitoring_tactical\static\js\dashboard\servers\serverDashboard.js'

with open(js_path, 'r', encoding='utf-8') as f:
    js = f.read()

# Change KPI breakdown logic to display Degraded instead of Warning
# 1. buildHealthRows
def repl_health_rows(m):
    return """function buildHealthRows(servers) {
    const counts = { Healthy: 0, Degraded: 0, Critical: 0, Offline: 0 };
    servers.forEach(s => { 
        let h = s.health === 'Warning' ? 'Degraded' : s.health;
        if (counts[h] !== undefined) counts[h]++; 
    });
    const colors = { Healthy: 'success', Degraded: 'warning', Critical: 'danger', Offline: 'secondary' };
    return `<table class="table table-sm table-dark mb-0">${
        Object.entries(counts).map(([status, n]) =>
            `<tr><td>${status}</td><td><span class="badge bg-${colors[status]}">${n}</span></td></tr>`
        ).join('')}</table>`;
}"""
js = re.sub(r'function buildHealthRows\(servers\) \{[\s\S]*?\}<\/table>\`;\n\}', repl_health_rows, js)

# 2. openKpiBreakdown Labels
js = js.replace("warning:  { label: 'Warning Servers',                 html: buildStatusRows(servers, 'Warning') }", "warning:  { label: 'Degraded Servers',                 html: buildStatusRows(servers, 'Warning') }")

with open(js_path, 'w', encoding='utf-8') as f:
    f.write(js)
print("serverDashboard.js updated")
