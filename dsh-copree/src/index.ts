// SPDX-License-Identifier: MIT
/**
 * dsh-copree — host half.
 *
 * Same-origin gateway for the local Copree backend. The browser half never
 * touches the backend address: every HTTP call goes to `/copree-api/*` and
 * every WebSocket to `/copree-ws?token=...`, both answered here and proxied
 * to the Copree FastAPI service (default http://127.0.0.1:5228, backend WS at
 * /ws). Authentication stays end-to-end: the browser's Authorization header
 * and WS token query are forwarded verbatim and never logged, stored, or
 * echoed back. No public address is ever referenced.
 *
 * Security posture:
 * - The proxy target defaults to the loopback interface and is set through
 *   plugin config, never through a client-supplied value.
 * - Hop-by-hop headers are stripped before forwarding; the browser cannot
 *   smuggle Connection/Transfer-Encoding directives to the backend.
 * - The WS upgrade forwards the client's Sec-WebSocket-Key and replies with
 *   the backend's own 101 handshake; no tokens cross this boundary in plain
 *   logs.
 */

import type { Context } from '@deepseek-ai/cordis'
import type { IncomingMessage, ServerResponse } from 'node:http'
import http from 'node:http'
import type { Duplex } from 'node:stream'
import z from '@deepseek-ai/schemastery'
import { createReadStream, existsSync, statSync, mkdirSync, readFileSync, writeFileSync, realpathSync, readdirSync, unlinkSync } from 'node:fs'
import { join, normalize, extname, sep } from 'node:path'
import os from 'node:os'
import { PACKAGE_ROOT, registerPluginRoutes } from './plugin-update.js'
import { HEARTBEAT_MS, registerBridge } from './bridge.js'
import { readJsonBody, sendJson } from './http.js'

/** Stable Cordis plugin name. */
export const name = 'dsh-copree'

/** 代理、世界工作区同步、世界操作工具与反向桥接都需要这些服务。 */
export const inject = ['webServer', 'tools', 'systemPrompt', 'sessionController', 'agents']

/** 插件自身版本（随桥接注册报给 Copree 显示；读不到就退化成 0.0.0）。 */
const PLUGIN_VERSION: string = (() => {
  try { return String(JSON.parse(readFileSync(join(PACKAGE_ROOT, 'package.json'), 'utf8')).version ?? '0.0.0') }
  catch { return '0.0.0' }
})()

/** Plugin config: backend base URL and an optional plugin update source. */
export type Config = {
  /** Copree backend base URL, e.g. http://127.0.0.1:5228 (loopback only). */
  backendUrl: string
  /**
   * 插件自更新的源目录（内含 lib/manifest.json 的构建产物）。
   * 留空则回溯安装来源：profile 的 package.json 里 dependencies['dsh-copree']
   * 的 file: 规格，通常无需配置。
   */
  pluginSourceDir: string
  /** 反向桥接总开关：关闭后不再向 Copree 注册，也不再暴露 /copree-bridge。 */
  bridgeEnabled: boolean
  /**
   * 反向桥接共享密钥，与 Copree 后端的 DSH_BRIDGE_SECRET 同值。
   * 留空则桥接不注册（宁可没有，也不开一个能改代码的匿名口子）。
   */
  bridgeSecret: string
  /**
   * 本插件对 Copree 侧宣告的可达地址（Copree 后端从这里回连 DSH）。
   * 例：http://<宿主网关>:<DSH 桥接端口>。留空则不注册，仅本机自用。
   */
  bridgeAdvertiseUrl: string
  /**
   * 桥接心跳间隔（毫秒，默认 20000）。
   * 为什么可调：Copree 侧的有效期是它的三倍（默认 90 秒），改小不会影响判活，
   * 但「同意 / 撤销」的生效延迟就是这个间隔——自检与特殊网络下需要缩短。
   */
  bridgeHeartbeatMs: number
}

export const Config: z<Config> = z.object({
  backendUrl: z.string().default('http://127.0.0.1:5228'),
  pluginSourceDir: z.string().default(''),
  bridgeEnabled: z.boolean().default(true),
  bridgeSecret: z.string().default(''),
  bridgeAdvertiseUrl: z.string().default(''),
  bridgeHeartbeatMs: z.natural().default(HEARTBEAT_MS),
})

// 自更新原语对外导出：scripts/update-test.mjs 离线验证用，也可供运维脚本按需调用。
export { applyUpdate, computeStatus, manifestId, readManifest, resolveSourceRoot, rollback } from './plugin-update.js'

/** Routes owned by this plugin. */
const HTTP_PREFIX = '/copree-api'
const WS_PATH = '/copree-ws'
/** 前端静态资源挂载前缀（iframe 嵌入沉浸式界面等页面）。 */
const UI_PREFIX = '/copree-ui'

/** 静态资源根目录：插件包内 dist/（前端 BASE_URL=/copree-ui/ 构建产物）。 */
const UI_ROOT = join(PACKAGE_ROOT, 'dist')

/** 静态文件 content-type 表（前端产物常用子集；缺省 application/octet-stream）。 */
const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.map': 'application/json',
  '.txt': 'text/plain; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
}

/**
 * 服务前端静态产物（`/copree-ui/*`）。路径解析锚定在 dist 根内，杜绝
 * `..` 穿越；SPA 路由（无扩展名的路径）回退到 index.html。
 */
function serveStatic(req: IncomingMessage, res: ServerResponse): void {
  const raw = (req.url ?? '/').split('?')[0]
  const rel = raw === UI_PREFIX || raw === `${UI_PREFIX}/` ? '/index.html' : raw.slice(UI_PREFIX.length)
  const candidate = normalize(join(UI_ROOT, rel))
  if (!candidate.startsWith(UI_ROOT)) {
    res.writeHead(403)
    res.end('forbidden')
    return
  }

  let file = candidate
  if (!existsSync(file) || statSync(file).isDirectory()) {
    // SPA 回退：未知路径一律给 index.html（前端路由接管）
    file = join(UI_ROOT, 'index.html')
  }
  if (!existsSync(file)) {
    res.writeHead(404)
    res.end('not found')
    return
  }

  res.writeHead(200, {
    'content-type': MIME[extname(file).toLowerCase()] ?? 'application/octet-stream',
    'cache-control': extname(file) === '.html' ? 'no-cache' : 'public, max-age=31536000, immutable',
  })
  createReadStream(file).pipe(res)
}

/** Headers that must not be forwarded (RFC 7230 hop-by-hop). */
const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
])

/**
 * Strip hop-by-hop headers so only end-to-end headers reach the backend.
 * @param headers - incoming headers object.
 * @returns a plain record safe to forward.
 */
function stripHopByHop(headers: IncomingMessage['headers']): Record<string, string | string[] | undefined> {
  const out: Record<string, string | string[] | undefined> = {}
  for (const [key, value] of Object.entries(headers)) {
    if (key === undefined || value === undefined) continue
    if (HOP_BY_HOP.has(key.toLowerCase())) continue
    out[key] = value
  }
  return out
}

