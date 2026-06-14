from pathlib import Path

from xhx_agent.tools.paths import resolve_with_scope


def test_resolve_with_scope(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    external = tmp_path / "external"
    external.mkdir()

    # 1. Relative path inside workspace
    res = resolve_with_scope(workspace, [], "src/file.py")
    assert res.in_scope is True
    assert res.target == workspace / "src" / "file.py"
    assert res.outside_root is None

    # 2. Absolute path inside workspace
    res = resolve_with_scope(workspace, [], str(workspace / "src/file.py"))
    assert res.in_scope is True
    assert res.target == workspace / "src" / "file.py"
    assert res.outside_root is None

    # 3. Absolute path outside workspace
    ext_file = external / "all-in-rag/README.md"
    res = resolve_with_scope(workspace, [], str(ext_file))
    assert res.in_scope is False
    assert res.target == ext_file
    # outside_root should be the parent directory of target
    assert res.outside_root == external / "all-in-rag"

    # 4. Path outside workspace, but parent is in allowed_dirs
    allowed = [external / "all-in-rag"]
    res = resolve_with_scope(workspace, allowed, str(ext_file))
    assert res.in_scope is True
    assert res.target == ext_file

    # 5. Path outside workspace, and a ancestor is in allowed_dirs
    # Let's test if subdirectories of allowed dirs are also allowed
    ext_sub_file = external / "all-in-rag/src/index.js"
    res = resolve_with_scope(workspace, allowed, str(ext_sub_file))
    assert res.in_scope is True
    assert res.target == ext_sub_file

    # 6. Escape using ..
    res = resolve_with_scope(workspace, [], "../external/all-in-rag/README.md")
    # resolved path should be absolute and since it is outside workspace, it should be in_scope=False
    assert res.in_scope is False
    assert res.target == (workspace / "../external/all-in-rag/README.md").resolve()

    # 7. Allowed_dirs check with .. escape inside workspace (still in_scope)
    res = resolve_with_scope(workspace, [], "src/../../workspace/file.py")
    assert res.in_scope is True
    assert res.target == workspace / "file.py"

def test_extract_glob_root(tmp_path):
    from xhx_agent.tools.paths import extract_glob_root
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert extract_glob_root(workspace, "*.py") == workspace.resolve()
    assert extract_glob_root(workspace, "src/*.py") == (workspace / "src").resolve()
    assert extract_glob_root(workspace, "../external/*.py") == (workspace / "../external").resolve()
    assert extract_glob_root(workspace, "D:\\all-in-rag\\*.py") == Path("D:\\all-in-rag").resolve()

