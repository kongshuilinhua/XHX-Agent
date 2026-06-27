"""回退对话命令。"""

from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_rewind(ctx: CommandContext) -> None:
    """回退对话到指定轮数之前。"""
    if ctx.conversation is None:
        ctx.ui.add_system_message("对话未初始化")
        return

    try:
        n = int(ctx.args.strip()) if ctx.args.strip() else 1
    except ValueError:
        ctx.ui.add_system_message("用法: /rewind <轮数>，如 /rewind 3 回退 3 轮")
        return

    if n < 1:
        ctx.ui.add_system_message("轮回退数必须 >= 1")
        return

    history = ctx.conversation.history
    removed = 0
    for _ in range(n):
        if not history:
            break
        # 从尾部移除一轮（user + assistant + tool results）
        while history and getattr(history[-1], "role", "") not in ("user",):
            history.pop()
            removed += 1
        while history and getattr(history[-1], "role", "") == "user":
            history.pop()
            removed += 1

    # 把回退掉的那几轮里改过的文件一并还原。每轮结束时 file_history 都按
    # len(history) 记了一个快照，快照里存的是“那一轮开局前”的文件内容。回退后
    # 历史长度为 L，要还原到的就是 message_index 最小且 > L 的那个快照——即被
    # 移除的最早一轮的开局状态。
    restored_files = 0
    file_history = ctx.config.get("file_history") if ctx.config else None
    if removed > 0 and file_history is not None:
        target_idx = _snapshot_index_for_history(file_history, len(history))
        if target_idx is not None:
            restored_files = len(file_history.rewind(target_idx))

    # 重绘聊天区，让界面反映回退后的历史（否则被移除的消息仍显示在屏幕上）。
    render_restored = ctx.config.get("render_restored") if ctx.config else None
    if render_restored is not None:
        await render_restored(list(history))

    msg = f"已回退 {n} 轮（移除 {removed} 条消息"
    if restored_files:
        msg += f"，还原 {restored_files} 个文件"
    msg += "）"
    ctx.ui.add_system_message(msg)


def _snapshot_index_for_history(file_history: object, history_len: int) -> int | None:
    """找到回退后该还原到的快照下标。

    返回 message_index 严格大于 history_len 的快照里 message_index 最小的那个；
    它对应被移除的最早一轮，其备份正是那一轮开局前（也即上一轮收尾后）的文件状态。
    若没有快照晚于当前历史（被移除的轮次没动过文件），返回 None。
    """
    get_snapshots = getattr(file_history, "get_snapshots", None)
    if get_snapshots is None:
        return None
    target_idx: int | None = None
    target_mi: int | None = None
    for i, snap in enumerate(get_snapshots()):
        mi = getattr(snap, "message_index", None)
        if mi is None or mi <= history_len:
            continue
        if target_mi is None or mi < target_mi:
            target_mi = mi
            target_idx = i
    return target_idx


REWIND_COMMAND = Command(
    name="rewind",
    description="回退对话 N 轮",
    usage="/rewind <轮数>",
    handler=handle_rewind,
)
