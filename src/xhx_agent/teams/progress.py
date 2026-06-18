"""队友进度追踪。"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

# 97个创意动画动词
SPINNER_VERBS = [
    "Beboppin'",
    "Flibbertigibbeting",
    "Hullaballooing",
    "Kerfuffling",
    "Bamboozling",
    "Skedaddling",
    "Lollygagging",
    "Hornswoggling",
    "Coddiwompling",
    "Bumfuzzling",
    "Gobsmacking",
    "Collywobbling",
    "Snollygosting",
    "Whippersnapping",
    "Flummoxing",
    "Persnicketing",
    "Discombobulating",
    "Mollifying",
    "Obfuscating",
    "Prevaricating",
    "Recalcitrating",
    "Tergiversating",
    "Absquatulating",
    "Bloviating",
    "Conflagrating",
    "Defenestrating",
    "Extrapolating",
    "Fungenerating",
    "Gallivanting",
    "Hullaballooing",
    "Interpolating",
    "Juxtaposing",
    "Kaleidoscoping",
    "Logarithmizing",
    "Magnetically",
    "Noodling",
    "Opalescing",
    "Peregrinating",
    "Querulously",
    "Rambunctiously",
    "Scintillating",
    "Tessellating",
    "Ubiquinating",
    "Vellicating",
    "Widdershinning",
    "Xenoglossying",
    "Yodeling",
    "Zephyrizing",
    "Algorithmizing",
    "Bitcrunching",
    "Codeweaving",
    "Debugging",
    "Encrypting",
    "Firmware",
    "Gitpushing",
    "Hexdumping",
    "Iterpolating",
    "JITcompiling",
    "Kernelpanicking",
    "Linting",
    "Mewing",
    "Nullchecking",
    "Optimizing",
    "Parsing",
    "Queryplanning",
    "Refactoring",
    "Syntaxhighlighting",
    "Typechecking",
    "Unicode",
    "Vectorizing",
    "WASMcompiling",
    "XHRfetching",
    "YAMLparsing",
    "Zstandard",
    "Assembling",
    "Bootstrapping",
    "Compiling",
    "Dependencyresolving",
    "Evaluating",
    "Fuzzing",
    "Garbagecollecting",
    "Hotpatching",
    "Inlining",
    "JITting",
    "Loopunrolling",
    "Monomorphizing",
    "Normalizing",
    "Offloading",
    "Profiling",
    "Quantizing",
    "Rehashing",
    "Serializing",
    "Tailrecursing",
    "Unboxing",
    "Validating",
    "Workstealing",
    "Yielding",
    "Zeroing",
    "Purring",
]

RANDOM = random.Random()


@dataclass
class ToolActivity:
    tool_name: str
    description: str

    @staticmethod
    def from_tool_use(tool_name: str, args: dict[str, Any]) -> ToolActivity:
        return ToolActivity(tool_name=tool_name, description=_describe(tool_name, args))


def _describe(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name == "read_file":
        return f"Reading {args.get('path', '...')}"
    if tool_name == "apply_patch":
        return f"Patching {args.get('path', '...')}"
    if tool_name == "search":
        return f"Searching {args.get('glob', '...')}"
    if tool_name == "terminal":
        cmd = str(args.get("command", ""))
        return f"Running {cmd[:40]}..."
    return tool_name


@dataclass
class TeammateProgress:
    name: str
    team_name: str = ""
    status: str = "running"
    tool_use_count: int = 0
    token_count: int = 0
    last_activity: ToolActivity | None = None
    recent_activities: list[ToolActivity] = field(default_factory=list)
    spinner_verb: str = ""
    start_time: float = field(default_factory=time.monotonic)
    last_message: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if not self.spinner_verb:
            self.spinner_verb = RANDOM.choice(SPINNER_VERBS)

    def record_tool_use(self, tool_name: str, args: dict[str, Any]) -> None:
        with self._lock:
            self.tool_use_count += 1
            activity = ToolActivity.from_tool_use(tool_name, args)
            self.last_activity = activity
            self.recent_activities.append(activity)
            if len(self.recent_activities) > 5:
                self.recent_activities = self.recent_activities[-5:]

    def record_tokens(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.token_count = input_tokens + output_tokens

    @property
    def activity_summary(self) -> str:
        if self.last_activity:
            return self.last_activity.description
        return self.spinner_verb

    @staticmethod
    def format_tokens(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}k"
        return str(n)
