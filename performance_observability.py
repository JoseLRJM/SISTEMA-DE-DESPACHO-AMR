from __future__ import annotations

import math
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Optional


class PerformanceMetrics:
    """Small, process-local aggregates. No request bodies or credentials are stored."""

    def __init__(self, sample_limit: int = 100):
        self.sample_limit = max(10, min(int(sample_limit), 500))
        self._lock = threading.Lock()
        self._values: dict[str, dict[str, object]] = {}

    def record(self, category: str, duration_ms: float, *, label: str = "all", size_bytes: Optional[int] = None) -> None:
        key = f"{category}:{label}"
        value = max(0.0, float(duration_ms))
        with self._lock:
            item = self._values.get(key)
            if item is None:
                item = {
                    "category": category,
                    "label": label,
                    "count": 0,
                    "total_ms": 0.0,
                    "max_ms": 0.0,
                    "last_samples_ms": deque(maxlen=self.sample_limit),
                    "total_bytes": 0,
                    "max_bytes": 0,
                }
                self._values[key] = item
            item["count"] = int(item["count"]) + 1
            item["total_ms"] = float(item["total_ms"]) + value
            item["max_ms"] = max(float(item["max_ms"]), value)
            item["last_samples_ms"].append(value)
            if size_bytes is not None:
                safe_size = max(0, int(size_bytes))
                item["total_bytes"] = int(item["total_bytes"]) + safe_size
                item["max_bytes"] = max(int(item["max_bytes"]), safe_size)

    def snapshot(self) -> dict:
        with self._lock:
            copied = []
            for item in self._values.values():
                samples = sorted(float(v) for v in item["last_samples_ms"])
                p95_index = max(0, math.ceil(len(samples) * 0.95) - 1) if samples else 0
                count = int(item["count"])
                copied.append({
                    "category": item["category"],
                    "label": item["label"],
                    "count": count,
                    "avg_ms": round(float(item["total_ms"]) / count, 3) if count else 0.0,
                    "max_ms": round(float(item["max_ms"]), 3),
                    "p95_ms": round(samples[p95_index], 3) if samples else 0.0,
                    "sample_count": len(samples),
                    "sample_limit": self.sample_limit,
                    "avg_bytes": round(int(item["total_bytes"]) / count, 1) if count and item["total_bytes"] else 0.0,
                    "max_bytes": int(item["max_bytes"]),
                })
        return {"sample_limit": self.sample_limit, "series": sorted(copied, key=lambda row: (row["category"], row["label"]))}


metrics = PerformanceMetrics(sample_limit=100)


@contextmanager
def observe(category: str, *, label: str = "all", size_getter=None):
    started = time.perf_counter()
    try:
        yield
    finally:
        size = size_getter() if size_getter is not None else None
        metrics.record(category, (time.perf_counter() - started) * 1000.0, label=label, size_bytes=size)
