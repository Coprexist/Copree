# 状态栈 × 情感记忆 × 工具隔离 设计（v0.1）

> **日期**：2026-08-08
> **状态**：阶段 1 已实现（情感向量/跨状态同步/工具隔离/分状态调用计数/配置开关）；记忆衰减（rough_memories importance）与长期情感记忆表留阶段 2
> **相关文档**：`brain_controller_design.md`（状态栈 §4.3/4.4）、`memory_system_design.md`（双重记忆）

---

## 一、背景与目标

现状：
- **状态栈**（已实现）：push/pop/close/resume，帧含 why/doing/todo/plan/journal，摘要注入 prompt——任务的**精确交接**已完整
- **双重记忆**（已实现）：System 1 向量情节 + System 2 结构语义
- **缺口**：状态切换只交接"任务"，不交接"情感"；工具集全局统一，不随状态分化；记忆衰减用现实时间，与 AI 本体论不符

目标（2026-08-08 探讨）：
1. **情感跨状态同步**：保留本状态情感基线，切换时附上来源状态情感（并置不抹除）
2. **情感向量化**：独立多维轴（非单轴对立），AI 可传增量/完整向量/概括词；配置开关「向量化情感」丰俭由人
3. **工具/技能按状态隔离**：聊天帧只有聊天工具，写代码帧才有代码工具，跨界即切换状态
4. **情感随时间（AI 调用次数）衰减**：符合 AI 本体论的时间尺度

---

## 二、学术依据

| 主张 | 依据 |
|------|------|
| 独立多维情绪轴（反对单轴对立） | **ESM 评估空间模型**（Cacioppo 1999）：积极/消极双系统独立，可双高（ambivalence）、双低（indifference）；推广为多情绪轴 = 离散情绪模型 multi-label 路线（Ekman/Plutchik） |
| 双高/双低表达 | 混合情绪研究（mixed emotions，Cacioppo 等）：人可同时体验积极与消极 |
| 情感向量化 + 多粒度输入 | 情感计算 free-form vs structured 标注；LLM 情绪 prompting 自选粒度 |
| 情感衰减 | mood homeostasis（情绪回归基线）；**创新点：用 API 调用次数作时间尺度**（AI 无真实时间感，经历量=被调用量） |
| 跨状态情感同步 | Mood vs Emotion 区分：mood 慢变跨情境保持，emotion 瞬时 |

> 单点均有先例；「状态栈×情感向量×跨状态同步×工具隔离×拟人开关」的组合为原创设计。

---

## 三、情感向量模型

### 3.1 轴定义（Ekman 6 类起步，可扩展）

```json
{
  "joy": 0.0,       // 开心
  "sadness": 0.0,   // 伤心
  "anger": 0.0,     // 愤怒
  "fear": 0.0,      // 恐惧
  "disgust": 0.0,   // 厌恶
  "surprise": 0.0   // 惊讶
}
```

- 每轴独立 0-1，**不设对立合并**（低落=joy 低+sadness 高；复杂情绪=joy/sadness 双高）
- 扩展预留：Plutchik 8 类（+trust 信任 / anticipation 期待）——轴集合可配置

### 3.2 输入模式（AI 三选一，或混用）

| 模式 | 示例 | 处理 |
|------|------|------|
| **增量** | `emotion: {anger: +0.2}` | 加到当前向量，clamp 0-1 |
| **完整向量** | `emotion: {joy: 0.8, sadness: 0.3}` | 覆盖 |
| **概括词** | `emotion: "平静" / "喜极而泣"` | 映射表 → 默认向量（可配置） |

### 3.3 情感持久化

- **状态帧 `emotion` 字段**：本状态情感基线（随帧生命周期）
- **切换时**：push 记录来源帧情感快照；新帧从基线开始，摘要并置显示
- **长期情感记忆**（可选阶段 2）：独立情感记忆表（向量+情感标注），走 System 1 检索

---

## 四、状态帧扩展

```python
class StateFrame:  # 扩展字段
    id: str
    type: str                # group_chat / dm / file_work / alarm / project / write
    context_ref: str
    why: str                 # 触发原因（为什么切换过来）
    doing: str               # 当前任务
    todo: list[str]          # 待办（含"干完后回去继续啥"）
    plan: str
    journal: str             # 已完成记录（干了啥）
    status: str              # active / paused / cancelled
    # ── 新增 ──
    emotion: dict            # 本状态情感向量（{joy: 0.2, ...}）
    emotion_text: str        # 文字心情描述（未向量化时用）
    source_emotion: dict     # 来源状态的情感快照（push 时记录）
    tools: list[str] | None  # 工具白名单（None = 全部，随状态隔离）
    skills: list[str] | None # 技能白名单
```

### 摘要格式（注入 prompt）

```
📋 状态栈（底→顶）
▸▶ [group_chat](group:2): 当前活跃任务  TODO: xxx
   🎭 情感: 开心 0.4 · 专注(文字描述: "平静")  ← 来源(写作): 开心 0.2
```

