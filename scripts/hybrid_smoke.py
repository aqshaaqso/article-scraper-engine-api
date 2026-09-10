"""Dependency-free smoke test for the hybrid middleware/worker stack."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request


def request(base_url: str, api_key: str, method: str, path: str, body=None):
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    call = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(call, timeout=10) as response:
        return json.load(response)


def wait_job(base_url: str, api_key: str, job_id: str, timeout: float = 20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = request(base_url, api_key, "GET", f"/v1/jobs/{job_id}")
        if job["status"] == "completed":
            return job
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} tidak selesai dalam {timeout} detik")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--include-search-failure", action="store_true")
    args = parser.parse_args()
    api_key = os.environ["SCRAPER_API_KEY"]

    urls = [f"https://127.0.0.1/concurrency?item={number}" for number in range(50)]
    accepted = request(args.base_url, api_key, "POST", "/v1/jobs", {"urls": urls})
    job = wait_job(args.base_url, api_key, accepted["job_id"])
    assert (job["total"], job["completed"], job["failed"]) == (50, 50, 50), job
    assert all(item["error_code"] == "unsafe_url" for item in job["items"]), job

    if args.include_search_failure:
        accepted = request(
            args.base_url,
            api_key,
            "POST",
            "/v1/search/jobs",
            {"query": "smoke test", "max_articles": 1, "max_pages": 1},
        )
        job = wait_job(args.base_url, api_key, accepted["job_id"])
        assert job["error_code"] == "fetch_failed", job

    print("hybrid smoke passed")


if __name__ == "__main__":
    main()
