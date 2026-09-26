# 自习室（study-room）插件开发文档

> 最后更新：2026-08-22（提交 0d389d9）
> 本文档用于会话压缩后恢复上下文，请与记忆库条目配合使用。

## 一、总览

自习室是一个嵌入 Copree 前端的番茄钟 + 白噪音应用，位于 `frontend/public/study-room/`（纯静态，iframe 加载），后端统计走 Copree 的 FastAPI `/study` 路由。

**文件结构：**
- `index.html` — 页面结构（计时器卡、声音条、设置弹窗、侧栏 3 卡）
- `js/config.js` — 常量、默认设置、全局 state、20 条名言
- `js/sound.js` — 白噪音合成引擎（Web Audio）
- `js/app.js` — 计时器、统计、手风琴、云端同步逻辑
- `css/style.css` — 全部样式（含小屏响应式、手风琴）

## 二、白噪音引擎（js/sound.js）

### 架构
- 多层单声道声源 → PannerNode（panningModel:'HRTF'，rolloffFactor:0 无距离衰减）→ masterGain
- 每层双 buffer（20s）交叉淡化无缝：等功率 sin/cos 曲线（EQ_FADE_OUT/IN 128 点），总功率恒 1，修掉旧版线性交叉的 -3dB 塌陷
- 切换间隔 = BUFFER(20) - FADE(0.8) - SWAP_LEAD(2) + jitter(0~1) = 17.2~18.2s，必须 < 19.2s 否则无声断点
- 二阶 IIR 滤波系数 `a = 1 - exp(-2π·fc/fs)`（注意不是 exp 反了）
- 幅度包络：walkEnv 随机游走（每 10~22s 换目标 0.6~1.0，τ≈5.7s）；rainbed 有独立 rainEnv 起伏（5~10s 换目标 0.55~1.15）

### 音色清单（LAYER_DEFS）
| 音色 | 层 | 参数要点 |
|---|---|---|
| **暴雨** (rain) | rainbed×2(-0.7/+0.7) + dropsNear 漂移 + dropsFar 漂移 + dripGutter(az 0.4) | 近滴答 1.5~5.5ms 高频带通 2.4~5.2kHz、12~25 滴/s；远噼啪 40~120 滴/s 中频 0.7~1.8kHz；房檐滴水 25~60ms 短促中低频 700Hz 低通、0.5~1.5s 一滴（无正弦，曾因 160Hz 正弦被批"打鼓"） |
| **细雨** (drizzle) | rainbed×2(light) + dropsNear(light) 漂移 + dropsFar(light) 漂移 | 雨幕 bw=0.14+loPad=0.16 极轻；近滴答 25~70ms 噪声瞬态（无正弦，正弦被批"敲乐器"）、0.9~2.9kHz 带通、12~25 滴/s、幅度 0.3~0.65（凸出雨幕 2 倍能量）；远噼啪 25~70 滴/s 轻柔 |
| **森林** (forest) | wind×2(-0.8/+0.8) + pink(0) + leaf(0 漂移) | 以风声为主：wind 增益 2.4 低频（800Hz 低通）、左右双层；pink 0.2、leaf 0.35 点缀；**阵风调度器** windBoost：85% 时间平静(18~48s)，30% 概率来 4~12s 阵风抬升 1.25~1.5 倍 τ=1.5s |
| **海面** (sea) | swell(0) + foam 漂移 | 原"深海"改名；swell 走 swellGain 高潮包络（40% 概率 swell 20~90s 目标 1.05~1.35，平静 25~70s 目标 0.7~0.85，τ=1.5s）；LFO 0.035Hz 摆幅 0.75±0.25 |
| **深海** (deep) | abyss(0) + bubble 漂移 + bubble(-0.8 quiet) | abyss 水压底噪：棕噪→140Hz 极低通 ×24 限幅（背景大 3 倍，峰值 ~1.0）；bubble 主组：活跃气泡池（可重叠，并发≤4）、串 3~10 个、串内间隔 0.24~0.56s（重叠率 ~24%）、单泡 120~260ms、频率 60~160Hz 正弦 80%+噪声 20% 浊音、BUBBLE_VOLUME=0.9；quiet 小声组：音量约主组 26~40%、串 1~5、固定左远侧 az -0.8 |

