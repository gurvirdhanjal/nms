#!/usr/bin/env python
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts'
ARTIFACTS.mkdir(exist_ok=True)

TARGETS_JSON = ROOT / 'quality_gate_targets.json'
PY_COV_JSON = ARTIFACTS / 'python_coverage.json'
JS_COV_JSON = ARTIFACTS / 'js-coverage' / 'coverage-summary.json'
SUMMARY_JSON = ARTIFACTS / 'quality_gate_summary.json'

PERF_COMPLIANCE_THRESHOLD = 95.0


def run(command: list[str], env: dict | None = None) -> int:
    print(f"[quality-gate] running: {' '.join(command)}")
    proc = subprocess.run(command, cwd=ROOT, env=env)
    return proc.returncode


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def normalize_path(value: str) -> str:
    return str(value).replace('/', '\\').lower()


def load_targets() -> dict:
    payload = read_json(TARGETS_JSON)
    if not payload:
        raise FileNotFoundError(f'missing quality gate config: {TARGETS_JSON}')
    return payload


def top_level_source_roots(paths: list[str]) -> list[str]:
    roots = []
    for value in paths:
        normalized = Path(value)
        parts = normalized.parts
        if not parts:
            continue
        root = parts[0]
        if root not in roots:
            roots.append(root)
    return roots or ['.']


def build_python_results(targets: dict) -> tuple[dict, list[dict], float]:
    payload = read_json(PY_COV_JSON)
    files = payload.get('files', {})
    failures = []
    details = {}
    min_percent = float(targets.get('min_percent', 95.0))

    for rel_path in targets.get('modules', []):
        match_key = normalize_path(rel_path)
        file_payload = None
        for candidate_key, candidate_value in files.items():
            if normalize_path(candidate_key) == match_key:
                file_payload = candidate_value
                break
        percent = float((file_payload or {}).get('summary', {}).get('percent_covered', 0.0))
        details[rel_path] = percent
        if percent < min_percent:
            failures.append({'path': rel_path, 'percent': percent, 'minimum': min_percent})

    total = float(payload.get('totals', {}).get('percent_covered', 0.0))
    return details, failures, total


def build_js_results(targets: dict) -> tuple[dict, list[dict], float]:
    payload = read_json(JS_COV_JSON)
    failures = []
    details = {}
    total = float(payload.get('total', {}).get('lines', {}).get('pct', 0.0))

    thresholds = {
        'lines': float(targets.get('min_lines', 95.0)),
        'functions': float(targets.get('min_functions', 95.0)),
        'statements': float(targets.get('min_statements', 95.0)),
        'branches': float(targets.get('min_branches', 50.0)),
    }

    for rel_path in targets.get('files', []):
        expected = normalize_path(str(ROOT / rel_path))
        file_payload = None
        for candidate_key, candidate_value in payload.items():
            if candidate_key == 'total':
                continue
            if normalize_path(candidate_key) == expected:
                file_payload = candidate_value
                break

        metrics = {
            name: float((file_payload or {}).get(name, {}).get('pct', 0.0))
            for name in ('lines', 'functions', 'statements', 'branches')
        }
        details[rel_path] = metrics
        for metric_name, minimum in thresholds.items():
            if metrics[metric_name] < minimum:
                failures.append(
                    {
                        'path': rel_path,
                        'metric': metric_name,
                        'percent': metrics[metric_name],
                        'minimum': minimum,
                    }
                )

    return details, failures, total


def npm_command() -> str:
    return 'npm.cmd' if platform.system().lower().startswith('win') else 'npm'


def main() -> int:
    targets = load_targets()
    python_targets = targets.get('python', {})
    js_targets = targets.get('javascript', {})

    results = {
        'python': {},
        'javascript': {},
        'performance': {},
        'checks': {},
    }

    py_erase_before = [sys.executable, '-m', 'coverage', 'erase']
    source_roots = top_level_source_roots(list(python_targets.get('modules', [])))
    py_cmd = [
        sys.executable,
        '-m',
        'coverage',
        'run',
        f"--source={','.join(source_roots)}",
        '-m',
        'pytest',
        '-m',
        'unit or integration',
    ]
    py_json_cmd = [sys.executable, '-m', 'coverage', 'json', '-o', PY_COV_JSON.as_posix()]
    py_erase_after = [sys.executable, '-m', 'coverage', 'erase']

    _ = run(py_erase_before)
    py_unit_integration_code = run(py_cmd)
    py_json_code = run(py_json_cmd) if py_unit_integration_code == 0 else 1
    _ = run(py_erase_after)
    results['python']['unit_integration_exit_code'] = py_unit_integration_code
    results['python']['coverage_json_exit_code'] = py_json_code

    py_perf_cmd = [
        sys.executable,
        '-m',
        'pytest',
        '-m',
        'performance',
    ]
    py_perf_code = run(py_perf_cmd)
    results['performance']['python_perf_exit_code'] = py_perf_code

    npm_bin = npm_command()
    js_env = dict(os.environ)
    js_env['QUALITY_GATE_JS_INCLUDE'] = json.dumps(list(js_targets.get('files', [])))
    js_unit_code = run([npm_bin, 'run', 'test:js'], env=js_env)
    js_cov_code = run([npm_bin, 'run', 'test:js:coverage'], env=js_env)
    js_perf_code = run([npm_bin, 'run', 'test:js:perf'])
    results['javascript']['unit_exit_code'] = js_unit_code
    results['javascript']['coverage_exit_code'] = js_cov_code
    results['performance']['js_perf_exit_code'] = js_perf_code

    py_details, py_failures, py_cov = build_python_results(python_targets)
    js_details, js_failures, js_cov = build_js_results(js_targets)

    results['python']['coverage_percent'] = py_cov
    results['python']['per_file'] = py_details
    results['python']['failures'] = py_failures
    results['javascript']['coverage_percent'] = js_cov
    results['javascript']['per_file'] = js_details
    results['javascript']['failures'] = js_failures

    python_gate = not py_failures and py_unit_integration_code == 0 and py_json_code == 0
    js_gate = not js_failures and js_cov_code == 0 and js_unit_code == 0
    perf_gate = py_perf_code == 0 and js_perf_code == 0

    results['checks'] = {
        'python_coverage_gate': python_gate,
        'js_coverage_gate': js_gate,
        'unit_integration_pass': py_unit_integration_code == 0 and js_unit_code == 0,
        'performance_sla_gate': perf_gate,
        'thresholds': {
            'python_coverage': float(python_targets.get('min_percent', 95.0)),
            'js_lines': float(js_targets.get('min_lines', 95.0)),
            'js_functions': float(js_targets.get('min_functions', 95.0)),
            'js_statements': float(js_targets.get('min_statements', 95.0)),
            'js_branches': float(js_targets.get('min_branches', 50.0)),
            'performance_sla': PERF_COMPLIANCE_THRESHOLD,
        },
    }

    SUMMARY_JSON.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(f"[quality-gate] summary written to {SUMMARY_JSON}")

    if all([python_gate, js_gate, perf_gate]):
        return 0
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
