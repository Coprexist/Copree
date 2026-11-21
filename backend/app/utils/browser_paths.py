"""浏览器会直接访问的后端路径前缀 —— 唯一清单。

为什么需要这份清单：这些路径**不吃前端的 `/api` 前缀**，而是以绝对路径出现在浏览器
地址栏、iframe 的 src、EventSource / WebSocket URL 里（都是后端先拼好、原样交给前端的）。
前端服务器（vite dev 的 proxy、生产反代）必须按前缀把它们原样转发到后端；漏一条，
浏览器就落到 SPA fallback，看到的是平台自制的 404 —— 看起来像"后端没这条路由"，
其实请求根本没出前端。

所以两边各写一份是错的：清单只在这里写一次，后端拼路径引用这里的常量，
`frontend/scripts/check-proxy-prefixes.mjs`（`npm run proxy:check`，CI 也跑）拿它和
vite 的 proxy 表对账 —— 少一条、多一条（比如改了前缀却留着旧键）都会失败。

新增一条的流程：这里加常量与元组项 → `frontend/vite.config.ts` 的 proxy 加一条同名键
→ `npm run proxy:check` 绿。
"""

#: 群视界资源与文件：`/world/<id>/…`。
#: 注意它和前端 SPA 路由 `/worlds`（世界列表）只差一个字母，所以 vite 那边写成 `'/world/'`
#: 带尾斜杠 —— 否则 `/worlds` 也会被转走后端。
WORLD = "/world"

#: 联邦：HTTP 接口与 `/federation/ws` 同前缀。
FEDERATION = "/federation"

#: 平台主 WebSocket（`/ws?token=…`）。
WS = "/ws"

#: 上面全部前缀（顺序无关；检查脚本按集合比对）。
BROWSER_ROOT_PREFIXES = (WORLD, FEDERATION, WS)
