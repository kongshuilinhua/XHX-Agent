# MCP 远程传输（Streamable HTTP + SSE）+ 静态 token + 迁移到官方 SDK Implementation Plan

> **For agentic workers (Gemini):** 自包含、可冷启动。按 Task 顺序 TDD 推进，步骤用 `- [ ]`。完成交回，Claude 两段式审查 + 全量回归 + 真模型/真 server 联调 + 合并。
> **本文分两部分（本期交付）：Part A = 引入官方 SDK + 迁移 stdio；Part B = HTTP/SSE 远程 + 静态 token。Part C（完整 OAuth 流）明确后置、本期不实现，仅留接口余量。建议先 A 后 B。**

**已锁定决策：**
1. **用官方 `mcp` Python SDK**（stdio/SSE/Streamable HTTP/OAuth 客户端现成、spec 合规、安全），**stdio 也迁到 SDK 统一**——替换现有手写同步 `MCPClient`。
2. **async 桥接用 `anyio.from_thread.start_blocking_portal()`**（后台事件循环线程；SDK 本就是 anyio-based，这是把 async 库接进同步运行时的惯用法），让运行时其余部分保持同步。
3. **OAuth 分阶段**：本期 = Streamable HTTP/SSE + **静态 bearer token/API key**（覆盖一大批托管 server）；完整授权码+PKCE+本地回调+刷新 = **Part C 后续**。
4. **复用既有约定不破**：`mcp_<server>_<tool>` 命名空间、`mcp_` 前缀 CONFIRM 闸门、server 连不上 → emit `mcp_server_failed` 跳过、真实路径禁 mock、密钥不进 git。

**Goal:** 让 MCP 从"仅本地 stdio"扩到"也能连远程/托管 server"（Streamable HTTP 为主、HTTP+SSE 兼容），底层换成官方 SDK 以获得 spec 合规与安全，并为后续完整 OAuth 留好结构。

---

## 当前状态（关键事实，带 file:line）

- **手写同步 stdio 客户端**：`src/xhx_agent/skills/mcp.py` 的 `MCPClient`（`connect` 握手 `mcp.py:67`、`list_tools` `:145`、`call_tool` `:184`、`register_tools_to_registry` 走 `register_definition` `:255-305`、`read_line_with_timeout` 5s 硬超时 `:12`、`allow_mock`+mock 工具 `:34-65`）。**本计划整体替换为 SDK + 桥接。**
- **配置仅 stdio**：`src/xhx_agent/runtime/mcp_config.py` 的 `MCPServerConfig(name/command/args/env/transport:Literal["stdio"])`（`mcp_config.py:12-19`）；`load_mcp_servers(workspace)` 项目→全局级联（`:22-40`）。
- **app 接线 + 一个 worktree 隐患**：`src/xhx_agent/runtime/app.py` `run_task` 内 `servers = load_mcp_servers(self.workspace)`——但此处 `self.workspace` 已是 **worktree**（`app.py:151` 切换），而 `.xhx/` 被 gitignore、不进 worktree → **项目级 `.xhx/mcp.json` 运行时读不到**（与上一计划修过的 Tavily key 同一类 bug，靠回落全局才生效）。本计划基线修复：改用 `original_workspace`。`original_workspace` 在 `run_task` 内可得（`app.py:148`）。
- **CONFIRM 闸门已就位**：`decide_tool` 对 `mcp_` 前缀 CONFIRM 放行（`src/xhx_agent/safety/policy.py:61-69`），远程 MCP 工具同样走这条，**无需改策略**。
- **ToolContext 已有 `original_workspace`**（上一计划新增，`src/xhx_agent/tools/registry.py` ToolContext），可复用。
- **测试**：`tests/test_skills.py`、`tests/test_mcp_config.py`、`tests/test_runtime_mcp.py` 现针对手写客户端，需随迁移重写。

---

# Part A — 引入官方 SDK + anyio 桥接 + 迁移 stdio

