"""send_voice 工具 —— AI 用语音说一段话（独立一条语音消息，不是塞进正文）

为什么是"独立一条"：QQ 里语音消息（msg_type=7）本身就是独立一条，塞在正文里没有
这个形态；站内也按一条带音频附件的消息渲染，点开就播。

为什么不在工具里转 silk：QQ 实测 mp3 直发能播（2026-11-14），转码只在某些端才需要，
那是通道的事（qq-channel 自己会降级），平台只负责给出 mp3。

只有在"配了语音 API 且检测通过"时这个工具才会出现在工具表里（见
response_worker 里的门控）；没配的 AI 根本不知道有这功能。
"""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)

MAX_TEXT = 1200          # 一段话的上限：再长就该分几条说，转一篇长文成语音没人听


class SendVoice(ToolPlugin):
    name = "send_voice"
    description = (
        "用语音说一段话（发一条独立的语音消息，正文另发）。"
        "想比打字更亲近、对方在忙不方便看字、或者这句话本身就该带声音时用它。"
        "text 是要说的内容（口语，别写成文章）；style 是怎么说，例如"
        "「轻快、好奇，尾音带一点笑」——支持它的供应商会照着念，不支持的会被忽略。"
        "不给 goal 参数，发给当前会话即可。"
    )
    segment = "chat_social"
    parameters = {
        "text": {"type": "string", "description": "要念出来的话（口语化，一到三句最好）"},
        "style": {
            "type": "string", "nullable": True,
            "description": "语气描述：快慢、情绪、像在做什么。留空 = 供应商默认",
        },
        "group_id": {"type": "integer", "nullable": True, "description": "目标群聊 ID（群聊时填写）"},
        "target_user_id": {"type": "integer", "nullable": True, "description": "目标用户 ID（私信时填写）"},
    }
    required = ["text"]
    states = ["active"]
    admin_description = "用语音说一段话。AI 想让对方听见自己的声音时调用。"
    trigger_condition = "AI 想用语音而非文字表达时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.chat.dm import get_or_create_dm_session, send_dm_message
        from app.chat.gm import gm_message_to_dict, send_gm_message
        from app.models.agent import Agent as AgentModel
        from app.services.agent.voice_config import voice_of
        from app.services.agent.voice_tts import VoiceSynthError, synthesize
        from app.services.content.file_service import ai_stored_path, upload_file

        text = str(arguments.get("text") or "").strip()
        style = str(arguments.get("style") or "").strip()
        target_group = arguments.get("group_id", group_id)
        target_user = arguments.get("target_user_id")

        if not text:
            return {"error": True, "message": "要念的内容（text）是空的"}
        if len(text) > MAX_TEXT:
            return {
                "error": True,
                "message": f"这段话有 {len(text)} 字，太长了（上限 {MAX_TEXT}）。"
                           f"挑最要紧的一两句说，剩下的用文字，或者分几条发。",
            }
        if target_group is not None and target_user is not None:
            return {"error": True, "message": "不能同时指定 group_id 和 target_user_id，请二选一"}
        if target_group is None and target_user is None:
            return {"error": True, "message": "请指定 group_id（群聊）或 target_user_id（私信）"}

        # 行 + 生效配置一次取齐：选了配置档就用档里的，否则用这行手填的（与门控同一处）
        cfg, eff = await voice_of(db, agent_id)
        if cfg is None or eff is None or not eff.base_url or not eff.voice_id:
            return {"error": True, "message": "这个 AI 还没配语音，先用文字说"}
        if not cfg.probe_ok:
            return {
                "error": True,
                "message": f"语音暂时不可用（{cfg.probe_kind or '未检测'}）："
                           f"{cfg.probe_message or '这套配置没测通过'}。先用文字说。",
            }

        # ── 合成 ──
        try:
            audio = await synthesize(
                provider=eff.provider, base_url=eff.base_url, api_key=eff.api_key or "",
                voice_id=eff.voice_id, text=text, style=style, model=eff.model or "",
            )
        except VoiceSynthError as e:
            logger.warning(f"send_voice 合成失败（agent={agent_id}, kind={e.kind}）: {e.message}")
            return {
                "error": True,
                "message": f"这次没能合成语音（{e.kind}）：{e.message}。"
                           f"先把话说成文字，别让对话停在这里。",
            }

        # ── 落成站内附件（与 send_file 同一条路：零拷贝引用，前端按 path 渲染）──
        filename = f"voice_{datetime.now().strftime('%Y%m%d%H%M%S')}_{agent_id}.mp3"
        try:
            meta = await upload_file(
                # 语音是 AI 自己产生的文件，落在它自己的空间里（agents/{id}/voice），
                # 不混进用户的 attachments——AI 的账不记在主人头上（§5.5）
                db, path=ai_stored_path(int(agent_id), "voice"),
                filename=filename, content=audio,
                mime_type="audio/mpeg", owner_type="ai", owner_id=int(agent_id),
            )
        except Exception as e:
            logger.error(f"send_voice 存附件失败: {e}", exc_info=True)
            return {"error": True, "message": f"语音生成了但没能存下来：{e}"}

        # transcript：这段语音念的就是 text，文稿是现成的，直接跟着附件走——
        # 没有它，AI 回看历史只看到 voice_xxx.mp3 这种文件名，连自己说过什么都不知道；
        # 人的语音没有这段，要靠 ASR 之后补同一个字段（见 docs/dev/TODO.md「语音」）。
        # 文稿写回文件本身（唯一来源）：消息快照只留 file_id，渲染时按 id 取，
        # 所以转发、ASR 补写都不会出现"同一份音频 N 份文稿"的问题。
        from app.services.content.file_service import set_transcript

        await set_transcript(db, meta.id, text)
        attachments = [{
            "file_id": meta.id,
            "name": filename,
            "path": meta.path,
            "size": meta.size,
            "mime_type": "audio/mpeg",
            # 时长是不可变属性（文件内容定了就不会变），随快照走一份：会话列表预览要写
            # 「[语音 0'42"]」，为它每条会话再查一次库不值。源头仍在 file_metadata.duration_ms
            "duration_ms": meta.duration_ms,
        }]

        # ── 发出去 ──
        agent_name = context.get("agent_name", f"AI:{agent_id}")
        sender_avatar = None
        agent_user_id = None
        a_obj = (await db.execute(select(AgentModel).where(AgentModel.id == agent_id))).scalar_one_or_none()
        if a_obj is not None:
            sender_avatar = a_obj.avatar_url
            agent_user_id = a_obj.user_id

        if target_user is not None:
            if agent_user_id is None:
                return {"error": True, "message": "AI 尚未初始化统一用户 ID"}
            session = await get_or_create_dm_session(
                db, current_user_id=agent_user_id, target_user_id=int(target_user),
            )
            if session is None:
                return {"error": True, "message": "无法创建 DM 会话"}
            try:
                msg = await send_dm_message(
                    db, session["session_id"], sender_id=agent_user_id,
                    content=" ", attachments=attachments,
                )
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error(f"send_voice DM 失败: {e}", exc_info=True)
                return {"error": True, "message": f"语音发不出去：{e}"}
            manager = context.get("manager")
            if manager:
                await manager.broadcast_to_dm(
                    session["session_id"],
                    {"type": "message", "conversation_type": "dm",
                     "data": {**msg, "sender_name": agent_name}},
                )
            return {"success": True, "message": "语音发出去了", "attachments": attachments}

        try:
            message = await send_gm_message(
                db, group_id=int(target_group), sender_type="ai", sender_id=agent_user_id,
                content="", attachments=attachments,
            )
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(f"send_voice 群聊失败: {e}", exc_info=True)
            return {"error": True, "message": f"语音发不出去：{e}"}

        msg_data = gm_message_to_dict(message, sender_name=agent_name, sender_avatar_url=sender_avatar)
        manager = context.get("manager")
        if manager:
            await manager.broadcast_to_group(int(target_group), {"type": "message", "data": msg_data})
        return {"success": True, "message": "语音发出去了", "attachments": attachments}


ToolRegistry.register(SendVoice)
