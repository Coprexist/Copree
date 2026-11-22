# AI 知识库：文件是真源，索引是派生视图

> 读者：改知识库 / 文件工具 / 上下文注入的人。用户怎么用看 `docs/guides/用户手册.md`。

## 1. 一句话

知识库正文就是 **AI 文件空间里的普通文件**（`knowledge/<path>`），`agent_knowledge` 那一行只存「标题 / 摘要 / 开关」这类人工元数据。所以：**写文件就是改知识库，不存在第二个编辑入口。**

## 2. 为什么不给 AI 加「知识库编辑」工具

AI 已经有 `file_write` / `file_edit` / `file_delete`，它们本来就伸得进 `knowledge/`。再加一个 `manage_knowledge` 会出现两条都能改知识库的路，AI 得在两者之间选（还会和 `manage_records`、`recall_memory` 抢语义）。

现在的做法：把索引维护挂在**文件写路径**上（`file_service.ai_write_file` / `ai_move_file` / `ai_delete_file`），一处生效，工具数不变。

## 3. 同步规则（`knowledge_service`）

| 文件动作 | 行怎么变 |
| --- | --- |
| 写入/覆盖 `knowledge/x.md` | 无行则建（标题取首个 `# `，否则文件名；摘要取首个正文段落）；有行则只刷 `size` |
| 改名/移动 | 行的 `path` 跟着改 |
| 删除 | 行删掉 |

**创建者手改过的标题/摘要不会被 AI 覆盖**：只有「摘要还是空的」「标题仍等于文件名派生值」时才刷新。创建者在 `KnowledgePanel` 里改过的，AI 下一次保存正文也不会把它冲掉。

## 4. 前端看到的一定是最新

`_row()` 输出时用磁盘 `stat` 覆盖 `size` / `updated_at`——文件才是大小与时间的真源。于是即使 AI 绕过 `file_service` 直接写盘（`run_script` 之类），AI 详情页知识库列表里的大小与时间仍然是当前文件的；正文本来就是点开现读的。

## 5. 权限

写入受 `agents.is_ai_editable`（「AI 自修改」总开关）约束：关闭时 `file_write` / `file_edit` / `file_delete` 碰 `knowledge/` 直接报错，说明「知识库只能由创建者改」。创建者自己的接口不受影响（走 `knowledge_service`，不经过工具层）。

## 6. 索引注入有上限

`knowledge_index_text()` 最多列 `INDEX_MAX_ENTRIES` 条（按创建时间），多的只报「还有 N 份，用 `file_list knowledge/` 看」——109 份文档全量铺进系统提示，每轮都要为它付 token。
