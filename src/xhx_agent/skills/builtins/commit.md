---
name: commit
description: 生成规范的 git commit message 并提交
mode: inline
context: full
triggers:
  - commit
  - 提交
  - git commit
---

## 提交规范

当用户要求提交代码时，请按以下流程操作：

1. 用 `git diff --staged` 和 `git diff` 确认变更范围
2. 检查变更是否引入调试代码、临时代码或敏感信息
3. 按以下格式生成 commit message：

```
<type>(<scope>): <subject>

<body>
```

type 可选值：feat / fix / refactor / perf / test / docs / chore / style

原则：
- subject 不超过 50 字符，用中文
- body 说明做了什么、为什么这么做
- 不要提交调试代码、临时代码

4. 用户确认后执行 `git commit -m "..."`

References:
- Conventional Commits: https://www.conventionalcommits.org/
