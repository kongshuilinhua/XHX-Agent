def human_tokens(n: int) -> str:
    """Format an integer number of tokens to a human-readable string (e.g. 8.8k)."""
    if abs(n) < 1000:
        return str(n)
    val = n / 1000
    s = f"{val:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    return s + "k"


def context_meter(used: int, budget: int) -> tuple[str, float | None, str]:
    """Calculate and return context usage label, percentage, and severity level."""
    if budget <= 0:
        return "Context —", None, "none"
    pct = (used / budget) * 100
    label = f"Context {human_tokens(used)}/{human_tokens(budget)} {pct:.1f}%"
    level = "ok" if pct < 70 else "warn" if pct < 90 else "crit"
    return label, pct, level
