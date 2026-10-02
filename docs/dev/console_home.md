# 管理台首页（总览）的版面口径

> 代码：`frontend/src/pages/console/general/OverviewTab.tsx`；最近访问：`frontend/src/pages/console/recentTabs.ts`

## 一句话

首页按顺序回答三件事：**要你处理什么 → 现在什么状况 → 去哪个页面**。

## 版面（从上到下）

1. **需要你处理**（0~N 行，空时一行"一切正常"）
   - 待审申请 —— `GET /requests/pending`（好友 / 入群 / 成员邀请同一口径，跳应用内 `/list`）
   - 疑似攻击来源、失败被锁 —— `GET /admin/ops/overview?days=1`（跳 `?tab=logs&log_type=login_failed`）
   - API Key 池没有启用的 Key —— `GET /admin/api-key-pool`（跳 `?tab=apipool`）
   - 备份缺失、或最近一份超过 2 天 —— `GET /admin/backups`（跳 `?tab=backup`；2 天 = 每日备份的正常间隔）
   - 维护模式开着 —— `GET /admin/maintenance`（跳 `?tab=system`）
2. **平台现状**：六个数字一线摆开。用户 / AI / 群来自 `GET /admin/overview`；
   近 24 小时对话轮次 / 失败登录 / 新用户来自 `GET /admin/ops/overview?days=1`（口径写在那边的接口里，
   首页不另算一份）。点数字进对应页。
3. **最近访问**：最近 5 个页签（本地存 key，`recentTabs.ts`），ConsolePage 在页签生效时记一次。
4. **维护模式开关 + 文案编辑**：沿用原有组件。
5. **全部入口**：把 20 个页签按工作区平铺，数据直接取 `workspaces.tsx`——新增页签自动出现，不用改首页。

首页每个块各自失败不影响其它块：六处接口并发取，任一失败只让那一段空着。

## 为什么不把所有东西都堆在首屏

调研过的控制台首页没有一个是按钮墙，共同结构是"待办 + 少量数字 + 最近访问/收藏"，外加一条
"到任何地方"的路（目录 / 搜索 / 命令面板）：

- AWS Console Home 是可配置的 widget 面板，默认带 **Recently visited** 与 **AWS Health**；
  全局导航（搜索 / Favorites / Recently visited）承担"去任何服务"。
  https://docs.aws.eu/awsconsolehelpdocs/latest/gsg/work-with-widgets.html
- Azure 的 Favorites 从 "All services" 里自选；首页面是 Favorites + Dashboards。
  https://learn.microsoft.com/en-us/azure/azure-portal/azure-portal-add-remove-sort-favorites
- 阿里云控制台首页明确以"资源为中心"对抗"多入口、碎片化"：最近访问、我的收藏、资源概览、
  **监控告警**、费用概览。 https://www.alibabacloud.com/help/zh/management-console/console-home-1
- 腾讯云靠"云产品目录 + 搜索框 + 最近访问"+ 自加快捷入口。 https://cloud.tencent.cn/document/product/567/14459
- Stripe 首页 = 图表 + **重要通知**（未决争议、身份验证）；Sentry 落地页是排好序的 Issues 流。
  https://docs.stripe.com/dashboard/basics ｜ https://docs.sentry.io/product/issues/
- NN/g：渐进披露（先给最重要的，进阶的按需展开）、仪表盘要"一眼可读且对应一个决定"、
  告警一多就没人看；顺带否掉用"三次点击"给按钮墙辩护。
  https://www.nngroup.com/articles/progressive-disclosure/ ｜
  https://www.nngroup.com/articles/dashboards-preattentive/ ｜
  https://www.nngroup.com/videos/alert-fatigue-user-interfaces/ ｜
  https://www.nngroup.com/articles/3-click-rule/

## 颜色规则（只用主站品牌四色，色相只表示语义）

| 色 | 含义 | 用在哪 |
|---|---|---|
| 紫 `primary` | 存量 / 动作 | 区块标题的色块、数字里的用户/AI/群、"去处理"的箭头、入口悬停与当前页 |
| 金 `accent` | 需要你处理 | 「需要你处理」整块的边框、标题带与渐变、每条待办的小色块 |
| 玫瑰 `rose` | 风险 | 数字里的登录失败、维护模式的"已暂停" |
| 薄荷 `mint` | 正常 / 增长 | 无待办时的标题带、数字里的新用户、维护"正常" |

- 同一种元素（六个数字、每条待办、每个入口）**形状与字号完全一致**，色相只用来区分不同类的东西；
  不同类之间靠色相 + 位置区分，不靠边框粗细或字号乱变。
- 色块一律 `bg-{色}-500/10` 一档透明度，数字卡再加一层 `/5` 的底色；渐变只用静态 Tailwind 类
  （`bg-gradient-to-r from-accent-500/12 to-transparent`），不写运行时算色、不用动画——不额外吃帧。
- 圆角只用尺度令牌（`rounded-control` / `rounded-card`），不出现裸数值。

## 外壳

左边栏顶端的「返回应用」是刻意放在顶部的：应用侧边栏在控制台里是收起的，"离开"是随时可能点的
动作，不该跟着列表长度往下跑。

## 已知取舍

- **"全部入口"与左边栏重复**：左边栏只列当前工作区的页签，跨工作区要点两次；平铺一屏换来
  "一眼看全"。所以它放在最下面、按工作区分组，不是首屏主角。
- **没有做全局搜索 / 命令面板**：页签只有 20 个、单人低频使用，按上面 NNG 的判据还不够格；
  等页签数或动作数继续涨（比如每个页签再挂操作）再上。
- **没做的块**：延迟 / 队列深度之类的实时指标（没有采集源）、按页签挂红点（只有"真的有人等你"
  才配红点，否则就是告警疲劳）、可配置的卡片顺序与收藏（等首页稳定后再看值不值）。
- **数字口径**：首页只负责摆，口径全部留在各自接口里；改口径时改一处。
