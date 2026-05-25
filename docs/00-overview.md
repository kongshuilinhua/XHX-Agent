# 项目概览

xhx-agent 是一个计划中的本地编码 Agent。它运行在用户机器上，通过 CLI/REPL 交互，读取项目文件、搜索代码、运行命令、生成补丁、验证修改，并输出可追溯的任务报告。

项目目标是做成一个类似 HPD-Agent 的完整编码 Agent，但核心主线不是单独的证据图，而是一个 **上下文预算驱动的 Agent Runtime**。它要解决完整编码 Agent 最容易失控的几个问题：上下文爆炸、工具执行不安全、验证不足、历史污染和失败无法复盘。

## 定位

xhx-agent 是一个上下文预算驱动、证据可追溯、执行可控的本地编码 Agent。

这句话包含三个工程约束：

- 上下文预算驱动：每轮进入模型的内容必须经过预算选择。
- 证据可追溯：完整轨迹落盘，摘要按需进入上下文。
- 执行可控：工具调用、补丁、验证、修复和停止条件由 Runtime 管理。

## 目标

- 提供一个完整的本地编码 Agent 项目。
- 支持从用户需求到代码修改、验证、最终报告的完整闭环。
- 通过 Context Pack Compiler 控制上下文大小和质量。
- 通过 Evidence Runtime 保存完整审计轨迹。
- 通过 Safe Execution Kernel 控制工具权限和修改风险。
- 后续逐步扩展代码智能、多 Agent 调度、Skill、MCP、评测和 RPC。

## v0.1 非目标

- 不做 Web UI。
- 不接完整 LSP。
- 不做向量 RAG。
- 不做插件市场。
- 不自动执行危险命令。
- 不自动 commit、push 或发起远程 PR。
- 不在核心流程中加入星穹铁道主题彩蛋。

## 目标用户

第一阶段目标用户是本地开发者。这个用户需要一个可控、可审计、能检查仓库、修改代码、运行验证并解释依据的编码 Agent。

## 产品形态

项目从 CLI 应用开始，但架构上要预留后续 Headless、RPC 和 UI 集成。CLI 必须是正式工作流，不是临时 Demo。

## 参考方向

xhx-agent 应综合参考多个 Agent 项目的优点：

- HPD-Agent：图规划、Review 回路和复杂任务调度。
- pi：Runtime 分层、JSONL 会话、Skill progressive disclosure 和未来 RPC 思路。
- aider：仓库地图、精确补丁和 lint/test 修复回路。
- Cline / Roo Code：Plan/Act 分离和命令审批。
- OpenHands / SWE-agent：真实仓库任务闭环、headless 和 benchmark。
- Continue：上下文提供者、检查报告和 CI 工作流思路。

## 设计原则

- 完整轨迹可以无限增长，但进入 Prompt 的上下文必须有限。
- 安全边界由代码实现，不依赖 Prompt 里的一句“请谨慎”。
- DAG 是复杂任务策略，不是所有任务的默认路径。
- Skill 只能提供流程和提示词，不能提升工具权限。
- 每次修改都应有验证结果；无法验证时必须说明原因。
