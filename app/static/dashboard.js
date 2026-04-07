/* ============================================
   CCServer Dashboard — State & Data Fetching
   ============================================ */

const API_BASE = window.location.origin;

/* ---------- Theme ---------- */

function initTheme() {
    const saved = localStorage.getItem('ccserver-theme');
    if (saved === 'light') {
        document.documentElement.classList.remove('dark');
    } else {
        document.documentElement.classList.add('dark');
    }
}

function toggleTheme() {
    const isDark = document.documentElement.classList.toggle('dark');
    localStorage.setItem('ccserver-theme', isDark ? 'dark' : 'light');
}

initTheme();

/* ---------- Formatting helpers ---------- */

function formatCost(usd) {
    if (usd == null) return '$0.00';
    if (usd < 0.01) return `$${usd.toFixed(4)}`;
    return `$${usd.toFixed(2)}`;
}

function formatNumber(n) {
    if (n == null) return '0';
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
    return n.toLocaleString();
}

function formatTps(tps) {
    if (tps == null || tps === 0) return '0';
    return tps.toFixed(1);
}

function formatDuration(ms) {
    if (ms == null) return '-';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
}

function formatTimestamp(iso) {
    if (!iso) return '-';
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
        month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
}

function shortModel(model) {
    if (!model) return '-';
    return model.replace('claude-', '').replace('-20251001', '');
}

/* ---------- Data fetching ---------- */

async function fetchStats(params = {}) {
    const query = new URLSearchParams(params).toString();
    const url = `${API_BASE}/v1/stats${query ? '?' + query : ''}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`Stats fetch failed: ${resp.status}`);
    return resp.json();
}

async function fetchLogs(params = {}) {
    const query = new URLSearchParams(params).toString();
    const url = `${API_BASE}/v1/logs${query ? '?' + query : ''}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`Logs fetch failed: ${resp.status}`);
    return resp.json();
}

