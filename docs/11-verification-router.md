# Verification Router

Verification Router 根据项目结构、changed files 和任务类型选择最小有效验证命令。它的目标是让 Agent 修改后尽量验证，但不盲目运行昂贵或危险命令。

## 输入

- changed files。
- 项目文件：`pyproject.toml`、`pytest.ini`、`package.json`、`tsconfig.json` 等。
- 当前执行模式。
- Evidence Index 中的错误和测试摘要。
- 用户指定的验证命令。

## 输出

- 推荐验证命令。
- 风险等级。
- 是否需要用户确认。
- 跳过验证的原因。
- 验证结果 evidence id。

## Python 项目

优先级：

1. 用户指定命令。
2. changed files 对应测试文件。
3. `python -m pytest <test-file>`。
4. `python -m pytest`。

触发条件：

- 修改 `.py` 文件。
- 存在 `tests/`。
- 存在 `pytest.ini` 或 `pyproject.toml` 的 pytest 配置。

## JavaScript / TypeScript 项目

优先级：

1. 用户指定命令。
2. `npm test`。
3. `npm run typecheck`。
4. `npm run build`。

触发条件：

- 修改 `.js`、`.jsx`、`.ts`、`.tsx`。
- 存在 `package.json`。
- 存在 `tsconfig.json`。

## 文档修改

优先级：

1. Markdown link check。
2. 文档站点 build。
3. 仅输出人工检查建议。

触发条件：

- 修改 `.md`、`.mdx`。
- 存在 docs 构建脚本。

## 配置修改

配置修改默认高风险。

优先级：

1. 配置解析命令。
2. 项目 smoke test。
3. 用户确认的验证命令。

## 跳过验证

允许跳过验证，但必须记录原因：

- 没有可推断命令。
- 用户拒绝执行。
- 命令风险过高。
- 当前任务是只读分析。
- 依赖缺失。

跳过验证时，最终报告必须明确说明未验证风险。
