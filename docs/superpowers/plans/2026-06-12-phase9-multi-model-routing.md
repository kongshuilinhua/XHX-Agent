# Phase 9 — 多模型路由 + fallback 降级

> 价值：成本优化（便宜模型做探索/压缩，强模型做规划/改代码）+ 健壮性（强模型失败/限流→降级）。
> 对应 ROADMAP §9.2。复用现有 profiles 系统（`.xhx/profiles.json`）。
>
> **工作流**：本切片**全部实现代码 + 全部测试都交 Gemini**；Claude 只出本计划（设计 + 接口签名 + 语义边界 + check 点）并验收。下方签名是契约，照此实现。

## 先读
`src/xhx_agent/runtime/profiles.py`（ModelProfile / get_profile）、`src/xhx_agent/runtime/config.py`（ProjectConfig）、`src/xhx_agent/models/__init__.py`（build_chat_client）、`src/xhx_agent/models/types.py`（ModelClientError）、`src/xhx_agent/orchestrators/{loop,plan,graph,subagent}.py`（现都 `build_chat_client(ctx.profile)`）。

## 边界（不许动）
- 不改 `build_chat_client` 的签名与现有行为（路由在其之上叠一层）。
- **零行为变更铁律**：routing 为空（默认）时，所有路径必须与今天**完全一致**——现有 356 passed 必须保持。
- mock profile 行为不变；不引新第三方依赖。

## 设计（契约签名，照此实现）

### 1) 配置 schema —— `runtime/config.py`
新增 `RoutingConfig` 并挂到 `ProjectConfig`（**defaulted，向后兼容**：老 `config.json` 无 `routing` 字段也能 load）：
```python
class RoutingConfig(BaseModel):
    roles: dict[str, str] = Field(default_factory=dict)   # role -> profile name，如 {"explore": "cheap"}
    fallback: list[str] = Field(default_factory=list)     # 失败时按序尝试的 profile 名

class ProjectConfig(BaseModel):
    ...
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
```

### 2) 路由 + fallback —— 新文件 `models/routing.py`
```python
def resolve_profile_for_role(workspace, role: str, base_profile_name: str) -> ModelProfile:
    """role 在 config.routing.roles 里→用映射的 profile；否则→base_profile_name。"""

class FallbackChatClient:
    """按序包住多个 client；某个抛 ModelClientError 就试下一个，全失败抛最后一个 error。
    保持 chat(messages, tools) 接口。可选 on_fallback(idx, err) 回调用于发事件/trace。"""
    def __init__(self, clients: list, on_fallback=None): ...
    def chat(self, messages, tools): ...

def build_routed_client(workspace, *, role: str, base_profile_name: str, event_callback=None):
    """解析 role 的主 profile + config.routing.fallback 链，build 出客户端。
    无 fallback 时返回**单个**普通 client（不包 wrapper）；有 fallback 时返回 FallbackChatClient。"""
```
语义细节：
- `FallbackChatClient.chat`：遍历 clients，`try client.chat(...) -> return`；`except ModelClientError as e: 记 e、调 on_fallback、continue`；循环结束抛最后的 e。**主成功则后续 client 绝不调用**（测试要数调用次数）。
- `build_routed_client`：primary = `resolve_profile_for_role(...)`；fallback_names = `load_config(workspace).routing.fallback`（去掉与 primary 同名的）；`clients = [build_chat_client(primary)] + [build_chat_client(get_profile(workspace, n)) for n in fallback_names]`；`len==1 → clients[0]`，否则 `FallbackChatClient(clients, on_fallback=…)`。
- on_fallback 里用 `emit_event(event_callback, "model_fallback", …)`（events 现有机制）。

### 3) 接线 —— 四个 orchestrator/subagent
把各处 `client = build_chat_client(ctx.profile)` 换成 `build_routed_client(ctx.original_workspace, role=<见下>, base_profile_name=ctx.profile.name)`，并把 `event_callback=ctx.event_callback` 传入：
- `loop.py` → role `"loop"`
- `plan.py` → role `"plan"`
- `graph.py` → role `"graph"`（三个节点共用这一个 client，本切片不细分 coordinate/worker/review）
- `subagent.py` → role `"explore"`（**头牌 demo：把 explore 映射到便宜 profile**）
> `chat_and_count` 不变——它只在外面调一次 `client.chat`，fallback 在 client 内部发生，token 只计一次。

## Checkpoints（Gemini 写测试覆盖）
1. **配置向后兼容**：不含 `routing` 的 `config.json` 能 `load_config` 成功且 `routing.roles=={}`、`fallback==[]`；显式带 routing 的能正确解析。
2. **resolve_profile_for_role**：role 命中映射→返回映射 profile；未命中→返回 base profile；映射到不存在的 profile 名→`get_profile` 抛 `ValueError`。
3. **FallbackChatClient**：主 client 抛 `ModelClientError`→返回次 client 的结果且 on_fallback 被调用；**主成功→次 client 零调用**（计数验证）；全部失败→抛最后一个 error。
4. **build_routed_client**：空 routing→返回单个普通 client（非 wrapper，可用 `isinstance`/类型断言）；配了 fallback→返回 `FallbackChatClient` 且内部 client 数正确；role 映射解析出正确 primary。
5. **零行为变更**：默认（空 routing）下 mock 跑 `--mode loop`/`--mode plan` 仍成功；**现有全量套件全绿**。
6. **role 接线生效**：配置 `routing.roles={"explore": "<某 profile>"}` 后，dispatch 出的 explore 子 agent 实际用该 profile（建议：monkeypatch `build_chat_client` 记录收到的 profile.name，跑一个会触发 dispatch 的 mock loop，断言 explore 用了映射 profile、主 loop 用 base）。
7. **fallback 端到端（确定性）**：primary = 一个会失败的 openai-compatible profile（如 `api_key_env` 指向未设置的变量→`ModelClientError`），`routing.fallback=["mock"]`；经 `build_routed_client` 的 `chat` 自动降级到 mock 并成功返回。
8. **全绿**：`PYTHONUTF8=1 uv run pytest -q`（356→更多 passed）+ `ruff check .` 全绿，零回归。

## 纪律
TDD（红→绿→回归→提交）；命令前置 `PYTHONUTF8=1`；ruff B023 默认参数绑定循环变量；只在分支 `phase9-multi-model-routing` 提交；全绿后 `git push origin phase9-multi-model-routing`。报告：新增 commit `git log --oneline`、pytest 统计行、ruff 结果、每个 check 点一句话如何满足。
