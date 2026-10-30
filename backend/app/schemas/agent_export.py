"""
AI 灵魂档案（Soul Archive）导出/导入格式

2.0（当前）：记忆连权值/类型/焦段一起走，知识库连正文一起走。
1.0：记忆只有 title/content/scope/group_id/created_at，且不含知识库——导入端继续认。

不打包好友关系：friendships 挂在本实例 users 表的 id 上，换实例无法复现，
硬带只会导入一堆指向陌生人的行。整实例搬迁请用数据库备份。
"""


class AgentSoulArchive:
    """
    AI 角色灵魂档案 —— 导出/导入格式
    不使用 Pydantic BaseModel 以保持最大兼容性，
    导入时做宽松校验（老档案、人手改过的档案都要能进来）。
    """
    export_version: str = "2.0"
    exported_at: str = ""
    agent_name: str = ""
    agent_config: dict | None = None      # 当前 config
    original_config: dict | None = None   # 原始 config
    config_history: list[dict] | None = None
    memories: list[dict] | None = None    # 一条 rough 一条：字段 + details[{content, created_at}]
    knowledge: list[dict] | None = None   # 一份文档一条：元数据 + content（正文）

    def __init__(
        self,
        export_version="2.0",
        exported_at="",
        agent_name="",
        agent_config=None,
        original_config=None,
        config_history=None,
        memories=None,
        knowledge=None,
    ):
        self.export_version = export_version
        self.exported_at = exported_at
        self.agent_name = agent_name
        self.agent_config = agent_config or {}
        self.original_config = original_config or {}
        self.config_history = config_history or []
        self.memories = memories or []
        self.knowledge = knowledge or []

    def to_dict(self) -> dict:
        return {
            "export_version": self.export_version,
            "exported_at": self.exported_at,
            "agent_name": self.agent_name,
            "agent_config": self.agent_config,
            "original_config": self.original_config,
            "config_history": self.config_history,
            "memories": self.memories,
            "knowledge": self.knowledge,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentSoulArchive":
        return cls(
            export_version=data.get("export_version", "2.0"),
            exported_at=data.get("exported_at", ""),
            agent_name=data.get("agent_name", ""),
            agent_config=data.get("agent_config", {}),
            original_config=data.get("original_config", {}),
            config_history=data.get("config_history", []),
            memories=data.get("memories", []),
            knowledge=data.get("knowledge", []),
        )

    @classmethod
    def validate(cls, data: dict) -> list[str]:
        """校验导入数据的结构，返回错误列表（空列表 = 合法）"""
        errors = []
        if "agent_config" not in data:
            errors.append("缺少 agent_config 字段")
        else:
            cfg = data["agent_config"]
            if not isinstance(cfg, dict):
                errors.append("agent_config 必须是对象")
        for key, label in (("memories", "记忆"), ("knowledge", "知识库"), ("config_history", "配置历史")):
            if key in data and not isinstance(data[key], list):
                errors.append(f"{key}（{label}）必须是数组")
        return errors
