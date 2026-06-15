# MCP 接线 + WebFetch + WebSearch(Tavily) Implementation Plan

> **For agentic workers (Gemini):** 自包含、可冷启动。按 Task 顺序 TDD 推进,步骤用 `- [ ]`。完成交回,Claude 两段式审查 + 全量回归 + 真模型联调 + 合并。
> **本文分三部分,可独立交付:Part A = MCP 接线;Part B = WebFetch;Part C = WebSearch(Tavily)。建议顺序 A → B → C**(B 先建 `network` 工具维度,C 复用它)。

**Goal:**
1. **Part A**:把**已存在但未接线**的 MCP 客户端(`src/xhx_agent/skills/mcp.py`)接入运行时——读 `.xhx/mcp.json` 配置 → 连接 stdio server → 把其工具**带 schema 注册**进工具表,让真模型能在 `tool_schemas` 里看到并调用。修三个坑(schema 注册缺失 / mock 污染真实运行 / 多 server 撞名)。
2. **Part B**:新增内置工具 `web_fetch`(抓 URL → HTML 转 Markdown → 截断),并为 `ToolDefinition` 引入 `network: bool` 维度,经 `decide_tool` 走 CONFIRM 闸门。核心是工具内 **SSRF 黑名单**。
3. **Part C**:新增 `web_search`(单一 provider = Tavily,REST via httpx),key 存 `.xhx/config.json`(已 gitignore),缺 key 明确报"未配置"不造假。

**根因(为什么要做):** 项目目前无任何联网能力(`search` 工具是仓库内 ripgrep,非 web);MCP 客户端代码齐全(连接/握手/`tools/list`/`tools/call`/动态注册 `mcp_` 前缀 + mock)但**没有任何 cli/config/app 实例化它**,`default_tool_registry()` 不含 MCP,只在 `tests/` 用。对齐 `ROADMAP.md` 第 8 节(MCP 优先级 > web;web 因 SSRF/key 列最低)。

---

## 当前状态(关键事实,带 file:line)

- **MCP 客户端齐全但未接线**:`src/xhx_agent/skills/mcp.py` 有 `MCPClient`(`connect` 握手 `mcp.py:32-60`、`list_tools` `:100-120`、`call_tool` `:122-183`、`register_tools_to_registry` `:185-227`)。引用仅见 `__init__.py`(导出)与 `tests/`,**无运行时实例化**。
- **坑①:注册不带 schema** → 模型看不见。`register_tools_to_registry`(`mcp.py:227`)调 `registry.register(tool_name, runner)`,**只塞 runner**。但喂给模型的 `tool_schemas()` 只遍历 `_definitions`(`src/xhx_agent/tools/registry.py:346-351`),`register`(`registry.py:331-332`)不写 `_definitions`。**结果:MCP 工具能跑但模型 schema 里没有 → 永不被调用。** 必须改走 `register_definition`(`registry.py:334-337`,构造 `ToolDefinition`)。
- **坑②:mock 会污染真实运行**。`MCPClient.__init__` 无 command 默认 `is_mock=True`(`mcp.py:11-12`);`connect` 异常静默 fallback mock(`mcp.py:57-60`);`_send_request` 异常也 fallback(`mcp.py:85-88`),会吐假工具 `mcp_fetch_weather`/`mcp_calculate`(`mcp.py:15-30`、`:122-172`)。违反「TUI 展示用真实值/禁 mock 冒充」红线。
- **坑③:多 server 撞名**。工具名仅 `mcp_<tool>` 单前缀(`mcp.py:108-110`),两个 server 各有 `search` 会互相覆盖。
- **安全闸门已对 `mcp_` 放行**:`decide_tool`(`src/xhx_agent/safety/policy.py:48-56`)对 `mcp_`/`custom_` 前缀返回 CONFIRM allow(以 agent 权限运行,无沙箱)。`kernel.execute_tool` 调 `decide_tool`(`src/xhx_agent/safety/kernel.py:67-71`),仅 `decision=="deny"` 短路;路径越界预检只覆盖 `read_file`/`search`/`apply_patch`(`kernel.py:81-106`)——web/MCP 无路径参数,不受影响。
- **ToolDefinition / 注册表**:dataclass 在 `registry.py:163-171`(字段 `read_only`/`destructive`/`is_command`/`runner`);`tool_schemas` `:346-351`;`default_tool_registry` `:377-381`。
- **config**:`ProjectConfig`(`src/xhx_agent/runtime/config.py:23-39`);`load_config` 项目级 `.xhx/config.json` → 全局 `~/.xhx/config.json` → 默认级联(`config.py:84-93`)。
- **profiles 的 secret 模式**:`ModelProfile.api_key_env` 存**环境变量名**,值在 env(`src/xhx_agent/runtime/profiles.py:18`)。本计划 Tavily key 改为**直接存 `.xhx/config.json`**(已 gitignore,见 `.gitignore:126`),并保留"值空则读 env"兜底。
- **app 运行入口**:`RuntimeApp.__init__` 建 `self.tool_registry = tool_registry or default_tool_registry()`(`src/xhx_agent/runtime/app.py:108-110`);`run_task` 在 `WorktreeContext` 内建 kernel(`app.py:149-154`)。`ToolContext` 构造处在 `app.py` 注入 `permission_mode`/`allowed_dirs`(约 `app.py:174` 附近)。