/**
 * Proxy one HTTP request to the Copree backend. The request body is piped
 * through untouched; the backend response is streamed back with its status
 * and headers. Errors produce a fixed 502 without echoing internals.
 */
function proxyHttp(
  backendUrl: string,
  req: IncomingMessage,
  res: ServerResponse,
  targetPath: string,
): void {
  let target: URL
  try {
    target = new URL(targetPath, backendUrl.endsWith('/') ? backendUrl : `${backendUrl}/`)
  } catch (error) {
    res.writeHead(500, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ error: 'invalid proxy target' }))
    return
  }

  const upstream = http.request(
    {
      protocol: target.protocol,
      hostname: target.hostname,
      port: target.port || (target.protocol === 'https:' ? 443 : 80),
      path: `${target.pathname}${target.search}`,
      method: req.method ?? 'GET',
      headers: {
        ...stripHopByHop(req.headers),
        host: target.host,
        // 告知后端当前部署形态：世界代码注入 window.WORLD_API / WORLD_UI 用
        // （群聊面板、平台菜单等组件据此拼同源代理前缀，避免落到宿主 SPA fallback）。
        'x-copree-api-prefix': '/copree-api',
        'x-copree-ui-prefix': '/copree-ui',
      },
    },
    (upRes) => {
      const headers = stripHopByHop(upRes.headers)
      // 重定向重写：后端 3xx 的 Location 是站内绝对路径（如 /world/1/files/...），
      // 浏览器按原样跟随会打到宿主自身的 SPA fallback。统一补上 /copree-api
      // 前缀，让重定向继续走本代理。
      const status = upRes.statusCode ?? 502
      if (status >= 300 && status < 400 && headers.location !== undefined) {
        const loc = Array.isArray(headers.location) ? String(headers.location[0]) : String(headers.location)
        if (loc.startsWith('/') && !loc.startsWith('/copree-api')) {
          headers.location = `/copree-api${loc}`
        }
      }
      res.writeHead(status, upRes.statusMessage ?? '', headers)
      upRes.pipe(res)
    },
  )

  upstream.on('error', () => {
    if (!res.headersSent) {
      res.writeHead(502, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: 'backend unreachable' }))
    } else {
      res.destroy()
    }
  })

  req.pipe(upstream)
}

/**
 * Proxy one WebSocket upgrade to the Copree backend. The browser's upgrade
 * request (with its Sec-WebSocket-Key and token query) is replayed against
 * the backend; on the backend's 101 the response headers are written back to
 * the browser socket and both directions are piped until either side closes.
 */
function proxyWs(
  backendUrl: string,
  req: IncomingMessage,
  socket: Duplex,
  head: Buffer,
): void {
  let target: URL
  try {
    target = new URL('/ws', backendUrl.endsWith('/') ? backendUrl : `${backendUrl}/`)
  } catch {
    socket.destroy()
    return
  }

  // Preserve the query string (carries ?token=... to the backend).
  const search = req.url?.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''
  const upstream = http.request({
    protocol: target.protocol,
    hostname: target.hostname,
    port: target.port || (target.protocol === 'https:' ? 443 : 80),
    path: `/ws${search}`,
    method: 'GET',
    headers: {
      ...stripHopByHop(req.headers),
      host: target.host,
      connection: 'Upgrade',
      upgrade: 'websocket',
    },
  })

  upstream.on('upgrade', (upRes, upSocket, upHead) => {
    // Replay the backend's 101 response line and headers to the browser.
    const statusLine = `HTTP/1.1 ${upRes.statusCode ?? 101} ${upRes.statusMessage ?? 'Switching Protocols'}\r\n`
    let headerText = statusLine
    for (const [key, value] of Object.entries(upRes.headers)) {
      if (value === undefined) continue
      headerText += `${key}: ${Array.isArray(value) ? value.join(', ') : value}\r\n`
    }
    headerText += '\r\n'
    socket.write(headerText)
    if (upHead.length > 0) socket.write(upHead)

    upSocket.pipe(socket)
    socket.pipe(upSocket)

    const close = (): void => {
      upSocket.destroy()
      socket.destroy()
    }
    upSocket.on('error', close)
    socket.on('error', close)
    upSocket.on('close', close)
    socket.on('close', close)
  })

  upstream.on('error', () => {
    socket.destroy()
  })

  upstream.on('response', () => {
    // Backend refused the upgrade (e.g. 401 without token): destroy.
    socket.destroy()
  })

  if (head.length > 0) upstream.write(head)
  upstream.end()
}

// ════════════════════════════════════════════════════════════════════════
// 世界工作区（Copree群视界 → DSH Workspace 文件夹 + 会话）
//
// 每个 Copree 世界对应 DSH 工作区里一个真实目录：
//   $DSH_HOME/copree-worlds/Copree群视界-<世界名>/
//     .copree-world.json  { worldId, name }   ← 世界身份（工具据此路由）
// client 同步流程：列世界 → 建目录 → workspaces.create({path}) →
// connectWorkspace() 得会话 → 上报 {sessionId, token}（token 仅存内存，
// 用于需要 owner 鉴权的写操作，不落盘、不打日志）。
// ════════════════════════════════════════════════════════════════════════

const WORLD_DIR_BASE = join(process.env.DSH_HOME ?? join(os.homedir(), '.dsh'), 'copree-worlds')
const WORLDS_PREFIX = '/copree-worlds'

/**
 * 改名前的老目录（aischat-worlds）与老身份文件（.aischat-world.json）继续认：
 * 用户磁盘上已经存在的世界工作区、以及指向它们的 DSH 会话不能因为改名失效。
 * 新世界一律用新名字；老世界原目录原样复用（worldDirFor 先查新目录、再查老目录）。
 */
const LEGACY_WORLD_DIR_BASE = join(process.env.DSH_HOME ?? join(os.homedir(), '.dsh'), 'aischat-worlds')
const WORLD_DIR_BASES = [WORLD_DIR_BASE, LEGACY_WORLD_DIR_BASE]
const META_FILES = ['.copree-world.json', '.aischat-world.json']

/** 读世界身份：新名优先，老名兜底（两者都缺 = 不是世界目录） */
function readWorldMeta(dir: string): { worldId?: unknown; name?: unknown } | null {
  for (const file of META_FILES) {
    const p = join(dir, file)
    try {
      if (existsSync(p)) return JSON.parse(readFileSync(p, 'utf8'))
    } catch { /* 损坏就试下一个名字 */ }
  }
  return null
}

/**
 * worldId -> Copree token（仅内存，供 owner 鉴权写操作；不落盘）。
 * 按世界而非会话路由：用户在工作区新建/切换会话不影响 token 归属
 * （会话可能由 DSH 新建流程创建，sessionId 不稳定；世界目录是稳定标识）。
 */
const worldTokenMap = new Map<number, string>()
/** 兼容旧上报：sessionId -> token（已弃用，保留以兼容旧 client）。 */
const sessionTokenMap = new Map<string, string>()

