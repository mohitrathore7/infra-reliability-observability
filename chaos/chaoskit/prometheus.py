"""Minimal Prometheus HTTP API client."""
from __future__ import annotations

import requests


class Prometheus:
    def __init__(self, base_url: str, timeout_s: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def scalar(self, query: str) -> float | None:
        """Return the single value of an instant query, or None if empty/failed."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/v1/query",
                params={"query": query},
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            result = resp.json()["data"]["result"]
        except (requests.RequestException, KeyError, ValueError):
            return None
        if not result:
            return None
        return float(result[0]["value"][1])

    def firing_alerts(self) -> set[str]:
        try:
            resp = requests.get(f"{self.base_url}/api/v1/alerts", timeout=self.timeout_s)
            resp.raise_for_status()
            alerts = resp.json()["data"]["alerts"]
        except (requests.RequestException, KeyError, ValueError):
            return set()
        return {a["labels"]["alertname"] for a in alerts if a.get("state") == "firing"}
