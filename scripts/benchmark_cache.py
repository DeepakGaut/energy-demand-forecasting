"""Measure the latency impact of Redis caching on the EcoCompute API.

For each cached endpoint it:
  1. deletes the relevant cache key(s) to force a cold (cache-miss) request,
  2. times that cold request (hits PostgreSQL),
  3. times a batch of warm (cache-hit) requests (served from Redis),
  4. reports mean latencies and the speed-up.

Usage (with the stack running):
    python scripts/benchmark_cache.py
    python scripts/benchmark_cache.py --region WR --iterations 50

Env overrides: API_URL (default http://localhost:8000),
REDIS_URL (default redis://localhost:6379/0).
"""

from __future__ import annotations

import argparse
import os
import statistics
import time

import redis
import requests

API_URL = os.getenv("API_URL", "http://localhost:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _delete_keys(client: redis.Redis, pattern: str) -> None:
    keys = list(client.scan_iter(match=pattern))
    if keys:
        client.delete(*keys)


def _time_request(method: str, url: str, json_body: dict | None) -> float:
    start = time.perf_counter()
    response = requests.request(method, url, json=json_body, timeout=30)
    response.raise_for_status()
    return (time.perf_counter() - start) * 1000.0  # milliseconds


def benchmark_endpoint(
    label: str,
    method: str,
    url: str,
    json_body: dict | None,
    client: redis.Redis,
    key_pattern: str,
    iterations: int,
) -> None:
    # Cold: clear the cache first so this request must hit the database.
    _delete_keys(client, key_pattern)
    cold_ms = _time_request(method, url, json_body)

    # Warm: the value is now cached, so every following request is a cache hit.
    warm_samples = [_time_request(method, url, json_body) for _ in range(iterations)]
    warm_mean = statistics.mean(warm_samples)
    warm_median = statistics.median(warm_samples)

    speedup = cold_ms / warm_mean if warm_mean else float("inf")
    print(f"\n{label}")
    print(f"  cold  (cache miss, 1 call) : {cold_ms:8.2f} ms")
    print(f"  warm  (cache hit,  n={iterations:<3d}) : {warm_mean:8.2f} ms mean"
          f"  |  {warm_median:8.2f} ms median")
    print(f"  speed-up (cold / warm mean): {speedup:8.2f}x")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="WR")
    parser.add_argument("--iterations", type=int, default=50)
    args = parser.parse_args()

    region = args.region.upper()
    client = redis.Redis.from_url(REDIS_URL)

    calc_body = {
        "region": region,
        "model_name": "Intel Xeon Platinum 8480+",
        "n_cores": 28,
        "runtime_hours": 5,
        "usage_factor": 0.8,
        "memory_gb": 64,
    }

    print(f"Benchmarking against {API_URL} (region={region}, iterations={args.iterations})")

    benchmark_endpoint(
        label=f"GET /ci/{region}",
        method="GET",
        url=f"{API_URL}/ci/{region}",
        json_body=None,
        client=client,
        key_pattern=f"ci:{region}:*",
        iterations=args.iterations,
    )

    benchmark_endpoint(
        label="POST /calculate",
        method="POST",
        url=f"{API_URL}/calculate",
        json_body=calc_body,
        client=client,
        key_pattern=f"calc:{region}:*",
        iterations=args.iterations,
    )


if __name__ == "__main__":
    main()
