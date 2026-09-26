# web_search（多后端检索）

工具壳在 backend/app/tools/file_operations/web_search.py，编排在同目录 search/ 包；
世界侧 tools/world/web_search.py 复用同一份文案与参数（唯一来源）。

## 1. 为什么不是「一条原话丢给引擎」

搜索引擎对查不到的冷门词**不返回空集**，而是塞一批无关结果。实测（本机容器）：

| 查询 | 旧实现的返回 |
|------|--------------|
| Copree AIsChat | 外设驱动站 / 视频站 / 黑龙江人社平台（每次还不一样） |
| Copree（AIsChat）类似平台的发展和发布 | 留学中介、知乎录取率帖 |
| 清华大学 官网 | 清华官网、学校概况、研招网（正常） |

旧实现把这些原样当成功结果端给模型，于是「搜不到」表现为「全是导航站和产品首页」。
所以相关性判定是**硬闸门**，不是排序里的一项权重。

## 2. 契约

web_search(queries=[...], exclude=[...], count=8, per_domain=3)

- queries：1~4 条检索式并行；单条写法 query 保留兼容
- exclude：负向词，既拼进引擎查询（-term）又对标题/摘要后置过滤
- count：结果上限（<=10，默认 8）
- per_domain：同一域名上限（<=5，默认 3）——不让一个站刷满整页

返回 {success, queries, provider_used, count, results[], failed?[], hint?}；
results[] 每项含 title/url/snippet/published_at/domain/provider/score/matched_query。

## 3. 回退阶梯

只在上一级真的 0 条相关结果时往下走，单次调用对外请求 <=6：

1. 主后端并行跑调用方给的检索式；
2. 机械改写补出的候选（去括号标点 -> 拆粘连词 CopreeAIsChat -> 单拉丁词元）；
3. 其余后端重试。

同一后端失败或整页空手时先重试一次（见下节），再往下走。回退受两道闸：单次调用对外请求 <=6，
总时长 <=8 秒（DEADLINE）——救场不该把一个对话轮次拖到十几秒。

全空仍返回 success=true，附 hint：让模型向用户索要官网 / GitHub / Product Hunt / X 链接，
再用 web_fetch 打开（建议先试 /sitemap.xml、/blog、/changelog、/docs、/release-notes）。

语言层面的规划（挑哪个实体、要不要英文说法）留给调用方模型：它本来就在场，
工具内部再调一次模型只多一次往返。机械改写能做到哪一步、做不到哪一步，见 plan.py 的文件头。

## 4. 后端表

search/backends.py，顺序即优先级，只登记实测过的：

| 后端 | 状态 |
|------|------|
| 必应（RSS 出口，优先） | 唯一能搜到 AIsChat 的后端（掘金 / GitHub Coprexist / CSDN / gitcode 全部命中）；约 4KB 结构化 XML，字段齐全且无广告位 |
| 必应（网页出口） | RSS 空手时兜底；正常查询相关，冷门词会被塞无关填充，由闸门拦掉 |
| 360 搜索 | 可达；结果真实地址在 a[data-mdurl]（页面 href 是跳转链）；对 AIsChat 只有抖音号这类边角结果 |
| 搜狗 | 可达；真实地址在 a 的 linkurl 属性 |
| DuckDuckGo | 本机网络不可达（Errno 101），不登记 |

**必应会间歇性空手**：同一个 URL、同一份代码，一次返回 8 条正确结果、几分钟后整轮拿不到东西
（实测踩过，表现为静默落到 360，于是「平台搜不到、浏览器搜得到」）。所以传输出错或整页解析为空时
自动重试一次——重试比换引擎便宜；解析出来但不相关的不重试，那是引擎对这条查询的真实看法。

要接需要密钥的搜索 API（Tavily / Serper 之类）在这张表里加一行即可，契约与排序都不用动。

## 5. 排序

rank.py：实体命中 0.55 + 域名权威 0.30 + 新近 0.15。

- 实体命中：**实体词必须全部命中**，只命中一个不算——AIs Chat 的 AIs 会命中 AIS 船舶系统、
  AIsChat github 的 github 会命中任意 GitHub 页面（都是实测踩过的坑）；拉丁词按整词匹配
  （chat 不该命中 WeChatAppEX），来源标记（github / producthunt / docs 这类）不算实体词。
  纯中文查询退回非泛词二元组
- **机械拆词派生的候选要相邻命中**：拆出来的 AIs Chat 既钓到船舶 AIS，也钓到
  「Two AIs Talking ... Chat」这种同名站，只有整串出现才算数（调用方自己给的检索式不受此限）
- 权威：authority.py 唯一来源；域名主干与实体同名即官网 1.0，Product Hunt 0.9、GitHub 0.85、
  Crunchbase 0.8、科技媒体 0.7、聚合导航 0.1、其余 0.5
- 新近：有日期按新旧给分，取不到日期给中性 0.5，不让没日期的吃亏
- 去重：URL 归一 -> 标题归一 -> 内容 simhash（64 位；摘要 >=30 字才用正文指纹，
  否则短摘要会把不同页面判成同一篇）
- 每域名限量后截断

## 6. 刻意不做

- **不猜域名**（copree.ai / copree.com ...）：猜出来的域名与我们无关，会撞上别人的站，
  还会让模型以为那是官网。
- **不自动抓 sitemap / blog / changelog**：那是 web_fetch 的职责；工具只给建议，
  避免一次搜索偷偷拉二十个页面。
- **不替调用方翻译**：把英文说法直接放进 queries，比在工具里再调一次模型便宜。

## 7. 验证

docker exec ai_group_backend bash -c 'export TEST_DATABASE_URL=...; cd /app && python tests/run_without_pytest.py test_web_search'

覆盖：改写阶梯、相关性闸门（拦无关填充 / 放行真结果 / 负向词）、权威与每域名限量、
转载判重、空结果给提示。真实网络行为手工跑 WebSearch().execute(...)：清华大学 官网 官方站第一、
Copree AIsChat 判 0 条 + hint、DeepSeek 开源 模型 发布 官网 0.925 / GitHub 0.880。
