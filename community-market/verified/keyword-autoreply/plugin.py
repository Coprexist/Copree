"""
行为插件示例：关键词自动回复（inject 类）。

展示行为插件契约：
- 声明与行为合一：@skill 一个装饰器完成元数据注册 + 处理器注册
- 处理器签名与内置 inject handler 一致：async fn(db, agent, skill, config, prompts, now)
- 关键词匹配由现有 trigger 机制完成（skill 配置 trigger.match_type=keyword），
  处理器负责把命中转换为注入提示词——职责清晰，不重复造轮子。
"""
from app.services.plugin.api import skill


@skill(
    type="keyword_autoreply",
    category="inject",
    name="关键词自动回复",
    description="消息命中关键词时，注入一条回复指引，让 AI 优先回应相关主题",
    config_schema={
        "reply_guide": {
            "type": "string",
            "default": "请优先围绕命中关键词展开回答，保持简洁具体。",
            "description": "命中后注入给 AI 的回复指引",
        },
    },
)
async def handle_keyword_autoreply(db, agent, skill, config, prompts, now):
    """inject 类处理器：把关键词命中转换为注入提示词。"""
    guide = config.get("reply_guide") or "请优先围绕命中关键词展开回答，保持简洁具体。"
    prompts.append(guide)
