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
    assert "return a + b" in target.read_text(encoding="utf-8")


def test_apply_patch_multiple_hunks_same_file(tmp_path: Path) -> None:
    target = tmp_path / "demo.py"
    target.write_text("one = 1\ntwo = 2\nthree = 3\n", encoding="utf-8")
    patch = """*** Begin Patch
*** Update File: demo.py
@@
-one = 1
+one = 10
@@
-three = 3
+three = 30
*** End Patch
"""

    result = apply_patch(tmp_path, patch)

    assert result.status == "success"
    assert result.changed_files == ["demo.py"]
    assert target.read_text(encoding="utf-8") == "one = 10\ntwo = 2\nthree = 30\n"


def test_apply_patch_multiple_files_and_add_file(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("value = 1\n", encoding="utf-8")
    patch = """*** Begin Patch
*** Update File: a.py
@@
-value = 1
+value = 2
*** Add File: docs/new.md
+# New
+
+created by patch
*** End Patch
"""

    result = apply_patch(tmp_path, patch)

    assert result.status == "success"
    assert result.changed_files == ["a.py", "docs/new.md"]
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "value = 2\n"
    assert (tmp_path / "docs" / "new.md").read_text(encoding="utf-8") == "# New\n\ncreated by patch\n"


def test_apply_patch_is_atomic_when_later_operation_fails(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("value = 1\n", encoding="utf-8")
    second.write_text("value = 2\n", encoding="utf-8")
    patch = """*** Begin Patch
*** Update File: first.py
@@
-value = 1
+value = 10
*** Update File: second.py
@@
-missing = 2
+value = 20
*** End Patch
"""

    result = apply_patch(tmp_path, patch)

    assert result.status == "failed"
    assert result.changed_files == []
    assert first.read_text(encoding="utf-8") == "value = 1\n"
    assert second.read_text(encoding="utf-8") == "value = 2\n"


def test_apply_patch_rejects_path_escape(tmp_path: Path) -> None:
    patch = """*** Begin Patch
*** Add File: ../outside.txt
+nope
*** End Patch
"""

    result = apply_patch(tmp_path, patch)

    assert result.status == "failed"
    assert "Invalid patch path" in result.stderr
    assert not (tmp_path.parent / "outside.txt").exists()


def test_apply_patch_rejects_duplicate_operations_for_same_file(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    patch = """*** Begin Patch
*** Update File: demo.py
@@
-value = 1
+value = 2
*** Update File: demo.py
@@
-value = 2
+value = 3
*** End Patch
"""

    result = apply_patch(tmp_path, patch)

    assert result.status == "failed"
    assert result.changed_files == []
    assert "multiple operations for the same file" in result.stderr
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "value = 1\n"


def test_apply_patch_rejects_add_existing_file(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    patch = """*** Begin Patch
*** Add File: demo.py
+value = 2
*** End Patch
"""

    result = apply_patch(tmp_path, patch)

    assert result.status == "failed"
    assert result.changed_files == []
    assert "already exists" in result.stderr
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "value = 1\n"
