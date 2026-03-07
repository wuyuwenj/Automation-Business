"""Category-level seller comparison memory with Supabase persistence.

Tracks which sellers have been tested for each query category, their
quality scores, and which seller is currently preferred. Supports N
sellers per category (not limited to 2).

Also maintains a global seller blocklist — sellers that scored too low
or failed repeatedly are automatically skipped.

Flow:
- First query in a category: test the highest-relevance untested seller
- Second query: test the next untested seller
- After 2+ tested: exploit the best scorer (unless scores are weak)
- Supabase is the primary store; local JSON is a write-through cache
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .log import get_logger, log

_logger = get_logger("buyer.comparison")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_minimum_score() -> float:
    raw = os.getenv("BUYER_MIN_ACCEPTABLE_SCORE", "6").strip()
    try:
        return max(0.0, min(8.0, float(raw)))
    except ValueError:
        return 6.0


# Auto-block thresholds
_BLOCK_SCORE_THRESHOLD = int(os.getenv("BUYER_BLOCK_SCORE_THRESHOLD", "3"))
_BLOCK_FAILURE_THRESHOLD = int(os.getenv("BUYER_BLOCK_FAILURE_THRESHOLD", "2"))


@dataclass
class BlockedSeller:
    """A seller that has been blocked from selection."""

    seller_url: str = ""
    seller_name: str = ""
    reason: str = ""
    blocked_at: str = field(default_factory=_now_iso)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SellerTestResult:
    """Result from testing one seller for a category."""

    seller_url: str = ""
    seller_name: str = ""
    quality_score: float = 0.0
    roi: float = 0.0
    attempts: int = 0
    last_purchase_id: str = ""
    last_reasoning: str = ""
    last_tested_at: str = ""


@dataclass
class CategoryComparisonRecord:
    """Tracks all sellers tested for a given query_category."""

    category: str
    tested_sellers: list[SellerTestResult] = field(default_factory=list)
    preferred_seller_url: str = ""
    minimum_acceptable_score: float = field(default_factory=_default_minimum_score)
    needs_rebrowse: bool = False
    updated_at: str = field(default_factory=_now_iso)

    def __post_init__(self):
        self.tested_sellers = [
            SellerTestResult(**s) if isinstance(s, dict) else s
            for s in self.tested_sellers
        ]

    def tested_urls(self) -> set[str]:
        return {s.seller_url for s in self.tested_sellers if s.seller_url}

    def get_result(self, seller_url: str) -> SellerTestResult | None:
        return next(
            (s for s in self.tested_sellers if s.seller_url == seller_url), None
        )


# ---------------------------------------------------------------------------
# Comparison memory with Supabase persistence
# ---------------------------------------------------------------------------

class CategoryComparisonMemory:
    """Category-level comparison memory backed by Supabase + local JSON."""

    def __init__(self, path: str = "category_comparisons.json"):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._records: dict[str, CategoryComparisonRecord] = {}
        self._blocklist: dict[str, BlockedSeller] = {}
        self._failure_counts: dict[str, int] = {}
        self._supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self._supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        self._supabase_table = os.getenv(
            "SUPABASE_CATEGORY_COMPARISONS_TABLE",
            "buyer_category_comparisons",
        )
        self._load_local()
        self._load_manual_blocklist()

    # -- Local persistence ---------------------------------------------------

    def _load_manual_blocklist(self) -> None:
        """Load manually blocked sellers from BUYER_BLOCKED_SELLERS env var."""
        raw = os.getenv("BUYER_BLOCKED_SELLERS", "").strip()
        if not raw:
            return
        for url in raw.split(","):
            url = url.strip()
            if url and url not in self._blocklist:
                self._blocklist[url] = BlockedSeller(
                    seller_url=url, reason="manual (env var)")
        if self._blocklist:
            log(_logger, "COMPARISON", "BLOCKLIST",
                f"loaded {len(self._blocklist)} manually blocked seller(s)")

    def _load_local(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        for item in raw.get("comparisons", []):
            try:
                record = CategoryComparisonRecord(**item)
            except TypeError:
                continue
            self._records[record.category] = record
        for item in raw.get("blocklist", []):
            try:
                entry = BlockedSeller(**item) if isinstance(item, dict) else item
                self._blocklist[entry.seller_url] = entry
            except (TypeError, AttributeError):
                continue

    def _save_local(self) -> None:
        payload = {
            "comparisons": [asdict(r) for r in self._records.values()],
            "blocklist": [asdict(b) for b in self._blocklist.values()],
        }
        self._path.write_text(json.dumps(payload, indent=2))

    # -- Supabase persistence ------------------------------------------------

    def _supabase_enabled(self) -> bool:
        return bool(self._supabase_url and self._supabase_key)

    def _supabase_headers(self) -> dict[str, str]:
        return {
            "apikey": self._supabase_key,
            "Authorization": f"Bearer {self._supabase_key}",
            "Content-Type": "application/json",
        }

    def _fetch_remote(self, category: str) -> CategoryComparisonRecord | None:
        if not self._supabase_enabled():
            return None
        url = (
            f"{self._supabase_url}/rest/v1/{self._supabase_table}"
            f"?category=eq.{quote(category, safe='')}&select=*"
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
            return CategoryComparisonRecord(**rows[0])
        except TypeError:
            return None

    def _upsert_remote(self, record: CategoryComparisonRecord) -> None:
        if not self._supabase_enabled():
            return
        url = f"{self._supabase_url}/rest/v1/{self._supabase_table}"
        headers = self._supabase_headers()
        headers["Prefer"] = "resolution=merge-duplicates"
        body = asdict(record)
        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(url, headers=headers, json=body).raise_for_status()
        except Exception as e:
            log(_logger, "COMPARISON", "SUPABASE_ERROR",
                f"upsert failed: {e}")

    # -- Public API ----------------------------------------------------------

    def get_or_create(self, category: str) -> CategoryComparisonRecord:
        """Get or create the comparison record for a category."""
        cat = category.strip().lower()
        with self._lock:
            local = self._records.get(cat)
        if local:
            return local

        # Try Supabase
        remote = self._fetch_remote(cat)
        if remote:
            with self._lock:
                self._records[cat] = remote
                self._save_local()
            return remote

        # Create new
        record = CategoryComparisonRecord(category=cat)
        with self._lock:
            self._records[cat] = record
            self._save_local()
        return record

    def record_result(
        self,
        query_category: str,
        seller_url: str,
        seller_name: str,
        quality_score: float,
        roi: float,
        purchase_id: str = "",
        reasoning: str = "",
    ) -> CategoryComparisonRecord:
        """Record a test result for a seller in a category."""
        cat = query_category.strip().lower()
        record = self.get_or_create(cat)

        with self._lock:
            record = self._records[cat]

            # Find or create the seller entry
            existing = record.get_result(seller_url)
            if existing:
                existing.quality_score = float(quality_score)
                existing.roi = float(roi)
                existing.attempts += 1
                existing.last_purchase_id = purchase_id
                existing.last_reasoning = reasoning[:500]
                existing.last_tested_at = _now_iso()
            else:
                record.tested_sellers.append(SellerTestResult(
                    seller_url=seller_url,
                    seller_name=seller_name,
                    quality_score=float(quality_score),
                    roi=float(roi),
                    attempts=1,
                    last_purchase_id=purchase_id,
                    last_reasoning=reasoning[:500],
                    last_tested_at=_now_iso(),
                ))

            # Recompute preferred seller
            tested = [s for s in record.tested_sellers if s.attempts > 0]
            if tested:
                best = max(tested, key=lambda s: (s.quality_score, s.roi))
                record.preferred_seller_url = best.seller_url
                record.needs_rebrowse = (
                    len(tested) >= 2
                    and best.quality_score < record.minimum_acceptable_score
                )

            record.updated_at = _now_iso()
            self._save_local()
            saved = self._records[cat]

        self._upsert_remote(saved)

        log(_logger, "COMPARISON", "RECORDED",
            f"category={cat} seller={seller_name} "
            f"score={quality_score} tested={len(record.tested_sellers)}")
        return saved

    def select_next_seller(
        self,
        query_category: str,
        candidate_sellers: list[dict],
        failed_sellers: set[str] | None = None,
    ) -> dict | None:
        """Return the next untested seller for this category, or None."""
        cat = query_category.strip().lower()
        record = self.get_or_create(cat)
        tested = record.tested_urls()
        failed = failed_sellers or set()

        untested = [
            s for s in candidate_sellers
            if s["url"] not in tested and s["url"] not in failed
        ]

        if not untested:
            return None

        # Sort by relevance (from embeddings) desc, then cheapest
        untested.sort(key=lambda s: (
            -s.get("relevance_score", 0),
            s.get("credits", 999),
        ))
        return untested[0]

    def should_exploit(
        self, query_category: str
    ) -> tuple[bool, str | None]:
        """Check if we've tested enough sellers to exploit the best one.

        Returns (True, preferred_url) if 2+ sellers tested and winner found.
        """
        cat = query_category.strip().lower()
        record = self.get_or_create(cat)
        tested = [s for s in record.tested_sellers if s.attempts > 0]

        if len(tested) < 2:
            return False, None
        if record.needs_rebrowse:
            return False, None
        if not record.preferred_seller_url:
            return False, None
        return True, record.preferred_seller_url

    # -- Blocklist API -------------------------------------------------------

    def is_blocked(self, seller_url: str) -> bool:
        """Check if a seller is blocked."""
        with self._lock:
            return seller_url in self._blocklist

    def get_blocked_urls(self) -> set[str]:
        """Return all blocked seller URLs."""
        with self._lock:
            return set(self._blocklist.keys())

    def block_seller(
        self, seller_url: str, seller_name: str = "", reason: str = "",
    ) -> None:
        """Add a seller to the blocklist."""
        with self._lock:
            if seller_url in self._blocklist:
                return
            self._blocklist[seller_url] = BlockedSeller(
                seller_url=seller_url,
                seller_name=seller_name,
                reason=reason,
            )
            self._save_local()
        log(_logger, "COMPARISON", "BLOCKED",
            f"{seller_name or seller_url}: {reason}")

    def record_failure(self, seller_url: str, seller_name: str = "") -> bool:
        """Record a purchase failure. Returns True if seller was auto-blocked."""
        with self._lock:
            self._failure_counts[seller_url] = self._failure_counts.get(seller_url, 0) + 1
            count = self._failure_counts[seller_url]
        if count >= _BLOCK_FAILURE_THRESHOLD:
            self.block_seller(
                seller_url, seller_name,
                reason=f"auto: {count} consecutive failures",
            )
            return True
        return False

    def check_auto_block_score(
        self, seller_url: str, seller_name: str, quality_score: float,
    ) -> bool:
        """Auto-block if score is below threshold. Returns True if blocked."""
        if quality_score < _BLOCK_SCORE_THRESHOLD:
            self.block_seller(
                seller_url, seller_name,
                reason=f"auto: score {quality_score}/8 < {_BLOCK_SCORE_THRESHOLD}",
            )
            return True
        return False

    def list_all(self) -> list[dict[str, Any]]:
        """Return all comparison records as dicts."""
        with self._lock:
            records = list(self._records.values())
        return [asdict(r) for r in records]


# Backward-compatible alias
TaskComparisonMemory = CategoryComparisonMemory
