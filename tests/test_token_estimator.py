import sys
from unittest.mock import patch

from xhx_agent.context.compiler import _estimate_tokens


def test_token_estimate_with_tiktoken():
    text = "Hello, world! This is a test of tiktoken compilation."
    # 验证当 tiktoken 可用时，计数精确且合理
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    expected = len(enc.encode(text, disallowed_special=()))
    assert _estimate_tokens(text) == expected


def test_token_estimate_fallback():
    # 模拟 tiktoken 模块缺失导致导入失败的情形
    with patch.dict(sys.modules, {"tiktoken": None}), patch("xhx_agent.context.compiler._tiktoken_encoding", None):
        text = "Hello, world! 降级测试。"
        # 应当回退到基于字符的快速粗算
        val = _estimate_tokens(text)
        assert val > 0
        # 确认降级时估算正常
        assert isinstance(val, int)
