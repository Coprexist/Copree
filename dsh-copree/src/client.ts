// SPDX-License-Identifier: MIT
/**
 * dsh-copree — browser half.
 *
 * Copree as a first-class sidebar board, like the Workspace board:
 *
 * - `sidebar.footer.action` `copree-entry`: toggles the Copree board.
 * - `shell.overlay` `copree-board`: while the board is open it covers the
 *   whole frame and renders its own left rail (联系人板块: 置顶/私信/群聊,
 *   expanded like workspace folders) beside the conversation column (messages
 *   + composer). Opening the board hides the Workspace board; closing it
 *   restores DSH. No DSH session or composer semantics are touched — the
 *   composer inside the board sends to the selected Copree conversation.
 * - `settings.section` `copree`: settings page (login / sign-out / note).
 *
 * All traffic is same-origin: HTTP `/copree-api/*`, WS
 * `/copree-ws?token=...`, both proxied by the host half to the local Copree
 * backend. The token lives in browser localStorage only.
 *
 * @module dsh-copree/client
 */
const React = require('react')
const { useEffect, useState, useRef, useCallback, useMemo } = React
// Reuse the shipped DSH Markdown renderer (GFM + KaTeX math + safe-HTML
// filtering) so Copree messages render exactly like conversation text.
// 我方与对方消息都用 MarkdownText（完整 GFM/LaTeX，我方保留 DSH 用户
// 气泡样式）——任何针对 DSH 对话渲染风格的主题/插件改动都会自动作用到
// Copree。
const { MarkdownText, IconNewChatOutline16 } = require('@deepseek-ai/dsh-client-ui-primitives')

/**
 * MarkdownText 的代码块 / 脚注文案。
 *
 * 外壳的 MarkdownText 强制要求这个 labels 包：渲染围栏代码块时会直接读
 * labels.code.copyLabel，整包缺失就在渲染期抛 TypeError。异常发生在
 * shell.overlay 槽内，会被外壳的错误边界整块摘掉——表现出来就是
 * 「点开某个含代码块的会话，Copree 界面直接消失」。
 * 外壳自己的对话页从 locale seat 生成同一份文案（markdownLabels(t)）；
 * 客户端插件没有这个 seat，这里给等价文案。
 */
const MARKDOWN_LABELS = {
  code: { copyLabel: '复制', copiedLabel: '已复制' },
  footnotes: '脚注',
}

/** Plugin identity. */
const PLUGIN_ID = 'dsh-copree'

/** Same-origin API base answered by the host half. */
const API = '/copree-api'
/** WebSocket endpoint answered by the host half (upgrade proxy). */
const WS_BASE = '/copree-ws'
/** Plugin self-update endpoints answered by the host half (outside the Copree proxy prefix). */
const PLUGIN_API = '/copree-plugin'
// 桥接闸门的控制端点：与 /copree-bridge 刻意不共享前缀——没同意时那边整条都不存在
const CONSENT_API = '/copree-consent'

/** Browser-local storage keys (guarded read/write). */
const K_TOKEN = 'aisc.token'
const K_USER = 'aisc.user'

/** Module-level store: survives view re-mounts within one page lifetime. */
const store = {
  token: guardedGet(K_TOKEN),
  user: parseUser(guardedGet(K_USER)),
  contacts: null,
  contactsLoadedAt: 0,
  nameCache: {}, // sender_id -> display name
  active: null,  // { kind: 'group'|'dm', id: number|string, title: string }
  messages: null,
  ws: null,
}

