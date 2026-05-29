from pathlib import Path
import pytest
from xhx_agent.cli.completion import XhxCompleter

def test_completer_slash_commands(tmp_path):
    completer = XhxCompleter(tmp_path)
    # 补全斜杠命令前缀
    res1 = completer.get_completions("/ve")
    assert "/verify" in res1
    
    # 补全全部斜杠命令
    res2 = completer.get_completions("/")
    assert len(res2) >= 12
    assert "/help" in res2

def test_completer_paths(tmp_path):
    # 构造假工作区结构和文件
    workspace = tmp_path
    src_dir = workspace / "src" / "agent"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "app.py").touch()
    (workspace / "README.md").touch()
    
    completer = XhxCompleter(workspace)
    
    # 输入以 "src/" 开头的文件路径补全
    res = completer.get_completions("src/")
    assert "src/agent/app.py" in res
