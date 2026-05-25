# Skills 与扩展

Skill 用于提供轻量专业能力，避免把所有规则都塞进核心 Prompt 或 Runtime。Skill 应封装可复用流程、提示词和验证规则。

Skill 只能影响计划、上下文选择和验证建议，不能提升工具权限，也不能绕过 Safe Execution Kernel。

## Skill 目录

```text
.xhx/skills/<skill-name>/SKILL.md
```

## Skill Metadata

每个 Skill 以 metadata 开头。

```yaml
name: python-debugger
description: Debug Python test failures and propose minimal patches.
triggers:
  - pytest
  - traceback
  - assertion failed
permissions:
  terminal: confirm
  write: apply_patch
```

## 加载规则

- Runtime 默认只加载 Skill 名称、描述和触发词。
- 只有命中触发词或用户显式请求时，才加载完整 Skill 内容。
- Skill 内容不能高于系统安全规则。
- Skill 不能提升工具权限。
- Skill 只能通过授权工具写文件。
- Skill 的执行结果必须写入 Evidence Runtime。

## 初始 Skill

建议第一批 Skill：

- `python-debugger`：分析 pytest 失败。
- `js-build-fixer`：分析 npm build 和 typecheck 失败。
- `docs-writer`：修改 README 和开发文档。
- `safe-refactor`：执行小范围、有证据支撑的重构。

## Extension Hooks

v0.8 支持以下 Hook：

- `before_plan`
- `before_patch`
- `after_verify`
- `before_summary`

Hook 应尽量保持确定性，并把结果写入 Evidence Runtime。

## MCP

MCP 是可选能力，应在本地 Skill 系统稳定后再接入。MCP 工具必须显式注册权限，并在使用时返回结构化证据。

## 反模式

避免：

- 每次请求都加载所有 Skill 正文。
- 给 Skill 不受限制的 terminal 权限。
- 让 Skill 篡改验证结果。
- 让 Skill 绕过 Safe Execution Kernel。
- 让 Skill 把大段日志直接塞进 Prompt。
