"""Persistent purchase ledger with evaluation and ROI tracking.

Records every purchase with quality scores, ROI calculations, and
per-seller/per-category aggregate stats. Persists to a JSON file
so data survives restarts.

Used by the smart buyer agent to make explore/exploit decisions.
"""

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Evaluation:
    """Structured rubric evaluation of a purchase response."""

    relevance: int = 0       # 0-2: Did it directly answer the query?
    depth: int = 0           # 0-2: Did it include specific data/facts/numbers?
    actionability: int = 0   # 0-2: Could the user make a decision from this?
    specificity: int = 0     # 0-2: Was it beyond generic/boilerplate?
    reasoning: str = ""      # LLM explanation of the scores

    @property
    def total(self) -> int:
        return self.relevance + self.depth + self.actionability + self.specificity

    @property
    def max_score(self) -> int:
        return 8


@dataclass
class PurchaseRecord:
    """A single purchase with evaluation data."""

    id: str
    query: str
    query_category: str
    seller_url: str
    seller_name: str
    cost: int
    response_summary: str
    evaluation: Evaluation
    timestamp: str
    quality_score: float = 0.0
    roi: float = 0.0

    def __post_init__(self):
        if isinstance(self.evaluation, dict):
            self.evaluation = Evaluation(**self.evaluation)
        self.quality_score = float(self.evaluation.total)
        self.roi = self.quality_score / max(self.cost, 1)


class PurchaseLedger:
    """Thread-safe, file-persisted purchase ledger."""

    def __init__(self, path: str = "purchase_ledger.json"):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._records: list[PurchaseRecord] = []
        self._load()

    def _load(self):
        """Load existing records from disk."""
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                for r in data.get("records", []):
                    self._records.append(PurchaseRecord(**r))
            except (json.JSONDecodeError, TypeError, KeyError):
                self._records = []

    def _save(self):
        """Persist records to disk."""
        data = {"records": [asdict(r) for r in self._records]}
        self._path.write_text(json.dumps(data, indent=2))

    def record(
        self,
        query: str,
        query_category: str,
        seller_url: str,
        seller_name: str,
        cost: int,
        response_summary: str,
        evaluation: Evaluation,
    ) -> PurchaseRecord:
        """Record a purchase with its evaluation."""
        rec = PurchaseRecord(
            id=str(uuid.uuid4())[:8],
            query=query[:200],
            query_category=query_category.lower(),
            seller_url=seller_url,
            seller_name=seller_name,
            cost=cost,
            response_summary=response_summary[:300],
            evaluation=evaluation,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._records.append(rec)
            self._save()
        return rec

    def get_seller_stats(self, seller_url: str) -> dict:
        """Get aggregate stats for a specific seller."""
        with self._lock:
            records = [r for r in self._records if r.seller_url == seller_url]
        if not records:
            return {"total_purchases": 0}
        return {
            "seller_name": records[0].seller_name,
            "total_purchases": len(records),
            "total_spent": sum(r.cost for r in records),
            "avg_quality": round(sum(r.quality_score for r in records) / len(records), 2),
            "avg_roi": round(sum(r.roi for r in records) / len(records), 2),
            "categories": list({r.query_category for r in records}),
        }

    def get_category_stats(self, category: str) -> dict:
        """Get stats for a query category across all sellers."""
        with self._lock:
            records = [r for r in self._records if r.query_category == category.lower()]
        if not records:
            return {"total_purchases": 0, "sellers_tried": []}

        by_seller: dict[str, list[PurchaseRecord]] = {}
        for r in records:
            by_seller.setdefault(r.seller_url, []).append(r)

        seller_stats = {}
        for url, recs in by_seller.items():
            seller_stats[url] = {
                "name": recs[0].seller_name,
                "purchases": len(recs),
                "avg_quality": round(sum(r.quality_score for r in recs) / len(recs), 2),
                "avg_roi": round(sum(r.roi for r in recs) / len(recs), 2),
            }

        best_url = max(seller_stats, key=lambda u: seller_stats[u]["avg_roi"])
        return {
            "total_purchases": len(records),
            "sellers_tried": list(seller_stats.keys()),
            "by_seller": seller_stats,
            "best_seller": {"url": best_url, **seller_stats[best_url]},
        }

    def get_sellers_tried_for_category(self, category: str) -> list[str]:
        """Return list of seller URLs tried for a category."""
        with self._lock:
            return list({
                r.seller_url
                for r in self._records
                if r.query_category == category.lower()
            })

    def get_all_records(self) -> list[PurchaseRecord]:
        """Return all purchase records."""
        with self._lock:
            return list(self._records)

    def get_summary(self) -> dict:
        """Return overall ledger summary."""
        with self._lock:
            records = list(self._records)

        if not records:
            return {
                "total_purchases": 0,
                "total_spent": 0,
                "avg_roi": 0,
                "by_seller": {},
                "by_category": {},
                "recent": [],
            }

        by_seller: dict[str, list[PurchaseRecord]] = {}
        by_category: dict[str, list[PurchaseRecord]] = {}
        for r in records:
            by_seller.setdefault(r.seller_url, []).append(r)
            by_category.setdefault(r.query_category, []).append(r)

        seller_summary = {}
        for url, recs in by_seller.items():
            seller_summary[url] = {
                "name": recs[0].seller_name,
                "purchases": len(recs),
                "total_spent": sum(r.cost for r in recs),
                "avg_quality": round(sum(r.quality_score for r in recs) / len(recs), 2),
                "avg_roi": round(sum(r.roi for r in recs) / len(recs), 2),
            }

        category_summary = {}
        for cat, recs in by_category.items():
            category_summary[cat] = {
                "purchases": len(recs),
                "sellers_tried": len({r.seller_url for r in recs}),
                "avg_roi": round(sum(r.roi for r in recs) / len(recs), 2),
            }

        return {
            "total_purchases": len(records),
            "total_spent": sum(r.cost for r in records),
            "avg_roi": round(sum(r.roi for r in records) / len(records), 2),
            "by_seller": seller_summary,
            "by_category": category_summary,
            "recent": [asdict(r) for r in records[-5:]],
        }

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)
