"""
接口文档分区快照表 — 运行时权威值（md 文件是源，DB 是快照）

md（随 git）──「从文档中更新」──▶ 本表（运行时读取，快且可表单编辑）
                                        ▲
                   表单保存勾选「同步更新文档」──▶ 写回 md
"""
from sqlalchemy import Column, String, Text, DateTime, func
from app.db_providers import json_column
from app.database import Base


class ApiDocSection(Base):
    __tablename__ = "api_doc_sections"

    id = Column(String(4), primary_key=True, comment="分区号（01-09）")
    title = Column(Text, nullable=False, default="")
    intro = Column(Text, nullable=False, default="")
    doc_file = Column(String(100), nullable=False, default="", comment="对应 md 文件名（供从文档同步）")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