---

# Part A — MCP 接线

**Architecture:** 新增 `.xhx/mcp.json` loader → `run_task` 起 kernel 前,按配置实例化各 `MCPClient`、`connect()`、`register_tools_to_registry(self.tool_registry)`(带 schema、带 server 命名空间);`WorktreeContext` 退出时 `close()` 所有 client。真实路径 **mock 默认关**,server 连不上 → 记一条事件 + 跳过该 server(不污染其余工具)。

**Tech Stack:** 复用现有 `MCPClient`、`ToolRegistry.register_definition`、pydantic、`load_config` 级联、事件系统、pytest。

## File Structure(Part A)

- `src/xhx_agent/runtime/mcp_config.py` — **新增** `MCPServerConfig`(`name/command/args/env/transport`) + `load_mcp_servers(workspace) -> list[MCPServerConfig]`(读 `.xhx/mcp.json`,项目级优先、回落全局,缺文件返回 `[]`)。
- `src/xhx_agent/skills/mcp.py` — 修三坑:`register_tools_to_registry` 改走 `register_definition`(构造 `ToolDefinition`,`inputSchema`→`parameters`);`MCPClient` 加 `allow_mock: bool = False`(真实路径关 mock);命名空间 `mcp_<server>_<tool>`;`_send_request` 读超时。
- `src/xhx_agent/runtime/app.py` — `run_task` 内:`load_mcp_servers` → 实例化/connect/注册 → `try/finally` 关闭;失败 emit `mcp_server_failed` 事件并跳过。
- `src/xhx_agent/runtime/events.py` — (如需)加 `mcp_connected` / `mcp_server_failed` 事件类型。
- `tests/test_skills.py` — 扩充:注册后 `registry.definition("mcp_<server>_<tool>")` 非空且进 `tool_schemas()`;命名空间含 server 名;`allow_mock=False` 且无 command → 连接失败**抛/标记失败而非吐 mock 工具**。

## 关键设计约定(Part A · 必须遵守)

