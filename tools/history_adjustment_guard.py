"""Read-only guard for possible corporate-action price-scale discontinuities.

This module never changes canonical history and never declares a corporate
action from price data alone. It only quarantines derived metrics when the
current quote's official reference price cannot be reconciled with the most
recent canonical close.
"""

from __future__ import annotations

import math


STATUS_NOT_EVALUATED = "HISTORY_NOT_EVALUATED"
STATUS_NOT_DETECTED = "HISTORY_ADJUSTMENT_NOT_DETECTED"
STATUS_UNVERIFIED = "HISTORY_ADJUSTMENT_UNVERIFIED"

# A quarantine threshold, not a corporate-action rule.
SCALE_FACTOR_THRESHOLD = 5.0

DERIVED_METRIC_FIELDS = (
    *(f"ma{window}" for window in (5, 20, 60, 120, 240)),
    *(f"dist_ma{window}" for window in (5, 20, 60, 120, 240)),
    *(f"ret_{window}d" for window in (1, 5, 20, 60, 120, 240)),
    *(f"rolling_high_{window}" for window in (20, 60, 120, 240)),
    *(f"dist_high_{window}" for window in (20, 60, 120, 240)),
    "volume_ma20", "current_volume_ratio_20",
    "above_ma20", "above_ma60", "above_ma120",
    "ma20_rising", "ma60_rising", "bullish_alignment",
    "breakout_20_intraday", "breakout_60_intraday", "near_52week_high",
)


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def detect(current, previous_close):
    """Assess one row without confirming a corporate action."""
    current_price = _number(current.get("currentPrice"))
    change = _number(current.get("change"))
    previous_close = _number(previous_close)
    if previous_close is None or previous_close <= 0:
        return {
            "status": STATUS_NOT_EVALUATED,
            "reason": "CANONICAL_PREVIOUS_CLOSE_UNAVAILABLE",
            "officialReferencePrice": None,
            "canonicalPreviousClose": previous_close,
            "scaleRatio": None,
            "corporateActionConfirmed": False,
            "derivedMetricsUsable": True,
        }
    official_reference = (current_price - change
                          if current_price is not None and change is not None else None)
    if official_reference is None or official_reference <= 0:
        return {
            "status": STATUS_UNVERIFIED,
            "reason": "OFFICIAL_REFERENCE_PRICE_UNAVAILABLE",
            "officialReferencePrice": official_reference,
            "canonicalPreviousClose": previous_close,
            "scaleRatio": None,
            "corporateActionConfirmed": False,
            "derivedMetricsUsable": False,
        }
    ratio = previous_close / official_reference
    scale_ratio = max(ratio, 1 / ratio) if ratio > 0 else None
    if scale_ratio is not None and scale_ratio >= SCALE_FACTOR_THRESHOLD:
        return {
            "status": STATUS_UNVERIFIED,
            "reason": "PRICE_SCALE_MISMATCH",
            "officialReferencePrice": round(official_reference, 6),
            "canonicalPreviousClose": round(previous_close, 6),
            "scaleRatio": round(ratio, 6),
            "corporateActionConfirmed": False,
            "derivedMetricsUsable": False,
        }
    return {
        "status": STATUS_NOT_DETECTED,
        "reason": "REFERENCE_AND_HISTORY_SCALE_RECONCILED",
        "officialReferencePrice": round(official_reference, 6),
        "canonicalPreviousClose": round(previous_close, 6),
        "scaleRatio": round(ratio, 6),
        "corporateActionConfirmed": False,
        "derivedMetricsUsable": True,
    }


def apply_to_feature(feature, assessment):
    """Annotate a feature row and quarantine affected derived fields."""
    result = dict(feature)
    result.update({
        "historyAdjustmentStatus": assessment["status"],
        "historyAdjustmentReason": assessment["reason"],
        "officialReferencePrice": assessment["officialReferencePrice"],
        "canonicalPreviousClose": assessment["canonicalPreviousClose"],
        "historyScaleRatio": assessment["scaleRatio"],
        "corporateActionConfirmed": assessment["corporateActionConfirmed"],
        "derivedMetricsUsable": assessment["derivedMetricsUsable"],
    })
    if not assessment["derivedMetricsUsable"]:
        for key in DERIVED_METRIC_FIELDS:
            if key in result:
                result[key] = None
    return result


def derived_metrics_usable(row):
    """Fail closed only for this guard; preserve legacy rows by default."""
    return row.get("derivedMetricsUsable", True) is True
