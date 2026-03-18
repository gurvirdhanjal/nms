/**
 * Chart Component: Inventory Stats
 */

let inventoryChartInstance = null;

export function renderInventoryChart(data) {
    const canvas = document.getElementById('chart-inventory');
    if (!canvas) return;

    if (!data || !data.by_type) return;

    const ctx = canvas.getContext('2d');
    const labels = Object.keys(data.by_type);
    const values = Object.values(data.by_type);

    if (inventoryChartInstance) {
        inventoryChartInstance.data.labels = labels;
        inventoryChartInstance.data.datasets[0].data = values;
        inventoryChartInstance.update('none');
        return;
    }

    // Destroy any stale instance on the canvas (e.g. after hot reload / stale module var)
    // @ts-ignore
    Chart.getChart(canvas)?.destroy();
    // @ts-ignore
    inventoryChartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: [
                    '#3498db', '#9b59b6', '#2ecc71', '#f1c40f', '#e74c3c', '#34495e'
                ],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: { position: 'right', labels: { color: '#8b949e' } }
            },
            cutout: '70%'
        }
    });
}
