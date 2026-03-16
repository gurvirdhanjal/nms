import argparse
import concurrent.futures
import statistics
import threading
import time
from typing import Iterable

import requests


LOGIN_PATH = "/login"
DEFAULT_ENDPOINTS = [
    "/api/server/health",
    "/api/tracking/live-summary",
]


def _normalize_base_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("Base URL is required")
    if "://" not in value:
        value = f"http://{value}"
    return value.rstrip("/")


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = max(0.0, min(1.0, float(pct))) * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


def _login(base_url: str, username: str, password: str, timeout: float) -> requests.Session:
    session = requests.Session()
    response = session.post(
        f"{base_url}{LOGIN_PATH}",
        data={"username": username, "password": password},
        timeout=timeout,
        allow_redirects=True,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Login failed with HTTP {response.status_code}")
    if "invalid username or password" in response.text.lower():
        raise RuntimeError("Login failed: invalid username or password")
    return session


def _clone_session(base_session: requests.Session) -> requests.Session:
    session = requests.Session()
    session.cookies.update(base_session.cookies)
    session.headers.update(base_session.headers)
    return session


def _run_requests(
    *,
    base_url: str,
    endpoint: str,
    cookie_session: requests.Session,
    requests_per_worker: int,
    timeout: float,
):
    session = _clone_session(cookie_session)
    url = f"{base_url}{endpoint}"
    latencies_ms = []
    status_counts = {}
    errors = 0

    for _ in range(max(1, int(requests_per_worker))):
        started = time.perf_counter()
        try:
            response = session.get(url, timeout=timeout, allow_redirects=True)
            latency_ms = (time.perf_counter() - started) * 1000.0
            latencies_ms.append(latency_ms)
            status_counts[response.status_code] = int(status_counts.get(response.status_code, 0)) + 1
        except requests.RequestException:
            latency_ms = (time.perf_counter() - started) * 1000.0
            latencies_ms.append(latency_ms)
            errors += 1

    return {
        "latencies_ms": latencies_ms,
        "status_counts": status_counts,
        "errors": errors,
    }


def _flatten(iterables: Iterable[list[float]]) -> list[float]:
    merged = []
    for values in iterables:
        merged.extend(values)
    return merged


def benchmark_endpoint(
    *,
    base_url: str,
    endpoint: str,
    cookie_session: requests.Session,
    concurrency: int,
    total_requests: int,
    timeout: float,
):
    worker_count = max(1, int(concurrency))
    per_worker = max(1, (int(total_requests) + worker_count - 1) // worker_count)
    started = time.perf_counter()
    lock = threading.Lock()
    results = []

    def _worker():
        result = _run_requests(
            base_url=base_url,
            endpoint=endpoint,
            cookie_session=cookie_session,
            requests_per_worker=per_worker,
            timeout=timeout,
        )
        with lock:
            results.append(result)

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(_worker) for _ in range(worker_count)]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    total_duration_s = max(0.001, time.perf_counter() - started)
    latencies_ms = _flatten([item["latencies_ms"] for item in results])[:total_requests]
    total_errors = sum(int(item["errors"] or 0) for item in results)
    status_counts = {}
    for item in results:
        for status_code, count in item["status_counts"].items():
            status_counts[status_code] = int(status_counts.get(status_code, 0)) + int(count or 0)

    completed_requests = len(latencies_ms)
    success_requests = sum(
        count for status_code, count in status_counts.items() if 200 <= int(status_code) < 400
    )
    return {
        "endpoint": endpoint,
        "completed_requests": completed_requests,
        "success_requests": success_requests,
        "error_requests": total_errors,
        "status_counts": status_counts,
        "avg_ms": statistics.mean(latencies_ms) if latencies_ms else 0.0,
        "p50_ms": _percentile(latencies_ms, 0.50),
        "p95_ms": _percentile(latencies_ms, 0.95),
        "max_ms": max(latencies_ms) if latencies_ms else 0.0,
        "rps": round(float(completed_requests) / total_duration_s, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Basic authenticated endpoint load test for the tactical app.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000", help="Base app URL.")
    parser.add_argument("--username", default="admin", help="Login username.")
    parser.add_argument("--password", required=True, help="Login password.")
    parser.add_argument(
        "--endpoint",
        action="append",
        dest="endpoints",
        help="Endpoint path to test. Repeat to test multiple. Defaults to server health and live summary.",
    )
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent workers per endpoint.")
    parser.add_argument("--requests", type=int, default=100, help="Total requests per endpoint.")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout in seconds.")
    args = parser.parse_args()

    base_url = _normalize_base_url(args.base_url)
    endpoints = args.endpoints or list(DEFAULT_ENDPOINTS)
    base_session = _login(base_url, args.username, args.password, args.timeout)

    print(f"Authenticated successfully against {base_url}")
    print(f"Concurrency={args.concurrency} TotalRequestsPerEndpoint={args.requests} Timeout={args.timeout}s")

    for endpoint in endpoints:
        normalized_endpoint = endpoint if str(endpoint).startswith("/") else f"/{endpoint}"
        print(f"\nTesting {normalized_endpoint}")
        result = benchmark_endpoint(
            base_url=base_url,
            endpoint=normalized_endpoint,
            cookie_session=base_session,
            concurrency=args.concurrency,
            total_requests=args.requests,
            timeout=args.timeout,
        )
        print(
            "  completed={completed_requests} success={success_requests} errors={error_requests} "
            "avg={avg_ms:.1f}ms p50={p50_ms:.1f}ms p95={p95_ms:.1f}ms max={max_ms:.1f}ms rps={rps}".format(**result)
        )
        print(f"  status_counts={result['status_counts']}")


if __name__ == "__main__":
    main()
