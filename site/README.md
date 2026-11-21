# coprexist.com 官网 / 文档站

把仓库里的 README 与精选文档渲染成静态站点，由独立容器 `coprexist-site` 提供，经 frp 隧道对公网发布。

## 文件

| 文件 | 作用 |
|------|------|
| `build.mjs` | 生成器：读 README / docs、渲染 markdown、改写链接与图片、输出静态 HTML |
| `styles.css` | 全站样式（深色、紫色主色，与 Copree 应用一致） |
| `app.js` | 移动菜单、代码复制、目录高亮、文档筛选、回到顶部、移动端导航折叠 |
| `tools/shot.mjs` | 开发用小工具：用 headless Chrome 截图（全页切块 / 移动端），自查版式用 |
| 输出目录 | `/data_s001/coprexist-site/dist`（可用 `SITE_OUT` 覆盖） |

## 构建

```bash
# 在仓库根目录
node site/build.mjs
# 产物权限：nginx worker 需要可读（新生成的文件默认 644，若用特殊 umask 记得放开）
chmod -R a+rX /data_s001/coprexist-site/dist
```

依赖 `frontend/node_modules/marked`（已随前端安装），无需额外 npm install。

## 收录哪些文档

见 `build.mjs` 顶部的 `DOCS` 数组：`slug / file / title / group / desc`。
新增文档时加一行即可；不在清单里的 `.md` 链接会自动指向 GitHub。

## 部署

容器（已创建，独立于 compose 里的 `webpage` / `nginx-webpage`）：

```bash
docker run -d --name coprexist-site --restart unless-stopped \
  -p 127.0.0.1:18030:80 \
  --mount type=bind,source=/data_s001/coprexist-site/dist,target=/usr/share/nginx/html,readonly,bind-propagation=rslave \
  nginx:alpine
```

`bind-propagation=rslave` 必须带上：`/data_s001` 是 ZFS 数据集，容器用默认的 `rprivate` 时看不到后挂上的目录。

frp 客户端 `/etc/frp/frpc.toml` 的 `coprexist-web` 指向 `127.0.0.1:18030`；
VPS nginx 443 → 隧道出口 13017，证书在 VPS。

## 更新流程

```bash
node site/build.mjs
chmod -R a+rX /data_s001/coprexist-site/dist
```

无需重启容器或 frpc，刷新页面即可。

**构建器只清空 `dist` 里的内容，不会删掉 `dist` 目录本身。** 容器 bind mount 的是这个目录，
`rm -rf dist && mkdir dist` 会让容器里的挂载点指向已删除的旧 inode，表现是整站 403
（此时 `docker restart coprexist-site` 可恢复）。

## 自查（可选）

```bash
node site/tools/shot.mjs 1440 out "http://127.0.0.1:18030/"        # 全页按 1200px 切块
node site/tools/shot.mjs 390  m   "http://127.0.0.1:18030/wiki/readme/"
```

需要本机有 `google-chrome-stable`；脚本会先滚一遍页面，否则 `loading="lazy"` 的长图截出来是空白。
注意全页截图里 sticky 顶栏可能出现在中间，这是截图工具的已知现象，不是页面问题。

## 说明

- 站点是纯静态，无 JS 框架、无外部字体/脚本依赖（截图和 logo 都是本地文件）。
- shields.io 徽章在构建时被剔除（国内网络常不可达，避免破图）。
- 首页链接的「在线演示」指向 GitHub Pages 演示站，「现有实例」指向当前应用 `aischat.datongai.top`。