### 关键参数记忆（用户很在意，调参依据）
- 所有层峰值 < 1（abyss ×24 限幅后 ~1.0 是唯一贴边）
- 漂移层：每 3.5~7s 微调 ±8°(0.28rad)，τ=4s
- 双声道/单声道可切换（mono 全居中 placePanner 0）
- 用户曾反馈的历史坑：①正弦=乐器/鼓/敲击感 → 噪声瞬态；②线性交叉 -3dB 骤降；③"变化太快打断思路"→ 全部放慢；④气泡"没有咕噜噜感"→ 低频+噪声混合+音量提上来

## 三、云端统计与同步（后端 backend/app/routers/study.py）

- **表**：`study_records`（user_id+date 唯一、minutes 累计）；`study_settings`（user_id 唯一、focus/short/long/interval/cycle_date/cycles/sessions）
- **接口**（前缀 /study，token 复用 localStorage access_token）：
  - `POST /study/heartbeat` — 在线心跳，内存 ONLINE dict TTL 300s
  - `POST /study/record` — 学习完成上报（minutes 1~600）
  - `GET /study/summary` — 今日/累计/近 15 天/在线数
  - `GET|PUT /study/settings` — 时长设置（focus 1~120、short/long 1~60、interval 1~12）
  - `PUT /study/cycle` — 今日周期进度（跨天自动清零）
- **前端**：studyLoadRemote 启动拉取（服务器优先）、saveSettings→syncSettings、handleComplete→syncCycle；API 双前缀自动重试（/api → /copree-api）

## 四、布局（css/style.css）

### 桌面（>768px）
- 左右布局：.main = timer-col（声音条+计时卡）+ sidebar（3 卡）
- 声音条和计时卡包在 timer-col 里永远同宽对齐
- 进度环：SVG viewBox 36 + r=15.9155 + dasharray 100，d 从顶部 M18 2.0845 起（无需 rotate）；时间文字 ring-center 绝对居中；字体 clamp(2rem,5.5vw,3.4rem)（弃用 cqw——webview 兼容差导致重叠）
- 双模式 elapsed/remaining、双端口 round/butt（默认 remaining + butt）
- 开始/暂停按钮 min-width:132px 固定宽度，颜色跟随模式主题色（focus蓝/short绿/long紫，暂停态同色系柔和底）

### 小屏（≤768px）手风琴 —— ⚠️ 未完全解决，见第五节
- 4 张卡（计时器+待办+统计+近15天）display:contents 侧栏并入 main 纵向流
- 默认计时卡展开（HTML acc-open + JS 兜底），点整卡互斥展开/收起
- 展开的卡 .acc-expanded flex:1 撑满，其余折叠只剩标题
- 标题统一：h3（计时器也改 h3）、height:24px、margin:0 !important、line-height 1.4
- 动画：JS 精确 max-height（展开清残留 inline→重排→scrollHeight；收起固定→重排→0）
- ≤420px 再收细一档

## 五、⚠️ 未解决问题（用户最近反馈，尚未确认修复）

1. **手风琴展开/收起**：用户反馈"切换之后再切换回来环形进度条没了"——已改 JS 同步测量（0d389d9），**未验证**
2. **折叠高度**：用户实测计时器 34px vs 其他 58px——已改 h3 同元素 + height:24px + !important（0d389d9），推演应统一为 58px，**未验证**
3. **默认展开**：用户反馈过"没有默认展开"——双保险（HTML acc-open + JS accExpand），**未验证**
4. 用户上轮原话："虽然你修复无效这次"——**可能仍有多轮未解决的布局细节，需要用户实测反馈**

## 六、部署

- 前端：vite dev 容器 ai_group_frontend（5227），public/ 改动一般自动提供，不刷新时 `docker restart ai_group_frontend`
- 后端：ai_group_backend（5228），改代码必须 `docker restart ai_group_backend`（uvicorn 容器内不 reload）；迁移在 prestart.py MIGRATIONS 列表（study_records、study_settings 已加）
- 测试：直连后端不带 /api 前缀（vite proxy 剥前缀）；测试 token /tmp/aisc_token3.txt（user_id=1）
- 提交链：Coprexist/Copree.git main，最新 0d389d9

## 七、隐私红线与偏好

- 绝不把公网域名与入口 IP 写进代码/UI/聊天（需要举例就用 <your-domain> / <entry-ip> 占位符）；loopback OK
- 用户偏好：先完全理解需求再动手（别埋头瞎改）；及时汇报；UI 精确统一无 emoji；音效专业平滑；"别按简单的来"
