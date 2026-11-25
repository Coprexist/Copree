"""日志里的「这一轮用了哪条配置」——与 stepper 同一个 Resolution，别两边各说各话

2026-11-26 补：api_config_chain.md 批次一的「日志补列」一直没做，排障只能拿 model 反推。
写入方与 API 页签共用 _api_meta_of 的输入（Resolution），所以这里测的是"从结论到字段"这一段。
"""
import pytest

pytestmark = pytest.mark.anyio


def _resolution(source: str = "bound", hit_label: str = "默认") -> object:
    from app.services.agent.api_config_resolver import ChainStep, Resolution

    return Resolution(
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        chat_model="deepseek-flash",
        work_model=None,
        source=source,
        credit_source="profile" if source == "bound" else "none",
        pool_key_id=None,
        provider_info={},
        models=[],
        thinking_supported=True,
        system_layout="anywhere",
        needs_key=True,
        chain=[
            ChainStep(level="bound", status="hit" if source == "bound" else "skip",
                      reason="ok" if source == "bound" else "no_quota",
                      label=hit_label if source == "bound" else ""),
            ChainStep(level="pool", status="skip", reason="no_member"),
            ChainStep(level="default", status="skip", reason="no_bind"),
            ChainStep(level="fail", status="skip", reason="not_configured"),
        ],
    )


def test_api_meta_takes_the_hit_step_label():
    """source 说"哪一档"，label 说"哪条"——两件都要进日志"""
    from app.ai.executor import _api_meta_of

    meta = _api_meta_of(_resolution(), "https://api.deepseek.com")
    assert meta == {
        "source": "bound",
        "label": "默认",
        "model": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
    }


def test_log_detail_carries_api_meta():
    """详情接口把这列原样交出去；前端才有东西可渲染"""
    from app.models.conversation_log import ConversationLog
    from app.services.content.conversation_log_service import _log_to_detail

    log = ConversationLog(
        messages=[{"role": "user", "content": "在吗"}],
        message_count=1,
        conversation_type="group",
        has_output=False,
        api_meta={"source": "pool", "label": "平台池", "model": "deepseek-flash",
                  "base_url": "https://api.deepseek.com"},
    )
    detail = _log_to_detail(log)
    assert detail["api_meta"]["source"] == "pool"
    assert detail["api_meta"]["label"] == "平台池"
