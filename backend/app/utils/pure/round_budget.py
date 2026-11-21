"""工具轮次读数：每轮告诉 AI「这是第几轮、一共几轮、还剩几轮」。

为什么必须有：轮次上限对 AI 是隐形的。它会把每一轮都花在检索上，用尽后由平台直接结束
（executor 的「循环耗尽」出口不发言也不补发），用户侧看到的只是「AI 不回我了」。
读数**追加在上下文末尾**，绝不改写已经发出去过的消息：前缀缓存按字节前缀匹配，
改一条就等于把"它之后的所有内容"作废（2026-11-06 线上：原地改写时间那条，18 轮多烧
25 万 token）。
"""
from __future__ import annotations

# 读数的段标题：模型靠它认出"这是平台的轮次读数"，不是对话内容
HEADER = "## 本轮工具轮次"

# 剩多少轮开始催收尾。留 3 轮是因为「把结论发出去 + end_turn」本身要占掉末尾，
# 再留一点余量给「换个说法重试一次」。
WARN_REMAINING = 3


def round_budget_text(*, round_no: int, total: int, free: bool = False) -> str:
    """一段读数。free=额度外白送的轮次（提醒轮 / 收尾轮）。

    赠送轮照样报轮号与上限，但必须说明它不占额度：不然 AI 会以为自己在正常轮次里，
    继续开新的检索，把白送的那次也花掉。
    """
    head = f"{HEADER}\n- {round_no}/{total}"
    if free:
        return (
            f"{head}（赠送轮，不占额度）\n"
            "- 这一轮是白送的：只能说话或 end_turn，别再开新的检索。"
        )
    remaining = total - (round_no - 1)
    if remaining > WARN_REMAINING:
        return head
    return (
        f"{head}（还剩 {remaining} 轮）\n"
        "- 够用就先把结论 send_gm/send_dm 发出去；没做完的，现在用 key_note 或 store_memory "
        "记下进度——轮次用完会直接结束，不会再给你补发的机会。"
    )