1. **schema 必达**:MCP 工具必须经 `register_definition` 进 `_definitions`,否则模型 `tool_schemas` 看不见。注册时构造 `ToolDefinition(name=..., description=..., parameters=t["inputSchema"], runner=<closure>)`;`name` 走 `mcp_<server>_<tool>`。`decide_tool` 已对 `mcp_` 前缀 CONFIRM 放行,无需改策略。
2. **真实路径禁 mock**:`load_mcp_servers` 拿到的真实 server 一律 `MCPClient(command=..., allow_mock=False)`。`connect` 失败**不 fallback mock**,改为 raise/返回失败标志 → app 层 catch → emit `mcp_server_failed` + 跳过。mock 仅测试显式 `allow_mock=True` 时启用。
3. **server 命名空间**:`list_tools`/`call_tool` 的工具名统一 `mcp_<server>_<original>`;`call_tool` 内剥前缀还原 `original_name` 时按 server 名正确还原(不能只剥 `mcp_`)。
4. **生命周期**:client 在一次 `run_task` 内连接、`finally` 关闭(子进程不泄漏)。`register_definition` 按 name 幂等覆盖,多次 run 重注册无副作用。
5. **看门狗**:`_send_request` 的 `stdout.readline()` 加超时(线程/`select` 或设 server 进程超时),防一个卡死的 server 挂死整轮。

---

### Task A1: `.xhx/mcp.json` 配置 loader

**Files:** 新增 `src/xhx_agent/runtime/mcp_config.py`;Test `tests/test_mcp_config.py`(新增)

- [ ] **Step 1:写测试** — 写一个 tmp `.xhx/mcp.json`(`{"servers":[{"name":"fs","command":"npx","args":["-y","x"]}]}`)→ `load_mcp_servers` 返回 1 条,`transport` 默认 `"stdio"`,`env` 默认 `{}`;缺文件 → `[]`;项目级存在时不读全局。
- [ ] **Step 2:实现** `MCPServerConfig(BaseModel)`(`name:str`、`command:str`、`args:list[str]=[]`、`env:dict[str,str]={}`、`transport:Literal["stdio"]="stdio"`)+ `load_mcp_servers(workspace)`(仿 `config.py:84-93` 级联;路径 `xhx_dir(workspace)/"mcp.json"` 与 `global_xhx_dir()/"mcp.json"`)。
- ✅ **Check:** `pytest tests/test_mcp_config.py` 绿。

### Task A2: 修 `register_tools_to_registry`(schema + 命名空间 + 关 mock + 超时)

**Files:** 改 `src/xhx_agent/skills/mcp.py`;Test `tests/test_skills.py`(扩充)

- [ ] **Step 1:写测试** —
  - 用一个 fake server(monkeypatch `list_tools` 返回 `[{"name":"search","inputSchema":{...}}]`,server 名 `"demo"`)→ `register_tools_to_registry(registry)` 后 `registry.definition("mcp_demo_search")` 非空,且其出现在 `registry.tool_schemas()` 里。
  - 两个 server 各有 `search` → 注册成 `mcp_a_search` / `mcp_b_search`,不互相覆盖。
  - `MCPClient(command=None, allow_mock=False).connect()` / `list_tools()` → **不返回** mock 工具(抛或返回空 + 失败标志);`allow_mock=True` 时才返回 mock(保留给测试)。
- [ ] **Step 2:实现** — `register_tools_to_registry` 改用 `register_definition(ToolDefinition(name=mcp_<server>_<tool>, description=..., parameters=inputSchema, runner=make_runner(...)))`;`MCPClient` 加 `allow_mock=False` 默认,`is_mock` 仅在 `allow_mock and not command` 时为真,`connect`/`_send_request` 失败不再静默转 mock(当 `allow_mock=False` 时 raise/标记 failed);工具名全程带 server 命名空间,`call_tool` 按命名空间正确还原 `original_name`;`_send_request` 加读超时。
- ✅ **Check:** `pytest tests/test_skills.py` 绿;旧 mock 测试改成显式 `allow_mock=True`。

### Task A3: app 接线 + 生命周期

**Files:** 改 `src/xhx_agent/runtime/app.py`、(如需)`src/xhx_agent/runtime/events.py`;Test `tests/test_runtime_app.py`(扩充)

