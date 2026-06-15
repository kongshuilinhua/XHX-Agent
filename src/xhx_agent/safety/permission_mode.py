PERMISSION_MODES = ("default", "auto", "bypass")


def next_permission_mode(mode: str) -> str:
    if mode == "default":
        return "auto"
    elif mode == "auto":
        return "bypass"
    else:
        return "default"


def permission_mode_from_string(s: str) -> str:
    s = s.strip().lower()
    if s in PERMISSION_MODES:
        return s
    return "default"


def permission_mode_title(mode: str) -> str:
    titles = {
        "default": "默认",
        "auto": "自动",
        "bypass": "越过",
    }
    return titles.get(mode, "默认")
