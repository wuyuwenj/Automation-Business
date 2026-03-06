"""Task-level two-seller comparison memory for the buyer agent.

This module keeps a small persistent memory per task/query family so the buyer
can compare two similar sellers across repeated runs:

- first run: test seller A
- second run: test seller B
- later runs: use the better-scoring seller
- if both scores are weak: mark the task for re-browsing/discovery

Local persistence is file-based for development. If Supabase environment
variables are present, writes are also mirrored to a PostgREST table so the
memory survives container restarts in hosted environments.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_minimum_score() -> float:
    raw = os.getenv("BUYER_MIN_ACCEPTABLE_SCORE", "6").strip()
    try:
        return max(0.0, min(8.0, float(raw)))
    except ValueError:
        return 6.0


@dataclass
class ComparedSeller:
    """Stored score state for one seller in a two-seller comparison."""

    seller_url: str = ""
    seller_name: str = ""
    tested: bool = False
    quality_score: float = 0.0
    roi: float = 0.0
    attempts: int = 0
    last_purchase_id: str = ""
    last_reasoning: str = ""
    last_tested_at: str = ""


@dataclass
class TaskComparisonRecord:
    """Persistent comparison state for one normalized task."""

    task_key: str
    query_category: str
    query_preview: str
    seller_a: ComparedSeller = field(default_factory=ComparedSeller)
    seller_b: ComparedSeller = field(default_factory=ComparedSeller)
    preferred_seller_url: str = ""
    minimum_acceptable_score: float = field(default_factory=_default_minimum_score)
    needs_rebrowse: bool = False
    updated_at: str = field(default_factory=_now_iso)

    def __post_init__(self):
        if isinstance(self.seller_a, dict):
            self.seller_a = ComparedSeller(**self.seller_a)
        if isinstance(self.seller_b, dict):
            self.seller_b = ComparedSeller(**self.seller_b)

    def sellers(self) -> list[ComparedSeller]:
        return [slot for slot in (self.seller_a, self.seller_b) if slot.seller_url]


class TaskComparisonMemory:
    """Thread-safe comparison memory with optional Supabase mirroring."""

    def __init__(self, path: str = "task_comparisons.json"):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._records: dict[str, TaskComparisonRecord] = {}
        self._supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self._supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        self._supabase_table = os.getenv(
            "SUPABASE_TASK_COMPARISONS_TABLE",
            "buyer_task_comparisons",
        )
        self._load_local()

    @staticmethod
    def build_task_key(query: str, query_category: str = "") -> str:
        """Normalize a question into a stable task key."""
        normalized = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()
        words = normalized.split()
        slug = "-".join(words[:8]) or "task"
        prefix = (query_category or "general").strip().lower() or "general"
        digest = hashlib.sha1(f"{prefix}:{normalized}".encode("utf-8")).hexdigest()[:10]
        return f"{prefix}:{slug[:64]}:{digest}"

    def _load_local(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        comparisons = raw.get("comparisons", [])
        for item in comparisons:
            try:
                record = TaskComparisonRecord(**item)
            except TypeError:
                continue
            self._records[record.task_key] = record

    def _save_local(self) -> None:
        payload = {
            "comparisons": [asdict(record) for record in self._records.values()],
        }
        self._path.write_text(json.dumps(payload, indent=2))

    def _supabase_enabled(self) -> bool:
        return bool(self._supabase_url and self._supabase_key)

    def _supabase_headers(self) -> dict[str, str]:
        token = self._supabase_key
        return {
            "apikey": token,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _fetch_remote(self, task_key: str) -> TaskComparisonRecord | None:
        if not self._supabase_enabled():
            return None
        url = (
            f"{self._supabase_url}/rest/v1/{self._supabase_table}"
            f"?task_key=eq.{quote(task_key, safe='')}&select=*"
        )
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=self._supabase_headers())
                resp.raise_for_status()
                rows = resp.json()
        except Exception:
            return None
        if not rows:
            return None
        try:
            return TaskComparisonRecord(**rows[0])
        except TypeError:
            return None

    def _upsert_remote(self, record: TaskComparisonRecord) -> None:
        if not self._supabase_enabled():
            return
        url = f"{self._supabase_url}/rest/v1/{self._supabase_table}"
        headers = self._supabase_headers()
        headers["Prefer"] = "resolution=merge-duplicates"
        body = asdict(record)
        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(url, headers=headers, json=body).raise_for_status()
        except Exception:
            # Remote sync is best-effort so local operation still succeeds.
            return

    def get(self, task_key: str) -> TaskComparisonRecord | None:
        with self._lock:
            local = self._records.get(task_key)
        if local:
            return local
        remote = self._fetch_remote(task_key)
        if not remote:
            return None
        with self._lock:
            self._records[task_key] = remote
            self._save_local()
        return remote

    def get_for_query(self, query: str, query_category: str = "") -> TaskComparisonRecord | None:
        return self.get(self.build_task_key(query, query_category))

    def list_all(self) -> list[dict[str, Any]]:
        with self._lock:
            records = list(self._records.values())
        return [asdict(record) for record in records]

    def ensure_pair(
        self,
        query: str,
        query_category: str,
        candidate_sellers: list[dict],
        exclude_urls: set[str] | None = None,
        force_replace: bool = False,
    ) -> TaskComparisonRecord | None:
        """Create or refresh the two-seller comparison set for a task."""
        task_key = self.build_task_key(query, query_category)
        exclude_urls = exclude_urls or set()
        candidate_pool = [
            s for s in candidate_sellers
            if s.get("url") and s["url"] not in exclude_urls
        ]

        with self._lock:
            existing = self._records.get(task_key)
            if existing and not force_replace:
                existing_urls = {
                    slot.seller_url
                    for slot in existing.sellers()
                    if slot.seller_url
                }
                available_urls = {s["url"] for s in candidate_pool}
                if existing_urls and existing_urls.issubset(available_urls):
                    return existing

            if not candidate_pool:
                return existing

            seller_a = ComparedSeller(
                seller_url=candidate_pool[0]["url"],
                seller_name=candidate_pool[0].get("name", ""),
            )
            seller_b = ComparedSeller()
            if len(candidate_pool) > 1:
                seller_b = ComparedSeller(
                    seller_url=candidate_pool[1]["url"],
                    seller_name=candidate_pool[1].get("name", ""),
                )

            record = TaskComparisonRecord(
                task_key=task_key,
                query_category=query_category.lower(),
                query_preview=query[:200],
                seller_a=seller_a,
                seller_b=seller_b,
                minimum_acceptable_score=(
                    existing.minimum_acceptable_score if existing else _default_minimum_score()
                ),
                preferred_seller_url="",
                needs_rebrowse=False,
                updated_at=_now_iso(),
            )
            self._records[task_key] = record
            self._save_local()

        self._upsert_remote(record)
        return record

    def record_result(
        self,
        query: str,
        query_category: str,
        seller_url: str,
        seller_name: str,
        quality_score: float,
        roi: float,
        purchase_id: str = "",
        reasoning: str = "",
    ) -> TaskComparisonRecord:
        """Update the task comparison state after evaluating one seller."""
        task_key = self.build_task_key(query, query_category)
        record = self.get(task_key)
        if not record:
            record = self.ensure_pair(
                query=query,
                query_category=query_category,
                candidate_sellers=[{"url": seller_url, "name": seller_name}],
            )
        if not record:
            raise RuntimeError("Failed to initialize task comparison record")

        with self._lock:
            record = self._records[task_key]
            slots = [record.seller_a, record.seller_b]
            target = next((slot for slot in slots if slot.seller_url == seller_url), None)
            if target is None:
                target = next((slot for slot in slots if not slot.seller_url), None)
            if target is None:
                target = record.seller_b

            target.seller_url = seller_url
            target.seller_name = seller_name
            target.tested = True
            target.quality_score = float(quality_score)
            target.roi = float(roi)
            target.attempts += 1
            target.last_purchase_id = purchase_id
            target.last_reasoning = reasoning[:500]
            target.last_tested_at = _now_iso()

            tested_slots = [slot for slot in record.sellers() if slot.tested]
            if tested_slots:
                best = max(tested_slots, key=lambda slot: (slot.quality_score, slot.roi))
                record.preferred_seller_url = best.seller_url
                record.needs_rebrowse = (
                    len(tested_slots) >= 2
                    and best.quality_score < record.minimum_acceptable_score
                )
            record.updated_at = _now_iso()
            self._save_local()
            saved = self._records[task_key]

        self._upsert_remote(saved)
        return saved