- [ ] **Step 1:写测试** — monkeypatch `load_mcp_servers` 返回一条假 server + monkeypatch `MCPClient` 为可控 fake → `run_task` 后该 server 工具进了 `app.tool_registry`;run 结束 fake 的 `close()` 被调用;连接失败的 server → emit `mcp_server_failed`、**不影响内置工具可用**、不抛。
- [ ] **Step 2:实现** — `run_task` 内(`app.py:149` worktree 激活后、`app.py:154` 建 kernel 前):`servers = load_mcp_servers(self.workspace)`;逐个 `client = MCPClient(command=[s.command,*s.args], allow_mock=False)`,`try: client.connect(); client.register_tools_to_registry(self.tool_registry); clients.append(client)` `except` → emit `mcp_server_failed` 跳过;在外层 `finally` 里 `for c in clients: c.close()`。
- ✅ **Check:** `pytest tests/test_runtime_app.py` 绿。

### Task A4: 真模型联调(Claude 验收)

- ✅ **Check(我来验收,真模型 + 真 server):** 写一个真实 `.xhx/mcp.json`(如 `@modelcontextprotocol/server-filesystem`),DeepSeek profile 启动 → 模型 `tool_schemas` 含 `mcp_<server>_*` → 提一个需该工具的任务 → 真实调用成功;把 command 改错 → 该 server 报 `mcp_server_failed`、其余工具照常、无假 weather/calculate 漏出。

---

# Part B — WebFetch + `network` 工具维度

**Architecture:** 为 `ToolDefinition` 加 `network: bool` 维度;`decide_tool` 增 `network` 入参 → CONFIRM 放行(审计可见);`kernel.execute_tool` 调用处传 `network=bool(d and d.network)`。`web_fetch` 工具:校验 URL → SSRF 黑名单 → 抓取(限大小/超时/重定向逐跳校验)→ HTML 转 Markdown → 截断到预算内。**真正的护栏是工具内 SSRF 校验**(CONFIRM 在 kernel 里实际是 allow+记审计,不二次弹框,同 `mcp_`/`apply_patch`)。

**Tech Stack:** `httpx`(同步)、`markdownify`+`beautifulsoup4`(或 `html2text`)、`ipaddress`(SSRF 判定)、pytest(`respx`/monkeypatch 拦 HTTP)。**先确认 `pyproject.toml` 是否已有 httpx**,缺则加依赖。

## File Structure(Part B)

- `src/xhx_agent/tools/web.py` — **新增** `web_fetch(url, prompt=None, max_bytes=...) -> str` + SSRF 校验 `_is_safe_url(url) -> (ok, reason)`。
- `src/xhx_agent/tools/registry.py` — `ToolDefinition` 加 `network: bool = False`;新增 `_run_web_fetch` runner + `web_fetch` 的 `ToolDefinition(network=True, read_only=False, destructive=False)`,并入 `TOOL_DEFINITIONS`。
- `src/xhx_agent/safety/policy.py` — `decide_tool(..., network: bool = False)`:`read_only`→SAFE、`destructive`→CONFIRM、**`network`→CONFIRM(新)**、`mcp_/custom_`→CONFIRM、否则 deny。
- `src/xhx_agent/safety/kernel.py` — `execute_tool` 里 `decide_tool(...)` 调用补 `network=bool(d and d.network)`(`kernel.py:67-71`)。
- `pyproject.toml` — 加 `httpx`/`markdownify`/`beautifulsoup4`(按已有情况)。
- Tests:`tests/test_web_tools.py`(SSRF + 抓取转换)、`tests/test_safety.py`(network 定级)。

## 关键设计约定(Part B · 必须遵守)

