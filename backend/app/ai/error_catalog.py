"""错误目录：用户可见文案的唯一来源。

为什么单独一张表：`executor._send_system_error` 原来把四种文案硬编码在函数里，
加一种错误就要改函数、改测试，还可能漏掉某个调用点。表放这里，投递函数只负责投递
（docs/dev/error_handling.md §3.1）。

边界：只放「说给用户听」的错误。工具内部错误码（UNKNOWN_TOOL / INVALID_ARGS）回给
模型，不进这张表；它们不是用户视角的语言。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
    """一条错误规格。

    template 里的占位符固定为 {agent_name} / {agent_id} / {detail} / {tool}——
    调用方只传这几个值，别在调用点现拼文案（C5）。
    outward 标记「失败后果由第三方承担」（发消息/发文件），这类错误要额外通知 owner。
    """

    code: str
    template: str
    action_label: str | None = None
    action_path: str | None = None
    outward: bool = False
    retryable: bool = False


CATALOG: dict[str, ErrorSpec] = {
    "no_api_key": ErrorSpec(
        code="no_api_key",
        template=(
            "⚠️ AI「{agent_name}」缺少 API Key，无法回复消息。\n\n"
            "📌 **解决方法**（任选其一）：\n"
            "1. 为 AI 单独配置：[AI 设置页](/agents/{agent_id}) → 完整设置 → API 提供商 → 填写 API Key\n"
            "2. 使用全局 Key：前往 [个人设置](/settings) → API 配置 → 填写你的 API Key（所有 AI 共用）\n"
            "3. 使用额度兑换：前往 [兑换码页面](/me) 输入兑换码获取 API 池额度\n\n"
            "设置完成后 AI 即可正常回复。"
        ),
        action_label="去配置",
        action_path="/agents/{agent_id}",
    ),
    "insufficient_balance": ErrorSpec(
        code="insufficient_balance",
        template=(
            "⚠️ AI「{agent_name}」的 API 余额不足（402），无法回复消息。\n\n"
            "📌 **解决方法**：\n"
            "1. 前往所用 API 提供商的官网充值\n"
            "2. 前往 [个人设置](/settings) 更换全局 API Key\n"
            "3. 前往 [AI 设置页](/agents/{agent_id}) 更换此 AI 的 Key\n"
            "4. 或前往 [兑换码页面](/me) 输入兑换码获取额度"
        ),
        action_label="去充值",
        action_path="/settings",
    ),
    "auth_error": ErrorSpec(
        code="auth_error",
        template=(
            "⚠️ AI「{agent_name}」的 API Key 无效（401），无法回复消息。\n\n"
            "📌 **解决方法**：\n"
            "1. 前往 [AI 设置页](/agents/{agent_id}) → API 提供商 → 检查并更新 API Key\n"
            "2. 或前往 [个人设置](/settings) 更新全局 API Key\n"
            "3. 确认 API Key 未过期、未被删除"
        ),
        action_label="去检查",
        action_path="/agents/{agent_id}",
    ),
    "all_failed": ErrorSpec(
        code="all_failed",
        template=(
            "⚠️ AI「{agent_name}」的 API 调用全部失败。\n\n"
            "错误详情：{detail}\n\n"
            "📌 请前往 [AI 设置页](/agents/{agent_id}) 或 [个人设置](/settings) 检查 API 配置。"
        ),
        action_label="去检查",
        action_path="/agents/{agent_id}",
        retryable=True,
    ),
    # ── 以下为 2026-11-25 从外部反馈补的分类（同一个出口，不新开通道）──
    "tool_outward_failed": ErrorSpec(
        code="tool_outward_failed",
        template=(
            "⚠️ AI「{agent_name}」没能把消息发出去。\n\n"
            "工具：{tool}\n"
            "原因：{detail}\n\n"
            "这轮它选择了别的做法，**那条消息没有发出去**。"
        ),
        outward=True,
    ),
    "tool_timeout": ErrorSpec(
        code="tool_timeout",
        template="⚠️ AI「{agent_name}」的工具 {tool} 执行超时，已经跳过。\n\n原因：{detail}",
        retryable=True,
    ),
    "session_polluted": ErrorSpec(
        code="session_polluted",
        template=(
            "⚠️ AI「{agent_name}」这一轮遇到数据状态异常，已经跳过。\n\n"
            "原因：{detail}"
        ),
    ),
}


def spec_for(code: str) -> ErrorSpec:
    """取规格；未知 code 落到 all_failed（永不抛——发通知的路径自己炸掉比不发更糟）。"""
    return CATALOG.get(code) or CATALOG["all_failed"]


def render(code: str, *, agent_name: str = "", agent_id: int | str = "",
           detail: str = "", tool: str = "") -> str:
    """把 code 渲染成给用户看的正文。"""
    spec = spec_for(code)
    return spec.template.format(
        agent_name=agent_name or "（未命名）",
        agent_id=agent_id if agent_id != "" else "-",
        detail=(detail or "（无详情）").strip(),
        tool=tool or "工具",
    )
