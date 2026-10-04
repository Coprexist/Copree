# 插件商城（总商城 · 插件分区）

> 状态：一期已实现（本地安装包）。社区索引仓的目录与 CI 已就位，客户端直连索引安装属于二期。
> 相关代码：backend/app/services/plugin/store.py、backend/app/routers/plugins.py、
> frontend/src/pages/StorePage.tsx、frontend/src/pages/store/PluginMarketSection.tsx、
> community-market/

## 1. 定位与分级

**世界商城**是给普通用户找世界的地方，留在 /market 对外开放（浏览、发布、一键导入）。
**插件商城、已安装、商城源**都是管理动作，住在控制台里：管理 → 能力与扩展 → **商城**，
下面三个子页签，实现见 frontend/src/components/StoreConsoleTab.tsx。

这么分是因为两者受众不同：世界包是内容，普通用户自己就能用；插件是往宿主里装可执行代码，
必须管理员经手（后端 require_admin，普通用户调装卸接口是 403）。

排版约定（和设计体系一致）：

| 区域 | 形态 | 为什么 |
| --- | --- | --- |
| 插件商城 | 卡片网格 | 浏览型：一眼比较"哪个是我要的" |
| 已安装 | 管理型表格（直接复用能力面板的 PluginManager） | 管理型：状态、开关、配置 |
| 插件详情 | 右侧抽屉 | 安装前把 manifest 摊开，列表留在原地当上下文 |
| 控制台 | 收起应用侧边栏 + 可折叠导航栏 | 管理项会越来越多，先要宽度，再要折叠 |

## 2. 三层信任

| 层级 | 源码在哪 | 门禁 | 界面标识 |
| --- | --- | --- | --- |
| 内置 | 主仓 backend/plugins/ | 我们自己审，随版本发布 | 内置 |
| 已验证 verified | community-market/verified/<id>/ | 我们自己审源码与权限，索引留 reviewed_by / reviewed_at | 已验证 |
| 社区 community | 作者自持仓库，索引只记 source + sha256 | CI 通过即收录 | 社区（CI 校验） |
| 未收录 | 管理员上传的本地包（一期内容） | 无（管理员自己负责） | 未收录 |

规则一句话：**源码在不在我们仓里，决定能不能给 verified 标**。

## 3. 客户端安装流程与安全边界

安装包 = 一个 zip，根目录含 plugin.json（或整个插件套一层目录，自动下钻）。

1. 上传：只做校验与入库（data/plugin_packages/），**不安装**
2. 审阅：界面把 manifest 摊开——id / 版本 / 作者 / 类型 / 载荷文件 / 解压体积 / sha256 / 文件清单
3. 安装：data/plugin_packages 的 zip → data/plugins/<id>/ → 同步 DB → 运行时加载

安全边界（都在 store.py 一处，客户端是最后一道闸）：

- 只写 DATA_DIR；**id 撞内置一律拒绝**（内置插件不能被用户包覆盖）
- 拒绝绝对路径、..、符号链接、隐藏目录（.git / __pycache__）、可执行后缀（.exe/.sh/.dll…）
- 限单包 5MB、解压后 20MB、条目 500、单文件 2MB
- 覆盖安装走「临时目录 + 原子替换」，失败保留原目录，不留半个插件
- 卸载只删 DATA_DIR/plugins 下的目录，并把该插件的服务期望状态一起清掉

接口（前缀 /plugins，读对登录用户开放，装卸删要管理员）：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | /plugins/store | 安装包清单 + 每个包的安装状态（含坏包标记） |
| POST | /plugins/store/packages | 上传安装包（只入库） |
| POST | /plugins/store/packages/{file}/install | 安装 / 更新（upgrade=true） |
| DELETE | /plugins/store/packages/{file} | 删除安装包（不影响已安装） |
| DELETE | /plugins/store/installed/{id} | 卸载插件（内置插件拒绝） |

## 4. 社区索引仓（community-market/）

形态上像 awesome 列表，机制上像注册表：一个仓 + index.json 作为唯一事实来源，
README 目录由 CI 生成（手写列表一定漂移）。

~~~
index.json      唯一事实来源（id / 版本 / source / sha256 / 分类 / 层级 / 下架标记）
schema.json     条目的 JSON Schema（编辑器提示与 CI 校验同一份契约）
tools/verify.py 两层校验：索引格式（离线）+ 制品（下载 pin 住的发布物核对 sha256 与 manifest）
verified/<id>/  我们审过的源码——只有它才配 tier: verified
~~~

条目字段：

| 字段 | 说明 |
| --- | --- |
| id | 插件 id，同时是安装目录名（字母数字开头，含 . _ -，≤40） |
| source | owner/repo@ref，ref 必须是 tag，不许用分支 |
| asset | Release 资产文件名（只能是 zip 文件名，不含路径） |
| sha256 | 制品的 sha256。**客户端只认它**：只写 tag 的话作者挪 tag 就能绕过"CI 校验过" |
| tier | verified / community |
| yanked / yank_reason | 下架标记：客户端显示已下架并拒绝新装，已装的不动 |

CI（.github/workflows/verify.yml）：索引格式 → 下载发布物核对 sha256 → 校验 manifest 的
id/version/category 与索引一致 → 生成目录并比较是否过时。全过即合入，这就是社区层的"通过"。

## 5. 为什么不这么设计

- **不做 ClawHub 那样的中心注册表服务**：账号体系、发布 CLI、审核员、公开 API，我们维护不起；
  我们的商店嵌在实例里，索引仓 + GitHub 就够分发。
- **不只做 awesome 列表**：那只解决"发现"，装不上、更不了、撤不掉。
- **不自动抓取 GitHub**：收录量一大就是垃圾场（awesome 那边 13655 个仓要靠人工逐个复核），
  只收作者主动提交 + CI 通过的条目。

## 6. 二期（未做）

1. 客户端直连索引：/plugins/store 增加远端索引分区，按 sha256 下载并校验后安装
2. verified 审阅流程落地：审阅记录进索引并在界面展示审阅人
3. 版本历史与降级：目前同 id 只保留当前版本，历史在作者仓库