async function fetchTimeseries(params = {}) {
    const query = new URLSearchParams(params).toString();
    const url = `${API_BASE}/v1/stats/timeseries${query ? '?' + query : ''}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`Timeseries fetch failed: ${resp.status}`);
    return resp.json();
}

/* ---------- Alpine.js Dashboard Component ---------- */

function dashboardApp() {
    return {
        // State
        stats: null,
        logs: null,
        timeseries: null,
        loading: true,
        error: null,
        activeTab: 'overview',

        // Filters
        modelFilter: '',
        originFilter: '',
        streamFilter: '',
        bucketFilter: 'hour',
        timeRange: '24h',

        // Time range presets
        timeRangeOptions: [
            { label: '1h', hours: 1, bucket: '5min' },
            { label: '6h', hours: 6, bucket: '5min' },
            { label: '24h', hours: 24, bucket: 'hour' },
            { label: '7d', hours: 168, bucket: 'day' },
            { label: '30d', hours: 720, bucket: 'day' },
            { label: 'All', hours: 0, bucket: 'day' },
        ],

        // Pagination
        page: 1,
        perPage: 20,

        // Auto-refresh
        autoRefresh: false,
        refreshInterval: null,

        // Charts
        tpsChart: null,
        costChart: null,

        async init() {
            await this.refresh();
        },

        async setTimeRange(label) {
            this.timeRange = label;
            const preset = this.timeRangeOptions.find(o => o.label === label);
            if (preset) {
                this.bucketFilter = preset.bucket;
            }
            this.page = 1;
            await this.refresh();
        },

        _getSinceParam() {
            const preset = this.timeRangeOptions.find(o => o.label === this.timeRange);
            if (!preset || preset.hours === 0) return null;
            const since = new Date(Date.now() - preset.hours * 60 * 60 * 1000);
            return since.toISOString();
        },

        async refresh() {
            this.loading = true;
            this.error = null;
            try {
                const filterParams = {};
                if (this.modelFilter) filterParams.model = this.modelFilter;
                if (this.originFilter) filterParams.origin = this.originFilter;
                const since = this._getSinceParam();
                if (since) filterParams.since = since;

                const [stats, logs, timeseries] = await Promise.all([
                    fetchStats(filterParams),
                    fetchLogs({
                        ...filterParams,
                        page: this.page,
                        per_page: this.perPage,
                        ...(this.streamFilter !== '' ? { is_stream: this.streamFilter } : {}),
                    }),
                    fetchTimeseries({
                        ...filterParams,
                        bucket: this.bucketFilter,
                    }),
                ]);

                this.stats = stats;
                this.logs = logs;
                this.timeseries = timeseries;

                // Use setTimeout to ensure canvas elements are fully rendered
                setTimeout(() => this.renderCharts(), 50);
            } catch (e) {
                this.error = e.message;
                console.error('Dashboard refresh error:', e);
            } finally {
                this.loading = false;
            }
        },

        async changePage(newPage) {
            this.page = newPage;
            await this.refresh();
        },

        async applyFilters() {
            this.page = 1;
            await this.refresh();
        },

        async clearFilters() {
            this.modelFilter = '';
            this.originFilter = '';
            this.streamFilter = '';
            this.page = 1;
            await this.refresh();
        },

        toggleAutoRefresh() {
            this.autoRefresh = !this.autoRefresh;
            if (this.autoRefresh) {
                this.refreshInterval = setInterval(() => this.refresh(), 30000);
            } else {
                clearInterval(this.refreshInterval);
                this.refreshInterval = null;
            }
        },

        /* ---------- Chart rendering ---------- */

        renderCharts() {
            if (!this.timeseries || !this.timeseries.data.length) return;

            const data = this.timeseries.data;
            const labels = data.map(d => d.period);
            const isDark = document.documentElement.classList.contains('dark');
            const gridColor = isDark ? 'rgba(148, 163, 184, 0.1)' : 'rgba(15, 23, 42, 0.06)';
            const textColor = isDark ? '#94a3b8' : '#475569';

            const chartDefaults = {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: textColor, font: { size: 12 } } },
                },
                scales: {
                    x: { ticks: { color: textColor, font: { size: 11 } }, grid: { color: gridColor } },
                    y: { ticks: { color: textColor, font: { size: 11 } }, grid: { color: gridColor } },
                },
            };

            // TPS chart
            const tpsCanvas = document.getElementById('tps-chart');
            if (tpsCanvas) {
                if (this.tpsChart) this.tpsChart.destroy();
                this.tpsChart = new Chart(tpsCanvas, {
                    type: 'line',
                    data: {
                        labels,
                        datasets: [{
                            label: 'Avg Tokens/sec',
                            data: data.map(d => d.avg_tokens_per_second),
                            borderColor: '#3b82f6',
                            backgroundColor: 'rgba(59, 130, 246, 0.1)',
                            fill: true,
                            tension: 0.3,
                            pointRadius: 3,
                        }],
                    },
                    options: chartDefaults,
                });
            }

            // Cost/Requests chart
            const costCanvas = document.getElementById('cost-chart');
            if (costCanvas) {
                if (this.costChart) this.costChart.destroy();
                this.costChart = new Chart(costCanvas, {
                    type: 'bar',
                    data: {
                        labels,
                        datasets: [
                            {
                                label: 'Requests',
                                data: data.map(d => d.requests),
                                backgroundColor: 'rgba(59, 130, 246, 0.6)',
                                yAxisID: 'y',
                            },
                            {
                                label: 'Cost ($)',
                                data: data.map(d => d.cost_usd),
                                backgroundColor: 'rgba(16, 185, 129, 0.6)',
                                yAxisID: 'y1',
                            },
                        ],
                    },
                    options: {
                        ...chartDefaults,
                        scales: {
                            ...chartDefaults.scales,
                            y: { ...chartDefaults.scales.y, position: 'left' },
                            y1: {
                                ...chartDefaults.scales.y,
                                position: 'right',
                                grid: { drawOnChartArea: false },
                            },
                        },
                    },
                });
            }
        },

        /* ---------- Helpers for template ---------- */

        get totalPages() {
            return this.logs?.pagination?.total_pages || 0;
        },

        get pageNumbers() {
            const total = this.totalPages;
            if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
            const pages = [1];
            const start = Math.max(2, this.page - 1);
            const end = Math.min(total - 1, this.page + 1);
            if (start > 2) pages.push('...');
            for (let i = start; i <= end; i++) pages.push(i);
            if (end < total - 1) pages.push('...');
            pages.push(total);
            return pages;
        },

        get modelList() {
            if (!this.stats?.by_model) return [];
            return Object.keys(this.stats.by_model).sort();
        },
    };
}
