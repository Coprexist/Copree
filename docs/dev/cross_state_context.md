# 跨状态交接：原文尾巴 + 跨状态便签

> 解决的问题：AI 在私信里和人约好一件事（"在群里喊『少宇』，你就回那句"），
> 到了群里它却答不出来。AI 的记忆是**按会话拼上下文**的，会话一换，线头就断了。
>
> 这不是"给 AI 加记忆"，是给它换会话时**接上线头**；而且要接得不破坏前缀缓存
> （见 [能力懒加载](./capability_lazy_loading.md) 的锁定/解锁不变式）。

## 两条机制，语义不同，别混

| | 原文尾巴 | 跨状态便签 |
|---|---|---|
| 谁产生 | 平台自动（切会话时抄最后几轮原文） | AI 自己（工具 `cross_state_note`） |
| 装什么 | 刚离开那段对话的**原文** | 临时、有时效的一句约定 |
| 时效 | 无（一次性交接） | 写下后 **40 次 API 调用**内可投递 |
| 进上下文的方式 | 尾部注入**一次** | **投递制**：投进哪个会话就固化进它的前缀 |
| 用途 | 保持连续（"刚才说到哪了"） | 带上要执行的事（暗号、触发条件、待办） |

**保底 vs 主机制**：尾巴是保底（只保证"接得上话头"）；真正要带过去的临时约定靠便签。
所以尾巴窗口**不为便签兜底**（用户 2026-09-25：「这个不应该是提高保底」）。

## 原文尾巴

- 位置：切会话那一轮的尾部（历史之后、当前时间之前），只出现一次。
- 窗口：**4 轮对话 / 1200 字**（`app/utils/pure/state_stack.py: frame_tail`）。
  实测：那次跨对话测试的契约落在倒数第 3 个用户轮上，2 轮/1200 字装不下（3 条，不含契约）；
  4 轮/1200 字装得下（7 条、1136 字、含契约原文）；4 轮/2000 字结果**完全一样**——
  多给的字数是浪费，别拿 token 换保底。
- 存放：会话的状态帧（`agents.state_stack` 的 `tail` 字段），切走时随交接打包带走
  （`handoff.tail`），注入后清空。
- 标签写两条边界（`format_handoff_tail`）：**别串台**（不要在群里回应私信的内容）+
  **约定要履约**（暗号/触发条件该执行就执行）。只写禁令会让 AI 明明看见了也不敢答——
  2026-09-25 就是这么翻的车。

## 跨状态便签（投递制）

数据分两层：

1. **记录**：`agents.cross_state_notes`（JSON 数组，迁移 `b8d2e4f6a1c3`）。
   字段 `{id, kind, text, from_context_ref, from_label, created_call, expires_at_call}`。
   时效刻度是 `agents.llm_call_count`（这个 AI 所有状态帧加起来烧掉的调用数），
   不是墙上时间——时间量不出"它还记不记得"（三天没被叫醒 vs 一小时烧 40 次调用）。
   过期只决定**还能不能投递**（清除过时便签：AI 一直忙工作时就不会再收到它）。
2. **投递副本**：会话状态帧的 `notes` 字段（`note_copy`）。投递时抄一份进帧，
   此后每轮由 `format_frame_notes` 渲染出**字节完全相同**的块。

**进上下文的姿势（关键，对齐锁）**：便签块固定在 **message 0 之后、会话头之前**
（`app/ai/llm.py: _frame_notes_prefix`）。位置固定 + 内容固定 → 每轮前缀完全一致 →
缓存命中；它就这样"躺在上下文里"，不重复花 token。

- **只渲染一次**：状态帧是便签的"抽屉"，不是第二个出口——`format_state_stack_summary`
  只读 doing/todo/plan/情感/handoff，从不读 `notes`（回归测试
  `test_note_body_is_rendered_once_not_by_the_state_summary` 钉住；摘要要是也念一遍，
  同一段文字就会在同一份提示词里出现两次）。
- **时机在调用之前**：投递写在构建提示词里（`build_messages` / `build_dm_messages`
  内部，返回的消息数组就是马上要发出去的那份）→ 同一次调用就收到。会话帧在收消息时
  就建好（`response_worker.ensure_active_frame`），早于构建，所以不存在"帧还没建、
  这轮投不进去"的空窗。计时尺度也对齐：`bump_frame_call_count` 在请求返回**之后**
  才 +1，第 N 次调用写下的便签，写它的那次调用不会把自己算进去。
