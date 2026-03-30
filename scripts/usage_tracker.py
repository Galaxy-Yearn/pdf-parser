from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import contextvars


_CURRENT_TRACKER: contextvars.ContextVar[Optional["UsageTracker"]] = contextvars.ContextVar(
    "moa_usage_tracker",
    default=None,
)


def get_current_tracker() -> Optional["UsageTracker"]:
    return _CURRENT_TRACKER.get()


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:  # noqa: BLE001
        return 0


def normalize_usage(usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """
    Normalize usage dicts across providers.

    Common shapes:
    - OpenAI: {prompt_tokens, completion_tokens, total_tokens}
    - Qwen/others: {input_tokens, output_tokens, total_tokens}
    """
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    inp = _as_int(usage.get("input_tokens")) or _as_int(usage.get("prompt_tokens"))
    out = _as_int(usage.get("output_tokens")) or _as_int(usage.get("completion_tokens"))
    total = _as_int(usage.get("total_tokens"))
    if total <= 0:
        total = max(0, inp) + max(0, out)
    # Some providers only return total_tokens without an explicit input/output split.
    # For non-generative calls (e.g. rerank/embeddings), treating total as input is the least confusing.
    if total > 0 and inp == 0 and out == 0:
        inp = total
    return {
        "input_tokens": max(0, int(inp)),
        "output_tokens": max(0, int(out)),
        "total_tokens": max(0, int(total)),
    }


@dataclass
class UsageEvent:
    category: str
    model: str
    usage: Dict[str, int]


class UsageTracker:
    def __init__(self) -> None:
        self._events: list[UsageEvent] = []

    def __enter__(self) -> "UsageTracker":
        self._token = _CURRENT_TRACKER.set(self)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:  # noqa: ANN001
        try:
            _CURRENT_TRACKER.reset(self._token)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            _CURRENT_TRACKER.set(None)

    def record(self, *, category: str, model: str, usage: Optional[Dict[str, Any]]) -> None:
        cat = str(category or "").strip().lower() or "unknown"
        mod = str(model or "").strip() or "unknown"
        self._events.append(UsageEvent(category=cat, model=mod, usage=normalize_usage(usage)))

    def summary(self) -> Dict[str, Any]:
        totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0}
        by_category: Dict[str, Dict[str, int]] = {}
        by_model: Dict[str, Dict[str, int]] = {}

        for ev in self._events:
            u = ev.usage
            totals["calls"] += 1
            totals["input_tokens"] += int(u.get("input_tokens") or 0)
            totals["output_tokens"] += int(u.get("output_tokens") or 0)
            totals["total_tokens"] += int(u.get("total_tokens") or 0)

            c = by_category.setdefault(
                ev.category,
                {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0},
            )
            c["calls"] += 1
            c["input_tokens"] += int(u.get("input_tokens") or 0)
            c["output_tokens"] += int(u.get("output_tokens") or 0)
            c["total_tokens"] += int(u.get("total_tokens") or 0)

            m = by_model.setdefault(
                ev.model,
                {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0},
            )
            m["calls"] += 1
            m["input_tokens"] += int(u.get("input_tokens") or 0)
            m["output_tokens"] += int(u.get("output_tokens") or 0)
            m["total_tokens"] += int(u.get("total_tokens") or 0)

        return {
            "totals": totals,
            "by_category": by_category,
            "by_model": by_model,
        }

    @staticmethod
    def format_compact(summary: Dict[str, Any]) -> str:
        """
        Compact UI display.

        Format (2 lines, word-wrapped by UI):
          tokens total=133 (in=10, out=123, calls=3)
          chat total=133 (in=10, out=123, calls=1) | embedding total=22 (in=22, out=0, calls=1)
        """
        if not isinstance(summary, dict):
            return ""

        totals = summary.get("totals")
        by_cat = summary.get("by_category")
        if not isinstance(totals, dict):
            return ""

        inp_t = _as_int(totals.get("input_tokens"))
        out_t = _as_int(totals.get("output_tokens"))
        total_t = _as_int(totals.get("total_tokens"))
        calls_t = _as_int(totals.get("calls"))
        head = f"tokens total={total_t} (in={inp_t}, out={out_t}, calls={calls_t})"

        if not isinstance(by_cat, dict) or not by_cat:
            return head

        def _cat_label(cat: str) -> str:
            c = str(cat or "").strip().lower()
            if c in {"embeddings", "embedding"}:
                c = "embedding"
            elif c in {"policy", "permission"}:
                c = "permission"
            # Keep UI simple: collapse everything into these buckets.
            if c not in {"chat", "embedding", "rerank", "permission", "ocr"}:
                c = "chat"
            return c or "chat"

        # Aggregate categories after normalization (e.g. embeddings->embedding, policy->permission).
        agg: Dict[str, Dict[str, int]] = {}
        for raw_cat, row in by_cat.items():
            if not isinstance(row, dict):
                continue
            calls = _as_int(row.get("calls"))
            if calls <= 0:
                continue
            inp = _as_int(row.get("input_tokens"))
            out = _as_int(row.get("output_tokens"))
            total = _as_int(row.get("total_tokens"))
            if total <= 0:
                total = max(0, inp) + max(0, out)

            k = _cat_label(str(raw_cat))
            a = agg.setdefault(k, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
            a["calls"] += max(0, int(calls))
            a["input_tokens"] += max(0, int(inp))
            a["output_tokens"] += max(0, int(out))
            a["total_tokens"] += max(0, int(total))

        if not agg:
            return head

        preferred = ["chat", "embedding", "rerank", "permission", "ocr"]
        parts: list[str] = []
        used: set[str] = set()
        for k in preferred:
            u = agg.get(k)
            if not u:
                continue
            used.add(k)
            inp = int(u.get("input_tokens") or 0)
            out = int(u.get("output_tokens") or 0)
            total = int(u.get("total_tokens") or 0)
            calls = int(u.get("calls") or 0)
            if total > 0 and inp == 0 and out == 0:
                inp = total
            parts.append(
                f"{k} total={total} (in={inp}, out={out}, calls={calls})"
            )

        return head if not parts else (head + "\n" + " | ".join(parts))


def record_usage(*, category: str, model: str, usage: Optional[Dict[str, Any]]) -> None:
    tracker = get_current_tracker()
    if not tracker:
        return
    try:
        tracker.record(category=category, model=model, usage=usage)
    except Exception:  # noqa: BLE001
        return
