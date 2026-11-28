"""用户默认配置的「场景」清单 —— 全仓唯一来源

场景是一次业务请求的归属：AI 对话、群世界调度、智能整理、创建助手、语音。
清单定义在这里，前端只按 key 查文案，不在界面代码里写死「有哪些场景」。

每个场景在解析链第 ③ 格持有一条**有序的默认配置序列**（详见
`docs/dev/api_config_chain.md` §3.4）。本模块只放清单与 key 的校验；
序列本身、开关状态、失败状态都在数据层，不在这里硬编码。

**总开关只记「关掉的」**：没有记录即开启，所以新场景上线时老用户默认就是开的，
不需要数据迁移把人一个个改成开；勾回来等于删行。

**2026-11-28 设计变更（待实现）**：全局默认档（`user_api_profiles.is_default`）取消，
改为每场景一组有序配置，迁移 0137 把现有默认档复制进每个场景的第一格；
「优先使用本人配置」开关的归属（用户级 / AI 级）待拍板。改动范围与规则见
`docs/dev/api_config_chain.md` §3.4 与 §4.2。本模块的 `Scenario` 形状不变。
"""
from __future__ import annotations

from dataclasses import dataclass

KIND_LLM = "llm"
KIND_VOICE = "voice"


@dataclass(frozen=True)
class Scenario:
    """一个场景：`key` 是前后端共用的标识，`kind` 决定它用哪一类默认档"""

    key: str
    kind: str
    default_enabled: bool = True


# 顺序即界面顺序。kind=voice 的默认不开不对——用户说了「先都勾上」，
# 所以这里统一 default_enabled=True，没有例外（例外只会让人以为自己配错了）。
SCENARIOS: tuple[Scenario, ...] = (
    Scenario("agent_llm", KIND_LLM),
    Scenario("world_ai", KIND_LLM),
    Scenario("smart_organize", KIND_LLM),
    Scenario("agent_create", KIND_LLM),
    Scenario("voice_default", KIND_VOICE),
)

SCENARIO_KEYS: tuple[str, ...] = tuple(s.key for s in SCENARIOS)

_BY_KEY = {s.key: s for s in SCENARIOS}


def get_scenario(key: str) -> Scenario | None:
    """按 key 取场景；未知 key 返回 None（调用方据此报 404，而不是静默放行）"""
    return _BY_KEY.get(key)


def scenarios_of(kind: str) -> tuple[Scenario, ...]:
    return tuple(s for s in SCENARIOS if s.kind == kind)