**Architecture:** 新增 `MCPManager`（持有一个 `BlockingPortal` = 后台 anyio 事件循环线程）。每个配置的 server，在 portal 里 spawn 一个**长生命周期 task**：进入 `transport(...)` + `ClientSession(...)` 的 `async with`、`await session.initialize()`、把 session 存进 holder、置 ready 事件、然后 `await shutdown_event`（保持连接存活直到关闭）。`MCPManager` 对外暴露**同步** API：`connect_all()` / `list_tools(server)` / `call_tool(server, name, args)` / `close()`，内部用 `portal.call(...)` 桥接到 holder.session。`register_tools_to_registry` 改为基于 manager 的同步包装。

**Tech Stack:** 官方 `mcp` SDK（`mcp.ClientSession`、`mcp.client.stdio.stdio_client` + `StdioServerParameters`）、`anyio.from_thread.start_blocking_portal`、现有 `ToolRegistry.register_definition`、pytest + SDK 内存测试夹具（`mcp.shared.memory.create_connected_server_and_client_session`，或在测试里用 `FastMCP` 起一个真 stdio 子进程）。

## File Structure（Part A）

- `pyproject.toml` — 加依赖 `mcp>=1.0`（Gemini 装后**核对实际 client API 签名**：`streamablehttp_client` 返回 3 元组含 get_session_id；`ClientSession` 用法可能随版本微调）。`uv.lock` 同步。
- `src/xhx_agent/skills/mcp.py` — **重写**：删手写 JSON-RPC + mock，改 `MCPManager`（BlockingPortal + 每 server holder task）+ 同步 `connect_all/list_tools/call_tool/close` + `register_tools_to_registry`。
- `src/xhx_agent/runtime/app.py` — 用 `MCPManager` 替换 `MCPClient` 循环；`load_mcp_servers(original_workspace)`（修 worktree 隐患）；`finally` 调 `manager.close()`（停 portal 线程）。
- `src/xhx_agent/__init__.py` / `skills/__init__.py` — 导出从 `MCPClient` 改为 `MCPManager`（或同时保留薄别名，避免 `tests/test_package.py` 断言炸——按实际调整）。
- Tests:`tests/test_skills.py` 重写（SDK 内存/真子进程 server）、`tests/test_runtime_mcp.py` 重写。

## 关键设计约定（Part A · 必须遵守）

1. **桥接不死锁**：所有对 SDK session 的调用只在 portal 的 loop 里跑；同步侧只通过 `portal.call(...)`。**绝不**在 portal loop 内部再 `portal.call`。session 仅供 portal loop 使用。
2. **连接存活**：transport+session 的 `async with` 必须在一个**持续运行的 task** 里保持进入态（用 `shutdown_event` 挂起），不能开一次 op 关一次（那样每次都重握手/重 OAuth，破坏会话状态）。
3. **命名空间/闸门/降级不变**：工具名 `mcp_<server>_<tool>`；注册走 `register_definition`（带 schema，模型才看得见）；server 连接失败 → emit `mcp_server_failed` 跳过、不影响其余工具；超时可配（替换 5s 硬编码，默认如 30s）。
4. **真实路径禁 mock**：删除手写 mock 工具；测试用 SDK 内存 server 或真 `FastMCP` 子进程，**不得**让任何假工具进入生产路径（守红线）。
5. **生命周期收口**：`manager.close()` 必须置 shutdown_event 让所有 server task 干净退出、再停 portal；幂等、吞异常不挂主流程。修上一版"close 后共享 registry 残留陈旧 definition"的小问题：close 时把本 manager 注册过的 `mcp_*` 定义从 registry 注销（或 app 每 run 用全新 registry 快照——按现状择一，注销更省）。

### Task A1: MCPManager + BlockingPortal 桥接 + stdio（SDK）

**Files:** 改 `pyproject.toml`；重写 `src/xhx_agent/skills/mcp.py`;Test `tests/test_skills.py`