function guardedGet(key) {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}
function guardedSet(key, value) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* private mode: session-lifetime only */
  }
}
function parseUser(raw) {
  try {
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

/** Notify every mounted surface (settings page, board) to re-read state. */
function broadcast(what) {
  window.dispatchEvent(new CustomEvent('copree:' + what))
}

/**
 * Same-origin JSON fetch through the host proxy. The Authorization header is
 * attached when a token exists; the proxy forwards it to the backend.
 */
async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  if (options.json !== undefined) headers['Content-Type'] = 'application/json'
  if (store.token) headers['Authorization'] = `Bearer ${store.token}`
  const res = await fetch(`${API}${path}`, {
    method: options.method || 'GET',
    headers,
    body: options.json !== undefined ? JSON.stringify(options.json) : options.body,
    cache: 'no-store',
  })
  let data = null
  try {
    data = await res.json()
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) {
    // 已登录状态下收到 401：token 失效/被拒 → 自动登出回到登录页，而不是
    // 让调用方把失败静默吞成"暂无联系人"之类的假空态。登录接口自身的 401
    // 是凭据错误，由 LoginForm 展示，不触发登出。
    if (res.status === 401 && store.token && !path.startsWith('/auth/login')) {
      doLogout()
    }
    const detail = data && (data.detail || data.message)
    const err = new Error(typeof detail === 'string' ? detail : `request failed (${res.status})`)
    err.status = res.status
    throw err
  }
  return data
}

/**
 * Call a host-half plugin endpoint. These live outside the Copree proxy prefix,
 * so they bypass api() and are not authenticated.
 */
async function pluginApi(path, options = {}) {
  const res = await fetch(PLUGIN_API + path, { method: options.method || 'GET', cache: 'no-store' })
  let data = null
  try { data = await res.json() } catch { /* non-JSON body */ }
  if (!res.ok || (data && data.ok === false)) {
    throw new Error((data && (data.error || data.detail)) || `request failed (${res.status})`)
  }
  return data
}

/** 构建身份是内容摘要，展示前 7 位即可（同镜像 digest 的短标识用法）。 */
function shortId(id) {
  return id ? String(id).slice(0, 7) : '—'
}

/** 把插件状态翻译成一句人话；失败原因如实展示，不笼统报“不可用”。 */
function pluginStateText(status) {
  if (!status) return '读取中…'
  // 分发出去的副本不归本地的版本语义管：装了市场就推荐走市场，
  // 没装市场则由这里代劳，用包管理器从原来源更新。
  if (status.updateChannel === 'market') return '推荐在「设置 → 插件市场」更新'
  if (status.updateChannel === 'package-manager') {
    return `从原来源更新（${status.installKind === 'git' ? 'git' : 'npm'}）`
  }
  if (status.state === 'update-available' && status.reason === 'incomplete') {
    return `安装不完整，缺 ${status.missing} 个文件`
  }
  if (status.state === 'update-available') return `可更新到 ${shortId(status.available && status.available.id)}`
  if (status.state === 'up-to-date') return '已是最新'
  if (status.state === 'source-unavailable') return '未找到更新源'
  if (status.state === 'not-installed') return '安装目录缺少构建清单'
  return String(status.state)
}

/** 该不该出按钮：本地来源可换入；没有市场时由我们代劳；有市场则不出手。 */
function pluginCanAct(status) {
  return Boolean(status) && (status.updateChannel === 'self' || status.updateChannel === 'package-manager')
}

/** 需要提示更新的两种情形：本地来源确有新版，或内置界面与后端 API 已漂移。 */
function pluginNeedsAttention(status) {
  if (!status) return false
  const localOutdated = status.updateChannel === 'self' && status.state === 'update-available'
  return localOutdated || Boolean(status.backend && status.backend.mismatch)
}

/** Load contacts once per page (cached; refresh() re-fetches). */
async function loadContacts(force = false) {
  if (!store.token) return
  if (!force && store.contacts && Date.now() - store.contactsLoadedAt < 30_000) return store.contacts
  const [groups, dms] = await Promise.all([
    api('/groups').catch(() => []),
    api('/dm/sessions').catch(() => []),
  ])
  store.contacts = { groups: Array.isArray(groups) ? groups : [], dms: Array.isArray(dms) ? dms : [] }
  store.contactsLoadedAt = Date.now()
  return store.contacts
}

/** Load message history for the active conversation (newest 50). */
async function loadMessages(active) {
  if (!active) return []
  let list = []
  // 群聊与私信同形：GET /gm/{group_id}/messages ↔ GET /dm/{session_id}/messages。
  // 两端都带认证、游标分页，且由服务端补齐 sender_name。
  const path = active.kind === 'group'
    ? `/gm/${encodeURIComponent(active.id)}/messages?limit=50`
    : `/dm/${encodeURIComponent(active.id)}/messages?limit=50`
  const fetched = await api(path)
  list = Array.isArray(fetched) ? fetched : []
  store.messages = list
  warmNameCache(list)
  return list
}

/**
 * Resolve display names for senders appearing in a message list. REST history
 * carries sender_name: null (the DB has no such column), so names are looked
 * up once per sender via /chat/user/{id} and cached. AI senders fall back to
 * their agent name when resolvable, else a neutral label.
 */
async function warmNameCache(messages) {
  if (!store.token || !Array.isArray(messages)) return
  const ids = new Set()
  for (const m of messages) {
    if (m && m.sender_id != null && store.nameCache[m.sender_id] === undefined) ids.add(m.sender_id)
  }
  for (const id of ids) {
    if (!store.token) break
    try {
      const u = await api(`/chat/user/${encodeURIComponent(id)}`)
      store.nameCache[id] = (u && (u.username || u.name)) || `用户${id}`
    } catch {
      store.nameCache[id] = `用户${id}`
    }
  }
}

/** Display name for a message sender. */
function senderName(m, user) {
  if (m == null) return ''
  if (m.sender_id != null && String(m.sender_type) !== 'ai' && String(m.sender_id) === String(user ? user.id : '')) return '我'
  if (m.sender_name) return m.sender_name
  // 优先按 "type:id" 查（群聊成员表缓存，含 AI 名字），再查无前缀（DM 用户缓存）
  if (m.sender_id != null && m.sender_type) {
    const keyed = store.nameCache[`${m.sender_type}:${m.sender_id}`]
    if (keyed) return keyed
  }
  if (m.sender_id != null && store.nameCache[m.sender_id]) return store.nameCache[m.sender_id]
  if (String(m.sender_type) === 'ai') return 'AI'
  return '用户' + (m.sender_id != null ? m.sender_id : '')
}

/** Build the WS URL for this origin (same-origin upgrade proxy). */
function wsUrl(token) {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}${WS_BASE}?token=${encodeURIComponent(token)}`
}

/**
 * Rewrite an Copree media URL for the DSH same-origin proxy.
 * Copree stores relative paths like `/api/fs/download-avatar/x.png` that its
 * own frontend serves through a vite proxy stripping the `/api` prefix. In
 * DSH the host half strips `/copree-api`, so `/api/...` maps 1:1 to
 * `/copree-api/...`. Absolute URLs (external avatars) pass through untouched.
 */
function mediaUrl(url) {
  if (!url || typeof url !== 'string') return url
  if (url.startsWith('/api/')) return url.replace(/^\/api\//, API + '/')
  // 后端其他相对路径（如 /fs/download/{id}）走同源代理前缀
  if (url.startsWith('/') && !url.startsWith(API + '/')) return API + url
  return url
}

/** Avatar URL with thumbnail hint for the backend's `?thumb=1` support. */
function avatarUrl(url) {
  const rewritten = mediaUrl(url)
  if (!rewritten) return rewritten
  if (rewritten.includes('/download-avatar/') && !/\.gif($|\?)/i.test(rewritten) && !rewritten.includes('thumb=')) {
    return rewritten + (rewritten.includes('?') ? '&thumb=1' : '?thumb=1')
  }
  return rewritten
}

/**
 * 消息附件图片：后端 /fs/download/{file_id} 需要认证，`<img>` 标签无法带
 * Authorization，所以用带 token 的 fetch 拉取 blob → objectURL 再展示。
 * objectURL 按 file_id 缓存（跨消息列表重渲染复用），卸载/不再使用时 revoke。
 */
const attachmentBlobCache = new Map() // fileId -> objectURL

function AttachmentImage({ fileId, name, style: imgStyle }) {
  const [src, setSrc] = useState(() => attachmentBlobCache.get(String(fileId)) || null)
  useEffect(() => {
    const key = String(fileId)
    if (attachmentBlobCache.has(key)) {
      setSrc(attachmentBlobCache.get(key))
      return
    }
    let url = null
    let cancelled = false
    ;(async () => {
      try {
        const headers = {}
        if (store.token) headers['Authorization'] = `Bearer ${store.token}`
        const res = await fetch(`${API}/fs/download/${encodeURIComponent(fileId)}`, { headers, cache: 'no-store' })
        if (!res.ok || cancelled) return
        const blob = await res.blob()
        url = URL.createObjectURL(blob)
        attachmentBlobCache.set(key, url)
        if (!cancelled) setSrc(url)
      } catch {
        /* 加载失败保持占位 */
      }
    })()
    return () => {
      cancelled = true
      /* 缓存里的 objectURL 由后续组件复用，不在此 revoke */
    }
  }, [fileId])
  return src
    ? h('img', { src, alt: name || '', style: imgStyle })
    : h('div', { style: { ...imgStyle, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--dsw-alias-label-tertiary)', fontSize: 12, background: 'var(--dsw-alias-interactive-bg-hover)' } }, '…')
}

/**
 * Prepare message text for the shipped Markdown renderer: it only displays
 * absolute HTTP(S) images and disables relative links, while Copree stores
 * media as relative `/api/...` paths. Rewrite markdown link/image targets to
 * absolute same-origin URLs (through the host proxy) so they render.
 */
function mdText(content) {
  const raw = String(content || '')
  return raw.replace(/(!?\[[^\]]*\]\()([^)\s]*)(\))/g, (_all, pre, url, post) => {
    if (!url || /^[a-z]+:/i.test(url) || url.startsWith('//') || url.startsWith('#') || url.startsWith('data:')) {
      return _all
    }
    const rewritten = mediaUrl(url)
    const absolute = rewritten.startsWith('/') ? window.location.origin + rewritten : rewritten
    return pre + absolute + post
  })
}

/** Open the WS, subscribe to the active conversation, wire message handling. */
function connectWs() {
  if (!store.token) return
  closeWs()
  let ws
  try {
    ws = new WebSocket(wsUrl(store.token))
  } catch {
    return
  }
  store.ws = ws
  ws.onopen = () => {
    subscribeActive()
  }
  ws.onmessage = (event) => {
    let msg
    try {
      msg = JSON.parse(event.data)
    } catch {
      return
    }
    if (!msg || typeof msg !== 'object') return
    if (msg.type === 'ping') {
      try { ws.send(JSON.stringify({ type: 'pong' })) } catch { /* noop */ }
      return
    }
    if (msg.type === 'error') {
      broadcast('error:' + (msg.message || msg.code || 'unknown'))
      return
    }
    if (msg.type !== 'message' && msg.type !== 'ai_response') return
    const data = msg.data
    if (!data) return
    // Only append when the pushed message belongs to the active conversation.
    const active = store.active
    if (!active) return
    // Prefer the outer conversation_type; fall back to the id key present.
    const ctype = msg.conversation_type || (data.session_id !== undefined ? 'dm' : 'group')
    const matches = active.kind === 'group'
      ? (ctype === 'group' || String(data.group_id) === String(active.id))
      : (ctype === 'dm' || String(data.session_id || data.dm_session_id) === String(active.id))
    if (!matches) return
    const list = store.messages || []
    if (!list.some((m) => m && m.id === data.id)) {
      list.push(data)
      store.messages = list
      warmNameCache([data])
      broadcast('message')
    }
  }
  ws.onclose = () => {
    if (store.ws === ws) {
      store.ws = null
      // Reconnect with backoff when token still present (clean 4001 auth close: no).
      if (store.token) setTimeout(() => { if (store.token && !store.ws) connectWs() }, 3000)
    }
  }
  ws.onerror = () => { /* onclose handles it */ }
}

function closeWs() {
  if (store.ws) {
    try { store.ws.close(1000) } catch { /* noop */ }
    store.ws = null
  }
}

function subscribeActive() {
  const ws = store.ws
  const active = store.active
  if (!ws || ws.readyState !== WebSocket.OPEN || !active) return
  const payload = { type: 'subscribe' }
  if (active.kind === 'group') payload.group_id = active.id
  else payload.session_id = active.id
  ws.send(JSON.stringify(payload))
}

/** Send a message: WS when open, HTTP fallback. */
async function sendMessage(content) {
  const active = store.active
  if (!active || !content) return
  if (store.ws && store.ws.readyState === WebSocket.OPEN) {
    const payload = { type: 'send', content }
    if (active.kind === 'group') payload.group_id = active.id
    else payload.session_id = active.id
    store.ws.send(JSON.stringify(payload))
    return
  }
  // 群聊与私信同形：POST /gm/{group_id}/messages ↔ POST /dm/{session_id}/messages。
  // 发送者身份由服务端从 token 解出，客户端不再自报 sender_type/sender_id。
  await api(
    active.kind === 'group'
      ? `/gm/${encodeURIComponent(active.id)}/messages`
      : `/dm/${encodeURIComponent(active.id)}/messages`,
    { method: 'POST', json: { content } },
  )
}

async function doLogin(loginId, password) {
  const data = await api('/auth/login', {
    method: 'POST',
    json: { login_id: loginId, password, method: 'direct' },
  })
  store.token = data.access_token
  store.user = { id: data.user_id, name: data.username, role: data.role }
  guardedSet(K_TOKEN, store.token)
  guardedSet(K_USER, JSON.stringify(store.user))
  store.contacts = null
  store.nameCache = {}
  connectWs()
  broadcast('auth')
}

function doLogout() {
  closeWs()
  store.token = null
  store.user = null
  store.contacts = null
  store.active = null
  store.messages = null
  store.nameCache = {}
  guardedSet(K_TOKEN, '')
  guardedSet(K_USER, '')
  broadcast('auth')
}

// ── tiny React helpers (no JSX in this bundle) ────────────────────────────

function h(type, props, ...children) {
  return React.createElement(type, props, ...children)
}

const style = {
  board: { position: 'fixed', inset: 0, zIndex: 30, display: 'flex', background: 'var(--dsw-alias-bg-base)', fontFamily: 'var(--ds-font-family, system-ui, sans-serif)', color: 'var(--dsw-alias-label-primary)' },
  // 侧边栏 rail：照搬官方 SidebarRoot 容器（--dsh-sidebar-inline-padding:12px、6px 12px 内边距、sidebar-fill 背景、14px 字号）。
  rail: { width: 264, flex: 'none', borderRight: '1px solid var(--dsw-alias-border-l2)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--dsw-specific-sidebar-fill)', padding: '6px 12px', boxSizing: 'border-box', fontSize: 14, color: 'var(--dsw-alias-label-primary)' },
  // 板块头：照搬官方 WorkspaceBrowser sectionHeader（36px 高、tertiary 色、12px 圆角、左内边距 4px）。
  railHead: { boxSizing: 'border-box', height: 36, color: 'var(--dsw-alias-label-tertiary)', borderRadius: 12, flex: 'none', alignItems: 'center', gap: 4, marginBottom: 4, paddingLeft: 4, display: 'flex', overflow: 'hidden' },
  // 板块头标题：照搬官方 sectionLabel（nowrap、max-width 45%、20px 行高）。
  railLabel: { whiteSpace: 'nowrap', minWidth: 0, maxWidth: '45%', flex: 'none', lineHeight: '20px', overflow: 'hidden' },
  railUser: { display: 'flex', alignItems: 'center', gap: 8, padding: '8px 4px', borderTop: '1px solid var(--dsw-alias-border-l2)' },
  group: { padding: '2px 0' },
  groupLabel: { padding: '8px 12px 4px', fontSize: 12, color: 'var(--dsw-alias-label-tertiary)', fontWeight: 600, letterSpacing: '.02em' },
  row: { display: 'flex', alignItems: 'center', gap: 8, padding: '7px 4px', cursor: 'pointer', fontSize: 13, color: 'var(--dsw-alias-label-primary)', border: 'none', background: 'transparent', width: '100%', textAlign: 'left', boxSizing: 'border-box' },
  rowHover: { background: 'var(--dsw-alias-interactive-bg-hover)' },
  rowActive: { background: 'var(--dsw-alias-interactive-bg-hover-solid, var(--dsw-alias-interactive-bg-hover))' },
  avatar: { width: 28, height: 28, borderRadius: '50%', flex: 'none', background: 'var(--dsw-alias-state-business-tertiary)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 600, color: 'var(--dsw-alias-label-primary)', overflow: 'hidden' },
  rowText: { minWidth: 0, flex: 1 },
  rowTitle: { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  rowSub: { fontSize: 11, color: 'var(--dsw-alias-label-tertiary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  badge: { flex: 'none', minWidth: 16, height: 16, borderRadius: 8, background: 'var(--dsw-alias-state-danger-primary, #e5484d)', color: '#fff', fontSize: 10, lineHeight: '16px', textAlign: 'center', padding: '0 5px', boxSizing: 'border-box' },
  main: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 },
  mainHead: { padding: '12px 20px', borderBottom: '1px solid var(--dsw-alias-border-l2)', fontSize: 14, fontWeight: 600, color: 'var(--dsw-alias-label-primary)', flex: 'none', display: 'flex', alignItems: 'center', gap: 8 },
  msgs: { flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 14 },
  msgRow: { display: 'flex', flexDirection: 'row', alignItems: 'flex-start', gap: 8, width: '100%' },
  msgMine: { justifyContent: 'flex-end' },
  msgOther: { justifyContent: 'flex-start' },
  msgCol: { display: 'flex', flexDirection: 'column', minWidth: 0, alignItems: 'flex-start' },
  msgColMine: { alignItems: 'flex-end' },
  // 我方消息：完全照搬 DSH 用户气泡（gdEzaW_bubble）——专用气泡 token +
  // label-primary 文字 + 22px 圆角 + 16/24 排版，随主题/风格插件联动。
  msgMineBubble: { background: 'var(--dsw-specific-bubble)', color: 'var(--dsw-alias-label-primary)', maxWidth: 'min(525px, 100%)', borderRadius: 22, padding: '10px 16px', fontSize: 16, lineHeight: '24px', wordBreak: 'break-word' },
  // 对方消息：DSH AI 消息同款——无气泡，MarkdownText 原生排版。
  msgOtherBubble: { color: 'var(--dsw-alias-label-primary)', maxWidth: 'min(720px, 100%)', fontSize: 15, lineHeight: '24px', wordBreak: 'break-word' },
  msgMeta: { fontSize: 11, color: 'var(--dsw-alias-label-tertiary)', marginBottom: 3, padding: '0 4px' },
  msgImages: { display: 'flex', flexDirection: 'row', flexWrap: 'wrap', gap: 6, maxWidth: 'min(525px, 100%)' },
  msgImage: { maxWidth: 240, maxHeight: 240, borderRadius: 12, objectFit: 'cover', cursor: 'zoom-in' },
  inviteCard: { display: 'flex', flexDirection: 'column', gap: 8, minWidth: 260, maxWidth: 320, borderRadius: 14, border: '1px solid var(--dsw-alias-border-l2)', background: 'var(--dsw-alias-bg-layer-2)', padding: '12px 14px', color: 'var(--dsw-alias-label-primary)' },
  inviteHead: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 600 },
  inviteBody: { fontSize: 13, lineHeight: '20px', color: 'var(--dsw-alias-label-secondary)' },
  inviteActions: { display: 'flex', gap: 8, marginTop: 2 },
  inviteBtn: { flex: 1, height: 30, borderRadius: 8, border: 'none', fontSize: 12, fontWeight: 600, cursor: 'pointer' },
  inviteAccept: { background: 'var(--dsw-alias-state-business-primary)', color: '#fff' },
  inviteReject: { background: 'var(--dsw-alias-interactive-bg-hover)', color: 'var(--dsw-alias-label-primary)' },
  inviteStatus: { fontSize: 12, color: 'var(--dsw-alias-label-tertiary)', paddingTop: 2 },
  headBtn: { flex: 'none', padding: '4px 10px', borderRadius: 8, border: 'none', background: 'transparent', color: 'var(--dsw-alias-label-secondary)', cursor: 'pointer', fontSize: 14, lineHeight: 1 },
  headBtnActive: { background: 'var(--dsw-alias-interactive-bg-hover)', color: 'var(--dsw-alias-label-primary)' },
  settingsPanel: { flex: 'none', maxHeight: '40%', overflowY: 'auto', borderBottom: '1px solid var(--dsw-alias-border-l2)', background: 'var(--dsw-alias-bg-layer-2)', padding: '12px 16px', fontSize: 13, color: 'var(--dsw-alias-label-primary)', display: 'flex', flexDirection: 'column', gap: 10 },
  settingsRow: { display: 'flex', alignItems: 'center', gap: 8 },
  settingsLabel: { flex: 1, minWidth: 0, color: 'var(--dsw-alias-label-secondary)' },
  settingsTitle: { fontSize: 13, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' },
  settingsHint: { fontSize: 12, color: 'var(--dsw-alias-label-tertiary)', lineHeight: 1.5 },
  memberRow: { display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' },
  memberName: { flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 13 },
  memberRole: { flex: 'none', fontSize: 11, color: 'var(--dsw-alias-label-tertiary)' },
  smallBtn: { flex: 'none', padding: '4px 12px', borderRadius: 8, border: '1px solid var(--dsw-alias-border-l2)', background: 'transparent', color: 'var(--dsw-alias-label-primary)', cursor: 'pointer', fontSize: 12, fontWeight: 500 },
  smallBtnOn: { background: 'var(--dsw-alias-interactive-bg-hover)', borderColor: 'transparent' },
  // 沉浸式覆盖层：zIndex 必须高于 board（30），否则在 Copree board 打开时
  // 会被 board 盖住（两者同在 shell.overlay 槽内，board fixed z30 > 本层 z5）。
  immersive: { position: 'fixed', inset: 0, zIndex: 40, display: 'flex', flexDirection: 'column', background: 'var(--dsw-alias-bg-base)' },
  immersiveBar: { flex: 'none', display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', borderBottom: '1px solid var(--dsw-alias-border-l2)', fontSize: 13, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' },
  immersiveFrame: { flex: 1, minHeight: 0, border: 'none', width: '100%', background: 'var(--dsw-alias-bg-base)' },
  composer: { flex: 'none', display: 'flex', gap: 8, padding: '12px 20px', borderTop: '1px solid var(--dsw-alias-border-l2)', background: 'var(--dsw-alias-bg-base)' },
  input: { flex: 1, minHeight: 38, borderRadius: 12, border: '1px solid var(--dsw-alias-border-l2)', background: 'var(--dsw-specific-input-major, var(--dsw-alias-bg-base))', color: 'var(--dsw-alias-label-primary)', padding: '8px 14px', fontSize: 13, outline: 'none', fontFamily: 'inherit' },
  send: { flex: 'none', padding: '0 18px', height: 38, borderRadius: 12, border: 'none', background: 'var(--dsw-alias-state-business-primary)', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer' },
  empty: { flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--dsw-alias-label-tertiary)', fontSize: 13 },
  login: { flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 0 },
  loginCard: { width: 320, padding: '24px 24px 20px', borderRadius: 14, border: '1px solid var(--dsw-alias-border-l2)', background: 'var(--dsw-alias-bg-base)', boxShadow: 'var(--dsw-shadow-lv2)' },
  loginTitle: { fontSize: 16, fontWeight: 600, marginBottom: 16, color: 'var(--dsw-alias-label-primary)' },
  field: { width: '100%', marginBottom: 10, padding: '8px 12px', borderRadius: 8, border: '1px solid var(--dsw-alias-border-l2)', background: 'var(--dsw-specific-input-major, var(--dsw-alias-bg-base))', color: 'var(--dsw-alias-label-primary)', fontSize: 13, boxSizing: 'border-box', outline: 'none', fontFamily: 'inherit' },
  btn: { width: '100%', padding: '8px 0', borderRadius: 8, border: 'none', background: 'var(--dsw-alias-state-business-primary)', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer' },
  err: { color: 'var(--dsw-alias-state-error-primary)', fontSize: 12, margin: '6px 0 0' },
  hint: { color: 'var(--dsw-alias-label-tertiary)', fontSize: 12, marginTop: 10, lineHeight: 1.5 },
  footBtn: { display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', borderRadius: 8, border: 'none', background: 'transparent', color: 'var(--dsw-alias-label-primary)', cursor: 'pointer', fontSize: 13, width: '100%' },
  // 侧边栏底部动作按钮：照搬官方设置 trigger（VOzbGW_trigger）——宽 calc(100%+8px)、
  // margin 4px -4px、高 34、圆角 12、14px/22px 排版，保证与下方"设置"按钮完全对齐。
  footTrigger: { boxSizing: 'border-box', cursor: 'pointer', width: 'calc(100% + 8px)', height: 34, color: 'var(--dsw-alias-label-primary)', background: 'transparent', border: 'none', borderRadius: 12, flex: 'none', display: 'flex', alignItems: 'center', gap: 8, margin: '4px -4px', padding: '6px 2px 6px 10px', fontFamily: 'inherit', fontSize: 14, lineHeight: '22px', overflow: 'hidden' },
  // rail（侧边栏收起）变体：官方 trigger rail——36px 圆形、仅图标。
  footTriggerRail: { borderRadius: '50%', justifyContent: 'center', gap: 0, width: 36, height: 36, margin: '8px 0 10px', padding: 0 },
  // 板块头/面板小按钮：官方 iconButton 观感——无边框、hover 圆角背景。
  closeBtn: { flex: 'none', height: 28, padding: '0 10px', borderRadius: 14, border: 'none', background: 'transparent', color: 'var(--dsw-alias-label-secondary)', cursor: 'pointer', fontSize: 12, lineHeight: 1, display: 'inline-flex', alignItems: 'center' },
  scroll: { flex: 1, minHeight: 0, overflowY: 'auto' },
}

function initials(name) {
  if (!name) return '?'
  const parts = String(name).trim().split(/\s+/)
  return (parts[0][0] || '?').toUpperCase()
}

/** Format `"2026-08-11 10:24:36.320007"` backend timestamps. */
function fmtTime(raw) {
  if (!raw) return ''
  const m = String(raw).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/)
  if (!m) return ''
  const [, , month, day, hh, mm] = m
  const now = new Date()
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  const datePart = `${m[1]}-${month}-${day}`
  const hm = `${hh}:${mm}`
  return datePart === today ? hm : `${month}-${day} ${hm}`
}

// ── components ────────────────────────────────────────────────────────────

function LoginForm() {
  const [id, setId] = useState('')
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const submit = async () => {
    setBusy(true)
    setErr(null)
    try {
      await doLogin(id, pw)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }
  return h('div', { style: style.login },
    h('div', { style: style.loginCard },
      h('div', { style: style.loginTitle }, 'Copree 登录'),
      h('input', { style: style.field, placeholder: '用户名 / 邮箱', value: id, onChange: (e) => setId(e.target.value), onKeyDown: (e) => { if (e.key === 'Enter') submit() } }),
      h('input', { style: style.field, placeholder: '密码', type: 'password', value: pw, onChange: (e) => setPw(e.target.value), onKeyDown: (e) => { if (e.key === 'Enter') submit() } }),
      h('button', { style: style.btn, onClick: submit, disabled: busy }, busy ? '登录中…' : '登录'),
      err ? h('div', { style: style.err }, err) : null,
      h('div', { style: style.hint }, '凭据仅保存在本机浏览器，通过本地同源代理访问 Copree 服务。'),
    ),
  )
}

function ContactRow({ contact, kind, active, onPick }) {
  const title = kind === 'group' ? contact.name : (contact.partner && contact.partner.name) || '私信'
  const sub = contact.last_message_preview || (contact.last_message_at ? '' : '')
  const unread = Number(contact.unread_count || 0)
  const avatarText = kind === 'group'
    ? initials(title)
    : initials(contact.partner && contact.partner.name)
  const avatarSrc = kind === 'group'
    ? avatarUrl(contact.avatar_url)
    : avatarUrl(contact.partner && contact.partner.avatar_url)
  const isActive = active && active.kind === kind && String(active.id) === String(kind === 'group' ? contact.id : contact.session_id)
  return h('button', {
    style: { ...style.row, ...(isActive ? style.rowActive : {}) },
    onMouseEnter: (e) => { if (!isActive) e.currentTarget.style.background = style.rowHover.background },
    onMouseLeave: (e) => { if (!isActive) e.currentTarget.style.background = 'transparent' },
    onClick: () => onPick(kind, kind === 'group' ? contact.id : contact.session_id, title),
  },
    avatarSrc
      ? h('img', { src: avatarSrc, style: { ...style.avatar, objectFit: 'cover' }, alt: '' })
      : h('span', { style: style.avatar }, avatarText),
    h('span', { style: style.rowText },
      h('div', { style: style.rowTitle }, title),
      sub ? h('div', { style: style.rowSub }, sub) : null,
    ),
    unread > 0 ? h('span', { style: style.badge }, unread > 99 ? '99+' : String(unread)) : null,
  )
}

/**
 * 提取消息中的图片附件（attachments 数组里 mime_type 为 image/* 的项）。
 * 下载走后端 /fs/download/{file_id}（通过同源代理），与 DSH 附件一致。
 */
function imageAttachments(m) {
  const atts = m && m.attachments
  if (!Array.isArray(atts)) return []
  return atts.filter((a) => a && (a.mime_type || '').startsWith('image/') && a.file_id != null)
}

/** 群聊邀请附件（message_type === 'group_invitation' 的卡片数据）。 */
function invitationAttachment(m) {
  if (!m || String(m.message_type) !== 'group_invitation') return null
  const atts = m.attachments
  if (!Array.isArray(atts)) return null
  return atts.find((a) => a && a.type === 'group_invitation') || null
}

/** 接受/拒绝群聊邀请（POST /group-invitations/{id}/accept|reject）。 */
async function respondInvitation(inv, accept) {
  if (!inv || !inv.invitation_id) return
  try {
    await api(`/group-invitations/${encodeURIComponent(inv.invitation_id)}/${accept ? 'accept' : 'reject'}`, { method: 'POST' })
    broadcast('message')
  } catch (e) {
    window.dispatchEvent(new CustomEvent('copree:error:' + (e.message || '操作失败')))
  }
}

const INVITE_STATUS_LABEL = { pending: '待处理', accepted: '已接受', rejected: '已拒绝' }

/** 群聊邀请卡片（自定义样式：独立卡片 + 接受/拒绝按钮 + 状态）。 */
function InviteCard({ inv }) {
  const label = INVITE_STATUS_LABEL[inv.status] || inv.status || ''
  const pending = inv.status === 'pending'
  return h('div', { style: style.inviteCard },
    h('div', { style: style.inviteHead },
      h('span', { style: { fontSize: 16 } }, '📨'),
      h('span', {}, '群聊邀请'),
    ),
    h('div', { style: style.inviteBody },
      (inv.inviter_name || '有人') + ' 邀请你加入群聊「' + (inv.group_name || '未知群组') + '」',
    ),
    pending
      ? h('div', { style: style.inviteActions },
          h('button', { style: { ...style.inviteBtn, ...style.inviteAccept }, onClick: () => respondInvitation(inv, true) }, '接受'),
          h('button', { style: { ...style.inviteBtn, ...style.inviteReject }, onClick: () => respondInvitation(inv, false) }, '拒绝'),
        )
      : h('div', { style: style.inviteStatus }, label),
  )
}

/** 群聊设置面板：成员列表、置顶、免打扰。 */
function GroupSettings({ active }) {
  const [members, setMembers] = useState(null)
  const [info, setInfo] = useState(null)
  const [, force] = useState(0)
  const refresh = () => force((n) => n + 1)

  useEffect(() => {
    let alive = true
    setMembers(null)
    api(`/groups/${encodeURIComponent(active.id)}`).then((g) => { if (alive) setInfo(g) }).catch(() => {})
    api(`/groups/${encodeURIComponent(active.id)}/members`).then((m) => { if (alive) setMembers(m) }).catch(() => {})
    return () => { alive = false }
  }, [active.id])

  const togglePin = async () => {
    try {
      await api(`/groups/${encodeURIComponent(active.id)}/pin`, { method: 'POST' })
      store.contacts = null
      loadContacts(true).catch(() => {})
      const g = await api(`/groups/${encodeURIComponent(active.id)}`)
      setInfo(g)
    } catch (e) { window.dispatchEvent(new CustomEvent('copree:error:' + (e.message || '操作失败'))) }
  }
  const toggleDnd = async () => {
    try {
      if (info && info.dnd_until) {
        await api(`/groups/${encodeURIComponent(active.id)}/dnd/cancel`, { method: 'POST' })
      } else {
        await api(`/groups/${encodeURIComponent(active.id)}/dnd`, { method: 'POST', json: { duration_minutes: null } })
      }
      const g = await api(`/groups/${encodeURIComponent(active.id)}`)
      setInfo(g)
    } catch (e) { window.dispatchEvent(new CustomEvent('copree:error:' + (e.message || '操作失败'))) }
  }

  const pinned = !!(info && info.is_pinned)
  const inDnd = !!(info && info.dnd_until)
  const roleLabel = (r) => (r === 'owner' ? '群主' : r === 'admin' ? '管理员' : '成员')

  return h('div', { style: style.settingsPanel },
    h('div', { style: style.settingsTitle }, '群聊设置'),
    info ? h('div', { style: style.settingsRow },
      h('span', { style: style.settingsLabel }, '置顶群聊'),
      h('button', { style: { ...style.smallBtn, ...(pinned ? style.smallBtnOn : {}) }, onClick: togglePin }, pinned ? '已置顶' : '置顶'),
    ) : null,
    info ? h('div', { style: style.settingsRow },
      h('span', { style: style.settingsLabel }, '免打扰'),
      h('button', { style: { ...style.smallBtn, ...(inDnd ? style.smallBtnOn : {}) }, onClick: toggleDnd }, inDnd ? '已开启' : '开启'),
    ) : null,
    info && info.announcement ? h('div', { style: { ...style.settingsHint, background: 'var(--dsw-alias-bg-base)', borderRadius: 8, padding: '8px 10px' } }, '公告：' + info.announcement) : null,
    h('div', { style: { ...style.settingsTitle, marginTop: 4 } }, '成员（' + (members ? members.length : '…') + '）'),
    members
      ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: 2 } },
          members.map((m) => h('div', { key: String(m.type) + ':' + String(m.id), style: style.memberRow },
            h('span', { style: style.memberName }, m.name),
            h('span', { style: style.memberRole }, roleLabel(m.role)),
          )))
      : h('div', { style: style.settingsHint }, '加载中…'),
  )
}

/** 私信设置面板：置顶、免打扰。 */
function DmSettings({ active }) {
  const [, force] = useState(0)
  const refresh = () => force((n) => n + 1)
  const [info, setInfo] = useState(null)

  useEffect(() => {
    let alive = true
    setInfo(null)
    api(`/dm/${encodeURIComponent(active.id)}?summary=true`).then((d) => { if (alive) setInfo(d) }).catch(() => {})
    return () => { alive = false }
  }, [active.id])

  const togglePin = async () => {
    try {
      await api(`/dm/${encodeURIComponent(active.id)}/pin`, { method: 'POST' })
      store.contacts = null
      loadContacts(true).catch(() => {})
      const d = await api(`/dm/${encodeURIComponent(active.id)}?summary=true`)
      setInfo(d)
    } catch (e) { window.dispatchEvent(new CustomEvent('copree:error:' + (e.message || '操作失败'))) }
  }
  const toggleDnd = async () => {
    try {
      const inDnd = !!(info && info.my_dnd_until)
      await api(`/dm/${encodeURIComponent(active.id)}/dnd`, { method: 'POST', json: { duration_minutes: inDnd ? 0 : null } })
      const d = await api(`/dm/${encodeURIComponent(active.id)}?summary=true`)
      setInfo(d)
    } catch (e) { window.dispatchEvent(new CustomEvent('copree:error:' + (e.message || '操作失败'))) }
  }

  const pinned = !!(info && info.is_pinned)
  const inDnd = !!(info && info.my_dnd_until)

  return h('div', { style: style.settingsPanel },
    h('div', { style: style.settingsTitle }, '私信设置'),
    h('div', { style: style.settingsRow },
      h('span', { style: style.settingsLabel }, '置顶对话'),
      h('button', { style: { ...style.smallBtn, ...(pinned ? style.smallBtnOn : {}) }, onClick: togglePin }, pinned ? '已置顶' : '置顶'),
    ),
    h('div', { style: style.settingsRow },
      h('span', { style: style.settingsLabel }, '免打扰'),
      h('button', { style: { ...style.smallBtn, ...(inDnd ? style.smallBtnOn : {}) }, onClick: toggleDnd }, inDnd ? '已开启' : '开启'),
    ),
  )
}

/**
 * 单条消息的渲染兜底。
 *
 * 正文走外壳的 MarkdownText，它的渲染异常会一路冒到 shell.overlay 的错误边界，
 * 结果是整块 Copree 面板被摘掉（而不是只有这一条消息显示不出来）。
 * 这里按条兜住：失败就降级成纯文本，面板其余部分照常可用。
 */
class MessageBoundary extends React.Component {
  constructor(props) { super(props); this.state = { failed: false } }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(error) { console.warn('[copree] 消息渲染失败，已降级为纯文本', error) }
  render() {
    if (this.state.failed) {
      return h('div', { style: { ...style.msgOtherBubble, whiteSpace: 'pre-wrap' } }, String(this.props.text || ''))
    }
    return this.props.children
  }
}

function MsgList({ messages, user }) {
  const listRef = useRef(null)
  // 对话默认到底：消息列表变化（切换对话/新消息/加载历史）时滚到底部
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])
  if (!messages || messages.length === 0) {
    return h('div', { style: style.empty }, '暂无消息，发送第一条吧')
  }
  return h('div', { ref: listRef, style: style.msgs },
    messages.map((m) => {
      const mine = String(m.sender_type) === 'user' || String(m.sender_type) === 'human'
        ? String(m.sender_id) === String(user ? user.id : '')
        : false
      const name = senderName(m, user)
      const avatarSrc = mine ? null : avatarUrl(m.sender_avatar_url)
      const images = imageAttachments(m)
      const inv = invitationAttachment(m)
      const hasText = String(m.content || '').trim() !== ''

      // 内容主体：邀请卡片 > 图片 > 文本
      let body = null
      if (inv) {
        body = h(InviteCard, { inv })
      } else {
        if (hasText) {
          body = h('div', { style: mine ? style.msgMineBubble : style.msgOtherBubble },
            h(MarkdownText, { text: mdText(m.content), labels: MARKDOWN_LABELS }),
          )
        }
        if (images.length > 0) {
          const gallery = h('div', { style: style.msgImages },
            images.map((img) => h(AttachmentImage, {
              key: String(img.file_id),
              fileId: img.file_id,
              name: img.name || '',
              style: style.msgImage,
            })),
          )
          body = body
            ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: 6, maxWidth: 'min(525px, 100%)' } }, body, gallery)
            : gallery
        }
        if (!body) body = h('div', { style: mine ? style.msgMineBubble : style.msgOtherBubble }, '')
      }

      // 对方消息带头像；我方名称靠右对齐（msgCol alignItems flex-end）
      return h('div', { key: m.id, style: { ...style.msgRow, ...(mine ? style.msgMine : style.msgOther) } },
        !mine && avatarSrc ? h('img', { src: avatarSrc, style: { ...style.avatar, width: 26, height: 26 }, alt: '' }) : null,
        h('div', { style: { ...style.msgCol, ...(mine ? style.msgColMine : {}) } },
          h('div', { style: style.msgMeta }, name + (m.created_at ? ' · ' + fmtTime(m.created_at) : '')),
          h(MessageBoundary, { text: m.content }, body),
        ),
      )
    }),
  )
}

/** The conversation column: message list + composer (sends to Copree). */
function ConversationColumn({ refresh, onImmersive }) {
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [worldId, setWorldId] = useState(null)
  const active = store.active
  const user = store.user
  const messages = store.messages

  // 群聊：查询是否绑定群视界（有绑定才显示"沉浸式界面"入口）
  useEffect(() => {
    let alive = true
    setWorldId(null)
    if (active && active.kind === 'group') {
      api(`/worlds/by-entity?entity_type=group&entity_id=${encodeURIComponent(active.id)}`)
        .then((d) => { if (alive && d && d.world_id != null) setWorldId(d.world_id) })
        .catch(() => {})
    }
    return () => { alive = false }
  }, [active])

  const send = async () => {
    const text = draft.trim()
    if (!text || sending) return
    setSending(true)
    try {
      await sendMessage(text)
      setDraft('')
      if (!store.ws || store.ws.readyState !== WebSocket.OPEN) {
        await loadMessages(active).catch(() => {})
        refresh()
      }
    } catch (e) {
      window.dispatchEvent(new CustomEvent('copree:error:' + (e.message || 'send failed')))
    } finally {
      setSending(false)
    }
  }

  if (!active) {
    return h('div', { style: style.main },
      h('div', { style: style.mainHead }, 'Copree'),
      h('div', { style: style.empty }, '从左侧选择一个对话'),
    )
  }

  return h('div', { style: style.main },
    h('div', { style: style.mainHead },
      h('span', { style: { fontWeight: 600 } }, active.title),
      h('span', { style: { fontSize: 12, color: 'var(--dsw-alias-label-tertiary)' } }, active.kind === 'group' ? '群聊' : '私信'),
      worldId != null
        ? h('button', {
            style: { ...style.headBtn, color: 'var(--dsw-alias-state-business-primary)', marginLeft: 'auto' },
            onClick: () => onImmersive && onImmersive(worldId),
            title: '在沉浸式界面打开',
          }, '沉浸式')
        : null,
      h('button', {
        style: { ...style.headBtn, ...(showSettings ? style.headBtnActive : {}), ...(worldId != null ? {} : { marginLeft: 'auto' }) },
        onClick: () => setShowSettings((v) => !v),
        title: active.kind === 'group' ? '群聊设置' : '私信设置',
      }, '⚙'),
    ),
    showSettings
      ? (active.kind === 'group'
          ? h(GroupSettings, { active })
          : h(DmSettings, { active }))
      : null,
    h(MsgList, { messages, user }),
    h('div', { style: style.composer },
      h('input', {
        style: style.input,
        placeholder: '输入消息，回车发送',
        value: draft,
        onChange: (e) => setDraft(e.target.value),
        onKeyDown: (e) => { if (e.key === 'Enter') send() },
      }),
      h('button', { style: style.send, onClick: send, disabled: sending }, sending ? '…' : '发送'),
    ),
  )
}

/**
 * 沉浸式界面：iframe 内嵌前端页面（同源托管 /copree-ui/...）。
 * path 为完整 iframe src（含 ?embed=1）；设置页功能导航与群聊沉浸式共用。
 */
function ImmersivePanel({ path, title, onClose }) {
  return h('div', { style: style.immersive },
    h('div', { style: style.immersiveBar },
      h('span', { style: { flex: 1 } }, title || '沉浸式界面'),
      h('button', { style: style.closeBtn, onClick: onClose }, '返回'),
    ),
    h('iframe', { src: path, style: style.immersiveFrame, title: title || '沉浸式界面', sandbox: 'allow-scripts allow-same-origin allow-forms allow-popups' }),
  )
}

/** 模块级沉浸式状态：board 与设置页都能打开/关闭同一个覆盖层。 */
const immersiveState = { path: null, title: '' }

/** 打开沉浸式 iframe：附加当前 token（前端 embed 桥注入复用登录态）。 */
function openImmersive(path, title) {
  const sep = path.includes('?') ? '&' : '?'
  immersiveState.path = path + sep + 'token=' + encodeURIComponent(store.token || '')
  immersiveState.title = title || ''
  window.dispatchEvent(new CustomEvent('copree:immersive'))
}

function closeImmersive() {
  immersiveState.path = null
  immersiveState.title = ''
  window.dispatchEvent(new CustomEvent('copree:immersive'))
}

/** 全局沉浸式覆盖层入口：监听 immersive 广播，有路径时渲染面板。 */
function ImmersiveOverlay() {
  const [, force] = useState(0)
  useEffect(() => {
    const on = () => force((n) => n + 1)
    window.addEventListener('copree:immersive', on)
    return () => window.removeEventListener('copree:immersive', on)
  }, [])
  if (!immersiveState.path) return null
  return h(ImmersivePanel, { path: immersiveState.path, title: immersiveState.title, onClose: closeImmersive })
}

/** AIC 功能导航：点击在沉浸式覆盖层打开前端对应页面（同源 iframe）。 */
const FEATURES = [
  { id: 'worlds', label: '群视界', path: '/worlds' },
  { id: 'friends', label: '好友', path: '/friends' },
  { id: 'agents', label: '我的 AI', path: '/agents' },
  { id: 'admin', label: '管理', path: '/admin' },
  { id: 'settings', label: '设置', path: '/settings' },
]

/** The Copree board: covers the whole frame like the Workspace board. */
function AisChatBoard({ onClose }) {
  const [, force] = useState(0)
  const refresh = useCallback(() => force((n) => n + 1), [])
  const user = store.user

  useEffect(() => {
    const onMsg = () => refresh()
    window.addEventListener('copree:message', onMsg)
    window.addEventListener('copree:auth', onMsg)
    window.addEventListener('copree:error', (e) => { console.warn('[copree]', e.detail) })
    return () => {
      window.removeEventListener('copree:message', onMsg)
      window.removeEventListener('copree:auth', onMsg)
    }
  }, [refresh])

  useEffect(() => {
    if (store.token) {
      loadContacts().then(() => refresh()).catch(() => {})
      connectWs()
    }
    return () => { /* WS stays alive across board closes */ }
  }, [refresh])

  const pick = async (kind, id, title) => {
    store.active = { kind, id, title }
    store.messages = null
    refresh()
    try {
      const list = await loadMessages(store.active)
      store.messages = list
      refresh()
    } catch { /* keep null; input still usable via WS */ }
    connectWs()
  }

  if (!user || !store.token) {
    return h('div', { style: style.board },
      h('div', { style: style.rail },
        h('div', { style: style.railHead },
          h('span', {}, 'Copree'),
          h('button', { style: style.closeBtn, onClick: onClose }, '返回工作区'),
        ),
      ),
      h(LoginForm, null),
    )
  }

  const contacts = store.contacts
  const pinnedGroups = (contacts && contacts.groups || []).filter((g) => g.is_pinned)
  const pinnedDms = (contacts && contacts.dms || []).filter((d) => d.is_pinned)
  const restGroups = (contacts && contacts.groups || []).filter((g) => !g.is_pinned)
  const restDms = (contacts && contacts.dms || []).filter((d) => !d.is_pinned)

  const section = (label, items, kind) => items.length
    ? h('div', { style: style.group },
        h('div', { style: style.groupLabel }, label),
        items.map((it) => h(ContactRow, { key: kind === 'group' ? String(it.id) : String(it.session_id), contact: it, kind, active: store.active, onPick: pick })))
    : null

  return h('div', { style: style.board },
    h('div', { style: style.rail },
      h('div', { style: style.railHead },
        h('span', { style: style.railLabel }, 'Copree'),
        h('button', { style: { ...style.closeBtn, marginLeft: 'auto', fontSize: 12 }, onClick: onClose }, '返回工作区'),
      ),
      h('div', { style: style.railUser },
        h('span', { style: style.avatar }, initials(user.name)),
        h('span', { style: { flex: 1, fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' } }, user.name),
        h('button', { style: { ...style.closeBtn, fontSize: 11 }, onClick: doLogout, title: '退出登录' }, '退出'),
      ),
      h('div', { style: style.scroll },
        section('置顶私信', pinnedDms, 'dm'),
        section('置顶群聊', pinnedGroups, 'group'),
        section('私信', restDms, 'dm'),
        section('群聊', restGroups, 'group'),
        (!pinnedDms.length && !pinnedGroups.length && !restDms.length && !restGroups.length)
          ? h('div', { style: { ...style.empty, padding: 24 } }, '暂无联系人')
          : null,
        h('div', { style: { ...style.group, marginTop: 10, borderTop: '1px solid var(--dsw-alias-border-l2)', paddingTop: 8 } },
          h('div', { style: style.groupLabel }, '功能'),
          FEATURES.map((f) => h('button', {
            key: f.id,
            style: { ...style.row, fontSize: 13 },
            onClick: () => openImmersive(`/copree-ui${f.path}?embed=1`, f.label),
          }, f.label)),
        ),
      ),
    ),
    h(ConversationColumn, {
      refresh,
      onImmersive: (wid) => openImmersive(`/copree-ui/world-view/${encodeURIComponent(wid)}?embed=1`, '沉浸式界面'),
    }),
  )
}

/**
 * 桥接闸门：**只有在这里点同意，Copree 才连得进来**。
 *
 * 为什么放在登录之前：闸门管的是「本机允不允许 Copree 接入」，与「我在 Copree 有没有账号」
 * 是两件事。放在登录判断之后，人就必须先登录 Copree 才能决定要不要让 Copree 进来。
 * 同意前：host 半不发心跳、不注册任何桥接路由——Copree 连「检测到有台 DSH」都做不到。
 */
/**
 * 读一次闸门状态。宿主半侧还没有这个端点时（插件刚更新、dsh-web 还没重启）会拿到 404/空响应，
 * 这时必须给一句人话：直接 res.json() 抛的是 "Unexpected end of JSON input"，人只会以为插件坏了。
 */
async function readConsentState() {
  const res = await fetch(CONSENT_API, { cache: 'no-store' })
  const text = await res.text()
  if (!res.ok || !text.trim()) throw new Error('HOST_STALE')
  const data = JSON.parse(text)
  return data.allowed === true
}

const CONSENT_STALE_HINT = '宿主半侧还没加载这个端点（插件刚更新过）：重启 dsh-web 后刷新本页即可'

function BridgeConsent() {
  const [allowed, setAllowed] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => {
    let alive = true
    readConsentState()
      .then((value) => { if (alive) { setAllowed(value); setErr('') } })
      .catch((e) => { if (alive) { setAllowed(null); setErr(e.message === 'HOST_STALE' ? CONSENT_STALE_HINT : '读取失败：' + e.message) } })
    return () => { alive = false }
  }, [])
  const set = async (next) => {
    setBusy(true); setErr('')
    try {
      const res = await fetch(CONSENT_API, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ allowed: next }),
      })
      const text = await res.text()
      let data = null
      try { data = text ? JSON.parse(text) : null } catch { data = null }
      if (!res.ok || !data || typeof data.allowed !== 'boolean') {
        throw new Error(data && data.error ? data.error : (res.ok ? 'HOST_STALE' : String(res.status)))
      }
      setAllowed(data.allowed === true)
    } catch (e) {
      setErr(e.message === 'HOST_STALE' ? CONSENT_STALE_HINT : '切换失败：' + e.message)
    } finally {
      setBusy(false)
    }
  }
  const state = allowed === null ? '状态未知' : allowed ? '已同意接入' : '未同意接入'
  return h('div', { style: { border: '1px solid var(--dsw-alias-border-l2, #e4e4e7)', borderRadius: 10, padding: 12, marginBottom: 18 } },
    h('div', { style: { fontSize: 13, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' } }, 'Copree 反向接入'),
    h('div', { style: { fontSize: 12, marginTop: 4, color: 'var(--dsw-alias-label-secondary)' } },
      '同意后，Copree 管理端才能看到本机 DSH 并按管理员指令操作会话；未同意时本机不发心跳、不开放任何桥接端点。当前：' + state),
    h('div', { style: { display: 'flex', gap: 8, marginTop: 10 } },
      h('button', {
        style: { ...style.smallBtn, opacity: busy || allowed === true ? 0.55 : 1 },
        disabled: busy || allowed === true,
        onClick: () => { void set(true) },
      }, '同意接入'),
      h('button', {
        style: { ...style.smallBtn, opacity: busy || allowed === false ? 0.55 : 1 },
        disabled: busy || allowed === false,
        onClick: () => { void set(false) },
      }, '撤销同意'),
    ),
    err ? h('div', { style: { ...style.hint, marginTop: 6 } }, err) : null,
  )
}

function SettingsPage() {
  const [, force] = useState(0)
  const refresh = useCallback(() => force((n) => n + 1), [])
  const user = store.user
  const [plugin, setPlugin] = useState(null)
  const [pluginMsg, setPluginMsg] = useState('')
  const [pluginBusy, setPluginBusy] = useState(false)

  useEffect(() => {
    window.addEventListener('copree:auth', refresh)
    let alive = true
    pluginApi('/status').then((s) => { if (alive) setPlugin(s) }).catch(() => {})
    return () => {
      alive = false
      window.removeEventListener('copree:auth', refresh)
    }
  }, [refresh])

  const applyPluginUpdate = async () => {
    setPluginBusy(true)
    setPluginMsg('')
    // 本地来源走自己的原子换入；没有市场时由包管理器从原来源更新。
    const viaPackageManager = Boolean(plugin) && plugin.updateChannel === 'package-manager'
    try {
      const result = await pluginApi(viaPackageManager ? '/update' : '/apply', { method: 'POST' })
      if (result.applyMode === 'restart') {
        setPluginMsg(viaPackageManager
          ? '已完成。host 半有变化，需重启 dsh-web 后生效。'
          : `已换入 ${result.changed.length} 个文件。host 半已变更，需重启 dsh-web 后生效。`)
        setPlugin(await pluginApi('/status').catch(() => plugin))
      } else if (viaPackageManager) {
        setPluginMsg('已是最新，或已更新到最新。')
        setPlugin(await pluginApi('/status').catch(() => plugin))
      } else {
        setPluginMsg('已更新，正在刷新页面…')
        setTimeout(() => window.location.reload(), 600)
      }
    } catch (e) {
      setPluginMsg(`更新失败：${e.message}`)
    } finally {
      setPluginBusy(false)
    }
  }

  if (!user || !store.token) {
    return h('div', { style: { padding: 20, maxWidth: 420 } },
      h('div', { style: { fontSize: 16, fontWeight: 600, marginBottom: 16, color: 'var(--dsw-alias-label-primary)' } }, 'Copree'),
      // 闸门放登录之前：允不允许 Copree 接入，与"我在 Copree 有没有账号"是两件事
      h(BridgeConsent, null),
      h('div', { style: { ...style.hint, marginTop: 0 } }, '登录 Copree 后即可在侧边栏使用聊天。凭据仅保存在本机浏览器。'),
      h(LoginForm, null),
    )
  }

  return h('div', { style: { padding: 20, maxWidth: 480 } },
    h('div', { style: { fontSize: 16, fontWeight: 600, marginBottom: 16, color: 'var(--dsw-alias-label-primary)' } }, 'Copree'),
    h(BridgeConsent, null),
    h('div', { style: { ...style.row, padding: '8px 0' } },
      h('span', { style: style.avatar }, initials(user.name)),
      h('div', { style: style.rowText },
        h('div', { style: style.rowTitle }, user.name),
        h('div', { style: style.rowSub }, '已登录'),
      ),
    ),
    h('div', { style: { fontSize: 13, fontWeight: 600, margin: '18px 0 8px', color: 'var(--dsw-alias-label-primary)' } }, '功能'),
    h('div', { style: { display: 'flex', flexDirection: 'column', gap: 2 } },
      FEATURES.map((f) => h('button', {
        key: f.id,
        style: { ...style.footBtn, padding: '9px 10px', fontSize: 14 },
        onClick: () => openImmersive(`/copree-ui${f.path}?embed=1`, f.label),
      }, f.label)),
    ),
    h('div', { style: { fontSize: 13, fontWeight: 600, margin: '18px 0 8px', color: 'var(--dsw-alias-label-primary)' } }, '插件'),
    h('div', { style: { ...style.row, padding: '6px 0' } },
      h('div', { style: style.rowText },
        h('div', { style: style.rowTitle },
          plugin
            ? `dsh-copree ${(plugin.installed && plugin.installed.version) || '未知'} · ${shortId(plugin.installed && plugin.installed.id)}`
            : 'dsh-copree 版本检测中…'),
        h('div', { style: style.rowSub }, pluginStateText(plugin)),
      ),
      pluginCanAct(plugin)
        ? h('button', {
            style: style.smallBtn,
            disabled: pluginBusy,
            onClick: applyPluginUpdate,
          }, pluginBusy
            ? '处理中…'
            : (plugin.updateChannel === 'package-manager' ? '检查更新' : '更新'))
        : null,
    ),
    plugin && plugin.backend && plugin.backend.mismatch
      ? h('div', { style: { ...style.hint, marginTop: 2 } },
          `插件构建时后端为 ${plugin.backend.builtAgainst}，当前为 ${plugin.backend.running}；` +
          '内置界面可能已与后端 API 不一致，建议更新插件。')
      : null,
    pluginMsg ? h('div', { style: { ...style.hint, marginTop: 2 } }, pluginMsg) : null,
    h('button', { style: { ...style.btn, background: 'var(--dsw-alias-state-danger-primary, #e5484d)', marginTop: 20 }, onClick: doLogout }, '退出登录'),
    h('div', { style: style.hint }, '服务通过本机同源代理访问，无公网地址参与。'),
  )
}

function FooterButton({ wide }) {
  const [open, setOpen] = useState(false)
  const [needsUpdate, setNeedsUpdate] = useState(false)
  useEffect(() => {
    let alive = true
    pluginApi('/status')
      .then((s) => { if (alive) setNeedsUpdate(pluginNeedsAttention(s)) })
      .catch(() => { /* 宿主半不可达时不打扰用户 */ })
    return () => { alive = false }
  }, [])
  useEffect(() => {
    const onRefresh = () => setOpen(boardOpenRef.current)
    window.addEventListener('copree:board-refresh', onRefresh)
    return () => window.removeEventListener('copree:board-refresh', onRefresh)
  }, [])
  const toggle = () => {
    const next = !open
    setOpen(next)
    window.dispatchEvent(new CustomEvent('copree:board-toggle', { detail: next }))
  }
  const rail = wide === false
  // 官方 trigger 风格：wide 全宽行（图标+文字），rail 时 36px 圆形仅图标。
  return h('button', {
    style: { ...style.footTrigger, ...(rail ? style.footTriggerRail : {}), ...(open ? { background: 'var(--dsw-alias-interactive-bg-hover-solid, var(--dsw-alias-interactive-bg-hover))' } : {}) },
    onClick: toggle,
    title: 'Copree 聊天',
    'aria-label': rail ? 'Copree' : undefined,
  },
    h(IconNewChatOutline16, { size: rail ? 18 : 16 }),
    rail ? null : h('span', { style: { fontWeight: 500 } }, 'Copree'),
    needsUpdate
      ? h('span', {
          title: 'Copree 插件有更新',
          style: { flex: 'none', width: 7, height: 7, borderRadius: '50%', background: 'var(--dsw-alias-state-danger-primary, #e5484d)' },
        })
      : null,
  )
}

/** Shared board-open flag readable by the footer button's state sync. */
const boardOpenRef = { current: false }

// ── plugin entry ──────────────────────────────────────────────────────────

module.exports = {
  name: 'dsh-copree',
  inject: ['slots', 'workspaces'],
  apply(ctx) {
    let boardOpen = false

    const bump = () => {
      boardOpenRef.current = boardOpen
      window.dispatchEvent(new CustomEvent('copree:board-refresh'))
    }

    /**
     * 把 Copree 世界同步为 DSH 工作区文件夹 + 会话：
     *   世界 → 目录（Copree群视界-世界名 + .copree-world.json）→
     *   ctx.workspaces.create({path}) → connectWorkspace() 得会话 →
     *   上报 {sessionId, token}（host 仅内存保存，供 owner 鉴权写操作）。
     * 全部走官方 API，幂等（重复同步复用同一 workspace/空白会话）。
     * 只同步当前用户自己创建（可编辑）的世界——/worlds 本就只返回 owner。
     */
    let lastWorldsSync = 0
    const syncWorlds = async (force = false) => {
      if (!store.token || !store.user || !ctx.workspaces) return
      const now = Date.now()
      if (!force && now - lastWorldsSync < 30_000) return
      lastWorldsSync = now
      try {
        const worlds = await api('/worlds').catch(() => null)
        if (!Array.isArray(worlds)) return
        for (const w of worlds) {
          if (!w || !w.id) continue
          const dirRes = await fetch('/copree-worlds/dir', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ worldId: w.id, name: w.name || `世界${w.id}` }),
          }).catch(() => null)
          if (!dirRes || !dirRes.ok) continue
          const dir = await dirRes.json().catch(() => null)
          if (!dir || !dir.path) continue
          const ws = await ctx.workspaces.create({ path: dir.path }).catch(() => null)
          // 官方 WorkspaceView 的主键是 workspaceId（不是 id）。
          if (!ws) continue
          const workspaceId = ws.workspaceId || ws.id
          if (!workspaceId) continue
          await ctx.workspaces.connectWorkspace(workspaceId).catch(() => null)
          // token 按 worldId 上报（稳定标识）：host 按世界路由，不依赖
          // sessionId（DSH 新建会话流程会创建新会话，sessionId 不稳定）。
          await fetch('/copree-worlds/token', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ worldId: w.id, token: store.token }),
          }).catch(() => {})
          // 温和自动拉取：host 端带快照/冲突保护——仅当「本地无未推送修改且
          // 世界有改动」时才拉取并报告变化；本地有修改或冲突一律拒绝，绝不覆盖
          // agent 正在工作的文件。对话进行中文件不会被自动改动。
          await fetch('/copree-worlds/pull', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ worldId: w.id }),
          }).catch(() => {})
        }
      } catch { /* 同步失败静默：不影响 Copree 主功能 */ }
    }

    // 登录态变化时自动同步世界到工作区（节流 30s）。
    window.addEventListener('copree:auth', () => {
      if (store.token) syncWorlds()
    })

    // 页面加载后若已恢复登录态（localStorage），立即静默上报一次——用户
    // 直接点进「Copree群视界」工作区即可使用 world 工具，无需先点开 Copree 面板。
    if (store.token && store.user) syncWorlds()

    // 打开 Copree board 时也补一次同步（force 跳过节流，幂等安全）。
    window.addEventListener('copree:board-toggle', (e) => {
      boardOpen = !!e.detail
      if (boardOpen && store.token) syncWorlds(true)
      bump()
    })

    window.addEventListener('copree:error', (e) => {
      console.warn('[copree]', e.detail)
    })

    const disposers = []

    // host 仅内存保存世界 token，dsh-web 重启即丢：监听连接重建
    // （connection/reset，host 重启后页面自动重连会触发）自动补上报，
    // 保证重启后点进世界工作区依然即用。幂等，syncWorlds 自带 30s 节流。
    const onConnectionReset = () => { if (store.token && store.user) syncWorlds() }
    if (typeof ctx.on === 'function') disposers.push(ctx.on('connection/reset', onConnectionReset))

    // 定时兜底：事件丢失等漏网上报时机 60s 内自动补齐（幂等；节流下空转成本≈0）。
    const syncTimer = setInterval(() => { if (store.token && store.user) syncWorlds() }, 60_000)
    disposers.push(() => clearInterval(syncTimer))

    disposers.push(ctx.slots.inject('sidebar.footer.action', () => ctx.slots.register(
      { name: 'sidebar.footer.action', id: 'copree-entry', order: 10, label: 'Copree' },
      FooterButton,
    )))

    // The board: a frame-wide overlay rendered only while the Copree board is
    // open. Registered once; the entry component reads the open flag from the
    // module store and renders null when closed.
    const BoardEntry = () => {
      const [openState, setOpenState] = useState(false)
      const [, force] = useState(0)
      useEffect(() => {
        const onRefresh = () => {
          setOpenState(boardOpen)
          force((n) => n + 1)
        }
        window.addEventListener('copree:board-refresh', onRefresh)
        return () => window.removeEventListener('copree:board-refresh', onRefresh)
      }, [])
      if (!openState) return null
      return h(AisChatBoard, {
        onClose: () => {
          boardOpen = false
          bump()
        },
      })
    }
    disposers.push(ctx.slots.inject('shell.overlay', () => ctx.slots.register(
      { name: 'shell.overlay', id: 'copree-board', order: 30 },
      BoardEntry,
    )))

    // 全局沉浸式覆盖层：群聊"沉浸式"按钮与设置页功能导航共用（iframe 打开后盖住整个 frame）。
    disposers.push(ctx.slots.inject('shell.overlay', () => ctx.slots.register(
      { name: 'shell.overlay', id: 'copree-immersive', order: 40 },
      ImmersiveOverlay,
    )))

    disposers.push(ctx.slots.inject('settings.section', () => ctx.slots.register(
      { name: 'settings.section', id: 'copree', order: 40, label: 'Copree' },
      SettingsPage,
    )))

    // Reconnect on window focus if the WS dropped while hidden.
    window.addEventListener('focus', () => {
      if (store.token && !store.ws) connectWs()
    })

    // 前端 iframe（嵌入模式）登录态失效时通知宿主：打开 Copree board 让用户登录。
    // 监听 copree-embed 消息（source 校验 + 只响应 iframe 子窗口）。
    window.addEventListener('message', (event) => {
      const data = event.data
      if (!data || typeof data !== 'object' || data.source !== 'copree-embed') return
      if (data.type === 'request-login' || data.type === 'unauthorized') {
        if (!boardOpen) {
          boardOpen = true
          bump()
        }
      }
    })

    return () => {
      for (const dispose of disposers.splice(0)) dispose()
      closeWs()
    }
  },
}
