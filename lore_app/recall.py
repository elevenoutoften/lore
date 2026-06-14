"""Recency- and salience-weighted recall scoring over the claim ledger.

Recall ranks ledger claims for an agent's query by combining four signals:

- **strength** — the reinforce/decay ledger value (repeated, corroborated claims
  rise; untouched claims decay). Already normalised to ``[0.01, 1.0]``.
- **recency** — exponential freshness from the claim's last-access/update anchor,
  so memory that was touched recently outranks stale memory of equal strength.
- **salience** — how often a claim has been recalled, log-scaled and saturating,
  so frequently-needed facts surface first without a single hot claim dominating.
- **relevance** — lexical overlap between the query and the claim text, used only
  when a query string is supplied.

The weights are deliberately simple and deterministic: recall must be explainable
to the agents that depend on it, and the score breakdown is returned alongside
every result so callers can see *why* a claim ranked where it did.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Recency half-life in days: a claim untouched for this long contributes half the
# recency signal it would when freshly accessed.
RECENCY_HALF_LIFE_DAYS = 30.0

# Access count at which salience effectively saturates (further accesses add little).
SALIENCE_SATURATION = 20.0

# Default signal weights. relevance is only mixed in when a query is supplied;
# otherwise its weight is redistributed across the remaining signals.
DEFAULT_WEIGHTS: dict[str, float] = {
    "strength": 0.45,
    "recency": 0.25,
    "salience": 0.15,
    "relevance": 0.15,
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class RecallScore:
    """A recall score and the component signals that produced it."""

    total: float
    strength: float
    recency: float
    salience: float
    relevance: float

    def as_dict(self) -> dict[str, float]:
        return {
            "total": round(self.total, 6),
            "strength": round(self.strength, 6),
            "recency": round(self.recency, 6),
            "salience": round(self.salience, 6),
            "relevance": round(self.relevance, 6),
        }


def recency_score(age_days: float, *, half_life: float = RECENCY_HALF_LIFE_DAYS) -> float:
    """Exponential freshness in ``[0, 1]``. age 0 -> 1.0, age half_life -> 0.5."""
    if half_life <= 0:
        return 1.0
    age = max(0.0, float(age_days))
    return float(0.5 ** (age / half_life))


def salience_score(access_count: int, *, saturation: float = SALIENCE_SATURATION) -> float:
    """Log-scaled, saturating recall frequency in ``[0, 1]``."""
    count = max(0, int(access_count))
    if count == 0:
        return 0.0
    if saturation <= 0:
        return 1.0
    return min(1.0, math.log1p(count) / math.log1p(saturation))


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.casefold())


def text_relevance(query: str, text: str) -> float:
    """Fraction of distinct query tokens present in ``text``, in ``[0, 1]``.

    Returns 1.0 for an empty query so relevance is neutral when no query is given.
    """
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return 1.0
    text_tokens = set(_tokenize(text))
    if not text_tokens:
        return 0.0
    overlap = query_tokens & text_tokens
    return len(overlap) / len(query_tokens)


def _effective_weights(query: str | None) -> dict[str, float]:
    weights = dict(DEFAULT_WEIGHTS)
    if query and query.strip():
        return weights
    # No query: drop relevance and renormalise the remaining signals to sum to 1.
    relevance_weight = weights.pop("relevance")
    remaining = sum(weights.values())
    if remaining <= 0:  # pragma: no cover - defensive
        return {key: 0.0 for key in weights}
    scale = (remaining + relevance_weight) / remaining
    return {key: value * scale for key, value in weights.items()}


def weights_for_query(query: str | None) -> dict[str, float]:
    """The effective signal weights recall uses for a given query (rounded)."""
    return {key: round(value, 6) for key, value in _effective_weights(query).items()}


def compute_recall_score(
    *,
    strength: float,
    age_days: float,
    access_count: int,
    query: str | None = None,
    text: str = "",
) -> RecallScore:
    """Combine strength, recency, salience, and relevance into one recall score."""
    weights = _effective_weights(query)
    strength_signal = max(0.0, min(1.0, float(strength)))
    recency_signal = recency_score(age_days)
    salience_signal = salience_score(access_count)
    relevance_signal = text_relevance(query or "", text) if "relevance" in weights else 1.0

    total = (
        weights.get("strength", 0.0) * strength_signal
        + weights.get("recency", 0.0) * recency_signal
        + weights.get("salience", 0.0) * salience_signal
        + weights.get("relevance", 0.0) * relevance_signal
    )
    return RecallScore(
        total=total,
        strength=strength_signal,
        recency=recency_signal,
        salience=salience_signal,
        relevance=relevance_signal,
    )