/** 目录名清理：去掉文件系统非法字符，保留中文。 */
function sanitizeDirName(name: string): string {
  return String(name || '').replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').trim() || '未命名世界'
}

/** 向 Copree 后端发一个请求，返回状态与文本。 */
function backendRequest(
  backendUrl: string,
  method: string,
  path: string,
  opts: { token?: string; headers?: Record<string, string>; json?: unknown } = {},
): Promise<{ status: number; text: string }> {
  return new Promise((resolve, reject) => {
    let target: URL
    try { target = new URL(path, backendUrl.endsWith('/') ? backendUrl : `${backendUrl}/`) }
    catch { reject(new Error('invalid target')); return }
    const data = opts.json === undefined ? null : JSON.stringify(opts.json)
    const req = http.request({
      protocol: target.protocol,
      hostname: target.hostname,
      port: target.port || (target.protocol === 'https:' ? 443 : 80),
      path: `${target.pathname}${target.search}`,
      method,
      headers: {
        'content-type': 'application/json',
        ...(opts.token ? { authorization: `Bearer ${opts.token}` } : {}),
        ...(opts.headers ?? {}),
        ...(data ? { 'content-length': String(Buffer.byteLength(data)) } : {}),
      },
    }, (res) => {
      // 用 Buffer 数组收集，最后统一 utf8 解码——避免跨 TCP chunk 截断多字节字符（U+FFFD 乱码）
      const chunks: Buffer[] = []
      res.on('data', (c: Buffer) => { chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c)) })
      res.on('end', () => resolve({ status: res.statusCode ?? 502, text: Buffer.concat(chunks).toString('utf8') }))
    })
    req.on('error', reject)
    if (data) req.write(data)
    req.end()
  })
}

/**
 * 取世界的沙箱 API token（world.config.api_token，经 owner 接口读取）。
 * 世界受控 API（/world/{id}/api/*）只认这个 token（X-World-Token 头）。
 * api_token 仅用于内部调用，绝不回传给模型。
 */
async function resolveWorldApiToken(backendUrl: string, worldId: number, ownerToken?: string): Promise<string | undefined> {
  if (!ownerToken) return undefined
  const res = await backendRequest(backendUrl, 'GET', `/worlds/${worldId}`, { token: ownerToken })
  if (res.status !== 200) return undefined
  try {
    const data = JSON.parse(res.text) as { config?: { api_token?: unknown } }
    const token = data.config?.api_token
    return typeof token === 'string' && token.length > 0 ? token : undefined
  } catch {
    return undefined
  }
}

/** 从工具执行上下文解析所属世界：会话 cwd 必须在任一世界目录（新旧）内。 */
function resolveWorldFromCwd(cwd: string | undefined): { worldId: number; name: string } | null {
  if (!cwd) return null
  try {
    const real = realpathSync(cwd)
    const bases = WORLD_DIR_BASES.filter((b) => existsSync(b)).map((b) => realpathSync(b))
    if (!bases.some((base) => real === base || real.startsWith(base + sep))) return null
    const meta = readWorldMeta(real)
    if (!meta) return null
    const worldId = Number(meta.worldId)
    if (!Number.isInteger(worldId) || worldId <= 0) return null
    return { worldId, name: String(meta.name ?? `世界${worldId}`) }
  } catch {
    return null
  }
}

/** 工具输出：文本化任意 JSON 值。 */
function textOutput(value: unknown): Array<{ type: 'text'; text: string }> {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  return [{ type: 'text', text }]
}

/** 列出世界目录（新旧两个 base，含 worldId，诊断用）。 */
function listWorldDirs(): Array<{ dir: string; worldId: number | null; name: string }> {
  const out: Array<{ dir: string; worldId: number | null; name: string }> = []
  const seen = new Set<string>()
  for (const root of WORLD_DIR_BASES) {
    try {
      if (!existsSync(root)) continue
      for (const entry of readdirSync(root, { withFileTypes: true })) {
        if (!entry.isDirectory() || seen.has(entry.name)) continue
        seen.add(entry.name)
        const meta = readWorldMeta(join(root, entry.name)) ?? {}
        out.push({
          dir: entry.name,
          worldId: Number(meta.worldId) || null,
          name: String(meta.name ?? ''),
        })
      }
    } catch { /* ignore */ }
  }
  return out
}

/** 世界镜像中应排除的文件（本地元数据 + 运行时产物）。 */
function isMirrorExcluded(relPath: string): boolean {
  const base = relPath.split('/').pop() ?? relPath
  if (META_FILES.includes(relPath)) return true          // 新老身份文件都不参与同步
  if (relPath === SNAPSHOT_FILE || relPath === LEGACY_SNAPSHOT_FILE) return true // 快照自身不计入对比
  if (base === '__pycache__' || relPath.includes('/__pycache__/')) return true
  if (base.endsWith('.pyc')) return true
  if (base === '.DS_Store') return true
  return false
}

