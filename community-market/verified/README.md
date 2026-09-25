# verified/ — 我们自己审过的插件

放这里的插件源码，是我们逐行看过行为、权限与外部依赖之后收录的，索引里标 tier: verified。

和社区层的区别只有一条：**源码在我们仓里**。

- 社区层：作者自持仓库，索引只记 source: owner/repo@ref + sha256，CI 通过即收录，界面标「社区（CI 校验）」
- verified：源码 PR 进本目录，审阅人在索引里留 reviewed_by / reviewed_at，界面标「已验证」
- 内置：随实例部署，源码在 Copree 主仓 backend/plugins/，走版本发布，不在这个索引里

审阅清单（每个 verified PR 都过一遍）：

1. plugin.json 与 plugin.py 声明一致，没有未声明的网络/文件访问
2. 不读取宿主环境里的其他密钥，不落盘明文凭据
3. 生命周期干净：start() / stop() 可反复调用，异常不炸宿主
4. 有 README 说明它要什么权限、做什么
5. 索引条目的 sha256 指向本次审阅的制品（审完再算，算完不再改代码）
