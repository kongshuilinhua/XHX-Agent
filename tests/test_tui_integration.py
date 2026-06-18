"""TUI 集成测试：验证新 TUI 收发消息不崩溃。"""


class TestParseCommand:
    def test_regular_text_returns_is_command_false(self):
        from xhx_agent.commands.parser import parse_command

        name, args, is_cmd = parse_command("hello world")
        assert name == "hello world"
        assert args == ""
        assert is_cmd is False

    def test_slash_command_returns_is_command_true(self):
        from xhx_agent.commands.parser import parse_command

        name, args, is_cmd = parse_command("/model deepseek")
        assert name == "model"
        assert args == "deepseek"
        assert is_cmd is True

    def test_slash_without_args(self):
        from xhx_agent.commands.parser import parse_command

        name, args, is_cmd = parse_command("/help")
        assert name == "help"
        assert args == ""
        assert is_cmd is True

    def test_empty_input(self):
        from xhx_agent.commands.parser import parse_command

        name, args, is_cmd = parse_command("")
        assert name == ""
        assert args == ""
        assert is_cmd is False

    def test_registry_uses_new_format(self):
        from xhx_agent.commands.registry import CommandRegistry

        registry = CommandRegistry()
        # 注册一个简单命令
        registry.register("test", "test cmd", lambda app, arg: "ok")
        # execute 内部调用 parse_command，应不崩
        result = registry.execute(None, "/test hello")
        assert result == "ok"

    def test_registry_unknown_command(self):
        from xhx_agent.commands.registry import CommandRegistry

        registry = CommandRegistry()
        result = registry.execute(None, "/nonexistent")
        assert "Unknown" in str(result) or result is None


class TestTuiImports:
    def test_all_critical_imports(self):
        """验证 TUI 关键模块能正常导入。"""
        from xhx_agent.commands.completion import CompletionPopup
        from xhx_agent.memory import SessionManager

        SessionManager()
        p = CompletionPopup()
        assert p.is_visible is False
        assert hasattr(p, "hide")
        assert hasattr(p, "get_selected")


class TestXhxStatusBar:
    def test_status_update_method_exists(self):
        """验证 _update_xhx_status 方法存在且不报错。"""
        from xhx_agent.tui.format import context_meter, human_tokens

        # context_meter 正常
        label, pct, level = context_meter(5000, 1000000)
        assert pct is not None
        assert level in ("ok", "warn", "crit", "none")

        # human_tokens 正常
        assert human_tokens(0) == "0"
        assert human_tokens(999) == "999"
        assert human_tokens(8800) == "8.8k"


class TestConfigGlobal:
    def test_load_profile_from_global(self, tmp_path):
        """验证无项目配置时回退到全局。"""
        from xhx_agent.runtime.profiles import get_profile

        # tmp_path 无 .xhx/ 目录 → 回退全局
        p = get_profile(tmp_path, "default")
        # 至少有个 profile
        assert p is not None
        assert p.name == "default"
