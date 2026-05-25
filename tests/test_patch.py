from pathlib import Path

from xhx_agent.tools.patch import apply_patch


def test_apply_patch_success(tmp_path: Path) -> None:
    target = tmp_path / "demo.py"
    target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    patch = """*** Begin Patch
*** Update File: demo.py
@@
-    return a - b
+    return a + b
*** End Patch
"""
    result = apply_patch(tmp_path, patch)
    assert result.status == "success"
    assert result.changed_files == ["demo.py"]
    assert "return a + b" in target.read_text(encoding="utf-8")


def test_apply_patch_context_failure(tmp_path: Path) -> None:
    target = tmp_path / "demo.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    patch = """*** Begin Patch
*** Update File: demo.py
@@
-    return a - b
+    return a + b
*** End Patch
"""
    result = apply_patch(tmp_path, patch)
    assert result.status == "failed"
    assert result.changed_files == []

