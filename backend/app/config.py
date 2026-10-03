"""
应用配置模块
使用 pydantic-settings 自动从环境变量 / .env 文件解析配置。
环境变量名默认为字段名大写（如 database_url → DATABASE_URL）。
"""
import json
import logging
import secrets
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """全局应用配置 — 所有字段由 pydantic-settings 自动从环境变量读取"""

    # ── 应用版本（可从环境变量 APP_VERSION 注入，默认 0.4.13）──
    app_version: str = "0.4.13"

    # ── 数据库 ──
    db_backend: str = "postgres"
    sqlite_db_path: str = "./data/copree.db"
    database_url: str = "postgresql+asyncpg://ai_chat:change_me@localhost:5432/ai_group_chat"
    database_url_sync: str = "postgresql://ai_chat:change_me@localhost:5432/ai_group_chat"

    # ── JWT ──
    jwt_secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7

    # ── API 默认配置 ──
    deepseek_base_url: str = "https://api.deepseek.com"
    default_chat_model: str = "deepseek-flash"
    default_work_model: str = "deepseek-v4-pro"
    # JSON 格式的自定义模型选项列表（优先于默认列表）
    model_options: str = ""

    # ── Embedding 提供方插件化 ──
    # 后端: disabled | ollama | api | local
    embedding_backend: str = "disabled"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    # 向量维度：默认 1536（兼容现状）
    embedding_dimension: int = 1536
    # 旧配置兼容别名
    default_embedding_model: str = "text-embedding-3-small"

    # ── DSH 桥接（Copree 管理端 ↔ DSH 本体会话）──
    # 与 DSH 侧 dsh-copree 插件的 bridgeSecret 同值。DSH 侧未配置密钥就不会来注册，
    # 这里也就什么都检测不到 —— 「DSH 先同意，Copree 才看得见」由这条不对称保证。
    dsh_bridge_secret: str = ""
    # 注册表存活窗口（秒）：DSH 插件每 20 秒心跳一次，超过窗口未心跳即视为掉线。
    dsh_bridge_ttl_seconds: int = 90

    @field_validator("db_backend", "embedding_backend", mode="before")
    @classmethod
    def _lowercase(cls, v: str) -> str:
        return v.lower() if isinstance(v, str) else v

    @field_validator(
        "embedding_dimension", "avatar_max_size_mb", "upload_max_size_mb",
        "agent_metrics_retention_days", "credit_per_10k_tokens",
        mode="before",
    )
    @classmethod
    def _coerce_int(cls, v):
        """允许字符串形式的整数（环境变量都是字符串）"""
        return int(v) if isinstance(v, str) else v

    @property
    def is_deepseek_api(self) -> bool:
        """自动检测当前 API 提供商是否为 DeepSeek"""
        return "deepseek.com" in self.deepseek_base_url

    def get_model_options(self) -> list[dict]:
        """
        返回可用模型选项列表。
        优先读 model_options 字段（MODEL_OPTIONS 环境变量 JSON），否则按 API 提供商给默认值。
        """
        if self.model_options:
            try:
                return json.loads(self.model_options)
            except json.JSONDecodeError:
                logger.warning("MODEL_OPTIONS 不是有效 JSON，使用默认模型列表")
        # 默认模型列表
        if self.is_deepseek_api:
            return [
                {"value": "deepseek-flash", "label": "DeepSeek V4.1 Flash（快速）", "provider": "deepseek"},
                {"value": "deepseek-v4-pro", "label": "DeepSeek V4 Pro（高质量）", "provider": "deepseek"},
            ]
        else:
            # 通用 OpenAI 兼容 API：默认给两个常见档位
            return [
                {"value": self.default_chat_model, "label": f"{self.default_chat_model}（默认）", "provider": "generic"},
                {"value": self.default_work_model, "label": f"{self.default_work_model}（工作）", "provider": "generic"},
            ]

    @staticmethod
    def is_thinking_supported_for(base_url: str) -> bool:
        """检查某个 API base URL 是否支持 thinking/reasoning 参数"""
        return "deepseek.com" in base_url

    # ── 数据卷：数据库之外的运行时文件（上传、AI 文件空间、插件、世界、商城）──
    # 同一个变量两种视角：宿主机上看到的是 .env 里的目录（compose 拿它做挂载源），
    # 容器里看到的是挂载点 /app/data —— compose 的 backend.environment 把容器内那份钉死，
    # 免得宿主路径漏进来（那样插件与世界的文件会写进容器可写层，重建即丢）。
    # 子目录布局（agents/ worlds/ plugins/ market/ …）见 app/paths.py，别在模块里另写相对路径。
    data_dir: str = "/app/data"

    # ── 文件上传 ──
    avatar_max_size_mb: int = 10
    upload_max_size_mb: int = 32
    # 头像落盘目录（容器内路径，唯一来源；各路由不再各写字面量）
    avatars_dir: str = "/app/uploads/avatars"

    # ── 防滥用 ──
    rate_limit_per_second: int = 2  # 每个 AI 每秒最多发言次数

    # ── 登录防爆破（进程内计数，见 services/infrastructure/login_guard.py）──
    auth_fail_threshold: int = 15        # 同一账号在窗口内失败这么多次即锁定
    auth_fail_window: int = 900          # 失败计数窗口（秒）
    auth_lock_seconds: int = 300         # 首次锁定 5 分钟；解锁后再犯翻倍
    auth_lock_max_seconds: int = 3600    # 翻倍上限
    # 来源 IP 的阈值比账号高：网关地址是共享的，15 次就锁 IP 等于全站登录可被一个人锁住
    auth_fail_ip_threshold: int = 40

    # ── /auth/* 请求限流（进程内滑动窗口，按 IP，见 middleware.py）──
    auth_rate_limit: int = 30            # 凭证端点（登录/注册/发验证码/校验验证码）每分钟
    auth_rate_limit_other: int = 240     # 其余 /auth/*（/auth/me 这类每页都会调）每分钟

    # ── 向量检索默认参数 ──
    default_top_k: int = 10
    vector_weight: float = 0.6
    bm25_weight: float = 0.3
    time_decay_weight: float = 0.1

    # ── 意愿评分 + 自动免打扰全局默认 ──
    default_auto_dnd_threshold: int = 20
    default_auto_dnd_duration: int = 5

    # ── 摘要缓存 TTL（秒） ──
    summary_cache_ttl: int = 600

    # ── 系统监控指标保留天数 ──
    agent_metrics_retention_days: int = 30

    # ── 额度消耗比例（1 credit = N tokens） ──
    credit_per_10k_tokens: int = 10000

    # ── OpenCLI 集成 ──
    opencli_global_enabled: bool = False
    opencli_default_rate_limit: int = 5
    opencli_timeout_seconds: int = 60
    opencli_stdout_max_chars: int = 2000

    # ── 运行环境（development / production） ──
    environment: str = "development"

    @field_validator("environment", mode="before")
    @classmethod
    def _lowercase_env(cls, v: str) -> str:
        return v.lower() if isinstance(v, str) else v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    # ── 加密密钥（用于 API Key 加密存储） ──
    # 生产环境必须显式设置；开发环境自动生成并持久化到 data/encryption_key
    encryption_key: str = ""

    # ── 显示时区 ──
    display_timezone: str = "Asia/Shanghai"

    # ── 联邦通信 — GitHub 注册表 ──
    github_token: str = ""
    registry_repo: str = "Coprexist/Copree"
    registry_file: str = "federation-registry.json"

    # ── 联邦通信 — 出站 TLS ──
    # 默认校验证书；对端用自签证书时指定 CA bundle（而不是全局关校验）
    federation_tls_verify: bool = True
    federation_ca_bundle: str = ""

    # ── CORS 跨域（默认不启用；同源代理部署不需要 CORS） ──
    allowed_origins: list[str] = []

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _parse_origins(cls, v):
        """支持逗号分隔的字符串（环境变量常见格式）"""
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    # ── 维护模式标记目录 ──
    maintenance_dir: str = "/tmp"

    class Config:
        env_file = ".env"
        extra = "allow"

    @model_validator(mode="after")
    def _warn_extra_fields(self):
        """启动时记录未识别的环境变量，帮助排查配置拼写错误"""
        known_fields = set(self.model_fields.keys())
        extra_fields = set(self.__pydantic_extra__ or {}) if hasattr(self, "__pydantic_extra__") else set()
        if extra_fields:
            logger.warning(
                f"[CONFIG] 检测到未识别的配置字段（可能拼写错误或多余）: {extra_fields}"
                f"（已知字段: {sorted(known_fields)}）"
            )
        return self

    @model_validator(mode="after")
    def _check_encryption_key(self):
        """启动时检查加密密钥安全性

        策略：
        - 生产环境（ENVIRONMENT=production）：未设置或等于 JWT 密钥 → 中止启动
        - 开发环境：自动生成随机密钥并持久化到文件（避免每次重启生成新密钥导致已加密数据无法解密）
        """
        needs_generate = not self.encryption_key or self.encryption_key == self.jwt_secret_key

        if needs_generate:
            if self.is_production:
                # 生产环境：中止启动
                raise RuntimeError(
                    "[SECURITY] 生产环境必须设置独立的 ENCRYPTION_KEY 环境变量！"
                    "当前 ENCRYPTION_KEY 未设置或与 JWT_SECRET_KEY 相同，拒绝启动"
                )

            # 开发环境：尝试从持久化文件读取
            key_file = Path(self.data_dir) / "encryption_key"
            if key_file.exists():
                try:
                    self.encryption_key = key_file.read_text().strip()
                    if self.encryption_key and len(self.encryption_key) == 64:
                        logger.info("[SECURITY] 从持久化文件读取 ENCRYPTION_KEY")
                        return self
                except Exception:
                    pass

            # 文件不存在或无效：生成新密钥并持久化
            self.encryption_key = secrets.token_hex(32)
            try:
                key_file.parent.mkdir(parents=True, exist_ok=True)
                key_file.write_text(self.encryption_key)
                key_file.chmod(0o600)
                logger.warning(
                    f"[SECURITY] ENCRYPTION_KEY 已自动生成并持久化到 {key_file}"
                    f"（开发环境；生产环境请显式设置 ENCRYPTION_KEY）"
                )
            except OSError as e:
                logger.warning(
                    f"[SECURITY] ENCRYPTION_KEY 已自动生成但持久化失败: {e}"
                    f"（每次重启将生成新密钥，已加密数据可能无法解密）"
                )

        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """配置分层：init > DB 覆盖 > env > dotenv > secrets（pydantic-settings 官方机制）

        DB 覆盖来自 system_settings（管理员前端图形化修改），
        未覆盖的字段自动落到 env/默认——两种部署方式兼容。
        """
        from app.db_config_source import DBConfigSource
        return (
            init_settings,
            DBConfigSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )


settings = Settings()

# 运行时可覆盖的配置（不持久化，重启后恢复为 env 默认值）
_runtime_overrides: dict = {}

def get_runtime_setting(key: str, default=None):
    return _runtime_overrides.get(key, default)

def set_runtime_setting(key: str, value):
    _runtime_overrides[key] = value

def get_effective_avatar_max_size_mb() -> int:
    return int(get_runtime_setting("avatar_max_size_mb", settings.avatar_max_size_mb))

def get_effective_upload_max_size_mb() -> int:
    return int(get_runtime_setting("upload_max_size_mb", settings.upload_max_size_mb))