- [ ] **Step 1:写测试**（SDK 内存或真子进程 stdio server，暴露 1-2 个工具）：`manager.connect_all([cfg])` 后 `list_tools("srv")` 含该工具；`call_tool("srv","echo",{...})` 返回预期文本；`close()` 后 portal 线程退出、无悬挂线程。
- [ ] **Step 2:实现** `MCPManager`：`start_blocking_portal` 起后台 loop；每 server 一个 holder + spawn 持久 task（stdio 用 `stdio_client(StdioServerParameters(command,args,env))` → `ClientSession` → `initialize` → 存 session → 等 shutdown）；同步 `connect_all/list_tools/call_tool(超时)/close`。
- ✅ **Check:** `pytest tests/test_skills.py` 绿；无线程泄漏（`threading.enumerate()` 在 close 后不含残留）。

### Task A2: 工具注册（基于 manager）

**Files:** 改 `src/xhx_agent/skills/mcp.py`;Test `tests/test_skills.py`

- [ ] **Step 1:写测试** — `register_tools_to_registry(registry)` 后 `registry.definition("mcp_srv_echo")` 非空且进 `tool_schemas()`；两 server 同名工具 → `mcp_a_x`/`mcp_b_x` 不撞；runner 调用经 manager 返回结构化 `ToolExecutionResult`（成功/`isError`→failed）。
- [ ] **Step 2:实现** — manager 遍历 `list_tools`，对每个工具 `register_definition(ToolDefinition(name=mcp_<server>_<tool>, description, parameters=inputSchema, runner=<bridge to manager.call_tool>))`。
- ✅ **Check:** `pytest tests/test_skills.py` 绿。

### Task A3: app 接线 + worktree 修复 + 生命周期

**Files:** 改 `src/xhx_agent/runtime/app.py`、`__init__.py`/`skills/__init__.py`;Test `tests/test_runtime_mcp.py`

- [ ] **Step 1:写测试** — monkeypatch `load_mcp_servers` + 一个 fake/内存 manager → `run_task` 后 server 工具进 `app.tool_registry`；run 结束 `manager.close()` 被调；连接失败 server → emit `mcp_server_failed`、不影响内置工具、不抛。**关键**：断言 `load_mcp_servers` 收到的是 `original_workspace`（项目根）而非 worktree 路径。
- [ ] **Step 2:实现** — `run_task` 用 `MCPManager`；`load_mcp_servers(original_workspace)`；`finally` `manager.close()`。修包导出。
- ✅ **Check:** `pytest tests/test_runtime_mcp.py tests/test_package.py` 绿。

### Task A4: stdio 真 server 联调（Claude 验收）

- ✅ **Check（我来验收，真模型 + 真 server）:** 写真实 `.xhx/mcp.json`（`@modelcontextprotocol/server-filesystem`），DeepSeek profile → 模型 `tool_schemas` 含 `mcp_fs_*` → 真实调用成功；command 改错 → `mcp_server_failed` 且其余工具照常；确认迁移后 stdio 行为与迁移前一致（无回归）。

---

# Part B — HTTP/SSE 远程传输 + 静态 token

**Architecture:** `MCPServerConfig` 增 `transport: "http"|"sse"` + `url` + `headers` + 静态认证（`auth_token` / `auth_token_env`）。`MCPManager` 按 transport 选 `streamablehttp_client(url, headers=...)`（http，**当前标准**）或 `sse_client(url, headers=...)`（sse，**legacy 兼容**），其余（session/holder/桥接/注册）与 stdio 完全共用。静态 token 解析：`auth_token`（非空优先）→ `auth_token_env` 环境变量 → 无则不加 Authorization 头。token 来源同样走 `original_workspace` 配置或 env，**不进 git**。

**Tech Stack:** `mcp.client.streamable_http.streamablehttp_client`、`mcp.client.sse.sse_client`、pytest（用 `FastMCP` 起一个真 http MCP server 或 SDK 测试夹具拦截）。

## File Structure（Part B）

- `src/xhx_agent/runtime/mcp_config.py` — `MCPServerConfig`:`transport: Literal["stdio","http","sse"]="stdio"`；`command: str | None`（仅 stdio 必填）；新增 `url: str | None`、`headers: dict[str,str]={}`、`auth_token: str=""`、`auth_token_env: str=""`。pydantic validator：stdio 必有 command；http/sse 必有 url。
- `src/xhx_agent/skills/mcp.py` — manager 的 server task 按 transport 分支选 transport client；http/sse 注入 `Authorization: Bearer <token>` 头（有 token 时）。
- `README.md` / `README.zh-CN.md` / `docs` — `.xhx/mcp.json` 远程配置说明 + 示例 + “token 放 gitignored 的 .xhx 或 env、勿提交”。
- Tests:`tests/test_mcp_config.py`（新字段 + validator）、`tests/test_skills.py`（http transport 接通 + token 头）。

