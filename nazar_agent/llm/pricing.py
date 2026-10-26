"""Price per million tokens, to report what a run cost (https://dev.meta.ai/docs/pricing-rate-limits)."""

from __future__ import annotations

# (input, output) USD per 1M tokens, as of Oct 2026.
_STANDARD = (1.25, 4.25)
_CONTRIBUTOR = (0.10, 0.20)
PRICES = {
    "muse-spark-1.3": _STANDARD,
    "muse-spark-1.2": _STANDARD,
    "muse-spark-1.1": _STANDARD,
    "muse-spark-1.3-contributor": _CONTRIBUTOR,
    "muse-spark-1.2-contributor": _CONTRIBUTOR,
    "muse-wonder-1.0": (4.00, 20.00),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