1. **SSRF 黑名单是硬护栏**:仅允许 `http`/`https`;解析 host 后,凡命中 `ipaddress` 的 `is_private`/`is_loopback`/`is_link_local`(覆盖云元数据 `169.254.169.254`)/`is_reserved`/`is_multicast` 一律拒;**禁用 httpx 自动重定向,手动逐跳重新校验**每个 redirect target(防重定向绕过)。限响应大小(流式读、超 `max_bytes` 截断)、连接/读超时。DNS rebinding 列为已知局限(v1 不防)。
2. **network ≠ read_only**:`web_fetch` 标 `network=True`、`read_only=False`。因此在 plan 模式只读阶段(`read_only_phase`)它会被拦(`kernel.py:46`)——v1 接受此行为(规划期不联网),如需后续再放开。
3. **CONFIRM 自动放行**:network 工具经 `decide_tool` 得 CONFIRM allow,`kernel` 不二次弹框(与 `mcp_`/`apply_patch` 一致),审计 `policy_decision` 仍落一条。安全靠 SSRF,不靠人工确认。
4. **输出受预算**:转 Markdown 后截断到 `context.max_file_bytes` 同量级,避免整页塞爆上下文。便宜模型"二次提取"**不在本期**(留 P2,接多模型路由)。

---

### Task B1: `network` 维度 + 策略 + kernel 接线

**Files:** 改 `src/xhx_agent/tools/registry.py`、`src/xhx_agent/safety/policy.py`、`src/xhx_agent/safety/kernel.py`;Test `tests/test_safety.py`(扩充)

- [ ] **Step 1:写测试** — `decide_tool("web_fetch", network=True)` → `decision=="allow"` 且 `risk==CONFIRM`;`network=False` 且非只读/破坏/动态前缀 → `deny`(回归不破)。
- [ ] **Step 2:实现** — `ToolDefinition` 加 `network: bool = False`;`decide_tool` 加 `network` 分支(置于 `destructive` 之后、`mcp_` 之前);`kernel.execute_tool` 调用处补 `network=bool(d and d.network)`。
- ✅ **Check:** `pytest tests/test_safety.py tests/test_safety_kernel.py` 绿。

### Task B2: `web_fetch` 工具 + SSRF

**Files:** 新增 `src/xhx_agent/tools/web.py`;改 `registry.py`(注册);Test `tests/test_web_tools.py`

- [ ] **Step 1:写测试** —
  - `_is_safe_url`:`http://127.0.0.1`、`http://localhost`、`http://169.254.169.254`、`http://10.0.0.1`、`http://192.168.1.1`、`file:///etc/passwd`、`ftp://x` → 全部 `ok=False`;`https://example.com` → `ok=True`。
  - 抓取(monkeypatch/respx 假响应):给一段 HTML → 返回含正文文本的 Markdown,脚本/样式被剥;超 `max_bytes` 被截断;重定向到 `http://127.0.0.1` → 被拒。
- [ ] **Step 2:实现** `web.py`:`_is_safe_url`(scheme + `ipaddress` 判定);`web_fetch`(httpx 禁自动重定向、手动逐跳校验、流式限大小、超时、HTML→Markdown)。`registry.py` 加 `_run_web_fetch` + `ToolDefinition(name="web_fetch", network=True, parameters={url, prompt?})`。
- ✅ **Check:** `pytest tests/test_web_tools.py` 绿;`tool_schemas()` 含 `web_fetch`。

---

# Part C — WebSearch(Tavily)

**Architecture:** `web_search(query)` → 读 `.xhx/config.json` 的 `web_search.tavily_api_key`(空则读 `TAVILY_API_KEY` env)→ Tavily REST(`POST https://api.tavily.com/search`)→ 返回 标题+摘要+URL 列表。缺 key → 返回 `status` 明确"未配置 Tavily key",不造假、不空跑。同样 `network=True` 走 CONFIRM。

**Tech Stack:** httpx(复用 Part B)、Tavily REST(不引 SDK)、pytest(monkeypatch 拦 HTTP)。

## File Structure(Part C)

- `src/xhx_agent/runtime/config.py` — 加 `WebSearchConfig(provider="tavily", tavily_api_key="", tavily_api_key_env="TAVILY_API_KEY", max_results=5)`;`ProjectConfig` 加 `web_search: WebSearchConfig = WebSearchConfig()`。
- `src/xhx_agent/tools/web.py` — 加 `web_search(query, api_key, max_results) -> list[dict]`(Tavily REST)。
- `src/xhx_agent/tools/registry.py` — `_run_web_search` runner(从 `load_config(context.workspace).web_search` 取 key,空则 `os.environ`)+ `ToolDefinition(name="web_search", network=True, parameters={query})`。
- `src/xhx_agent/runtime/app.py` — 无须改(走 config)。
- Tests:`tests/test_web_tools.py`(扩充)、`tests/test_config.py`(新字段)。

