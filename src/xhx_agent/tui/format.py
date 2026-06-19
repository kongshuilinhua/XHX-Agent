import re

# 经典 Windows 控制台（conhost）渲染不了彩色 emoji，会显示成方块/问号。剥掉这些高位
# 码点（仅 SMP emoji 区 + 变体选择符 + ZWJ + 杂项符号），保留 UI 在用的 BMP 符号
# （✓ U+2713 / ✻ U+273B / ❯ U+276F / • U+2022 / ● ○ / ☑ ☐ / → 等）。
_EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"  # 表情/图形/交通/补充符号等 SMP emoji（🎮🍎🐍🏆🎨…）
    "\U00002728"  # ✨ sparkles
    "\U00002b00-\U00002bff"  # ⭐ 等星形/符号
    "\U0000fe00-\U0000fe0f"  # 变体选择符（emoji 呈现）
    "\U0000200d"  # 零宽连接符（ZWJ emoji 序列）
    "]+",
    flags=re.UNICODE,
)


def strip_emoji(text: str) -> str:
    """移除终端无法渲染的 emoji（仅用于显示，不改动底层存储/会话文本）。"""
    return _EMOJI_RE.sub("", text)


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
    if stripped.startswith("──"):
        return "dim"
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