- 情感显示：向量化时显示各轴强度（或仅非零轴）；未向量化显示文字描述
- 来源情感：push 后新帧摘要显示"← 来源(旧状态): 情感快照"

---

## 五、情感衰减（阶段 1 简化版）

```
decay_score = base_decay × Δcalls − access_reward − emotion_bonus
```

| 参数 | 默认 | 说明 |
|------|------|------|
| base_decay | 0.01/次调用 | 每次 AI 被调用（LLM 请求）衰减 |
| access_reward | +0.1 | 被 recall_memory 命中 |
| emotion_bonus | 情感强度×0.05 | |joy|+|sadness|+... 高的记忆衰减慢 |
| 阈值 | < 0.3 | 降级 active → pending_archive → discarded（已有状态机） |

- **时间尺度 = Δ调用次数**（agent 的 LLM 调用计数），非现实时间
- 计数来源：`conversation_log` / `world_llm_usage` 按 agent 聚合，或新加 `llm_call_count` 字段（实现时定）

---

## 六、工具/技能按状态隔离

- 状态帧 `tools`/`skills` 白名单：push 时可指定，None=继承全局
- LLM 构建工具时：按**栈顶帧**的 tools/skills 过滤（llm.py 工具清单构建处）
- 跨界：AI 调 push_state 切到新类型帧（工具集随之切换）
- 默认映射（type → 工具集，可配）：
  | 帧类型 | 工具集 |
  |--------|--------|
  | group_chat / dm | 群聊社交 + 记忆 |
  | file_work / write | 文件操作 + 沙箱 |
  | alarm | 闹钟 + 消息 |
  | project | 全部 |

---

## 七、配置开关（AI 配置界面）

- **「向量化情感（更拟人）」勾选**：勾 → 用情感向量（0-1 各轴）；不勾 → 文字心情描述（emotion_text）
- 开关存 agents 表（新字段 `emotion_vectorized`）或 context_config
- 开关对摘要显示与 AI 可用工具/字段生效

---

## 八、实现计划

### 阶段 1（本次）
1. **迁移**：`agent_state_stack` 帧结构扩展（emotion/emotion_text/source_emotion/tools/skills 字段，JSONB）+ agents 表 `emotion_vectorized` + `llm_call_count`（衰减计数）
2. **状态栈服务**：push/pop 情感携带（push 记录 source_emotion；pop 恢复）、摘要格式扩展（情感行）
3. **情感工具**：`update_emotion(delta|vector|word)` 工具（AI 可调）；概括词映射表
4. **工具隔离**：llm 工具构建按栈顶帧 tools/skills 过滤 + type→默认映射
5. **情感衰减**：LLM 调用后按 agent 计数衰减记忆 importance（简化版）
6. **配置**：agents 配置界面勾选开关

### 阶段 2（后续）
- 长期情感记忆表（向量 + 情感标注，System 1 检索）
- 衰减完整版（访问奖励 + 情感加成 + 复活机制）

---

## 十、交接体系（已实现）

**核心语义：状态栈是“交接驱动”的，不是“历史渲染”的**

- **摘要只注入「当前帧 + 本次交接」**：旧交接已在 LLM 对话历史里出现过（工具调用参数保留），不重复注入——省 token、不冗余
- **切换时一次性打包交接**：push 时帧记 `handoff`（从哪来/在干嘛/为什么/回去继续 todo）
- **pop 选择性回跳**：`pop_state(target_frame_id)` 可指定目标层，中间帧归档（completed + journal）并在摘要汇报「跳过层（未完成）」，由 AI 决定是否继续
- **pop 回来注入「回来的交接」**：恢复帧记 `completed_handoff`（刚完成啥 + 跳过了谁），摘要顶部 `📝 刚完成` 展示——回来的自己天然知道出去干了啥
- **嵌套提示**：栈深 > 1 时摘要给「⏸ 另有 N 帧未完成（可 list_states 查看）」；新增 list_states 工具
- **摘要格式**：
  ```
  ## 📋 当前状态
  ▸▶ [alarm] (alarm:1): 处理闹钟提醒
     TODO: 处理完回消息
     🎭 情感: 惊讶 0.6
     ← 来源状态(group_chat)情感: 开心 0.4
     ⏸ 另有 2 帧未完成（可 list_states 查看）
     ← 从[group_chat] 回张三的消息来 · 原因: 闹钟响了 · 回去继续: 处理完回消息
  📝 刚完成: [alarm] 处理闹钟提醒
     （跳过了 [group_chat](回张三的消息)）
  ```
- **缓存视角**：摘要整体在帧内字节级不变（同帧 92% 命中保持）；内容大幅缩短（只当前帧+交接）→ 切换代价更小

## 十一、开放问题（后续）
- 记忆衰减（rough_memories importance）需记忆-状态绑定设计（阶段 2）
- 长期情感记忆表（阶段 2）
- 状态帧缓存 ID：实测同帧 92% 命中已天然成立，不再需要
