# Safe Execution Kernel

Safe Execution Kernel 是 xhx-agent 的执行安全内核。它统一管理工具权限、命令风险、补丁写入、验证、checkpoint 和失败停止条件。

## 执行流程

```text
tool request
  -> policy check
  -> risk classify
  -> confirm / deny / execute
  -> capture result
  -> write Raw Trace
  -> write Evidence Index
  -> route verification
  -> decide continue / repair / stop
```

## Policy Check

Policy Check 必须在工具执行前完成。

检查内容：

- 工具是否允许。
- 当前 mode 是否允许该工具。
- 命令是否需要用户确认。
- 目标路径是否在工作区内。
- 是否尝试绕过 `apply_patch`。
- 是否触碰敏感文件或系统路径。

## Checkpoint

修改前必须记录当前工作区状态。

v0.2 最小实现：

- `git status --short`
- `git diff --stat`
- changed files 列表

后续可选：

- 临时 worktree。
- patch snapshot。
- 任务分支。

## Repair Stop Condition

修复回路必须有限制。

默认规则：

- 同一验证失败最多修复 2 轮。
- 同一文件连续 patch 失败 2 次后停止。
- 权限被拒绝后不绕过。
- 无证据的高风险修改必须询问用户。
- 无法验证时输出手动验证建议。

## 输出要求

每次执行必须写入：

- 工具名。
- 参数摘要。
- 风险等级。
- 用户确认结果。
- 执行结果摘要。
- 关联 evidence id。

## 反模式

- 让模型自己判断是否安全。
- terminal 直接写文件。
- 失败后无限 repair。
- 用户拒绝后换命令绕过。
- 未 checkpoint 就执行修改。
