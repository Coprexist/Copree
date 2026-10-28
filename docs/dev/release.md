# 发版（release）

**单一来源**：版本号写在哪、tag 怎么来、发布仓怎么被触发、发完核对什么、红了怎么读。

## 一句话流程

改版本号 → 归档 CHANGELOG → 预检 → 打 tag 推主仓 →（CI 自动）通知发布仓构建三平台 → 核对产物与 latest。

四步都是 `scripts/release.py`：

    python scripts/release.py check              # 只读预检：版本号/tag/CHANGELOG/关键文件
    python scripts/release.py check --tests      # 预检 + 全量后端测试（约 70 秒）
    python scripts/release.py archive            # 把 [未发布] 归档成 ## [vX] - 日期
    python scripts/release.py tag --push         # 打注释 tag 并推主仓
    python scripts/release.py verify --site https://coprexist.com   # 发布后核对

预检把所有判据一次列全（`✔ / ✘ / ·`），不用逐条试。**它只读，不动 git**；打 tag 是单独一步。

## 版本号只有一个出处

`backend/app/config.py::app_version`。它同时是：`/health` 返回的版本、更新检查与最新 tag 比较的对象、
发布仓注入安装包版本号的比对基准。

`frontend/package.json` 的 version 不参与运行时，别拿它当"当前版本"；两者不一致时预检会提示。

为什么不能多处各写一份：tag 与代码里的版本号一旦分叉，用户装到的包就是"标称 vA、实际 vB"，
而更新检查会一直说"有新版本"（`docs/dev/update_check.md` 的版本比较只认版本号）。

## 推 tag 之后发生了什么

`.github/workflows/release.yml`（主仓）在 `push: tags: v*` 时：

1. 校验 `tag == app_version`，不一致直接失败——绝不发布一个版本号错位的包；
2. `repository_dispatch` 通知 `Coprexist/Copree-Releases`，payload 里带 tag；
3. 发布仓 `build.yml` 按这个 tag checkout 主仓，Windows / macOS / Linux 三平台构建，
   `generate_release_notes` 自动生成说明并发布 Release，`latest` 指向新 tag。

需要仓库 Secret `RELEASE_DISPATCH_TOKEN`（能对发布仓发起 `repository_dispatch` 的 PAT）。缺了会直接红并说明。

**不再在发布仓手工打同名 tag**：那是同一个动作做两遍，忘一处就是"构建没触发"或"构建了一个不存在的 tag"。

客户端（`Copree-Client`）的 Android 包由它自己的 `android.yml` 在 push `v*` 时出，
**不要本地出包**——每 ABI 十几分钟，CI 已经能出。

## 发布后核对四项

1. Release 里有**三个**平台的产物，且都不是 0 字节；
2. `latest` 指向新 tag（stable 渠道会跳过 draft / prerelease）；
3. 站点 `GET /api/system/update-status` 报告的新版本就是它（服务端缓存 TTL 1 小时，不必立刻刷）；
4. README 的下载链接用的是 `/releases/latest`，不是写死版本号。

`verify` 覆盖前三项；第四项靠人看一眼。

## 红了怎么读

| 现象 | 原因 | 处理 |
|---|---|---|
| release.yml 红在"校验 tag 与 app_version 一致" | app_version 没改 / tag 打错 | 删 tag 重打：`git tag -d vX`、`git push origin :vX` |
| 发布仓构建红在 `UnicodeEncodeError` | Windows 控制台是 cp1252，print 中文炸 | 打包脚本已给 stdout/stderr 套 UTF-8；新增 print 别绕过那一层 |
| 产物 0 字节或缺平台 | 某平台打包步骤失败但 Release 仍创建 | 看那个 job 的 failed step，修好后删 Release 重发 |
| `latest` 没换 | Release 还是 draft/prerelease，或发布失败 | 确认已 publish；stable 渠道会跳过 prerelease |
| 站点仍报旧版本 | 服务端 1 小时 TTL 缓存 | 等，或「管理面板 → 通用 → 立即检查」（有 120 秒节流） |

## 不要做的事

- **不要复用版本号**：tag 已存在就停（预检会拒）。
- **不要在发布仓手工打 tag**：触发只从主仓来。
- **删旧 Release、动 `latest` 之前先问人**：那是线上回滚路径。
- **不要在本地出 Android 包**。
