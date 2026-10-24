# 03 文件操作
> \`file_list / file_write / file_read / file_grep / file_edit / file_delete / file_move / file_copy\` 全部参数与返回、类型策略（允许清单 + 禁用后缀）、越界防护、读取截断规则，以及下载文件的固定落点。**建/改世界网页代码前必读**。

## 1. 概述

世界文件存储在**隔离目录** \`data/worlds/{world_id}/\`，只能通过工具或 \`/world/{id}/files/\` 路由访问。**不允许越界访问**：\`../\`、绝对路径、符号链接逃逸一律拒绝。

### 1.1 类型策略（写文件前先看这一节）

**允许**（不在清单内一律拒绝）：
- 网页与世界代码：\`html htm css js mjs json map py\`
- 纯文本源码/文档/配置/数据：\`md txt rst tex csv xml yaml yml toml ini cfg conf env log sql ts tsx jsx vue svelte java c h cpp hpp cs go rs kt swift\`
- 媒体与字体：\`png jpg jpeg gif svg webp ico bmp woff woff2 ttf otf eot mp3 wav ogg mp4 webm\`
- 文档/压缩包：\`pdf zip\`（zip 内的文件在导入时逐个过同一套检查）

**禁止**（可执行文件、安装包、系统库、宿主脚本）：
\`.exe .msi .msp .msu .com .scr .pif .cpl .dll .sys .drv .ocx .inf .lnk .reg .hta .msc .gadget .bat .cmd .sh .bash .zsh .ksh .csh .fish .vbs .vbe .jse .wsf .wsh .ps1 .psm1 .psd1 .jar .class .apk .app .deb .rpm .dmg .pkg .snap .iso .img .bin .so .o .a .dylib .elf\`

三层防护，AI 与用户同一套：① 提示词明写不得下载脚本/程序文件；② **创建时直接拒绝**（\`⛔ 禁止 .xxx 类型文件\`，改后缀也无效，因为按后缀判定）；③ 目录里遗留的会被**兜底扫描强制删除**（世界唤醒、zip 导入、后端启动各扫一次）。

需要运行逻辑就写成 \`.js\`（前端执行）或 \`.py\`（世界代码，沙箱执行）——**除这两种之外没有可执行的东西**。

**单文件上限 32MB。**

## 2. 工具一览

| 工具 | 用途 | 关键参数 |
|------|------|----------|
| \`file_list\` | 列出世界文件（默认排除产物；多了按目录汇总） | 可选 \`prefix\`、\`include_artifacts\` |
| \`file_write\` | 创建或写入文件（自动建目录，落盘前语法自检） | \`path\`（相对路径，如 \`css/style.css\`）、\`content\` |
| \`file_read\` | 读取文件内容（编辑前确认用） | \`path\`、可选 \`offset\`/\`limit\` 按行分页 |
| \`file_grep\` | 搜索文件内容并定位行号（可带上下文） | \`path\`（文件/目录/数组）、\`pattern\`、可选 \`max_hits\` |
| \`file_edit\` | 增量编辑（查找替换/插入/删行），省 token；落盘前语法自检 | \`path\`、\`operation\`、按需参数 |
| \`file_delete\` | 删除文件或目录 | \`path\` |
| \`file_move\` | 移动 / 重命名（目录可整体搬移） | \`from\`、\`to\`（都要写完整文件名） |
| \`file_copy\` | 世界内复制文件 / 目录 | \`from\`、\`to\` |

## 3. 各工具详细

### 3.1 file_list

\`\`\`json
{ "prefix": "js/", "include_artifacts": false }
\`\`\`
\`\`\`
→ { "success": true, "total": 203, "shown": 0, "truncated": true, "excluded": 11,
    "dirs": { "js/game/": 27, "content/data/achievements/": 9, "css/": 18, … },
    "files": [],
    "note": "共 203 个文件，超过 50 个只给目录汇总；用 prefix 缩小范围（如 prefix=\"js/game/\"）可看具体文件；已排除 11 个产物文件…" }
\`\`\`

- 返回**相对路径列表**（\`files\`）+ \`total/shown/truncated\`，可用于确认目录结构后再读写。
- \`prefix\` 只看某个子目录/前缀（如 \`js/\`、\`js/game/\`）；文件多时先看目录再钻进去，别一次列全部。
- 默认**不列产物**（\`__pycache__/node_modules/.git/dist/*.pyc\` 等）；确实要看传 \`include_artifacts: true\`。
- 文件超过 50 个：\`files\` 为空，改为给 \`dirs\`（各目录文件数）+ \`note\` 提示；目录名可直接当下一次 \`prefix\` 用。
- **根目录文件永远列名字**（\`root_files\`）：\`index.html\` / \`main.py\` 这类最该看的文件不会被塞进 \`dirs\` 的一个计数里。

### 3.2 file_grep

\`\`\`json
{ "path": ["js/", "main.py"], "pattern": "class", "max_hits": 30 }
\`\`\`
\`\`\`
→ { "success": true, "paths": ["js/", "main.py"], "pattern": "class", "total_hits": 3, "files_scanned": 18,
    "hits": [ { "path": "js/core.js", "line": 12, "content": "class Game {" } ] }
\`\`\`

- \`path\` 支持**文件、目录（递归搜索）与数组**（多文件/多目录混合），传 \`"."\` 搜整个世界——找「哪些文件用了某段代码」用它，不用自己写沙箱脚本扫目录。
- 目录递归**跳过产物目录与超过 2MB 的大文件**；每条命中带 \`path\` + \`line\`；**单文件**时返回形状与旧版一致（命中只有 \`line\`/\`content\`）。
- 命中默认最多 30 条（\`max_hits\`，上限 100），到上限在 \`note\` 里提示——缩小 \`path\` 或改用更精确的 \`pattern\`。
- \`pattern\` 按 Python 正则解释：emoji 直接写字符，码点转义用 \`\\uXXXX\` / \`\\U0001F300\`；JS 风格的
  \`\\x{1F300}\` 会自动换算成同样的码点。确实编译不成正则时按普通子串匹配（大小写不敏感），
  并在结果里带 \`pattern_note\` 说明——**0 命中又带 pattern_note 时是「搜法退化了」，不是「文件里没有」**。
- \`context: 1~5\` 直接带命中行的前后各 N 行（带行号、按行合并去重）——**多半就不用再 \`file_read\` 看上下文**；
  被邻近命中完全覆盖的命中其 \`context\` 为空数组（信息没丢，只是挂在别处）；\`context=0\`（默认）返回形状与旧版一致。

### 3.3 file_write

\`\`\`json
{ "path": "css/style.css", "content": "body { color: red; }" }
\`\`\`

- **自动创建缺失目录**；目标已存在则覆盖。
- **类型策略见 1.1**（禁用后缀直接拒绝）。
- **落盘前语法自检**：\`.py\` 走 \`ast\`（真解析器），\`.js\`/\`.css\` 走结构扫描
  （括号配平、字符串/注释/模板串闭合、\`}\` 后紧跟 \`{\`、JS 里出现 \`None\`、样式值里出现 \`12pxNone\`）。
  不通过会**拒写**并给行号与该行原文（文件不会落盘）；确认是误报再传 \`skip_lint: true\` 强制写入。只拦确定的语法损坏，拿不准一律放行。
- **返回改动摘要**：\`lines_added / lines_removed / first_change_line / diff\`（\`diff\` 是带行号的 \`-12|旧\` / \`+12|新\`）——多数情况不用再回读全文确认。
- **跨文件引用提示**：写 \`.js\` 时会核对世界里 \`window.UI = {...}\` 这类命名空间的键表与各处 \`UI.x\` 调用，
  对不上就回 \`warnings\`（**提示不拦写入**；判不准的情况——展开、计算键、\`Object.assign\`——一律不下结论）。
- **温和去重**：若新内容与现有内容完全一致，返回 \`unchanged: true\` + 提示，不重复写入（不是错误，别重试）。

### 3.4 file_read

\`\`\`
→ { "success": true, "path": "index.html", "content": "<html>…", "binary": false }
\`\`\`

- 二进制文件：返回 \`binary: true\`，\`content\` 为 null（工具侧显示"二进制文件，内容不返回"）。
- **按行分页**（推荐）：传 \`offset\`/\`limit\` 只读需要的段落，返回 \`total_lines / start_line / end_line / truncated\`——**先 file_grep 定位行号再读**，不要整文件全读浪费上下文。
- **建议**：编辑前先 \`file_read\` 确认当前内容，避免误覆盖。

### 3.5 file_edit（增量编辑，推荐）

\`\`\`json
{ "path": "index.html", "operation": "str_replace",
  "old_string": "<title>旧标题</title>", "new_string": "<title>新标题</title>" }
\`\`\`

三种 \`operation\`：

| operation | 必填参数 | 行为 |
|-----------|----------|------|
| \`str_replace\` | \`old_string\` + \`new_string\` | 精确替换；\`old_string\` 必须**唯一**，否则报错（可选 \`occurrence\`） |
| \`insert\` | \`line\` + \`new_string\` | 在 \`line\` 行之后插入（1 开头，\`0\` = 文件开头） |
| \`delete_lines\` | \`start_line\` + \`end_line\` | 删除 \`start_line..end_line\`（**含两端**） |

- **多处匹配**（如只差两个空格缩进的两行）：报错里会给出每处所在行号，传 \`occurrence: N\` 指定第几处，
  \`occurrence: 0\` 全部替换——不用再往上贴更多上下文。
- **多次插入时从最大行号开始往小插**（行号会随插入变化，从大往小插不会错位）。
- 编辑核心与主站共用同一份实现（\`apply_file_edit\`），语义一致。
- 与 \`file_write\` 同一套落盘前**语法自检**（\`skip_lint: true\` 可强制）与**改动摘要**
  （\`lines_added / lines_removed / first_change_line / diff\`）——改完不用回读全文确认。
- 同样会带跨文件引用提示（\`warnings\`）：改了导出表就会去核对调用方，\`UI.emptyBox\` 这类对不上当场报。
- 二进制文件不可编辑。

### 3.6 file_delete

\`\`\`json
{ "path": "old.html" }
\`\`\`

- 删除文件；路径是目录时**整个目录递归删除**（含里面的文件，谨慎）。
- 审阅模式下删除会弹窗请用户确认（见 08 分区「运行模式门禁」）。

### 3.7 file_move

\`\`\`json
{ "from": "css/old.css", "to": "assets/new.css" }
\`\`\`

- 同目录改名、跨目录搬移都行；源是目录时整体搬移（搬完会对目录做一次禁用后缀兜底扫描）。
- \`to\` 要写**完整文件名**（不是目录）；源与目标都在世界目录内，\`../\` 一律拒绝。

### 3.8 file_copy

\`\`\`json
{ "from": "index.html", "to": "backup/index.bak.html" }
\`\`\`

- 世界内复制文件/目录（原文件保留），**不能跨世界复制**。
- 复制前校验总大小（≤32MB）与目标后缀。

## 4. 下载文件（web_download）与下载纪律

\`\`\`json
{ "url": "https://raw.githubusercontent.com/vuejs/core/main/README.md", "path": "vue/README.md" }
\`\`\`

- **固定落点 \`downloads/\`**：\`path\` 只是 \`downloads/\` 下的子路径；不填则按 URL 文件名或 content-type 自动命名到 \`downloads/\`。**下载来的文件不要往别处塞**，需要移动/改用途用 \`file_move\` / \`file_copy\`。
- **代码站直链**：GitHub 的 \`blob\` 页面链接会自动转成 \`raw\` 直链，直接贴浏览器地址也能下到源码。
- **内容红线**：严禁下载色情、暴力、违法内容——平台按链接/目标文件名/文本正文关键词拦截并记录，**命中即失败且不写盘**。不要换个链接绕。
- **程序文件红线**：严禁下载可执行文件、安装包与脚本（见 1.1 禁用清单），创建时直接拒绝；代码只允许 \`.js\`/\`.py\` 与纯文本源码。
- **大小上限 32MB**；只能访问公网地址（内网/本机地址一律拒绝）。
- **审批**：审阅模式下平台会自动弹窗请用户确认（你不用自己问、不用等）；旁路场景（世界程序/定时任务发起的下载）平台会在下载完成后弹窗问是否保留，没人应答会删掉文件。

## 5. 页面内访问静态资源（不是工具，是路由）

写死这段路径即可，编号用变量：

\`\`\`
GET /world/\${WORLD_ID}/files/<相对路径>     # 静态资源（css/js/图片/页面）
\`\`\`

\`\`\`html
<link rel="stylesheet" href="/world/\${WORLD_ID}/files/css/style.css">
<img src="/world/\${WORLD_ID}/files/img/logo.png">
\`\`\`

## 6. 约定与防呆

- 页面内资源一律**相对路径**引用（跨文件夹用 \`../\`），不要用 \`/\` 开头绝对路径（会 404）。
- 创建文件后**告知用户文件路径**；调用工具时不要把工具调用的原始内容写进回复文本，直接说做了什么。
- 写文件前先 \`file_list\` / \`file_read\` 确认现状，避免重复写入或误覆盖用户改动。
- 看文件不要整文件全读：\`file_grep\` 定位 → \`file_read\` 分页读 → \`file_edit\` 精确改。
