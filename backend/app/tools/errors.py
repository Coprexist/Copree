"""工具错误协议 —— 插件/工具作者主动报错的地方。

在此之前作者只有两条路，都不好用：
- `raise ValueError("...")}`：被统一吞成 `执行失败：...`，语义全丢——分不清
  「用户参数不对」和「插件代码有 bug」，模型也不知道该不该重试；
- `return {"error": True}`：能到模型，但没有 error_code，同样没法判断。

这张协议把「说得出原因的错误」和「未捕获的异常」分开：
前者由作者用 ToolError 声明（原样转给模型），后者仍是 TOOL_EXEC_FAILED（进日志、带插件归属）。
"""
from __future__ import annotations


class ToolError(Exception):
    """工具作者主动抛的业务错误。

    用法：
        from app.tools.errors import ToolError, INVALID_ARGS
        raise ToolError(INVALID_ARGS, "缺少 group_id")

    字段：
        code      稳定标识（日志/计数器/前端共用），改文案不改 code
        message   给模型/用户看的一句话
        retryable 换一种方式可能成功（模型可重试）——默认 False，别鼓励瞎试
        outward   失败后果由第三方承担（发消息/发文件）——平台会额外通知 owner
    """

    def __init__(self, code: str, message: str = "", *,
                 retryable: bool = False, outward: bool = False):
        self.code = code or TOOL_EXEC_FAILED
        self.message = message or "执行失败"
        self.retryable = bool(retryable)
        self.outward = bool(outward)
        super().__init__(self.message)

    def to_result(self) -> dict:
        result = {"error": True, "error_code": self.code, "message": self.message}
        if self.retryable:
            result["retryable"] = True
        if self.outward:
            result["outward"] = True
        return result


# 与 ToolErrorCode 同名同值（那边是内部常量类，这里给作者 import 用，避免作者去碰框架内部）
INVALID_ARGS = "INVALID_ARGS"
TOOL_EXEC_FAILED = "TOOL_EXEC_FAILED"
TOOL_RATE_LIMITED = "TOOL_RATE_LIMITED"
TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
TOOL_TIMEOUT = "TOOL_TIMEOUT"
