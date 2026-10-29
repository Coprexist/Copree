"""
状态链模型 — 运行集合的双向链表（节点只存 id）

存储分三层，各管一件事：
- 帧内容 = `agents.state_stack`（JSON 数组，全量保留，改了才写）；
- **运行集合 = 本文件的两张表**（只存 id 与前后指针，一次切会话就是几条行更新）；
- 纯指针算术 = `app/utils/pure/state_chain.py`（这里只有行，没有算法）。

节点行不复制帧内容：帧随时在改（doing / todo / 情感 / 计数），链只关心"谁排在谁后面"，
两层各写各的，谁也不会因为对方一改就作废。
"""
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)

from app.database import Base


class AgentStateChain(Base):
    """一条 AI 一条链：两个端点 + 执行窗口大小。

    head/tail **不设外键**：节点会随帧出运行集合被删，外键会把"删节点"和"改链"
    绑成一件事，反而更容易写出半吊子状态（删了行、链还指着）。链的完整性由
    `state_chain.problems()` 体检、`reconcile()` 修复，日志里留痕。
    """

    __tablename__ = "agent_state_chain"

    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    head_id = Column(Integer, comment="链头节点号（压得最久、最不急的一端）")
    tail_id = Column(Integer, comment="链尾节点号（最后加入的一端 = 正在执行）")
    max_active = Column(
        Integer, nullable=False, server_default="1",
        comment="执行窗口：链尾起算的并发帧数（默认 1；将来按 AI 配置改这一列即可）",
    )
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentStateChainNode(Base):
    """链上节点 = 一个运行中的帧，只存 id 与前后邻居。

    `(agent_id, frame_id)` 唯一键就是「帧 id → 链节点号」的索引：
    AI 说的是哪一帧，算法直接定位到节点，不用扫 `state_stack` 找下标。
    """

    __tablename__ = "agent_state_chain_node"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    frame_id = Column(String(32), nullable=False, comment="agents.state_stack 里的帧 id")
    prev_id = Column(Integer, comment="前一个节点号（NULL = 本节点就是链头）")
    next_id = Column(Integer, comment="后一个节点号（NULL = 本节点就是链尾）")

    __table_args__ = (
        UniqueConstraint("agent_id", "frame_id", name="uq_state_chain_frame"),
        Index("ix_state_chain_prev", "agent_id", "prev_id"),
        Index("ix_state_chain_next", "agent_id", "next_id"),
    )