## 关键设计约定(Part C)

1. **key 取值顺序**:`config.web_search.tavily_api_key`(非空优先)→ 环境变量 `tavily_api_key_env`。两者皆空 → 工具返回 `status="failed"`,summary="未配置 Tavily API key",**不发请求**。
2. **key 不进 git**:实际 key 写 `.xhx/config.json`(已 gitignore `.gitignore:126`),**不得**写进任何 `docs/`、测试夹具或代码默认值。`write_default_config` 写出的 `tavily_api_key` 必须为空串。
3. **真实返回**:测试用 monkeypatch 假 Tavily 响应;**不得**用 mock 结果冒充真实(守红线)。

---

### Task C1: WebSearchConfig

**Files:** 改 `src/xhx_agent/runtime/config.py`;Test `tests/test_config.py`

- [ ] **Step 1:写测试** — 默认 `ProjectConfig().web_search.provider=="tavily"`、`tavily_api_key==""`、`max_results==5`;`write_default_config` 写出含空 `tavily_api_key` 的 `web_search` 段;读回一致。
- [ ] **Step 2:实现** `WebSearchConfig` + `ProjectConfig.web_search` 字段。
- ✅ **Check:** `pytest tests/test_config.py` 绿。

### Task C2: `web_search` 工具(Tavily)

**Files:** 改 `src/xhx_agent/tools/web.py`、`registry.py`;Test `tests/test_web_tools.py`

- [ ] **Step 1:写测试** — monkeypatch Tavily 响应 → `web_search` 返回结构化 标题/摘要/URL;无 key(config 空 + env 空)→ runner 返回 `status="failed"` 且未发 HTTP。
- [ ] **Step 2:实现** `web_search`(httpx POST Tavily)+ `_run_web_search`(取 key 顺序见约定)+ `ToolDefinition(network=True)`。
- ✅ **Check:** `pytest tests/test_web_tools.py` 绿。

### Task C3: 真模型 + 真 key 联调(Claude 验收)

- ✅ **Check(我来验收):** 把真 Tavily key 填进 `.xhx/config.json` → 真模型提一个需联网的问题 → `web_search` 返回真实结果 → 模型对感兴趣的 URL `web_fetch` 拿正文 → 给出带来源的回答;清空 key → 工具明确报"未配置"而非空/假结果。

---

## 验收(Claude 统一回归,三部分都完成后)

- [ ] `pytest`(全量)绿,无新增 flaky。
- [ ] CI 四关(`ruff check` / `ruff format` / `mypy` / `pytest cov≥80`)全绿(见既有 CI 雷区:pyc 陈旧、写死路径)。
- [ ] **真模型联调**(DeepSeek profile,控制台 UTF-8):MCP 真 server 调用通;`web_search`→`web_fetch` 带来源回答通。
- [ ] **安全回归**:`web_fetch` 对 `127.0.0.1`/`169.254.169.254`/私网/`file://`/重定向到内网 全部拒;无 key 时 `web_search` 不发请求只报未配置;无任何 mock 工具漏进真实运行。
- [ ] 文档:`README`/`xhx.md` 补 `.xhx/mcp.json`、`web_search`/`web_fetch`、Tavily key 配置说明。

## 不在本计划(后续 P2)

- WebFetch 的**便宜模型二次提取**(接 ROADMAP 多模型路由)。
- MCP 的 **SSE/HTTP 传输**与远程 connector(本期仅 stdio)。
- web 工具在 plan **只读规划阶段**放开(本期被 `read_only_phase` 拦)。
- 搜索 provider 抽象/多 provider(本期单一 Tavily)。
