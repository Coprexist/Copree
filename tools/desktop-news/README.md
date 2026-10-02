# 桌面新闻系统（班级一体机版）

每天早上自动联网抓取「上次运行 → 现在」之间的新闻，交给 AI 概括成一句话能读懂的标题 + 摘要 +
**作文可用角度**，以**大字、透明背景、压在桌面最底层**的形式铺在教室一体机上。

- 四个板块：**大事件 / 时政·社会 / 科技前沿 / 新奇趣闻**
- 纯 Python 标准库实现，**不需要 pip 安装任何东西**
- 没配 AI key 也能用：自动降级为规则分类 + 原文摘要，屏幕不会空

```
┌──────────────────────────── 每日新闻速览 ────────────────────────────┐
│ 2026年09月30日 星期三                              数据时间 06:48    │
├──────────────────────────────┬──────────────────────────────────────┤
│ ▍大事件                       │ ▍科技前沿                             │
│  我国成功发射试验二十八号卫星  │  开源大模型推理成本一年下降九成        │
│  卫星进入预定轨道，将开展空间  │  多家机构发布新一代开源模型，同等效果  │
│  环境探测与技术试验……          │  下推理开销显著降低……                 │
│  作文角度：长期主义：大工程…   │  作文角度：科技自立：核心技术买不来…   │
│  09-30 06:12  新华网 2 条报道  │  09-30 05:40  IT之家                  │
├──────────────────────────────┼──────────────────────────────────────┤
│ ▍时政·社会                    │ ▍新奇趣闻                             │
│  教育部部署新学期作业管理      │  野生大熊猫现身村民院坝                │
│  ……                           │  ……                                   │
└──────────────────────────────┴──────────────────────────────────────┘
 时间范围 09-29 06:45 → 09-30 06:45 · 源 17/18 · 抓取 1114 · 卡片 16 · 第 1/2 页
```

## 一、最快上手（一体机上）

### 方式 A：目标机已经装了 Python（最简单）

1. 把整个 `desktop-news` 文件夹复制到一体机（比如 `D:\desktop-news`）
2. 双击 `run_demo.bat` —— 用样例数据预览排版，先确认字号和位置
3. 双击 `run_debug.bat` —— 真实抓取一次，看看内容是否满意
4. 双击 `install_autostart.bat` —— 装好开机自启，以后每天开机自动出现在桌面上

要求：Python 3.9 及以上，安装时勾选 **Add python.exe to PATH** 和 **tcl/tk and IDLE**。

### 方式 B：目标机没有 Python，用编译好的 exe

1. 在一台装了 Python 的 Windows 机器上双击 `build_exe.bat`，得到 `dist\DesktopNews.exe`
2. 把 exe 单独拷到一体机任意目录（例如 `D:\DesktopNews\`），双击即可运行
3. 把 `install_autostart.bat` 也拷过去（会自动识别同目录下的 exe），双击装自启

也可以不自己编译：仓库推送到 GitHub 后，`.github/workflows/desktop-news-build.yml`
会自动构建，在 Actions 页面的 Artifacts 里下载 `DesktopNews-windows.zip`。

## 二、配置 AI（可选，但强烈建议）

编辑文件夹里的 `config.json`（第一次运行后会自动生成）：

```json
{
  "ai": {
    "enabled": true,
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "api_key": "sk-你的密钥"
  }
}
```

任何 **OpenAI 兼容**接口都能用，只改 `base_url` 与 `model` 即可：

| 服务 | base_url | model 示例 |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 本地 Ollama | `http://127.0.0.1:11434/v1` | `qwen2.5:7b` |

成本控制已经做在三处，正常使用**每天几分钱**量级（按各服务官网价目为准）：

- 先在本地把同一事件的多条报道**合并成一个候选**再送 AI，输入量降一个数量级；
- 单次最多 `ai.max_items_per_call × ai.max_batches` 个候选，超出部分自动走规则卡片；
- 概括结果按标题指纹缓存 `ai.cache_days` 天，白天多次刷新不重复计费。

## 三、每天是怎么跑起来的

1. **开机**：启动器延迟 20 秒拉起程序 → 程序发现距上次抓取超过 `startup_stale_hours`（默认 8 小时）→ 立刻抓一次
2. **抓取**：并发拉取全部启用的新闻源（RSS + 热搜榜 JSON），失败的源单独记录，不拖累其他源
3. **筛选**：只保留 `上次运行时间 → 现在` 之间的条目，已推送过的按指纹跳过
4. **合并**：本地判断「哪些报道是同一件事」，多源同时报道的自动加权（这就是大事件的排序依据）
5. **概括**：送 AI 生成标题/摘要/作文角度；AI 不可用则用最长原文摘要 + 规则分类兜底
6. **上屏**：写入 `data/latest.json`，界面读取后重绘；卡片放不下时每 25 秒自动翻页
7. **每天 06:45**（`refresh_hour`/`refresh_minute`）自动刷新一次

## 四、常用操作

