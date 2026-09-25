# Copree 社区插件索引

这是 Copree 插件商城的**社区层**索引仓。它是机器可读的单一事实来源（index.json），
README 里的目录表格由脚本生成，不手写——手写列表一定会和索引漂移。

> 拆出来之后建议的仓库名：copree-plugins（公开）。本目录已经是该仓的完整内容，
> 在 Copree 主仓里用 git subtree split 或直接拷贝即可独立成仓；.github/workflows/verify.yml
> 只有在仓库根目录才会生效。

## 三层信任

| 层级 | 源码在哪 | 门禁 | 界面标识 |
| --- | --- | --- | --- |
| 内置 | Copree 主仓 backend/plugins/ | 我们自己审，随版本发布 | 内置 |
| 已验证 verified | 本仓 verified/ 目录 | 我们自己审源码与权限，索引留 reviewed_by / reviewed_at | 已验证 |
| 社区 community | 作者自持仓库，索引只记 source + sha256 | **CI 通过即收录**，无人工审阅 | 社区（CI 校验） |

规则一句话：**源码在不在我们仓里，决定能不能给 verified 标**。

## 目录层级

~~~
index.json                     唯一事实来源：id / 版本 / source / sha256 / 分类 / 层级 / 下架标记
schema.json                    索引条目的 JSON Schema（编辑器提示 + CI 校验同一份契约）
README.md                      人类可读目录（CATALOG 标记之间由 tools/verify.py 生成）
tools/verify.py                校验器：索引层 + 制品层 + 目录生成，CI 与本地共用
verified/<id>/                 我们审过的插件源码（只有它才配 tier: verified）
.github/workflows/verify.yml   门禁：PR 与 main 上跑校验
~~~

## 收录一个插件（社区层）

1. 作者在自己仓库发一个 Release，资产是一个 zip：根目录含 plugin.json（或整个插件套一层目录）
2. 算 sha256：shasum -a 256 your-plugin-1.0.0.zip
3. 往 index.json 的 packages 加一条，source 形如 owner/repo@v1.0.0，asset 是资产文件名
4. 跑 ~~~python3 tools/verify.py --index index.json --render~~~ 更新目录，提 PR
5. CI 通过即合入。sha256 对不上、manifest 与索引不一致、含禁用文件或超限，CI 直接失败

**为什么必须 pin sha256**：只写 owner/repo@v1.0.0 的话，作者之后可以把 tag 挪到别的提交上，
"CI 校验过"就失效了。客户端只认索引里的 sha256。

## 更新与下架

- 更新：新版本 = 新条目版本号 + 新 sha256（同一个 id 只保留当前版本，历史在作者仓库）
- 下架：把条目标 yanked: true 并写 yank_reason，客户端显示"已下架"并拒绝新装，已装的不动
- 严重问题：维护者直接把条目从 index.json 删除，客户端刷新后即从商城消失

## 我想让你的插件变成"已验证"

把源码 PR 进 verified/<id>/，并在索引条目上把 tier 改成 verified、补 reviewed_by / reviewed_at。
审阅清单见 verified/README.md。

## 目录

<!-- CATALOG:BEGIN -->

| 插件 | 类型 | 版本 | 信任层级 | 来源 |
| --- | --- | --- | --- | --- |
| _暂无收录_ | | | | |

<!-- CATALOG:END -->
