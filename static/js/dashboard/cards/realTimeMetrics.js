/**
 * Component: Real-Time Metrics
 * Renders Interface Utilization and Network I/O Trend charts.
 * Uses backend data only (no simulated fallback).
 */

let interfaceChart = null;
let ioChart = null;

export function renderRealTimeMetrics(interfaceData, ioTrendData) {
    // 1. Interface Utilization (Horizontal Bar)
    renderInterfaceChart(interfaceData);

    // 2. Network I/O Trend (Line)
    renderIoChart(ioTrendData);
}

function renderInterfaceChart(data) {
    const ctx = document.getElementById('chart-interface-util');
    if (!ctx) return;
    const emptyEl = document.getElementById('chart-interface-util-empty');

    const hasData = Array.isArray(data) && data.length > 0;
    if (!hasData) {
        if (interfaceChart) {
            interfaceChart.destroy();
            interfaceChart = null;
        }
        ctx.style.display = 'none';
        if (emptyEl) emptyEl.style.display = 'block';
        return;
    }

    ctx.style.display = 'block';
    if (emptyEl) emptyEl.style.display = 'none';

    const labels = data.map(item => `${item.device}: ${item.name}`);
    const values = data.map(item => item.utilization_pct);

    if (interfaceChart) {
        interfaceChart.data.labels = labels;
        interfaceChart.data.datasets[0].data = values;
        interfaceChart.update('none');
        return;
    }

    // @ts-ignore
    interfaceChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Utilization %',
                data: values,
                backgroundColor: [
                    'rgba(231, 76, 60, 0.7)', // Red for high
                    'rgba(241, 196, 15, 0.7)', // Yellow
                    'rgba(46, 204, 113, 0.7)', // Green
                    'rgba(52, 152, 219, 0.7)', // Blue
                    'rgba(155, 89, 182, 0.7)'  // Purple
                ],
                borderColor: [
                    '#e74c3c', '#f1c40f', '#2ecc71', '#3498db', '#9b59b6'
                ],
                borderWidth: 1
            }]
        },
        options: {
            indexAxis: 'y', // Horizontal bar
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return context.parsed.x + '% Utilization';
                        }
                    }
                }
            },
            scales: {
                x: {
                    beginAtZero: true,
                    max: 100,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#8899a6' }
                },
                y: {
                    grid: { display: false },
                    ticks: { color: '#8899a6' }
                }
            }
        }
    });
}

function renderIoChart(data) {
    const ctx = document.getElementById('chart-network-io');
    if (!ctx) return;
    const emptyEl = document.getElementById('chart-network-io-empty');

    const hasData = data && data.labels && data.labels.length > 0;
    if (!hasData) {
        if (ioChart) {
            ioChart.destroy();
            ioChart = null;
        }
        ctx.style.display = 'none';
        if (emptyEl) emptyEl.style.display = 'block';
        return;
    }

    ctx.style.display = 'block';
    if (emptyEl) emptyEl.style.display = 'none';

    const labels = data.labels.map(l => new Date(l).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    const inData = data.inbound;
    const outData = data.outbound;

    if (ioChart) {
        ioChart.data.labels = labels;
        ioChart.data.datasets[0].data = inData;
        ioChart.data.datasets[1].data = outData;
        ioChart.update('none');
        return;
    }

    // @ts-ignore
    ioChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Inbound (Mbps)',
                    data: inData,
                    borderColor: '#2ecc71',
                    backgroundColor: 'rgba(46, 204, 113, 0.1)',
                    fill: true,
                    tension: 0.4
                },
                {
                    label: 'Outbound (Mbps)',
                    data: outData,
                    borderColor: '#3498db',
                    backgroundColor: 'rgba(52, 152, 219, 0.1)',
                    fill: true,
                    tension: 0.4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    position: 'top',
                    labels: { color: '#8899a6' }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#8899a6' }
                },
                y: {
                    beginAtZero: true,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#8899a6' }
                }
            }
        }
    });
}
