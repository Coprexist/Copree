"""
用量日聚合模型
按「日期 + AI + 模型」每天一行累计 token 消耗，只累加、从不删除。

对话日志为控制体积按条数滚动删除（每条含整段 messages），用量统计若也从它身上算，
就会跟着一起消失 —— 越是天天在聊的 AI，能看到的历史越短，最后只剩最近一两天。
所以用量单独记一份账，与对话日志的留存策略彻底解耦。
"""
from sqlalchemy import (
    BigInteger, Column, Date, DateTime, Integer, String, UniqueConstraint, func,
)
from app.database import Base


class UsageDaily(Base):
    """每天每个 AI（每个模型）一行的 token 用量累计"""

    __tablename__ = "usage_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stat_date = Column(Date, nullable=False, comment="统计日期（UTC）")
    # 下面三列用哨兵值而不是 NULL：唯一约束与 ON CONFLICT 都把 NULL 视为互不相等，
    # 只有非空值才能保证同一天同一来源每次都命中同一行、累加而不是插新行
    agent_id = Column(Integer, nullable=False, default=0,
                      comment="AI id；0 = 世界 AI 等没有 agent 行的记账")
    user_id = Column(Integer, nullable=False, default=0,
                     comment="世界 AI 的记账人；普通 AI 留 0，归属查询时走 agents.owner_id")
    model = Column(String(50), nullable=False, default="", comment="模型名；空串 = 未知")

    total_tokens = Column(BigInteger, nullable=False, default=0)
    prompt_tokens = Column(BigInteger, nullable=False, default=0)
    completion_tokens = Column(BigInteger, nullable=False, default=0)
    reasoning_tokens = Column(BigInteger, nullable=False, default=0)
    cached_tokens = Column(BigInteger, nullable=False, default=0)
    api_calls = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # 唯一键即查询索引：统计都是「某个日期区间」，键首列就是 stat_date；
    # 表本身只有「天数 × AI 数 × 模型数」量级，不值得再为 agent_id 单建索引
    __table_args__ = (
        UniqueConstraint("stat_date", "agent_id", "user_id", "model", name="uq_usage_daily_key"),
    )