| 想做的事 | 做法 |
| --- | --- |
| 立刻刷新 | 按 `Ctrl+Alt+R`，或双击 `run_once.bat` |
| 临时隐藏 / 显示 | `Ctrl+Alt+N` |
| 整块屏幕鼠标穿透（纯展示） | `Ctrl+Alt+T` |
| 退出程序 | `Ctrl+Alt+Q`（或重启后不再自启） |
| 抓取一次看结果 | 双击 `run_once.bat`（有控制台输出，能看到每个源成功与否） |
| 体检网络与接口 | 双击 `check_env.bat` |
| 调整字号 | 改 `config.json` 的 `ui.font_scale`（1.0 基准，想更大就 1.2、1.4） |
| 调整板块条数 | 改各分类的 `max` |
| 取消开机自启 | 双击 `uninstall_autostart.bat` |

## 五、关键配置项

```jsonc
{
  "app": {
    "refresh_hour": 6,              // 每天几点刷新
    "refresh_minute": 45,
    "startup_stale_hours": 8,       // 开机时数据超过 8 小时就立即抓
    "window_hours_default": 24,     // 首次运行回溯多久
    "max_window_hours": 96,         // 假期回来最多回溯多久
    "cluster_threshold": 0.42,      // 判定「同一件事」的相似度：调高更保守、调低更激进
    "allow_insecure_fallback": false // 校园网 SSL 中间人导致抓不到时改 true
  },
  "ui": {
    "desktop_mode": "auto",         // workerw / bottom / normal / auto
    "alpha": 0.9,                   // 面板透明度
    "font_scale": 1.0,              // 字号整体缩放
    "monitor": 0,                   // 多屏时选第几台显示器（0 开始）
    "click_through": false,         // true = 整窗鼠标穿透
    "page_rotate_seconds": 25,      // 翻页间隔
    "summary_lines": 3              // 每条新闻摘要显示几行
  }
}
```

### 三种置底模式

| 模式 | 效果 | 适用 |
| --- | --- | --- |
| `auto`（默认） | Windows 上等同 `bottom` | 一般情况 |
| `bottom` | 压在图标之上、所有窗口之下；按「显示桌面」会被一起藏起来 | 想看清桌面图标时 |
| `workerw` | 挂进壁纸层，成为桌面的一部分，**「显示桌面」也藏不住它**，但会被桌面图标盖住 | 教学软件老抢焦点时 |
| `normal` | 普通窗口，仅调试用 | 调排版 |

## 六、常见问题

**背景没有透明 / 整块变成灰色**
抠色透明要求窗口管理器支持 `-transparentcolor`。确认 `ui.transparent_color` 是 `#010203`
这种罕见颜色，并且没有被别的东西占用；远程桌面（RDP）下透明会失效，这是系统限制。

**新闻压在桌面图标下面看不见**
把 `ui.desktop_mode` 从 `workerw` 改成 `bottom`。

**被一体机的教学软件挡住**
`workerw` 模式下会变成桌面的一部分，通常不被遮挡；另外程序每 3 秒会重新压底一次。

**早上第一节课还是昨天的新闻**
看日志 `logs/newscast.log`：多半是开机时网络还没就绪。程序会在每次刷新时重试，
也可以在 `install_autostart.bat` 里把启动延迟加大（`--delay 45`）。

**某个源一直失败**
`check_env.bat` 会列出每个源的成败与原因。RSS 源改版很常见，把 `config.json` 里对应的
`enabled` 改成 `false`，或者补一个新的 RSS 地址即可，不影响其他源。

**AI 没生效**
界面底部会直接写明原因（未配置 key / 接口报错）。没有 AI 时程序自动走规则整理，照样能用。

**想看程序到底在干什么**
`logs/newscast.log`，逐次记录时间窗口、每个源的成败、聚类结果、AI 批次与错误。

## 七、目录结构

```
desktop-news/
├─ news_desktop.py        入口：常驻 / --once / --debug / --demo / --check
├─ autostart.py           开机自启安装器（生成 VBS 启动器）
├─ config.example.json    默认配置样例（首次运行会生成 config.json）
├─ newscast/
│  ├─ config.py           配置默认值与合并、来源清单
│  ├─ textutil.py         清洗、指纹、时间解析、相似度
│  ├─ netclient.py        urllib 封装：UA、gzip、编码嗅探、重试
│  ├─ sources.py          RSS/Atom 解析 + 热搜榜适配器 + 并发抓取
│  ├─ searchapi.py        可选搜索接口（Tavily / 博查 / Serper / 自定义）
│  ├─ classify.py         聚类、规则分类、作文角度、降级卡片
│  ├─ ai.py               OpenAI 兼容接口调用与 JSON 兜底解析
│  ├─ pipeline.py         时间窗口 → 去重 → 概括 → 落盘
│  ├─ render.py           Canvas 大字排版、分页轮播
│  ├─ win32.py            置底 / 抠色透明 / 穿透 / 热键 / 多显示器
│  └─ store.py            状态、最近结果、AI 缓存的原子读写
├─ tests/test_core.py     34 项离线单测（无需联网与 tkinter）
└─ *.bat                  一键脚本
```

## 八、开发与验证

```bash
python -m unittest discover -s tests          # 34 项离线单测
python news_desktop.py --check                # 体检：源连通性 / AI 接口 / 显示器
python news_desktop.py --once --no-ai         # 只抓取不调 AI，看抓取结果
python news_desktop.py --demo --debug         # 样例数据预览排版
```

非 Windows 系统同样能跑：自动降级为 `normal` 模式（没有透明与置底），
用来调排版、验证抓取逻辑都够用。
