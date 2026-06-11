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


def test_apply_patch_unified_diff_update(tmp_path: Path) -> None:
    target = tmp_path / "calc.py"
    target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    patch = """--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""
    result = apply_patch(tmp_path, patch)
    assert result.status == "success"
    assert result.changed_files == ["calc.py"]
    assert target.read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"


def test_apply_patch_unified_diff_add(tmp_path: Path) -> None:
    patch = """--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,2 @@
+def hello():
+    print("hello")
"""
    result = apply_patch(tmp_path, patch)
    assert result.status == "success"
    assert result.changed_files == ["new_file.py"]
    assert (tmp_path / "new_file.py").read_text(encoding="utf-8") == "def hello():\n    print(\"hello\")\n"


def test_apply_patch_fences(tmp_path: Path) -> None:
    # 1. Envelope inside ```diff
    target1 = tmp_path / "file1.py"
    target1.write_text("x = 1\n", encoding="utf-8")
    patch1 = """```diff
*** Begin Patch
*** Update File: file1.py
@@
-x = 1
+x = 2
*** End Patch
```"""
    result1 = apply_patch(tmp_path, patch1)
    assert result1.status == "success"
    assert target1.read_text(encoding="utf-8") == "x = 2\n"

    # 2. Unified diff inside ```patch
    target2 = tmp_path / "file2.py"
    target2.write_text("y = 10\n", encoding="utf-8")
    patch2 = """```patch
--- a/file2.py
+++ b/file2.py
@@ -1,1 +1,1 @@
-y = 10
+y = 20
```"""
    result2 = apply_patch(tmp_path, patch2)
    assert result2.status == "success"
    assert target2.read_text(encoding="utf-8") == "y = 20\n"

    # 3. Unified diff inside bare ```
    target3 = tmp_path / "file3.py"
    target3.write_text("z = 100\n", encoding="utf-8")
    patch3 = """```
--- a/file3.py
+++ b/file3.py
@@ -1,1 +1,1 @@
-z = 100
+z = 200
```"""
    result3 = apply_patch(tmp_path, patch3)
    assert result3.status == "success"
    assert target3.read_text(encoding="utf-8") == "z = 200\n"
