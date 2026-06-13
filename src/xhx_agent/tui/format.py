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


def line_style(line: str) -> str:
    """Return the Rich style string for a given timeline line based on its prefix/type."""
    if line.startswith("user>"):
        return "cyan"
    if line.startswith("assistant>"):
        return "bright_white"
    if line.startswith("system>"):
        return "yellow"
    if line.startswith("plan>"):
        return "blue"
    if line.startswith("summary>"):
        return "dim"
    if line.startswith("model (streaming"):
        return "dim italic"

    stripped = line.lstrip()
    if stripped.startswith("·"):
        return "dim"
    if stripped.startswith("⟶"):
        return "blue"
    if stripped.startswith("✓"):
        return "green"
    if stripped.startswith("✗"):
        return "red"
    if stripped.startswith("⚙"):
        return "blue"
    if stripped.startswith("▸ agent"):
        return "magenta"
    if stripped.startswith("💭"):
        return "dim italic"

    return ""