## 关键设计约定（Part B）

1. **传输共用上层**：http/sse 只换"怎么建 read/write 流"，session/holder/桥接/注册/命名空间/闸门**完全复用 Part A**，不另起一套。
2. **token 解析顺序**：config `auth_token`（非空）→ env `auth_token_env` → 无（不加认证头）。token 与 Tavily key 同规矩：值只存 gitignored `.xhx/config.json` 或 mcp.json（已 gitignore）或 env，**绝不**写进 docs/代码默认/测试夹具。
3. **transport 默认仍 stdio**：旧配置零改动照跑。
4. **超时/错误**：远程连接/请求超时可配；连不上同样 `mcp_server_failed` 跳过。

### Task B1: 配置扩展 + validator

**Files:** 改 `src/xhx_agent/runtime/mcp_config.py`;Test `tests/test_mcp_config.py`

- [ ] **Step 1:写测试** — http 配置（`transport:"http"`,`url`,`auth_token`）解析成功；缺 url 的 http → 校验报错；缺 command 的 stdio → 报错；旧 stdio 配置仍解析。
- [ ] **Step 2:实现** 字段 + `model_validator`。
- ✅ **Check:** `pytest tests/test_mcp_config.py` 绿。

### Task B2: manager 支持 http/sse + 静态 token

**Files:** 改 `src/xhx_agent/skills/mcp.py`;Test `tests/test_skills.py`

- [ ] **Step 1:写测试** — 用 `FastMCP` 起一个真 http MCP server（暴露 1 工具，校验请求头里带 `Authorization: Bearer <token>`）→ manager 以 `transport:"http"` 连上 → list/call 成功且服务端确实收到 token 头；sse 同理（如夹具成本可控）。
- [ ] **Step 2:实现** server task 按 transport 选 client + 注入 token 头。
- ✅ **Check:** `pytest tests/test_skills.py` 绿。

### Task B3: 文档 + 真实远程 server 联调（Claude 验收）

**Files:** 改 README / docs

- [ ] **Step 1:** 文档补远程配置示例（http + token），强调密钥不入库。
- ✅ **Check（我来验收）:** 用一个真实远程/托管（接受静态 token 的）MCP server 配 `.xhx/mcp.json` → 真模型成功调用其工具；无 token / token 错 → 干净失败、其余工具不受影响。

---

## 统一验收（Claude，两部分完成后）

- [ ] `pytest` 全量绿、无新增 flaky、**无线程泄漏**（manager close 后）。
- [ ] CI 四关全绿：`ruff check` / `ruff format` / `mypy` / `pytest cov≥80`。
- [ ] 真模型联调：stdio 真 server（迁移无回归）+ http 远程 server（静态 token）各调通一次。
- [ ] 安全回归：远程工具走 `mcp_` 前缀 CONFIRM 并记审计；token/密钥不在任何受跟踪文件（`grep` 自检）；连不上 server 不污染其余工具。
- [ ] 文档：README/xhx.md 补远程传输 + 静态 token 配置。

## Part C — 完整 OAuth（后续，本期不做）

- 用 SDK `mcp.client.auth.OAuthClientProvider`（授权码 + PKCE + discovery/动态注册），作为 `auth=` 传给 http/sse client。
- 本地回调 HTTP server 接 redirect；token 存 gitignored `.xhx/`（加密/权限）；刷新 token。
- 触发交互授权的 UX（CLI 打开浏览器 / TUI 提示）。
- 连 GitHub/Linear 等需交互授权的托管 connector。

## 不在本计划

- Resources / Prompts / Sampling 原语（仍只用 Tools）。
- `xhx mcp add/list` 管理 CLI（可另起小计划）。
