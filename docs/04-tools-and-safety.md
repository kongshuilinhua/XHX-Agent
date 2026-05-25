# 工具与安全

xhx-agent 必须默认保守。工具必须经过 Safe Execution Kernel 的权限策略，不能绕过安全边界。所有工具结果都必须写入 Evidence Runtime。

## v0.1 工具

- `read_file`：读取文件内容，并带有大小限制。
- `search`：搜索仓库文本，优先使用 `rg`。
- `terminal`：在命令权限策略下运行 shell 命令。
- `apply_patch`：应用结构化文本补丁。

## 后续工具

v0.6 可以新增：

- `code_outline`
- `symbol_search`
- `code_context`
- `diagnostics`

这些工具必须返回结构化结果，并且能关联到 Evidence Runtime。

## 写入策略

所有仓库写入都必须经过 `apply_patch`。

禁止的写入路径：

- 通过 terminal 重定向写文件。
- `sed -i`。
- 直接覆盖文件的 helper。
- 未经审查的代码生成写入。
- 自动 commit 或 push。

## 命令风险等级

### Safe

Safe 命令可以自动执行。

- `pwd`
- `ls`
- `dir`
- `rg`
- `cat` / `type`
- `git status`
- `git diff`

### Confirm

Confirm 命令执行前必须获得用户确认。

- `pytest`
- `python -m pytest`
- `npm test`
- `npm run build`
- `npm run typecheck`
- 项目本地脚本。
- 依赖安装。

### Deny

Deny 命令默认阻止。

- `rm -rf`
- `git reset --hard`
- `git checkout -- .`
- 全局包安装。
- 系统配置修改。
- 删除用户目录。
- 修改 shell profile 文件。

## 工具执行流程

```text
tool request
  -> policy check
  -> risk classify
  -> confirm / deny / execute
  -> capture result
  -> write Raw Trace
  -> write Evidence Index summary
  -> route verification when needed
```

## 验证命令推断

验证命令根据项目文件推断。

- 存在 `pyproject.toml`、`pytest.ini` 或 `tests/`：优先使用 `python -m pytest`。
- `package.json` 中存在 `test` 脚本：优先使用 `npm test`。
- `package.json` 中存在 `build` 脚本：没有测试时使用 `npm run build`。
- `tsconfig.json` 且存在 `typecheck` 脚本：使用 `npm run typecheck`。

如果无法推断安全命令，Agent 必须询问用户，或只给出手动验证建议。

## 补丁安全

`apply_patch` 必须支持：

- dry-run 校验。
- changed files 报告。
- 冲突检测。
- 仅处理 UTF-8 文本。
- 仅允许工作区相对路径。
- 拒绝符号链接和敏感文件。

## 报告要求

最终报告必须说明：

- 执行过的命令。
- 修改过的文件。
- 验证结果。
- 跳过的验证及原因。
- 未解决风险。
