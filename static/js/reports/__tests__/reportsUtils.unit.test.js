import { describe, it, expect } from 'vitest';
import { statusBadge } from '../reportsUtils.js';

describe('statusBadge', () => {
    it('returns Unknown for null input', () => {
        expect(statusBadge(null)).toContain('Unknown');
        expect(statusBadge(undefined)).toContain('Unknown');
    });

    it('returns Critical when anomaly_flag is true', () => {
        const badge = statusBadge({ anomaly_flag: true, uptime_pct: 99 });
        expect(badge).toContain('bg-danger');
        expect(badge).toContain('Critical');
    });

    it('anomaly_flag takes precedence over low uptime', () => {
        const badge = statusBadge({ anomaly_flag: true, uptime_pct: 50 });
        expect(badge).toContain('bg-danger');  // Critical, not Warning
        expect(badge).not.toContain('bg-warning');
    });

    it('returns Warning when uptime_pct < 90', () => {
        const badge = statusBadge({ anomaly_flag: false, uptime_pct: 89.9 });
        expect(badge).toContain('bg-warning');
        expect(badge).toContain('Warning');
    });

    it('returns Warning at exactly 0% uptime', () => {
        expect(statusBadge({ anomaly_flag: false, uptime_pct: 0 })).toContain('bg-warning');
    });

    it('returns Healthy for good metrics', () => {
        const badge = statusBadge({ anomaly_flag: false, uptime_pct: 99.5 });
        expect(badge).toContain('bg-success');
        expect(badge).toContain('Healthy');
    });

    it('returns Healthy when uptime_pct is exactly 90', () => {
        // 90% is the boundary — NOT below threshold
        expect(statusBadge({ anomaly_flag: false, uptime_pct: 90 })).toContain('bg-success');
    });

    it('returns Healthy when uptime_pct is null (no data yet)', () => {
        // null uptime should not trigger Warning — device may just be new
        expect(statusBadge({ anomaly_flag: false, uptime_pct: null })).toContain('bg-success');
    });

    it('returns Healthy when anomaly_flag is undefined (enterprise rows)', () => {
        // enterprise server rows always have anomaly_flag but guard for safety
        expect(statusBadge({ uptime_pct: 99 })).toContain('bg-success');
    });
});