- 投递条件（`deliverable_notes`）：记录还活着、不是本会话自己写的、还没投过。
- **投递是一次性过户**：投进来那一刻抄进状态帧，此后归那段会话的上下文管（锁），
  记录侧只剩「还能不能投」一件事。所以 `sync_frame_notes` 不再回头跟记录对账：只在「有新便签」时
  写一次帧，已投过的一律原样返回（前缀字节因此天然稳定，也少一次无谓写）。
- **过期不撤**（只是不再投给新会话）；**撤下走尾部通知（只发一次）**：AI 删记录 / 清空时
  `retire_frame_notes` 给各会话那份副本打个 `retired` 标记，前缀**一个字节都不动**（改一个字母
  也是断缓存）——前缀里那条照旧渲染成"活着的"样子，改由尾部发一条变更通知：
  `## 📌 便签撤下通知 …（以本通知为准）`（`format_retired_notes_notice`）。
  与「能力变更通知」同款：**一次性投递就完事**，发完 `mark_frame_notes_notified` 盖章，
  同一段对话不会再看到它。原文要带上（只给 id，AI 认不出撤掉的是哪条约定）。
- **解锁（compact / clear）才真删**：`release_active_frame_notes` 丢掉该会话帧的副本，
  便签连同撤下通知一起离场——前缀本来就要在这时重建，不额外付缓存代价。
- 已知边界：会话帧栈深上限 `MAX_STACK_DEPTH = 10`，`ensure_active_frame` 超限丢最旧帧。
  回到那个会话时会重建帧并**重新投递**（记录还在有效期内 → 同样的字节，前缀不破）；记录也过期了
  才真的消失。`doing / todo / tail` 没有这个自愈能力，它们随帧一起没了。真库实测栈深最深 5。
- AI 侧入口：工具 `cross_state_note`（list/add/update/remove/clear）；
  长期要记住的东西**不进便签**，用 `update_self_config` 写进它自己的提示词。

### 和 `manage_records` 的分工（实测踩过）

`manage_records` 是目录级结构记忆（长期画像/档案），它只把**路径**注入上下文，
值要 AI 再 `get` 一次。2026-09-25 那次测试就是 AI 把暗号写进了
`manage_records/admin_instructions/.../跨对话便签_…`：群里那轮确实看得到路径，
但没人去 `get`，值等于丢了。

所以：**临时的、有时效的**（暗号、触发条件、等会儿到别处要做的事）→ 便签；
长期的、要按需检索的结构化数据 → `manage_records`。两个工具的描述里都写明了这条边界。

## 实测记录：一次失败的跨对话测试（2026-09-25）

用户私信约定「群里发『少宇』就回『牛逼！…』」，然后群里喊暗号，AI 答了别的。三个原因：

1. 尾巴窗口 2 轮/1200 字 → 契约在窗口外，AI 只看到"便签：就剩『少宇』暗号那条待验证"；
2. 尾巴标签是**纯禁令** → 就算契约在窗口里，也等于告诉它别照做；
3. AI 没调 `cross_state_note`（6 小时内 0 次），写进了 `manage_records` → 只有路径进上下文。

修完之后（真机回放真实消息列表验证）：

- 新窗口 4 轮/1200 字 → 7 条、含契约原文；
- 便签投递后：第一轮 prompt index 1 = 便签块，第二轮两轮公共前缀 26 条、便签在 index 1 之内
  （即**在缓存前缀里**）、字节一致；记录过期或删除后本会话那份都还在（过户后归锁管）。

## 相关代码

- `app/utils/pure/state_stack.py`：`frame_tail` / `format_handoff_tail` / 帧字段白名单
- `app/services/agent/state_stack_service.py`：`ensure_active_frame`（切会话交接）、
  `frame_turn_context`（存尾巴 + 一次性注入）、`set_frame_notes`
- `app/utils/pure/cross_state_note.py` / `app/services/agent/cross_state_note_service.py`：
  便签纯逻辑与投递
- `app/tools/self_management/cross_state_note.py`：AI 侧工具
- `app/ai/llm.py`：`_inject_cross_state_context`（尾巴，尾部）、`_frame_notes_prefix`（便签，前缀）
- 测试：`tests/test_state_handoff.py` / `tests/test_cross_state_note.py`
