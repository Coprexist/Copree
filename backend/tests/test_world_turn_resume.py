"""「被迫终止后说继续」的记忆契约（用户 2026-09-23）。

余额不足（402）这类**被迫终止**：提示里写着「已记录未完成的工作流，充值后说「继续」即可接着做」，
那就必须真的记下来 —— 判据只看"正文是不是以「（」开头"会把友好错误提示误判成正常结束，
把记忆清掉，用户充完值说「继续」时什么都不知道。
"""
from app.services.world.world_chat_service import turn_completed


def test_real_summary_counts_as_completed():
    assert turn_completed("改完了 3 个文件，剩下音效没接。", False) is True


def test_friendly_errors_are_not_completion():
    assert turn_completed("💰 世界 AI 余额不足（402）：请为 API 账号充值…", True) is False
    assert turn_completed("⏳ 请求太频繁（429 限流）：稍等片刻再试。", True) is False
    assert turn_completed("🔑 世界 AI 的 API Key 无效（401/403）…", True) is False


def test_stub_closings_are_not_completion():
    assert turn_completed("（工具执行中断）", False) is False
    assert turn_completed("（对话中断：工具执行出错，请重试或换个说法）", True) is False
    assert turn_completed("", False) is False


def test_had_error_always_loses_even_with_plausible_text():
    """带 error 标记的一律不算结束：宁可多留一次工作流记忆，也别让「继续」失忆。"""
    assert turn_completed("看起来挺像总结的一段话", True) is False