/** 按 worldId 找工作区世界目录：先新 base、再老 base（老镜像原样复用，不重复建目录）。 */
function worldDirFor(worldId: number): string | null {
  for (const root of WORLD_DIR_BASES) {
    try {
      if (!existsSync(root)) continue
      for (const entry of readdirSync(root, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue
        const meta = readWorldMeta(join(root, entry.name))
        if (meta && Number(meta.worldId) === worldId) return join(root, entry.name)
      }
    } catch { /* ignore */ }
  }
  return null
}

// ════════════════════════════════════════════════════════════════════
// GitHub 式双向同步（.copree-sync.json 快照 + 三路对比）
//
// 快照记录每个文件「上次同步时的本地 mtime / 远端 mtime」：
//   本地 mtime 变了  = 本地有未推送修改（changedLocal）
//   远端 mtime 变了  = 世界上有改动（changedRemote）
//   两边都变          = 冲突（conflict，不自动覆盖，交 AI/用户裁决）
// ════════════════════════════════════════════════════════════════════

interface SyncSnapshot { v: 1; files: Record<string, { lm: number; rm: number }> }
interface SyncCompare {
  added: string[]          // 远端有、本地无（可安全拉取）
  removed: string[]        // 远端无、本地有且本地未改（可安全删除）
  changedRemote: string[]  // 世界改了、本地未改（可安全拉取）
  changedLocal: string[]   // 本地改了、世界未改（可安全推送）
  conflict: string[]       // 两边都改（需裁决）
}

const SNAPSHOT_FILE = '.copree-sync.json'
const LEGACY_SNAPSHOT_FILE = '.aischat-sync.json'

function readSnapshot(dir: string): SyncSnapshot {
  for (const file of [SNAPSHOT_FILE, LEGACY_SNAPSHOT_FILE]) {
    try {
      const parsed = JSON.parse(readFileSync(join(dir, file), 'utf8')) as SyncSnapshot
      if (parsed && parsed.v === 1 && parsed.files && typeof parsed.files === 'object') return parsed
    } catch { /* 无快照或损坏就试下一个名字 */ }
  }
  return { v: 1, files: {} }
}

function writeSnapshot(dir: string, snap: SyncSnapshot): void {
  try { writeFileSync(join(dir, SNAPSHOT_FILE), JSON.stringify(snap, null, 2), 'utf8') } catch { /* ignore */ }
}

function statMtime(p: string): number {
  try { return statSync(p).mtimeMs } catch { return 0 }
}

/** 三路对比：世界文件树（path→mtime）vs 本地文件 vs 快照。 */
function compareMirror(remoteTree: Array<{ path: string; mtime: number }>, dir: string, snap: SyncSnapshot): SyncCompare {
  const remote = new Map<string, number>()
  for (const f of remoteTree) if (f.path && !isMirrorExcluded(f.path)) remote.set(f.path, f.mtime)
  const localFiles = walkDir(dir).filter((p) => !isMirrorExcluded(p))
  const local = new Map<string, number>()
  for (const p of localFiles) local.set(p, statMtime(join(dir, p)))

  const out: SyncCompare = { added: [], removed: [], changedRemote: [], changedLocal: [], conflict: [] }
  const seen = new Set<string>()

  for (const [p, rm] of remote) {
    seen.add(p)
    const rec = snap.files[p]
    const localMtime = local.get(p) ?? 0
    if (rec === undefined) {
      if (localMtime === 0) out.added.push(p)
      else out.changedLocal.push(p) // 本地也有但无快照：视为本地新增，保留本地
      continue
    }
    const remoteChanged = rm !== rec.rm
    const localChanged = localMtime !== rec.lm
    if (remoteChanged && localChanged) out.conflict.push(p)
    else if (remoteChanged) out.changedRemote.push(p)
    else if (localChanged) out.changedLocal.push(p)
  }
  for (const p of local.keys()) {
    if (seen.has(p)) continue
    if (snap.files[p] === undefined) out.changedLocal.push(p) // 本地新增
    else out.removed.push(p) // 之前同步过、现在远端没了
  }
  return out
}

/** 远端文件树（path→mtime；无 token 返回 null）。 */
async function fetchRemoteTree(backendUrl: string, worldId: number, token?: string): Promise<Array<{ path: string; mtime: number }> | null> {
  if (!token) return null
  const tree = await backendRequest(backendUrl, 'GET', `/worlds/${worldId}/files?prefix=`, { token })
  if (tree.status !== 200) return null
  try {
    const files = (JSON.parse(tree.text || '{}') as { files?: Array<{ path: string; mtime?: number }> }).files ?? []
    return files.map((f) => ({ path: String(f.path ?? ''), mtime: Number(f.mtime) || 0 }))
  } catch { return null }
}

/** 拉取（带快照/冲突保护）：本地有未推送修改或冲突时拒绝（force 除外）。 */
async function pullWithSnapshot(
  backendUrl: string, worldId: number, dir: string, token: string | undefined, force = false,
): Promise<{ ok: boolean; message?: string; pulled?: number; skipped?: number; conflict?: string[] }> {
  const snap = readSnapshot(dir)
  const tree = await fetchRemoteTree(backendUrl, worldId, token)
  if (!tree) return { ok: false, message: '无法获取世界文件树（需登录态）' }
  const cmp = compareMirror(tree, dir, snap)

  if (!force && (cmp.changedLocal.length > 0 || cmp.conflict.length > 0)) {
    return {
      ok: false,
      message: '本地有未推送的修改或冲突，已取消拉取（不会覆盖你的改动）',
      conflict: cmp.conflict,
    }
  }

  const pullTargets = [...cmp.added, ...cmp.changedRemote]
  if (force) pullTargets.push(...cmp.conflict, ...cmp.changedLocal) // force：冲突与本地修改都以远端为准覆盖
  let pulled = 0
  let skipped = 0
  const pulledOk: string[] = []
  for (const rel of pullTargets) {
    try {
      let content: string | null = null
      // HTML 走带 token 的原始读取（静态路由会注入世界变量，拉下来的是注入版）
      if (/\.html?$/i.test(rel)) {
        if (!token) { skipped++; continue }
        const res = await backendRequest(backendUrl, 'GET', `/worlds/${worldId}/files/content?path=${encodeURIComponent(rel)}`, { token })
        if (res.status !== 200) { skipped++; continue }
        try {
          const data = JSON.parse(res.text) as { content?: unknown; binary?: boolean }
          if (data.binary) { skipped++; continue }
          content = typeof data.content === 'string' ? data.content : null
        } catch { skipped++; continue }
      } else {
        const res = await backendRequest(backendUrl, 'GET', `/world/${worldId}/files/${encodeURIComponent(rel)}`)
        if (res.status !== 200) { skipped++; continue }
        content = res.text
      }
      if (content === null) { skipped++; continue }
      const target = join(dir, rel)
      mkdirSync(join(target, '..'), { recursive: true })
      writeFileSync(target, content, 'utf8')
      pulled++
      pulledOk.push(rel)
    } catch { skipped++ }
  }
  // 远端删除：本地没改过（或 force）→ 删本地。
  const removedOk: string[] = []
  for (const rel of cmp.removed) {
    if (force || !cmp.changedLocal.includes(rel)) {
      try { unlinkSync(join(dir, rel)); pulled++; removedOk.push(rel) } catch { skipped++ }
    }
  }

  // 更新快照：只更新「实际成功同步」的文件，其余保留旧记录——
  // 否则未同步的本地修改/冲突会被洗白成「已同步」，下次对比就检测不到了。
  const nextSnap: SyncSnapshot = { v: 1, files: { ...snap.files } }
  for (const rel of pulledOk) {
    const rm = tree.find((t) => t.path === rel)?.mtime ?? snap.files[rel]?.rm ?? 0
    nextSnap.files[rel] = { lm: statMtime(join(dir, rel)), rm }
  }
  for (const rel of removedOk) delete nextSnap.files[rel]
  writeSnapshot(dir, nextSnap)

  const parts: string[] = []
  if (cmp.added.length) parts.push(`+${cmp.added.length} 新增`)
  if (cmp.changedRemote.length) parts.push(`~${cmp.changedRemote.length} 修改`)
  if (cmp.removed.length) parts.push(`-${cmp.removed.length} 删除`)
  if (pulled > 0 && !parts.length) parts.push(`↓${pulled} 覆盖`) // force 拉取本地改动/冲突时也如实上报
  return {
    ok: true,
    pulled,
    skipped,
    conflict: cmp.conflict,
    message: `已拉取世界最新文件${parts.length ? `：${parts.join(' ')}` : '（无变化）'}`,
  }
}

/** 推送（带快照/冲突保护）：推本地修改；冲突文件跳过（除非 force）。 */
async function pushWithSnapshot(
  backendUrl: string, worldId: number, dir: string, token: string | undefined, force = false,
): Promise<{ ok: boolean; message?: string; pushed?: number; skipped?: number; conflict?: string[] }> {
  if (!token) return { ok: false, message: '该世界会话未连接登录态，无法推送（需 owner 权限）' }
  const snap = readSnapshot(dir)
  const tree = await fetchRemoteTree(backendUrl, worldId, token)
  if (!tree) return { ok: false, message: '无法获取世界文件树（需登录态）' }
  const cmp = compareMirror(tree, dir, snap)

  const pushTargets = new Set([...cmp.changedLocal, ...cmp.added])
  if (force) for (const c of cmp.conflict) pushTargets.add(c) // force：冲突文件也以本地为准覆盖远端
  let pushed = 0
  let skipped = 0
  const errors: string[] = []
  const pushedOk: string[] = []
  const localFiles = walkDir(dir)
  for (const rel of localFiles) {
    if (!pushTargets.has(rel) || isMirrorExcluded(rel)) continue
    if (cmp.conflict.includes(rel) && !force) { errors.push(`${rel}（冲突，远端也改过——请先裁决或用 force 覆盖）`); continue }
    try {
      const content = readFileSync(join(dir, rel), 'utf8')
      const res = await backendRequest(backendUrl, 'PUT', `/worlds/${worldId}/files`, { token, json: { path: rel, content } })
      if (res.status === 200) { pushed++; pushedOk.push(rel) }
      else errors.push(`${rel} (${res.status})`)
    } catch { errors.push(`${rel}（读写失败）`) }
  }
  // 本地删除（远端有、本地没有且快照有记录）：推删除。
  const removedOk: string[] = []
  for (const p of cmp.removed) {
    if (!force && cmp.conflict.includes(p)) continue
    const res = await backendRequest(backendUrl, 'DELETE', `/worlds/${worldId}/files?path=${encodeURIComponent(p)}`, { token })
    if (res.status === 200) { pushed++; removedOk.push(p) }
    else errors.push(`${p} (删除 ${res.status})`)
  }

  // 更新快照：只更新「实际推送成功」的文件，其余保留旧记录——
  // 否则未推送的远端改动会被洗白，温和自动拉取就检测不到世界新版本了。
  // 远端 mtime 用推送后重新拉取的树（PUT 会更新远端 mtime）。
  let freshTree = tree
  if (pushed > 0) freshTree = (await fetchRemoteTree(backendUrl, worldId, token)) ?? tree
  const nextSnap: SyncSnapshot = { v: 1, files: { ...snap.files } }
  for (const rel of pushedOk) {
    const rm = freshTree.find((t) => t.path === rel)?.mtime ?? snap.files[rel]?.rm ?? 0
    nextSnap.files[rel] = { lm: statMtime(join(dir, rel)), rm }
  }
  for (const rel of removedOk) delete nextSnap.files[rel]
  writeSnapshot(dir, nextSnap)

  return {
    ok: true,
    pushed,
    skipped,
    conflict: cmp.conflict,
    message: `已同步到世界${pushed ? `（${pushed} 个文件）` : '（无变化）'}`,
  }
}

/** 递归列出一个目录下的全部文件（相对路径，跳过 __pycache__）。 */
function walkDir(root: string): string[] {
  const out: string[] = []
  const visit = (dir: string): void => {
    let entries: Array<{ name: string; isDirectory: () => boolean; isFile: () => boolean; isSymbolicLink: () => boolean }>
    try { entries = readdirSync(dir, { withFileTypes: true }) } catch { return }
    for (const e of entries) {
      const full = join(dir, e.name)
      const rel = full.slice(root.length).replace(/^[/\\]/, '')
      if (e.isDirectory()) {
        if (e.name === '__pycache__') continue
        visit(full)
      } else if (e.isFile() || e.isSymbolicLink()) {
        out.push(rel)
      }
    }
  }
  visit(root)
  return out
}

/** @param ctx - harness context exposing the webServer carrier. */
export function apply(ctx: Context, config: Config): void {
  const backendUrl = config.backendUrl.replace(/\/+$/, '')

  ctx.webServer.register({
    kind: 'prefix',
    path: HTTP_PREFIX,
    handler: (req, res) => {
      const targetPath = req.url ? req.url.slice(HTTP_PREFIX.length) : '/'
      proxyHttp(backendUrl, req, res, targetPath.startsWith('/') ? targetPath : `/${targetPath}`)
    },
  })

  ctx.webServer.register({
    kind: 'prefix',
    path: UI_PREFIX,
    handler: serveStatic,
  })

  ctx.webServer.registerUpgrade({
    path: WS_PATH,
    handler: (req, socket, head) => {
      proxyWs(backendUrl, req, socket, head)
    },
  })

  // ── 插件自更新端点（状态 / 换入 / 回滚） ────────────────────────────
  registerPluginRoutes((route) => ctx.webServer.register(route), {
    installRoot: PACKAGE_ROOT,
    backendUrl,
    sourceDir: config.pluginSourceDir,
    log: (message) => ctx.logger?.info?.(message),
  })

  // ── 反向桥接（Copree 管理端 → DSH 本体会话） ────────────────────────
  registerBridge(ctx, {
    enabled: config.bridgeEnabled,
    secret: config.bridgeSecret,
    advertiseUrl: config.bridgeAdvertiseUrl.replace(/\/+$/, ''),
    backendUrl,
    version: PLUGIN_VERSION,
    heartbeatMs: config.bridgeHeartbeatMs,
    log: (message) => ctx.logger?.info?.(message),
  })

  // ── 世界工作区同步端点 ──────────────────────────────────────────────
  ctx.webServer.register({
    kind: 'prefix',
    path: WORLDS_PREFIX,
    handler: (req, res) => {
      const route = (req.url ?? '/').split('?')[0]
      const send = (status: number, json: unknown): void => sendJson(res, status, json)
      if (req.method === 'POST' && route === `${WORLDS_PREFIX}/dir`) {
        readJsonBody(req).then((body) => {
          const worldId = Number(body.worldId)
          const name = String(body.name ?? '')
          if (!Number.isInteger(worldId) || worldId <= 0) { send(400, { error: 'invalid worldId' }); return }
          const existing = worldDirFor(worldId)   // 改名前的世界镜像（aischat-worlds/AIC群视界-*）原样复用
          if (existing) { send(200, { path: existing }); return }
          const dirName = sanitizeDirName(`Copree群视界-${name || `世界${worldId}`}`)
          const dir = join(WORLD_DIR_BASE, dirName)
          try {
            mkdirSync(dir, { recursive: true })
            const metaPath = join(dir, '.copree-world.json')
            if (!existsSync(metaPath)) {
              writeFileSync(metaPath, JSON.stringify({ worldId, name }, null, 2), 'utf8')
            } else {
              const prev = JSON.parse(readFileSync(metaPath, 'utf8')) as { worldId?: unknown }
              if (Number(prev.worldId) !== worldId) { send(409, { error: `目录已属于世界 ${prev.worldId}` }); return }
            }
            send(200, { path: dir })
          } catch (e) {
            send(500, { error: String((e as Error).message ?? e) })
          }
        }).catch(() => send(400, { error: 'bad request' }))
        return
      }
      if (req.method === 'POST' && route === `${WORLDS_PREFIX}/token`) {
        readJsonBody(req).then((body) => {
          const worldId = Number(body.worldId)
          const token = String(body.token ?? '')
          // 兼容旧上报（sessionId 形式）：忽略 sessionId，仅按 worldId 记。
          if (!Number.isInteger(worldId) || worldId <= 0) { send(400, { error: 'missing worldId' }); return }
          if (!token) { send(400, { error: 'missing token' }); return }
          worldTokenMap.set(worldId, token)
          ctx.logger?.info?.(`dsh-copree: token registered for world ${worldId}`)
          send(200, { ok: true })
        }).catch(() => send(400, { error: 'bad request' }))
        return
      }
      // 诊断：当前 host 内存里的世界 token 状态（仅调试用，不含 token 明文）。
      if (req.method === 'GET' && route === `${WORLDS_PREFIX}/status`) {
        send(200, {
          tokenWorlds: [...worldTokenMap.keys()],
          worldDirs: listWorldDirs(),
        })
        return
      }
      // 拉取世界文件到工作区镜像目录（列文件树需 owner token；读单个文件免鉴权）。
      if (req.method === 'POST' && route === `${WORLDS_PREFIX}/pull`) {
        readJsonBody(req).then(async (body) => {
          const worldId = Number(body.worldId)
          if (!Number.isInteger(worldId) || worldId <= 0) { send(400, { error: 'invalid worldId' }); return }
          const dir = worldDirFor(worldId)
          if (!dir) { send(404, { error: `工作区没有世界 ${worldId} 的目录（请先同步）` }); return }
          const result = await pullWithSnapshot(backendUrl, worldId, dir, worldTokenMap.get(worldId), body.force === true)
          send(result.ok ? 200 : 409, result)
        }).catch(() => send(400, { error: 'bad request' }))
        return
      }
      send(404, { error: 'not found' })
    },
  })

  // ── 世界操作工具（按会话所属世界路由） ─────────────────────────────
  const worldFromExec = (exec: { agent?: { session?: { header?: { cwd?: string }; id?: string } } }): { worldId: number; name: string; token?: string } | null => {
    const session = exec.agent?.session
    const world = resolveWorldFromCwd(session?.header?.cwd)
    if (!world) return null
    // 优先按世界取 token（稳定标识）；兼容旧 sessionId 上报。
    const token = worldTokenMap.get(world.worldId)
      ?? (session?.id ? sessionTokenMap.get(String(session.id)) : undefined)
    return { ...world, token }
  }

  const registerWorldTool = (
    toolName: string,
    description: string,
    parameters: Record<string, unknown>,
    execute: (args: Record<string, unknown>, world: { worldId: number; name: string; token?: string }) => Promise<unknown>,
  ): void => {
    ctx.tools.register({
      name: toolName,
      description,
      parameters,
      output: { schema: { type: 'object' }, render: (_args, value) => textOutput(value) },
      execute: async (rawArgs, exec) => {
        const world = worldFromExec(exec as never)
        if (!world) {
          return { error: '当前会话不属于任何 Copree 世界：请先在工作区打开一个「Copree群视界-世界名」会话（该会话目录需含 .copree-world.json）。' }
        }
        try {
          const result = await execute((rawArgs ?? {}) as Record<string, unknown>, world)
          // 版本提示注入：非同步类工具执行后，若世界有更新未拉取 / 存在冲突，
          // 在结果上附加提示，让 agent 知道可 world_pull / 需处理冲突（GitHub 式）。
          if (result && typeof result === 'object' && !toolName.startsWith('world_pull') && !toolName.startsWith('world_push')) {
            const dir = worldDirFor(world.worldId)
            if (dir) {
              try {
                const snap = readSnapshot(dir)
                const tree = await fetchRemoteTree(backendUrl, world.worldId, world.token)
                if (tree) {
                  const cmp = compareMirror(tree, dir, snap)
                  const updateCount = cmp.added.length + cmp.changedRemote.length
                  if (updateCount > 0) {
                    (result as Record<string, unknown>).updateHint = `世界有 ${updateCount} 个文件更新未拉取（用 world_pull 获取最新；若你刚改过文件，先 world_push）`
                  }
                  if (cmp.conflict.length > 0) {
                    (result as Record<string, unknown>).conflictHint = `以下文件存在同步冲突：${cmp.conflict.slice(0, 5).join(', ')}（用 world_pull / world_push 的 force 裁决）`
                  }
                }
              } catch { /* 提示失败不影响主结果 */ }
            }
          }
          return result
        } catch (e) {
          return { error: String((e as Error).message ?? e) }
        }
      },
    } as never)
  }

  registerWorldTool(
    'world_list_files',
    '列出当前 Copree 群视界世界的文件树（世界页面代码等）。返回文件列表（相对路径、大小、类型）。',
    { type: 'object', properties: { prefix: { type: 'string', description: '可选前缀过滤，如 css/ 或 blocks/' } }, additionalProperties: false },
    async (args, world) => {
      if (!world.token) return { error: '该世界会话未连接登录态，无法列文件（需 owner 权限）。请重新打开 Copree 同步一次。' }
      const prefix = encodeURIComponent(String(args.prefix ?? ''))
      const res = await backendRequest(backendUrl, 'GET', `/worlds/${world.worldId}/files?prefix=${prefix}`, { token: world.token })
      if (res.status !== 200) return { error: `列文件失败 (${res.status})`, detail: res.text.slice(0, 400) }
      try { return JSON.parse(res.text || '{}') as unknown } catch { return { ok: true, content: res.text.slice(0, 4000) } }
    },
  )

  registerWorldTool(
    'world_read_file',
    '读取当前 Copree 群视界世界的一个文件内容（如 index.html、script.js、style.css）。',
    { type: 'object', properties: { path: { type: 'string', description: '相对路径，如 index.html 或 blocks/group-chat/chat-panel.js' } }, required: ['path'], additionalProperties: false },
    async (args, world) => {
      const path = String(args.path ?? '')
      if (!path) return { error: '缺少 path' }
      const res = await backendRequest(backendUrl, 'GET', `/world/${world.worldId}/files/${encodeURIComponent(path)}`)
      if (res.status !== 200) return { error: `读文件失败 (${res.status})` }
      return { ok: true, path, content: res.text.slice(0, 60000) }
    },
  )

  registerWorldTool(
    'world_write_file',
    '写入当前 Copree 群视界世界的一个文件（覆盖；自动建目录）。用于修改世界页面代码。',
    { type: 'object', properties: { path: { type: 'string', description: '相对路径，如 index.html' }, content: { type: 'string', description: '完整文件内容' } }, required: ['path', 'content'], additionalProperties: false },
    async (args, world) => {
      if (!world.token) return { error: '该世界会话未连接登录态，无法写文件（需 owner 权限）。' }
      const res = await backendRequest(backendUrl, 'PUT', `/worlds/${world.worldId}/files`, {
        token: world.token,
        json: { path: String(args.path ?? ''), content: String(args.content ?? '') },
      })
      if (res.status !== 200) return { error: `写文件失败 (${res.status})`, detail: res.text.slice(0, 400) }
      try { return { ok: true, ...(JSON.parse(res.text || '{}') as object) } } catch { return { ok: true } }
    },
  )

  registerWorldTool(
    'world_delete_file',
    '删除当前 Copree 群视界世界的一个文件。',
    { type: 'object', properties: { path: { type: 'string', description: '相对路径' } }, required: ['path'], additionalProperties: false },
    async (args, world) => {
      if (!world.token) return { error: '该世界会话未连接登录态，无法删文件。' }
      const path = encodeURIComponent(String(args.path ?? ''))
      const res = await backendRequest(backendUrl, 'DELETE', `/worlds/${world.worldId}/files?path=${path}`, { token: world.token })
      if (res.status !== 200) return { error: `删文件失败 (${res.status})`, detail: res.text.slice(0, 400) }
      return { ok: true }
    },
  )

  registerWorldTool(
    'world_api',
    '调用当前 Copree 群视界世界的受控 API（GET/POST /world/{id}/api/{endpoint}）。常用：world（世界信息）、chat（对话历史）、memories（记忆）、usage（用量）、groups（绑定群列表）、group/messages（群消息）、state（状态）、data/{key}（世界数据）。',
    { type: 'object', properties: { endpoint: { type: 'string', description: 'API 路径，如 world / chat / memories / usage / groups / group/messages / state / data/myk' }, method: { type: 'string', enum: ['GET', 'POST', 'PUT', 'DELETE'], default: 'GET' }, query: { type: 'object', description: '查询参数键值（字符串化）' }, body: { type: 'object', description: 'POST/PUT 请求体' } }, required: ['endpoint'], additionalProperties: false },
    async (args, world) => {
      const endpoint = String(args.endpoint ?? '').replace(/^\/+/, '')
      if (!endpoint) return { error: '缺少 endpoint' }
      const method = String(args.method ?? 'GET').toUpperCase()
      const qs = new URLSearchParams()
      for (const [k, v] of Object.entries((args.query ?? {}) as Record<string, unknown>)) {
        if (v !== undefined && v !== null) qs.set(k, String(v))
      }
      const q = qs.toString()
      // 世界受控 API 认沙箱 api_token（X-World-Token），不认用户 token。
      const apiToken = await resolveWorldApiToken(backendUrl, world.worldId, world.token)
      if (!apiToken) return { error: '无法取得该世界的 API token（需 owner 登录态且世界已初始化）。' }
      const res = await backendRequest(backendUrl, method, `/world/${world.worldId}/api/${endpoint}${q ? `?${q}` : ''}`, {
        headers: { 'x-world-token': apiToken },
        json: method === 'GET' ? undefined : (args.body ?? {}),
      })
      if (res.status >= 400) return { error: `API 调用失败 (${res.status})`, detail: res.text.slice(0, 400) }
      try { return JSON.parse(res.text) as unknown } catch { return { ok: true, content: res.text.slice(0, 60000) } }
    },
  )

  registerWorldTool(
    'world_view_doc',
    '查看「群视界 API 文档」的接口文档：不传 section 返回分区列表（id/标题/区介绍），传 section（如 03）返回该分区完整内容。文档按区分区：01 世界编号变量（写页面代码前必读）、02 世界UI桥、03 文件操作、04 积木体系、05 群聊 API、06 页面与资源、07 懒通知与世界时间、08 错误与安全、09 世界 API。先看分区列表的区介绍判断要开哪个区，只开需要的，不要一次全读。',
    { type: 'object', properties: { section: { type: 'string', description: '分区号（01~09），不传则返回分区列表' } }, required: [], additionalProperties: false },
    async (args, world) => {
      const apiToken = await resolveWorldApiToken(backendUrl, world.worldId, world.token)
      if (!apiToken) return { error: '无法取得该世界的 API token（需 owner 登录态且世界已初始化）。' }
      const section = String(args.section ?? '').trim()
      const path = section ? `/world/${world.worldId}/api/docs/${encodeURIComponent(section)}` : `/world/${world.worldId}/api/docs`
      const res = await backendRequest(backendUrl, 'GET', path, { headers: { 'x-world-token': apiToken } })
      if (res.status >= 400) return { error: `文档读取失败 (${res.status})`, detail: res.text.slice(0, 400) }
      try { return JSON.parse(res.text) as unknown } catch { return { ok: true, content: res.text.slice(0, 60000) } }
    },
  )

  registerWorldTool(
    'world_chat',
    '读写当前 Copree 群视界世界绑定群聊的消息。action=read 拉最近消息（groupId 省略时自动取世界绑定的第一个群）；action=send 以世界身份发消息。',
    { type: 'object', properties: { action: { type: 'string', enum: ['read', 'send'] }, groupId: { type: 'number' }, content: { type: 'string', description: 'send 时的消息内容' }, limit: { type: 'number', default: 20 } }, required: ['action'], additionalProperties: false },
    async (args, world) => {
      const apiToken = await resolveWorldApiToken(backendUrl, world.worldId, world.token)
      if (!apiToken) return { error: '无法取得该世界的 API token（需 owner 登录态且世界已初始化）。' }
      const worldHeaders = { 'x-world-token': apiToken }
      const action = String(args.action ?? '')
      let groupId = Number(args.groupId)
      if (!groupId) {
        const g = await backendRequest(backendUrl, 'GET', `/world/${world.worldId}/api/groups`, { headers: worldHeaders })
        if (g.status === 200) {
          try {
            const groups = JSON.parse(g.text) as Array<{ id?: unknown }>
            groupId = Number(groups?.[0]?.id)
          } catch { /* ignore */ }
        }
      }
      if (!groupId) return { error: '该世界未绑定群聊，无法读写消息。' }
      if (action === 'read') {
        const limit = Number(args.limit ?? 20)
        const res = await backendRequest(backendUrl, 'GET', `/world/${world.worldId}/api/group/messages?group_id=${groupId}&limit=${limit}`, { headers: worldHeaders })
        if (res.status !== 200) return { error: `读消息失败 (${res.status})`, detail: res.text.slice(0, 300) }
        try { return JSON.parse(res.text) as unknown } catch { return { ok: true, content: res.text.slice(0, 60000) } }
      }
      if (action === 'send') {
        const content = String(args.content ?? '')
        if (!content) return { error: '缺少 content' }
        const res = await backendRequest(backendUrl, 'POST', `/world/${world.worldId}/api/group/messages`, {
          headers: worldHeaders,
          json: { group_id: groupId, content },
        })
        if (res.status >= 400) return { error: `发消息失败 (${res.status})`, detail: res.text.slice(0, 300) }
        return { ok: true, sent: content }
      }
      return { error: 'action 必须是 read 或 send' }
    },
  )

  registerWorldTool(
    'world_lifecycle',
    '唤醒或休眠当前 Copree 群视界世界（wake 应用离线时间补偿并启动常驻；sleep 休眠）。',
    { type: 'object', properties: { action: { type: 'string', enum: ['wake', 'sleep'] } }, required: ['action'], additionalProperties: false },
    async (args, world) => {
      if (!world.token) return { error: '该世界会话未连接登录态，无法控制世界生命周期。' }
      const action = String(args.action ?? '')
      if (action !== 'wake' && action !== 'sleep') return { error: 'action 必须是 wake 或 sleep' }
      const res = await backendRequest(backendUrl, 'POST', `/worlds/${world.worldId}/${action}`, { token: world.token })
      if (res.status >= 400) return { error: `${action} 失败 (${res.status})`, detail: res.text.slice(0, 400) }
      try { return JSON.parse(res.text) as unknown } catch { return { ok: true } }
    },
  )

  registerWorldTool(
    'world_pull',
    '把 Copree 世界的最新文件拉取到当前工作区目录（本地世界镜像）。' +
    '带冲突保护：本地有未推送的修改或冲突文件时会拒绝并报告，绝不覆盖你的改动；' +
    'force=true 时强制以世界为准覆盖。拉取后返回变化清单（新增/修改/删除）。' +
    '注意：返回里的 pulled N 是实际下载写盘的文件数（force 覆盖本地改动也计入）；' +
    'message 报「无变化」只表示远端无新增/修改/删除，不代表没拉东西。',
    { type: 'object', properties: { force: { type: 'boolean', description: 'true 时强制以世界为准覆盖本地（含冲突）' } }, additionalProperties: false },
    async (args, world) => {
      const dir = worldDirFor(world.worldId)
      if (!dir) return { error: '找不到该世界的工作区目录。' }
      const result = await pullWithSnapshot(backendUrl, world.worldId, dir, world.token, args.force === true)
      return result.ok
        ? { ok: true, message: result.message, pulled: result.pulled, skipped: result.skipped, conflict: result.conflict }
        : { error: result.message, conflict: result.conflict }
    },
  )

  registerWorldTool(
    'world_push',
    '把当前工作区目录（本地世界镜像）的全部改动同步回 Copree 世界。' +
    '只推送本地修改过的文件（带快照对比）；冲突文件（远端也改过）默认跳过并报告，' +
    'force=true 时以本地为准覆盖。排除本地元数据 .copree-world.json 与 __pycache__。' +
    '你（agent）用 DSH 原生 read/write/edit/bash 修改工作区文件后调用本工具让改动在 Copree 中生效。' +
    '注意：返回「已同步（无变化）」= 快照认为本地与远端已一致（改动很可能已在远端），用 world_read_file 复核内容，别当没生效。',
    { type: 'object', properties: { force: { type: 'boolean', description: 'true 时以本地为准强制覆盖冲突文件' } }, additionalProperties: false },
    async (args, world) => {
      const dir = worldDirFor(world.worldId)
      if (!dir) return { error: '找不到该世界的工作区目录。' }
      const result = await pushWithSnapshot(backendUrl, world.worldId, dir, world.token, args.force === true)
      return result.ok
        ? { ok: true, message: result.message, pushed: result.pushed, skipped: result.skipped, conflict: result.conflict }
        : { error: result.message, conflict: result.conflict }
    },
  )

  registerWorldTool(
    'world_run',
    '在 Copree 后端沙箱中运行一段 Python 代码（世界上下文：注入 WORLD_ID/WORLD_API_TOKEN 等环境；配额默认 24MB/10s）。' +
    '适合测试世界逻辑；完整的页面/逻辑改动请用 DSH 原生工具改工作区文件 + world_push。',
    { type: 'object', properties: { code: { type: 'string', description: '要运行的 Python 代码' }, entry: { type: 'string', description: '可选入口，如 main.py' } }, required: ['code'], additionalProperties: false },
    async (args, world) => {
      if (!world.token) return { error: '该世界会话未连接登录态，无法运行世界代码（需 owner 权限）。' }
      const res = await backendRequest(backendUrl, 'POST', `/worlds/${world.worldId}/run`, {
        token: world.token,
        json: { code: String(args.code ?? ''), entry: args.entry ? String(args.entry) : undefined },
      })
      if (res.status >= 400) return { error: `运行失败 (${res.status})`, detail: res.text.slice(0, 400) }
      try { return JSON.parse(res.text) as unknown } catch { return { ok: true, content: res.text.slice(0, 60000) } }
    },
  )

  registerWorldTool(
    'world_trigger',
    '触发当前 Copree 世界入口的 handle(event)（世界沙箱），用于测试世界对事件的响应。',
    { type: 'object', properties: { event: { type: 'object', description: '事件载荷，如 {type: "message", ...}' }, entry: { type: 'string', description: '可选入口，如 main.py' } }, required: ['event'], additionalProperties: false },
    async (args, world) => {
      if (!world.token) return { error: '该世界会话未连接登录态，无法触发世界（需 owner 权限）。' }
      const res = await backendRequest(backendUrl, 'POST', `/worlds/${world.worldId}/trigger`, {
        token: world.token,
        json: { event: args.event ?? {}, entry: args.entry ? String(args.entry) : undefined },
      })
      if (res.status >= 400) return { error: `触发失败 (${res.status})`, detail: res.text.slice(0, 400) }
      try { return JSON.parse(res.text) as unknown } catch { return { ok: true, content: res.text.slice(0, 60000) } }
    },
  )

  // ── 世界会话提示词（泛化引导，不依赖具体会话） ─────────────────────
  ctx.systemPrompt.section({
    name: 'copree-world-context',
    order: 150,
    text: '如果你的会话工作目录位于 copree-worlds 目录下（目录名以「Copree群视界-」开头），你正在操作一个 Copree 群视界世界：' +
      '该工作目录是世界的「本地镜像」——世界页面代码、数据文件都在里面，你可以直接用 DSH 原生的 read/write/edit/glob/grep/bash ' +
      '工具读写它们（bash 可直接运行世界 Python 代码测试）。修改完成后调用 world_push 把改动同步回 Copree 世界；' +
      '若世界在别处被改过、需要最新文件时用 world_pull 主动拉取。' +
      '精确操作（世界 API、绑定群聊消息、唤醒/休眠、沙箱运行）用 world_* 系列工具。' +
      '同步/限流机制（push「无变化」含义、429 处理、pulled 语义）用 world_view_doc 打开 10 分区查看。' +
      '世界是用户嵌入 DSH 的「可操作对象」——你的推理与工具仍走 DSH 体系，只是操作目标属于 Copree。',
  })

  ctx.logger?.info?.(`dsh-copree: proxying /copree-api and /copree-ws -> ${backendUrl}; serving /copree-ui; world sync at ${WORLDS_PREFIX}`)
}
