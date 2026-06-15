import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from xhx_agent.runtime.mcp_config import MCPServerConfig, load_mcp_servers
from xhx_agent.runtime.paths import xhx_dir


def test_load_mcp_servers_empty(tmp_path: Path) -> None:
    # No config files
    servers = load_mcp_servers(tmp_path)
    assert servers == []


def test_load_mcp_servers_project_only(tmp_path: Path) -> None:
    # Project-level config only
    mcp_dir = xhx_dir(tmp_path)
    mcp_dir.mkdir(parents=True, exist_ok=True)
    mcp_file = mcp_dir / "mcp.json"

    data = {
        "servers": [{"name": "fs", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "d:/"]}]
    }
    mcp_file.write_text(json.dumps(data), encoding="utf-8")

    servers = load_mcp_servers(tmp_path)
    assert len(servers) == 1
    server = servers[0]
    assert server.name == "fs"
    assert server.command == "npx"
    assert server.args == ["-y", "@modelcontextprotocol/server-filesystem", "d:/"]
    assert server.transport == "stdio"
    assert server.env == {}


def test_load_mcp_servers_cascade_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Global config path setup via XHX_HOME
    global_dir = tmp_path / "global_home"
    global_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XHX_HOME", str(global_dir))

    # Write to global config
    global_mcp_file = global_dir / "mcp.json"
    global_data = {"servers": [{"name": "global-server", "command": "python", "args": ["-m", "mcp_server"]}]}
    global_mcp_file.write_text(json.dumps(global_data), encoding="utf-8")

    # Scenario 1: Only global config exists, project config is missing
    proj_dir = tmp_path / "proj"
    servers = load_mcp_servers(proj_dir)
    assert len(servers) == 1
    assert servers[0].name == "global-server"

    # Scenario 2: Project config exists too. Project should take priority.
    proj_mcp_dir = xhx_dir(proj_dir)
    proj_mcp_dir.mkdir(parents=True, exist_ok=True)
    proj_mcp_file = proj_mcp_dir / "mcp.json"
    proj_data = {"servers": [{"name": "project-server", "command": "node"}]}
    proj_mcp_file.write_text(json.dumps(proj_data), encoding="utf-8")

    servers = load_mcp_servers(proj_dir)
    assert len(servers) == 1
    assert servers[0].name == "project-server"


def test_load_mcp_servers_http_with_token(tmp_path: Path) -> None:
    mcp_dir = xhx_dir(tmp_path)
    mcp_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "servers": [
            {"name": "remote", "transport": "http", "url": "https://api.example/mcp", "auth_token": "tok"},
            {"name": "legacy", "transport": "sse", "url": "https://api.example/sse"},
        ]
    }
    (mcp_dir / "mcp.json").write_text(json.dumps(data), encoding="utf-8")

    servers = load_mcp_servers(tmp_path)
    assert len(servers) == 2
    http = servers[0]
    assert http.transport == "http"
    assert http.url == "https://api.example/mcp"
    assert http.auth_token == "tok"
    assert http.command is None
    assert servers[1].transport == "sse"


def test_mcp_config_validator_http_requires_url() -> None:
    with pytest.raises(ValidationError):
        MCPServerConfig(name="r", transport="http")


def test_mcp_config_validator_stdio_requires_command() -> None:
    with pytest.raises(ValidationError):
        MCPServerConfig(name="s", transport="stdio")
