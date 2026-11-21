# DSH 提问/审批弹窗在 /dsh 页面不显示（2026-11-07）

## 现象

Copree 的 `/dsh` 页面里，AI 调 `ask_user_question` 或触发审批时：

- 屏幕上没有任何弹窗；
- 外层 `run_code` 一直显示「执行中」，直到手动「停止」；
- 停止后两个工具卡片一起变成「执行失败」。

## 通道

两条完全不同的路，别混：

| 内容 | 路径 |
|---|---|
| 正文、工具卡片、命令输出 | 桥用 `controller.follow()` 订阅 DSH **会话流**，转成 SSE 帧 |
| 提问、审批 | DSH 的 **scoped waterfall 事件**（`user-questions/request` / `approval/request`），桥接住后由 `AskBroker` 往页面流里补 `k:"ask"` 帧 |

所以「对话能到、只有问答看不到」= 会话流通、事件回调断。

## 根因

`dsh-copree` 的 `registerAnswerers()` 用 `request.agent.sessionId` 取会话键，
而 DSH 的 Agent 对象**没有 `sessionId` 字段**，会话 id 在 `agent.id` 上
（实测 `agent.id === "session-1e0874c8-…"`，与页面订阅的 `?sessionId=` 完全一致）。

键取到空 → `if (!sessionId) return next()` → 桥直接放弃 → 走原生 UI 应答链，
而 Copree 页面没有原生 UI → 请求永久悬挂 → 卡片一直「执行中」。

## 改动（`/root/.dsh/profiles/web/node_modules/dsh-copree/lib/index.js`）

1. 会话键：`String(agent?.sessionId || agent?.id || "")`（提问与审批两处）。
2. 新增 `AskBroker.watching(sessionId)`；只有「这个会话有页面在看」时才接管，
   否则 `next()` 交回原生 UI —— 原生 DSH 页面不受影响。
3. 两处 `ctx.on(event, handler, true)`（prepend）：桥排在原生应答者之前。

原文件备份在同目录 `index.js.bak-askfix`。

## 验证

重启 `dsh-web` 后，在 `/dsh` 页面发一次提问：弹窗出现、选项可点、回答能回传到模型
（实测返回 `{"answers":[{"id":"probe8","selected":["出现了"]}]}`）。

## 升级注意

这是 `node_modules` 里的产物，`dsh-copree` 插件升级会覆盖。升级后按上面 3 条重新打一次，
或把这 3 处反馈给插件作者。
