"""
能力懒加载：skills/tools 版本化 + 增量变更注入（2026-08-06 产品方案）

能力源：platform（内置工具）/ world-{id}（世界 skills 转出的工具定义）。
每个 AI 两个进度（agents.cap_known_versions / cap_effective_versions，JSONB {"{状态}|{源}": version}）：
- known（告知进度）：**按状态各记一份**——已注入变更通知的版本，落后则注入"增量 changelog"，注入后更新
- effective（生效进度）：**按状态各记一份**——该状态的前缀实际用的定义版本，compact / clear 时切到最新

为什么按状态记：通知只发一次的病根是进度挂 agent 级——先构建的那个状态把 known 推到最新，
别的状态就再也收不到那条变更。作用域决定"这条变更该不该送到我"，进度决定"我收过没有"，两件事分开：
- 版本的作用域（capability_versions.scope）：空 / "*" = 全部状态（平台、提示词、记忆索引这类内容
  每个状态的前缀都装同一份，所以作用域就是全部）；也可以是某个具体会话，或某个会话焦段。
- 进度的归属：永远是某个状态（{状态}|{源}）。读不到本状态的键 → 回落旧 agent 级键作为起点
  （迁移期不清除：每个状态各自继承一次旧值，之后互不影响；否则修这个 bug 会把历史全量 changelog
  灌给每个窗口一次）。

2026-08-12 扩展：前缀内容（用户 system_prompt / 强注入段 / 昵称）也纳入同一机制。
新增：ensure_text_source_version / get_effective_text / apply_pending_changes（解锁）/ guard_apply_change（防御）。

不变式：
- 请求 payload 的 tools = effective 版本的定义快照（compact 前不动 → 前缀缓存稳定）
- 变更告知 = 动态尾部 system 消息（不影响前缀）
- 旧版本定义永远保留在 capability_versions（平台发布后旧对话继续用旧定义请求）
- 锁定态（对话中）前缀永不因外部变更而变；compact / clear 是唯一解锁点
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging

from sqlalchemy import select
from app.repositories.capability_repo import CapabilityRepository

logger = logging.getLogger(__name__)

SOURCE_PLATFORM = "platform"

# 作用域取值全站统一，只有两种：
#   "*"（SCOPE_ALL）= 全部状态；其余 = 具体作用域（会话键 group:3 / 12_106，或 focus:{id}）。
# 版本行上**没有"空"这种取值**——scope 是写入的必填项：写 "*" 是全部，不写 "*" 就是当前会话
# （调用方把当前会话键传进来）。全局必须显式表态，因为"忘了写"的失败是静默不投递，
# 正是不好查的那类病；反过来最多只是多通知，看得见。
SCOPE_ALL = "*"


def memory_index_source(agent_id: int) -> str:
    """记忆索引（目录树）的版本源——与提示词/能力**共用同一条版本链**：

    冻结（锁定态取 effective 快照）+ 差分通知（尾部 changelog）都是现成的，不新造机制。
    """
    return f"memory-index-{agent_id}"

# 解锁上下文标记：apply_pending_changes（compact/clear）期间置 True，
# guard_apply_change 检查它——锁定态尝试应用变更 → 拒绝 + 报错（防御未来"强制修改"逻辑）
_unlock_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar("cap_unlock_ctx", default=False)


# 能力源 id → 人话标签（通知标题用：world-name-45 这种 id 世界 AI 看不出是什么）
_SOURCE_LABELS = {"forced-prompt": "强注入段", "ai-skills": "设计侧能力"}


def source_label(source: str) -> str:
    """能力源 id → 中文标签（world-prompt-45 → 世界AI提示词，world-name-45 → 世界AI昵称）"""
    if source in _SOURCE_LABELS:
        return _SOURCE_LABELS[source]
    for prefix, name in (("world-prompt-", "世界AI提示词"), ("world-name-", "世界AI昵称")):
        if source.startswith(prefix):
            return name
    return source


def defs_hash(definitions: list) -> str:
    """工具定义列表 → 内容哈希（检测变更）"""
    return hashlib.sha256(
        json.dumps(definitions, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _def_by_name(definitions: list, name: str) -> dict | None:
    for d in definitions or []:
        fn = (d or {}).get("function") or {}
        if fn.get("name") == name:
            return d
    return None


# 文本 diff 的预算：这是要进账本的一次性条目，写得下"改了哪几行"就够，别把整篇提示词搬进来
_TEXT_DIFF_BUDGET = 800


def _line_diff(old_text: str, new_text: str, budget: int = _TEXT_DIFF_BUDGET) -> str:
    """两份文本的**行级**差异——只给改变量，不给全文。

    为什么按行：提示词是人分点写的，行就是它最小的语义单位。按字符找"首处差异"只给一小段
    上下文（世界 AI 曾收到「6→3 字，首处差异：…」而没意识到那是它自己的新名字）——
    长文本尤其如此：知道"变了"却不知道变成什么，这条通知就白发，因为通知的全部意义是
    "以新的为准"，而它得先看得见新的是什么。
    等长块折成省略号，只留变化点；超预算就截断并说清还有多少行没列。
    """
    import difflib

    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    out: list[str] = []
    used = truncated = 0
    opcodes = difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes()
    for idx, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == "equal":
            # 只留变化点之间的原文：给个"改在哪"的锚点，首尾两端不必留
            if not 0 < idx < len(opcodes) - 1:
                continue
            block = ["  …"] if i2 - i1 > 2 else ["  " + line for line in old_lines[i1:i2]]
        else:
            block = (["- " + line for line in old_lines[i1:i2]]
                     + ["+ " + line for line in new_lines[j1:j2]])
        for line in block:
            if used + len(line) + 1 > budget:
                truncated += 1
                continue
            out.append(line)
            used += len(line) + 1
    body = "\n".join(out)
    if truncated:
        body += f"\n…（还有 {truncated} 行没列出）"
    return body


# 通知策略的三档声明（开发者写在世界配置 / 清单里）：
#   auto（缺省）= 自动算改变量；custom = 自己写文案；silent = 不通知（版本照写、解锁点照换）
NOTICE_AUTO = "auto"
NOTICE_CUSTOM = "custom"
NOTICE_SILENT = "silent"


def notice_policy(raw) -> tuple[str | None, bool]:
    """开发者声明的"这条变更怎么告知" → (人写文案 or None, 要不要通知)。

    唯一入口：读声明的规矩只在这里写一遍，谁读谁调它（现在只有世界工具源），
    于是"声明写错时怎么办"也只有一个答案——**按 auto 处理并告警**：
    宁可多通知，也不能因为一个拼错的字段就静默沉默。
    容错的形状（开发者最省心的写法优先）：
      None / "" / "auto"                  → 自动改变量
      False / "silent"                    → 不通知
      "一句人话"                           → 就用它当文案（custom）
      {"mode": "custom", "text": "…"}     → 显式写法
      {"text": "…"}                       → 有 text 即 custom
      {"mode": "silent"} / {"mode":"auto"}→ 同上
    认不出的形状告警后按 auto。
    """
    if raw is None or raw == "" or raw == NOTICE_AUTO:
        return None, True
    if raw is False or raw == NOTICE_SILENT:
        return None, False
    if isinstance(raw, dict):
        text = str(raw.get("text") or "").strip()
        mode = str(raw.get("mode") or (NOTICE_CUSTOM if text else NOTICE_AUTO)).strip()
        if mode == NOTICE_SILENT:
            return None, False
        if mode == NOTICE_CUSTOM:
            if not text:
                logger.warning("通知声明 mode=custom 但没给 text，按 auto 处理")
                return None, True
            return text, True
        if mode != NOTICE_AUTO:
            logger.warning(f"认不出的通知声明 mode={mode!r}，按 auto 处理")
        return None, True
    if isinstance(raw, str):
        return (raw.strip() or None), True
    logger.warning(f"认不出的通知声明 {raw!r}，按 auto 处理")
    return None, True


# 工具定义差异的预算：一个能力最多给多少字（多个能力各占一段，整体仍受通知长度约束）
_TOOL_DIFF_BUDGET = 600
# 短字段（一句话的说明）超过这个合长就走行级 diff，否则给「旧 → 新」更省
_FIELD_ARROW_BUDGET = 80


def _clip(text, limit: int) -> str:
    """一行字段的紧致渲染：折掉换行、超长截断（散文式说明不必全文进通知）。"""
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[:limit].rstrip() + "…"


def _props(fn: dict) -> dict:
    return (fn.get("parameters") or {}).get("properties") or {}


def _required(fn: dict) -> list[str]:
    return [str(x) for x in ((fn.get("parameters") or {}).get("required") or [])]


def _field_change(label: str, old: str, new: str) -> str:
    """同一字段的"改变量"：短字段给「旧 → 新」，长的走行级 diff（与文本源同一套写法）。"""
    if len(old) + len(new) <= _FIELD_ARROW_BUDGET:
        return f"{label}：「{_clip(old, 40)}」 → 「{_clip(new, 40)}」"
    return f"{label}：\n" + _line_diff(old, new, budget=300)


def _tool_diff(old_def: dict | None, new_def: dict | None,
               budget: int = _TOOL_DIFF_BUDGET) -> str:
    """一个工具定义的**改变量**：说明给差异，参数给增 / 删 / 改（名字、类型、说明、必填）。

    为什么参数要逐个列：模型是按参数名与说明调工具的，而**工具数组要等解锁才换**——
    这条通知是它当下唯一的准信。只报"更新能力 X"，它下一轮照旧按老习惯传参，白烧一轮
    （世界 AI 原话：「不只费 token，我会去找不存在的工具、白烧轮次」）。
    新能力要给全（名字、说明、参数、必填）：它眼前的工具数组里根本没有这个能力。
    """
    fn_old = (old_def or {}).get("function") or {}
    fn_new = (new_def or {}).get("function") or {}
    lines: list[str] = []
    if not fn_old:
        lines.append("说明：" + _clip(fn_new.get("description"), 240))
        for name, spec in _props(fn_new).items():
            spec = spec or {}
            lines.append(f"参数 {name}（{_clip(spec.get('type'), 20) or '?'}）："
                         + _clip(spec.get("description"), 120))
        if required := _required(fn_new):
            lines.append("必填：" + "、".join(required))
    elif not fn_new:
        lines.append("这个能力已经没有了，不要再调用它。")
    else:
        old_desc, new_desc = fn_old.get("description") or "", fn_new.get("description") or ""
        if old_desc != new_desc:
            lines.append(_field_change("说明", old_desc, new_desc))
        props_old, props_new = _props(fn_old), _props(fn_new)
        for name, spec in props_new.items():
            spec = spec or {}
            if name not in props_old:
                lines.append(f"新增参数 {name}（{_clip(spec.get('type'), 20) or '?'}）："
                             + _clip(spec.get("description"), 120))
                continue
            old_spec = props_old[name] or {}
            if old_spec == spec:
                continue
            old_text, new_text = old_spec.get("description") or "", spec.get("description") or ""
            if old_text != new_text:
                lines.append(_field_change(f"参数 {name} 说明", old_text, new_text))
            if (old_spec.get("type") or "") != (spec.get("type") or ""):
                lines.append(f"参数 {name} 类型：{old_spec.get('type') or '?'} → {spec.get('type') or '?'}")
        for name in props_old:
            if name not in props_new:
                lines.append(f"去掉参数 {name}")
        if _required(fn_old) != _required(fn_new):
            lines.append("必填项：" + ("、".join(_required(fn_old)) or "无")
                         + " → " + ("、".join(_required(fn_new)) or "无"))
    if not lines:
        lines = ["定义有调整（未识别出字段级差异）"]
    body = "\n".join(lines)
    if len(body) > budget:
        body = body[:budget].rstrip() + "\n…（这个能力的差异过长，已截断）"
    return body


def _diff_changelog(old_defs: list | None, new_defs: list) -> str:
    """自动 diff 两版定义 → 变更摘要（工具源列增删改；文本源给行级 diff + 字数变化）"""
    # 文本源：definitions = [{"type": "text", "content": "..."}]
    def _is_text(defs):
        return bool(defs) and all((d or {}).get("type") == "text" for d in defs)
    if _is_text(old_defs) or _is_text(new_defs):
        old_text = "".join((d or {}).get("content") or "" for d in (old_defs or []))
        new_text = "".join((d or {}).get("content") or "" for d in (new_defs or []))
        if old_text == new_text:
            return "内容无变化"
        # 短文本（昵称这类）：直接给「旧 → 新」；字数+首处差异看不懂（世界 AI 收到
        # 「6→3 字，首处差异：…群视界机器人… → …小傻福…」时没意识到那是它自己的新名字）
        if len(old_text) <= 60 and len(new_text) <= 60:
            return f"内容更新：「{old_text}」 → 「{new_text}」"
        if not old_text.strip():
            return f"从无到有（{len(new_text)} 字）：\n" + _line_diff(old_text, new_text)
        if not new_text.strip():
            return f"清空（原 {len(old_text)} 字）：\n" + _line_diff(old_text, new_text)
        return f"内容更新（{len(old_text)}→{len(new_text)} 字）：\n" + _line_diff(old_text, new_text)
    old_names = {((d or {}).get("function") or {}).get("name") for d in (old_defs or [])}
    new_names = {((d or {}).get("function") or {}).get("name") for d in (new_defs or [])}
    added = new_names - old_names
    removed = old_names - new_names
    changed = {
        n for n in (new_names & old_names)
        if _def_by_name(new_defs, n) != _def_by_name(old_defs, n)
    }
    lines = []
    for n in sorted(added):
        lines.append(f"新增能力 {n}：\n" + _tool_diff(None, _def_by_name(new_defs, n)))
    for n in sorted(removed):
        lines.append(f"移除能力 {n}：\n" + _tool_diff(_def_by_name(old_defs, n), None))
    for n in sorted(changed):
        lines.append(f"更新能力 {n}：\n" + _tool_diff(_def_by_name(old_defs, n),
                                                    _def_by_name(new_defs, n)))
    return "\n".join(lines) or "内容无变化"


# ── 版本写入（启动/变更检测时调用） ──

async def ensure_source_version(
    cap_repo: CapabilityRepository, source: str, definitions: list, label: str,
    *, scope: str, changelog: str | None = None, notice: bool = True,
) -> int:
    """对比该源最新版本：内容变了 → 写新版本（默认自动 diff 出"改变量"）；没变 → 返回当前版本号。

    scope 必填：这条变更该发给哪些状态。全局内容（平台工具 / 提示词 / 记忆索引）传 SCOPE_ALL；
    会话里产生的内容传那个会话键。
    changelog / notice 是给开发者留的口子（声明怎么读见 `notice_policy`）：
    - `changelog` 给了 → 用他写的这份文案，不再自动算；
    - `notice=False` → 这一版**不告知**。存储口径与"起点版本不告知"是同一条：摘要留空，
      而 `build_change_notice` 见到空摘要就不落条目。版本行照写、锁定/解锁照旧——
      也就是"悄悄换"，只该用在确实不影响行为的改动上（会留日志）。
    """
    from app.models.agent import CapabilityVersion

    h = defs_hash(definitions)
    latest = (await cap_repo.execute(
        select(CapabilityVersion)
        .where(CapabilityVersion.source == source)
        .order_by(CapabilityVersion.version.desc())
        .limit(1)
    )).scalar_one_or_none()

    if latest is not None and latest.content_hash == h:
        return latest.version

    new_version = (latest.version if latest else 0) + 1
    # 摘要留空 = 这一版不告知：起点版本本就如此（"从无到有"+全文只是噪音），
    # notice=False 也走这一条。判据只有"摘要空不空"这一个，两处各写一个迟早走散。
    summary = ""
    if notice and latest is not None:
        body = changelog if changelog is not None else _diff_changelog(latest.definitions, definitions)
        summary = f"[{label} v{new_version}] " + body
    cap_repo.add(CapabilityVersion(
        source=source, version=new_version, content_hash=h,
        changelog=summary, definitions=definitions, scope=scope,
    ))
    await cap_repo.commit()
    logger.info(f"🧬 能力源 {source} 新版本 v{new_version}: "
                f"{summary[:120] or '（无变更摘要：起点或声明不通知）'}")
    return new_version


async def ensure_platform_version(cap_repo: CapabilityRepository) -> int:
    """平台内置工具版本化（启动时调用）"""
    from app.tools.base import ToolRegistry
    return await ensure_source_version(
        cap_repo, SOURCE_PLATFORM, ToolRegistry.get_all_definitions(), "平台工具",
        scope=SCOPE_ALL,
    )


async def ensure_world_version(cap_repo: CapabilityRepository, world_id: int, skill_tools: list,
                           *, notice=None) -> int:
    """世界 skills 工具版本化（对话时调用）。

    notice 是这条源的开发者声明（世界配置里的 `tool_notice`）：自动改变量 / 自己写文案 /
    不通知，三选一，读法见 `notice_policy`——**声明怎么读只有一个入口**，这条源和将来别的
    开发者源都走它。
    """
    source = f"world-{world_id}"
    changelog, do_notice = notice_policy(notice)
    return await ensure_source_version(
        cap_repo, source, skill_tools, f"世界{world_id}", scope=SCOPE_ALL,
        changelog=changelog, notice=do_notice)


# ── 查询 ──

async def get_version(cap_repo: CapabilityRepository, source: str, version: int):
    from app.models.agent import CapabilityVersion
    return (await cap_repo.execute(
        select(CapabilityVersion).where(
            CapabilityVersion.source == source,
            CapabilityVersion.version == version,
        )
    )).scalar_one_or_none()


async def get_latest_version(cap_repo: CapabilityRepository, source: str):
    from app.models.agent import CapabilityVersion
    return (await cap_repo.execute(
        select(CapabilityVersion)
        .where(CapabilityVersion.source == source)
        .order_by(CapabilityVersion.version.desc())
        .limit(1)
    )).scalar_one_or_none()


# ── AI 进度读写 ──

def _holder_map(holder, key: str) -> dict:
    """holder 可以是 agent（有属性）或 dict（如 worlds.config）"""
    if isinstance(holder, dict):
        return dict(holder.get(key) or {})
    return dict(getattr(holder, key, None) or {})


def _set_holder_map(holder, key: str, m: dict) -> None:
    if isinstance(holder, dict):
        holder[key] = m
    else:
        setattr(holder, key, m)


def agent_prompt_source(agent_id: int, override: str | None = None,
                        owner: int | None = None) -> str:
    """人格段的版本源：本体是 agent-prompt-{id}，某个用户的人格覆盖是 ...-u{uid}。

    覆盖必须另立一个源：两个源装的是两份不同的文本，共用一个源的话每次构建哈希都不一样，
    会一轮写一个新版本（版本表被刷爆、通知变成每轮一条）。
    判据只看"这一轮有没有覆盖、且知道是谁的"——**拼前缀、发通知、解锁三处都调这一个函数**，
    免得各写一遍"有没有覆盖"而走散（历史上正是这条判断走散，人格段整条链在线上没跑过）。
    """
    if not override or not owner:
        return f"agent-prompt-{agent_id}"
    return f"agent-prompt-{agent_id}-u{owner}"


def agent_prompt_label(agent_id: int, override: str | None = None,
                       owner: int | None = None) -> str:
    """人格段在变更通知里叫什么——覆盖要写明是谁的覆盖，不然看不出改的是哪一份。

    判据复用 `agent_prompt_source`：标签与源必须同进同出，各判一遍迟早出现
    "通知说改了本体、实际换的是覆盖"。
    """
    if agent_prompt_source(agent_id, override, owner) == agent_prompt_source(agent_id):
        return f"AI{agent_id}提示词"
    return f"AI{agent_id}提示词（用户{owner}的覆盖）"


def progress_key(state: str | None, source: str) -> str:
    """进度键：{状态}|{源}。状态为空（世界 AI 一世界一对话）→ 退回扁平键，行为与改造前一致。"""
    return f"{state}|{source}" if state else source


def read_progress(holder, field: str, state: str | None, source: str) -> int | None:
    """读该状态的进度；没有本状态的键 → 回落旧 agent 级键（迁移起点，不删除）。"""
    m = _holder_map(holder, field)
    key = progress_key(state, source)
    if key in m:
        return m[key]
    if state and source in m:
        return m[source]
    return None


def write_progress(holder, field: str, state: str | None, source: str, version: int) -> None:
    m = _holder_map(holder, field)
    m[progress_key(state, source)] = version
    _set_holder_map(holder, field, m)


def scope_covers(scope: str | None, state: str | None, foci=None) -> bool:
    """这条版本的作用域覆盖不覆盖该状态——决定变更通知该不该送到这个状态。

    "*" = 全部状态；具体会话 = 只有它；focus:{id} = 该会话焦段下的会话（预置「所有聊天」恒命中）。
    空值只可能来自本机制之前的存量行（那时进度挂 agent 级，语义上就是全部），按全部处理：
    宁可多通知，也不能把历史变更静默丢掉。
    """
    if not scope or scope == SCOPE_ALL:
        return True
    if state is None:
        return False
    if scope == state:
        return True
    if scope.startswith("focus:"):
        from app.utils.pure import focus as pure_focus
        focus = pure_focus.find(foci, scope[len("focus:"):])
        if not focus or focus.get("axis") != pure_focus.SESSION:
            return False
        return bool(focus.get("builtin")) or state in (focus.get("elements") or [])
    return False


async def get_effective_definitions(
    cap_repo: CapabilityRepository, holder, source: str, fallback_definitions: list,
    state: str | None = None,
) -> list:
    """请求用的工具定义：按**本状态**的 effective 版本取快照；无记录 → 用当前定义并同步本状态。

    前缀是按会话拼的字节，所以生效进度只能按状态记：别的会话 compact 不该让这个会话的
    tools 数组换新（那正是"一个会话 compact，其他会话从第 0 字节 miss"的病根）。
    """
    ver = read_progress(holder, "cap_effective_versions", state, source)
    if ver is not None:
        row = await get_version(cap_repo, source, ver)
        if row is not None and row.definitions is not None:
            return row.definitions
    # 新状态 / 新源：直接用当前（latest）定义，并把本状态的 effective 对齐最新
    latest = await get_latest_version(cap_repo, source)
    if latest is not None:
        write_progress(holder, "cap_effective_versions", state, source, latest.version)
        if latest.definitions is not None:
            return latest.definitions
    return fallback_definitions


def keep_request_tools(effective_defs: list, allowed_names: set) -> list:
    """请求里的 tools = 快照 ∩ 当前允许集，**但已经删掉的工具保留旧定义**。

    为什么留着：锁定态把一个工具从请求里抠掉会改字节、整段前缀 miss；"这个能力没了"由变更
    通知条目告知，解锁时 effective 对齐最新，它自然消失（apply_pending_changes）。
    """
    from app.tools.base import ToolRegistry

    registered = {t["function"]["name"] for t in ToolRegistry.get_all_definitions()}
    out = []
    for d in effective_defs or []:
        name = ((d or {}).get("function") or {}).get("name")
        if name in allowed_names or name not in registered:
            out.append(d)
    return out


# 自己刚改过的源：{agent_id: {source}} —— 只是"下一轮通知别念变更量"的记号。
# 进程内内存：丢了最坏也就是多念一次完整变更（无害），所以不落库、不加列。
_SELF_CHANGES: dict[int, set[str]] = {}


def _holder_id(holder) -> int | None:
    if isinstance(holder, int):
        return holder
    if isinstance(holder, dict):
        return int(holder["id"]) if holder.get("id") is not None else None
    return int(holder.id) if getattr(holder, "id", None) is not None else None


def mark_self_change(holder, source: str) -> None:
    """记下"这条源是这个 AI 自己刚改的"（写记忆 / 改自己提示词的工具在写成功后调）。"""
    holder_id = _holder_id(holder)
    if holder_id is not None:
        _SELF_CHANGES.setdefault(holder_id, set()).add(str(source))


def take_self_change(holder, source: str) -> bool:
    """取走并清掉这个记号（第一个来读的状态拿到短句，其余状态照常拿完整变更）。"""
    holder_id = _holder_id(holder)
    marked = _SELF_CHANGES.get(holder_id) if holder_id is not None else None
    if not marked or str(source) not in marked:
        return False
    marked.discard(str(source))
    if not marked:
        _SELF_CHANGES.pop(holder_id, None)
    return True


async def build_change_notice(
    cap_repo: CapabilityRepository, holder, sources: list[str],
    state: str | None = None, foci=None,
) -> str | None:
    """增量变更通知：对比**本状态**的 known vs latest，落后则拼 changelog，并更新本状态 known。

    返回通知文本（追加进 system 尾部）；无变化返回 None。
    注意：调用方需把通知真正放进 messages 后再提交（known 更新与注入同事务）。

    两件事分开（见模块头）：该不该送到我 = 版本 scope 是否覆盖本状态；我收过没有 = 本状态自己的键。
    known 一律推进到 latest（哪怕这段没有可发的内容）——被作用域排除的版本要与本状态"结清"，
    否则每轮都要重扫一遍。
    首见（本状态还没有这条源的 known）不会凭空冒通知：起点版本（v1）不写 changelog，
    见 `ensure_source_version`——"从无到有"不是变更，内容是它眼前前缀里本来就有的东西。
    """
    from app.models.agent import CapabilityVersion

    lines: list[str] = []
    changed = False
    for source in sources:
        latest = (await cap_repo.execute(
            select(CapabilityVersion)
            .where(CapabilityVersion.source == source)
            .order_by(CapabilityVersion.version.desc())
            .limit(1)
        )).scalar_one_or_none()
        if latest is None:
            continue
        known = read_progress(holder, "cap_known_versions", state, source) or 0
        if known >= latest.version:
            continue
        # 取 known+1 .. latest 的 changelog（增量：只注入新变化）
        rows = (await cap_repo.execute(
            select(CapabilityVersion)
            .where(
                CapabilityVersion.source == source,
                CapabilityVersion.version > known,
                CapabilityVersion.version <= latest.version,
            )
            .order_by(CapabilityVersion.version.asc())
        )).scalars().all()
        # 单行 changelog 套一层方括号当条目标记；行级 diff 是多行的，它自带「[标签 vN] 摘要」头，
        # 再套一层会让最后一行挂着一个孤零零的 ]
        parts = [
            (r.changelog if "\n" in r.changelog else f"[{r.changelog}]") for r in rows
            if r.changelog and scope_covers(r.scope, state, foci)
        ]
        if parts:
            head = f"【能力变更通知 · {source_label(source)} v{known}→v{latest.version}】"
            if take_self_change(holder, source):
                # 自己刚改的：变更量对它没有信息量（工具结果已经回过"成功"），只报一句已生效
                lines.append(head + "已生效（你自己刚改的，不再列出变更量）。")
            else:
                lines.append(head + "\n" + "\n".join(parts))
            changed = True
        write_progress(holder, "cap_known_versions", state, source, latest.version)
    if not changed:
        return None
    # 版本链保证「前缀缓存稳定」：强注入段/工具定义的新版本要等 compact / 清空上下文才整体生效，
    # 但 changelog 是当轮就到的。世界 AI 曾经看到通知 v10 却仍按旧段去找不存在的工具
    # （2026-09-18 反馈：「不只费 token，我会去找不存在的工具、白烧轮次」）→ 明确谁说了算。
    head = ("【能力变更通知】以下变更**立即生效**：与系统提示 / 工具描述里的旧表述冲突时以本通知为准；"
            "系统提示全文会在下次 compact / 清空上下文时整体刷新。\n")
    return head + "\n\n".join(lines)


async def mark_effective_latest(
    cap_repo: CapabilityRepository, holder, sources: list[str], state: str | None = None,
) -> None:
    """compact 后调用：**本状态**的 effective 对齐最新（工具定义直接用最新的）"""
    from app.models.agent import CapabilityVersion

    for source in sources:
        latest = (await cap_repo.execute(
            select(CapabilityVersion)
            .where(CapabilityVersion.source == source)
            .order_by(CapabilityVersion.version.desc())
            .limit(1)
        )).scalar_one_or_none()
        if latest is not None:
            write_progress(holder, "cap_effective_versions", state, source, latest.version)


async def mark_known_latest(
    cap_repo: CapabilityRepository, holder, sources: list[str], state: str | None = None,
) -> None:
    """把「已知版本」也对齐最新：内容已经进了生效前缀时，不必再发一次变更通知。

    与 mark_effective_latest 配对（会话起点解锁：既生效、也已"告知"，避免下一轮多一条
    描述"已经生效的内容"的通知）。
    """
    from app.models.agent import CapabilityVersion

    for source in sources:
        latest = (await cap_repo.execute(
            select(CapabilityVersion)
            .where(CapabilityVersion.source == source)
            .order_by(CapabilityVersion.version.desc())
            .limit(1)
        )).scalar_one_or_none()
        if latest is not None:
            write_progress(holder, "cap_known_versions", state, source, latest.version)


# ═══════════════════════════════════════════════════════════════
# 前缀文本源版本化（所有进前缀的内容必须保证缓存命中）
# ═══════════════════════════════════════════════════════════════
# 用户 system_prompt / 强注入段 / 昵称等文本也走 capability_versions 版本链：
# - 变更（用户改提示词 / 系统更新强注入）→ 写新版本 + 尾部 changelog 告知，不碰前缀
# - compact / clear（解锁）→ effective 对齐最新，前缀用新内容

def text_defs(text: str) -> list:
    """文本内容 → definitions 存储格式（与工具定义同表，type=text 区分）"""
    return [{"type": "text", "content": text}]


def defs_to_text(definitions: list | None) -> str | None:
    """definitions 快照 → 文本（type=text 源）"""
    if not definitions:
        return None
    parts = []
    for d in definitions:
        if isinstance(d, dict) and d.get("type") == "text":
            parts.append(d.get("content") or "")
    return "".join(parts) if parts else None


async def ensure_text_source_version(
    cap_repo: CapabilityRepository, source: str, text: str, label: str,
    *, scope: str,
) -> int:
    """文本内容版本化：内容变了 → 写新版本（复用 ensure_source_version + 文本 diff）。

    scope 必填，口径同 ensure_source_version：全局内容传 SCOPE_ALL（提示词 / 记忆索引都是
    "每个状态的前缀里装同一份"，要通知到每个状态），会话内产生的传会话键。
    """
    return await ensure_source_version(cap_repo, source, text_defs(text), label, scope=scope)


async def get_effective_text(
    cap_repo: CapabilityRepository, holder, source: str, fallback_text: str,
    state: str | None = None,
) -> str:
    """锁定态取**本状态**的 effective 快照文本；无记录（新状态/新源）→ 用当前文本并同步本状态。"""
    ver = read_progress(holder, "cap_effective_versions", state, source)
    if ver is not None:
        row = await get_version(cap_repo, source, ver)
        if row is not None:
            t = defs_to_text(row.definitions)
            if t is not None:
                return t
    latest = await get_latest_version(cap_repo, source)
    if latest is not None:
        write_progress(holder, "cap_effective_versions", state, source, latest.version)
        t = defs_to_text(latest.definitions)
        if t is not None:
            return t
    return fallback_text


async def apply_pending_changes(
    cap_repo: CapabilityRepository, holder, sources: list[str], state: str | None = None,
) -> None:
    """解锁（compact / clear = 新对话）时调用：**本状态**的 effective 对齐最新，变更正式生效。

    这是唯一合法的"应用变更"入口：它先进入解锁上下文再操作。
    锁定态直接调用 mark_effective_latest / 改 effective → guard_apply_change 拒绝 + 报错。
    state 必须是触发解锁的那个状态：别的会话没有解锁，它的前缀不该跟着换字节。
    """
    token = _unlock_ctx.set(True)
    try:
        guard_apply_change(sources)  # 解锁上下文内校验通过（防御：万一 future 代码绕路）
        await mark_effective_latest(cap_repo, holder, sources, state=state)
    finally:
        _unlock_ctx.reset(token)


def guard_apply_change(sources: list[str]) -> None:
    """防御性守卫：非解锁上下文（对话进行中）尝试应用变更 → 拒绝 + 记录报错。

    设计口径："只要有在解锁之前尝试应用变更的都拒绝并记录报错"。
    目前代码都是动态读取拼接（不会强制修改），此守卫防止未来出现"强制修改"逻辑破坏不变式。
    """
    if not _unlock_ctx.get():
        logger.error(
            f"⛔ 拒绝在锁定态应用能力变更 {sources}："
            f"前缀必须保持缓存稳定，变更只能经 compact/clear 解锁后生效"
        )
        raise RuntimeError(
            f"锁定态禁止应用变更 {sources}（需 compact/clear 解锁）"
        )
