"""
SQLAlchemy ORM 模型
"""
from app.models.user import User
from app.models.external import ExternalIdentity
from app.models.agent import Agent, AgentConfigHistory, AgentUserConfig, AgentCollaborator, CapabilityVersion
from app.models.group import Group, GroupMember, GroupInvitation
from app.models.message import Message, GroupMessageEmbedding, PendingMessage
from app.models.memory import RoughMemory, DetailMemory
from app.models.vector_request import VectorAccelerationRequest
from app.models.file import FileMetadata, FileReference, FileCollaborator
from app.models.redemption import RedemptionCode
from app.models.system_log import SystemLog
from app.models.summary_cache import UnreadSummaryCache
from app.models.friendship import Friendship, FriendshipRequest
from app.models.dm import DMSession, DMMessage
from app.models.agent_skill import AgentSkill
from app.models.federation import InstanceConfig, FederationPeer, FederatedEntity, PendingProfileUpdate
from app.models.opencli import (
    OpenCLIConfig,
    OpenCLIAgentWhitelist,
    OpenCLICommandWhitelist,
    OpenCLIUsageLog,
    OpenCLIDeniedCommand,
)
from app.models.conversation_log import ConversationLogConfig, ConversationLog
from app.models.agent_metrics import AgentMetricsSnapshot
from app.models.api_key_pool import ApiKeyPool, UserApiAssignment
from app.models.api_usage_log import ApiUsageLog
from app.models.system_settings import SystemSettings
from app.models.verification_code import VerificationCode
from app.models.personality_anchor import PersonalityAnchor
from app.models.agent_config import AgentConfig
from app.models.agent_trigger import AgentTrigger
from app.models.agent_attention import AgentAttention
from app.models.group import MemberSilence
from app.models.agent_state_stack import AgentStateStack
from app.models.user_preferences import UserGroupPreference, UserDMPreference
from app.models.plugin import Plugin, UserPluginPref
from app.models.agent_skill_relation import AgentSkillRelation
from app.models.alarm import AgentAlarm
from app.models.structured_record import StructuredRecord
from app.models.workspace import AgentWorkspace
from app.models.study_record import StudyRecord
from app.models.study_setting import StudySetting
from app.models.world import (World, WorldBinding, WorldAgent, WorldChatMessage, WorldAI, WorldAIMemory, WorldStructuredRecord, WorldLLMUsage, WorldMarketItem, GroupAssistant, WorldData)

__all__ = [
    "User",
    "ExternalIdentity",
    "Agent",
    "AgentConfigHistory",
    "AgentUserConfig",
    "Group",
    "GroupMember",
    "GroupInvitation",
    "Message",
    "GroupMessageEmbedding",
    "PendingMessage",
    "UnreadSummaryCache",
    "OpenCLIConfig",
    "OpenCLIAgentWhitelist",
    "OpenCLICommandWhitelist",
    "OpenCLIUsageLog",
    "OpenCLIDeniedCommand",
    "RoughMemory",
    "DetailMemory",
    "VectorAccelerationRequest",
    "FileMetadata",
    "FileReference",
    "FileCollaborator",
    "RedemptionCode",
    "SystemLog",
    "Friendship",
    "FriendshipRequest",
    "DMSession",
    "DMMessage",
    "AgentSkill",
    "InstanceConfig",
    "FederationPeer",
    "FederatedEntity",
    "PendingProfileUpdate",
    "ConversationLogConfig",
    "ConversationLog",
    "AgentMetricsSnapshot",
    "ApiKeyPool",
    "UserApiAssignment",
    "ApiUsageLog",
    "SystemSettings",
    "VerificationCode",
    "PersonalityAnchor",
    "AgentConfig",
    "AgentTrigger",
    "AgentAttention",
    "AgentStateStack",
    "AgentCollaborator",
    "CapabilityVersion",
    "AgentSkillRelation",
    "AgentAlarm",
    "StructuredRecord",
    "AgentWorkspace",
    "World",
    "WorldBinding",
    "WorldAgent",
    "WorldChatMessage",
    "WorldAI",
    "WorldAIMemory",
    "WorldStructuredRecord",
    "WorldLLMUsage",
    "GroupAssistant",
    "WorldData",
    "UserGroupPreference",
    "UserDMPreference",
    "Plugin",
    "UserPluginPref",
]

from app.models.api_doc_section import ApiDocSection
