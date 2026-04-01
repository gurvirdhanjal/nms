/* devicePerformance.js — Performance charts for device details page
 * Called via initDevicePerformance(deviceId) after DOM is ready.
 * Expects: Chart.js loaded globally, #perfSection and its children in DOM.
 */
(function (global) {
    'use strict';

    var _charts = {};
    var _loading = false;
    var _activeRange = '1h';

    var CHART_CONFIGS = [
        { key: 'cpu',  canvasId: 'perfCpuChart',  shimId: 'perfCpuShimmer',
          color: '#3498db', label: 'CPU %',
          currentId: 'perfCpuCurrent', peakId: 'perfCpuPeak', avgId: 'perfCpuAvg',
          dataKey: 'cpu', unit: '%' },
        { key: 'mem',  canvasId: 'perfMemChart',  shimId: 'perfMemShimmer',
          color: '#2ecc71', label: 'Memory %',
          currentId: 'perfMemCurrent', peakId: 'perfMemPeak', avgId: 'perfMemAvg',
          dataKey: 'memory', unit: '%' },
        { key: 'disk', canvasId: 'perfDiskChart', shimId: 'perfDiskShimmer',
          color: '#e74c3c', label: 'Disk %',
          currentId: 'perfDiskCurrent', peakId: 'perfDiskPeak', avgId: 'perfDiskAvg',
          dataKey: 'disk', unit: '%' },
    ];

    function initDevicePerformance(deviceId) {
        _bindRangeSelector(deviceId);
        _loadMetrics(deviceId, _activeRange);
    }

    function _bindRangeSelector(deviceId) {
        var btns = document.querySelectorAll('#perfRangeSelector .perf-range-btn');
        btns.forEach(function (btn) {
            btn.addEventListener('click', function () {
                if (_loading) return;
                btns.forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                _activeRange = btn.dataset.range;
                _loadMetrics(deviceId, _activeRange);
            });
        });
    }

    function _loadMetrics(deviceId, range) {
        if (_loading) return;
        _loading = true;
        _showShimmer(true);
        _hideStates();

        fetch('/api/server/' + deviceId + '/metrics?range=' + range)
            .then(function (resp) {
                if (!resp.ok) {
                    _showError();
                    return null;
                }
                return resp.json();
            })
            .then(function (data) {
                if (!data) return;
                var labels = data.labels || [];
                var cpuData = data.cpu || [];
                if (!labels.length || !cpuData.length) {
                    _showEmpty();
                    return;
                }
                _renderCharts(data, labels);
                _renderNetworkSummary(data);
            })
            .catch(function () {
                _showError();
            })
            .finally(function () {
                _loading = false;
                _showShimmer(false);
            });
    }

    function _renderCharts(data, labels) {
        var istLabels = labels.map(function (ts) {
            var d = new Date(ts);
            if (isNaN(d)) return ts;
            return d.toLocaleString('en-IN', {
                timeZone: 'Asia/Kolkata',
                hour: '2-digit', minute: '2-digit', hour12: false
            });
        });

        CHART_CONFIGS.forEach(function (cfg) {
            var vals = (data[cfg.dataKey] || []).map(function (v) {
                return v != null ? Number(v) : null;
            });
            if (!vals.length) return;

            // Destroy existing chart — memory leak prevention
            if (_charts[cfg.key]) {
                _charts[cfg.key].destroy();
                delete _charts[cfg.key];
            }

            // Update stat trio above chart
            var validVals = vals.filter(function (v) { return v != null && !isNaN(v); });
            if (validVals.length) {
                var current = validVals[validVals.length - 1];
                var peak    = Math.max.apply(null, validVals);
                var avg     = validVals.reduce(function (a, b) { return a + b; }, 0) / validVals.length;
                _setText(cfg.currentId, current.toFixed(1) + cfg.unit);
                _setText(cfg.peakId,    peak.toFixed(1) + cfg.unit);
                _setText(cfg.avgId,     avg.toFixed(1) + cfg.unit);
            }

            var canvas = document.getElementById(cfg.canvasId);
            if (!canvas) return;

            _charts[cfg.key] = new Chart(canvas, {
                type: 'line',
                data: {
                    labels: istLabels,
                    datasets: [{
                        label: cfg.label,
                        data: vals,
                        borderColor: cfg.color,
                        backgroundColor: cfg.color + '22',
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: true,
                        tension: 0.35
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: {
                            ticks: { maxTicksLimit: 6, color: '#6b7280', font: { size: 10 } },
                            grid: { display: false },
                            border: { display: false }
                        },
                        y: {
                            min: 0,
                            max: 100,
                            ticks: { color: '#6b7280', font: { size: 10 } },
                            grid: { color: 'rgba(255,255,255,0.05)' },
                            border: { display: false }
                        }
                    }
                }
            });
        });
    }

    function _renderNetworkSummary(data) {
        // Network uses dual series (in/out in bytes → MB/s)
        var netIn  = (data.net_in  || []).filter(function (v) { return v != null; });
        var netOut = (data.net_out || []).filter(function (v) { return v != null; });
        var labels = data.labels || [];
        var istLabels = labels.map(function (ts) {
            var d = new Date(ts);
            if (isNaN(d)) return ts;
            return d.toLocaleString('en-IN', {
                timeZone: 'Asia/Kolkata',
                hour: '2-digit', minute: '2-digit', hour12: false
            });
        });

        // Summary stats
        var avgIn  = netIn.length  ? (netIn.reduce(function (a, b) { return a + b; }, 0)  / netIn.length  / 1048576) : 0;
        var avgOut = netOut.length ? (netOut.reduce(function (a, b) { return a + b; }, 0) / netOut.length / 1048576) : 0;
        _setText('perfNetIn',  avgIn.toFixed(2)  + ' MB/s avg');
        _setText('perfNetOut', avgOut.toFixed(2) + ' MB/s avg');

        // Destroy existing
        if (_charts.net) {
            _charts.net.destroy();
            delete _charts.net;
        }

        var canvas = document.getElementById('perfNetChart');
        if (!canvas || (!netIn.length && !netOut.length)) return;

        var inMB  = netIn.map(function  (v) { return v / 1048576; });
        var outMB = netOut.map(function (v) { return v / 1048576; });

        _charts.net = new Chart(canvas, {
            type: 'line',
            data: {
                labels: istLabels,
                datasets: [
                    {
                        label: 'In (MB/s)',
                        data: inMB,
                        borderColor: '#9b59b6',
                        backgroundColor: 'rgba(155,89,182,0.13)',
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: true,
                        tension: 0.35
                    },
                    {
                        label: 'Out (MB/s)',
                        data: outMB,
                        borderColor: '#1abc9c',
                        backgroundColor: 'rgba(26,188,156,0.13)',
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: true,
                        tension: 0.35
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: {
                    legend: {
                        display: true,
                        position: 'bottom',
                        labels: { color: '#9ca3af', font: { size: 10 }, boxWidth: 10 }
                    }
                },
                scales: {
                    x: {
                        ticks: { maxTicksLimit: 6, color: '#6b7280', font: { size: 10 } },
                        grid: { display: false },
                        border: { display: false }
                    },
                    y: {
                        min: 0,
                        ticks: { color: '#6b7280', font: { size: 10 } },
                        grid: { color: 'rgba(255,255,255,0.05)' },
                        border: { display: false }
                    }
                }
            }
        });
    }

    function _showShimmer(on) {
        var shimmers = ['perfCpuShimmer', 'perfMemShimmer', 'perfDiskShimmer', 'perfNetShimmer'];
        var canvases = ['perfCpuChart',   'perfMemChart',   'perfDiskChart',   'perfNetChart'];
        shimmers.forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.style.display = on ? 'block' : 'none';
        });
        canvases.forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.style.visibility = on ? 'hidden' : 'visible';
        });
    }

    function _showError() {
        var el = document.getElementById('perfError');
        if (el) el.classList.remove('hidden');
        var grid = document.getElementById('perfChartsGrid');
        if (grid) grid.style.display = 'none';
    }

    function _showEmpty() {
        var el = document.getElementById('perfEmpty');
        if (el) el.classList.remove('hidden');
        var grid = document.getElementById('perfChartsGrid');
        if (grid) grid.style.display = 'none';
    }

    function _hideStates() {
        ['perfError', 'perfEmpty'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.classList.add('hidden');
        });
        var grid = document.getElementById('perfChartsGrid');
        if (grid) grid.style.display = '';
    }

    function _setText(id, text) {
        var el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    // Expose globally for inline script call
    global.initDevicePerformance = initDevicePerformance;

})(window);
