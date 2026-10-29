"""
工具调用日聚合模型
按「日期 + 工具 + 帧类型 + 在线状态」每天一行累计调用次数，只累加、从不删除。

为什么单独落库：既有 metrics_collector 的计数是进程内的，重启即失，且没有维度——
用它没法回答「聊天帧里到底用哪几个工具」「哪个工具在勿扰下被反复调用」。
一个月后要拿这份一手数据决定「哪些段常驻、哪些折叠」，所以必须持久且有维度。

与 usage_daily 同一套口径（日聚合 + UPSERT + 只增不删），理由也相同：对话日志会被裁，
统计不能跟着一起消失。
"""
from sqlalchemy import (
    BigInteger, Column, Date, DateTime, Integer, String, UniqueConstraint, func,
)
from app.database import Base


class ToolUsageDaily(Base):
    """每天每个（工具 × 帧类型 × 在线状态）一行的调用计数"""

    __tablename__ = "tool_usage_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stat_date = Column(Date, nullable=False, comment="统计日期（UTC）")
    tool_name = Column(String(80), nullable=False, comment="工具名")
    # 帧类型 = 会话帧的 type（group_chat / dm / world / file_work / …）；没帧记空串。
    # 用哨兵空串而不是 NULL：唯一约束与 ON CONFLICT 把 NULL 视为互不相等，
    # 只有非空值才能保证同一维度每次都命中同一行、累加而不是插新行。
    frame_type = Column(String(40), nullable=False, default="", comment="帧类型；空串 = 无帧/未标注")
    agent_state = Column(String(20), nullable=False, default="", comment="active/dnd/inactive/blocked；空串 = 未知")

    calls = Column(Integer, nullable=False, default=0, comment="调用次数")
    errors = Column(Integer, nullable=False, default=0, comment="失败次数")
    latency_ms_sum = Column(BigInteger, nullable=False, default=0, comment="耗时累加（ms）；平均 = sum / calls")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "stat_date", "tool_name", "frame_type", "agent_state",
            name="uq_tool_usage_daily_key",
        ),
        # 查询都是「某日期区间 + 某工具/某帧」，键首列即 stat_date；表只有
        # 「天数 × 工具数 × 帧类型数 × 状态数」量级，不值得再单建索引
    )
