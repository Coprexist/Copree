"""
提示词文件加载器

所有系统提示词从 backend/app/prompts/*.txt 文件加载，无需 Python 转义。
直接编辑 .txt 文件即可修改提示词，重启生效。
管理员通过面板覆盖的优先级高于文件。
"""
import os as _os
import logging

logger = logging.getLogger(__name__)

_PROMPTS_DIR = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
    "prompts",
)


def load_prompt(name: str) -> str:
    """从 prompts/{name}.txt 加载提示词，末尾保留一个换行以便拼接。"""
    path = _os.path.join(_PROMPTS_DIR, f"{name}.txt")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().rstrip() + "\n"
    except FileNotFoundError:
        logger.warning(f"提示词文件不存在: {path}")
        return ""


# ── 模块常量：导入时加载，与原有 CORE_IDENTITY 等行为一致 ──
CORE_IDENTITY = load_prompt("core_identity")
PROTOCOL_CHAT = load_prompt("protocol_chat")
PROTOCOL_IMMERSIVE = load_prompt("protocol_immersive")
PROTOCOL_DIGITAL_LIFE = load_prompt("protocol_digital_life")
DM_PROTOCOL = load_prompt("dm_protocol")

# 平台出处：不是"规矩"而是事实，所以拼在管理员覆盖之后（见 llm._core_identity）
PLATFORM_ORIGIN = load_prompt("platform_origin")

# ── 可选的动态注入段 ──
MULTI_SESSION = load_prompt("multi_session")
PRIVACY_RULES = load_prompt("privacy_rules")
CHAT_CHAIN_RULES = load_prompt("chat_chain_rules")
