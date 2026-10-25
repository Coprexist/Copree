/**
 * Demo 模式初始化
 * 所有数据存 localStorage + 发消息直调 DeepSeek API
 */

import { initDemoData, getDemoUser, updateDemoUser, getApiKey, setApiKey,
  getGroups, getGroup, getMembers, getAgents,
  getMessages, addMessage, read, write, blobToBase64 } from './demoStorage'

const API = '/api'

let _processing = false

/**
 * 群视界的演示世界：列表页与增删改都只动 localStorage，
 * 不依赖后端。字段与 WorldsPage 的 World 接口一一对应。
 */
const WORLDS_KEY = 'demo_worlds'
const DEMO_WORLDS_SEED = [
  {
    id: 34, name: '枕流镇', status: 'active', time_flow_rate: 1,
    description: '一座靠在河湾上的小镇：雨天、旧书店，还有会说话的猫。天气会跟着群聊里的语气走。',
    bindings: [{ entity_type: 'group', entity_id: 2 }],
  },
  {
    id: 35, name: '灰烬回廊', status: 'sleeping', time_flow_rate: 0.5,
    description: '解谜世界：每一扇门都记得上一次被打开的时间，走错一步就要重新写信给它。',
    bindings: [],
  },
]

const readWorlds = () => read<any[]>(WORLDS_KEY, DEMO_WORLDS_SEED)

export function setupDemo() {
  initDemoData()
  localStorage.setItem('access_token', 'demo_token')

  // ── fetch 拦截 ──
  const original = window.fetch.bind(window)

  async function serve(reqPath: string, init?: RequestInit): Promise<Response | null> {
    const path = reqPath.replace(/\/$/, '')
    const method = (init?.method || 'GET').toUpperCase()

    // 用户认证
    if (path === `${API}/auth/me` || path === `${API}/user/me`) return jsonRes(getDemoUser())
    if (path === `${API}/auth/login` || path === `${API}/auth/register` || path === `${API}/auth/has-users`) {
      return path.endsWith('has-users') ? jsonRes({ has_users: true }) : jsonRes({ access_token: 'demo_token', user_id: 1, username: 'Demo' })
    }

    // 用户设置（提取 api_key 单独存）
    if (path === `${API}/user/settings`) {
      if (method === 'PUT' && init?.body) {
        try {
          const b = JSON.parse(init.body as string)
          if (b.api_key) { setApiKey(b.api_key); delete b.api_key }
          if (b.api_base_url) delete b.api_base_url
          updateDemoUser(b)
        } catch {}
      }
      return jsonRes({ status: 'ok' })
    }

    // 群视界：世界列表与增删改
    if (path === `${API}/worlds`) {
      const list = readWorlds()
      if (method === 'POST') {
        let body: any = {}
        try { body = JSON.parse(String(init?.body || '{}')) } catch {}
        const world = {
          id: Date.now() % 100000,
          name: body.name || '新世界',
          description: body.description || '',
          status: 'active',
          time_flow_rate: body.time_flow_rate ?? 1,
          bindings: [],
        }
        write(WORLDS_KEY, [...list, world])
        return jsonRes(world)
      }
      return jsonRes(list)
    }
    const worldMatch = path.match(/^\/api\/worlds\/(\d+)(?:\/(wake|sleep))?$/)
    if (worldMatch) {
      const id = Number(worldMatch[1])
      const list = readWorlds()
      if (method === 'DELETE') {
        write(WORLDS_KEY, list.filter((w) => w.id !== id))
        return jsonRes({ status: 'ok' })
      }
      if (worldMatch[2]) {
        const next = list.map((w) => w.id === id ? { ...w, status: worldMatch[2] === 'wake' ? 'active' : 'sleeping' } : w)
        write(WORLDS_KEY, next)
        return jsonRes(next.find((w) => w.id === id) || {})
      }
      return jsonRes(list.find((w) => w.id === id) || {})
    }

    // 群组
    if (path === `${API}/groups`) return jsonRes(getGroups())
    const gm = path.match(/^\/api\/groups\/(\d+)$/)
    if (gm) return jsonRes(getGroup(parseInt(gm[1])))

    // 群成员
    const mm = path.match(/^\/api\/groups\/(\d+)\/members$/)
    if (mm) return jsonRes(getMembers(parseInt(mm[1])))

    // 群活动/已读（静默返回）
    if (path.match(/\/groups\/\d+\/(activity|read)$/)) return jsonRes({})

    // 消息列表
    const ms = path.match(/^\/api\/groups\/(\d+)\/messages$/)
    if (ms) return jsonRes(getMessages(parseInt(ms[1])))

    // AI Agent
    if (path === `${API}/agents` || path === `${API}/agents/available`) return jsonRes(getAgents())

    // 系统设置
    if (path === `${API}/system/settings`) return jsonRes({ registration_enabled: false })
    if (path.match(/maintenance-msg/)) return jsonRes({})

    // 头像上传（用户/AI/群聊）→ 存 base64
    if (path.indexOf('/avatar') > 0 && method === 'POST') {
      try {
        const fd = init?.body as FormData
        const file = fd?.get('file') as File
        if (file) {
          const base64 = await blobToBase64(file)
          const segs = path.split('/').filter(Boolean)
          write('demo_avatar_' + segs.slice(1).join('_'), base64)
        }
      } catch {}
      return jsonRes({ avatar_url: path.replace('/api', '/demo/avatar') })
    }
    // 头像下载
    if (path.indexOf('/demo/avatar') === 0 || path.indexOf('/api/fs/download-avatar') === 0) {
      const key = 'demo_avatar_' + path.replace('/demo/avatar/', '').replace('/api/fs/download-avatar/', '')
      const data: string = read(key, '')
      if (data) return new Response(atob(data.split(',')[1] || ''), { status: 200, headers: { 'Content-Type': 'image/jpeg' } })
      return jsonRes({}, 404)
    }
    // 未匹配 API → 控制台警告 + 返回 null（由上层转安全默认值）
    if (path.startsWith(API)) {
      console.warn('[Demo] 未 mock:', method, path)
      return null
    }
    return null
  }

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const path = url.replace(/^https?:\/\/[^\/]+/, '').replace(/\?.*$/, '')
    const r = await serve(path, init)
    if (r) return r
    // API 路径未 mock → 返回安全默认值
    if (path.startsWith(API)) {
      const isList = /(groups|members|messages|dm|session|friends|files|agents|device|worlds)/.test(path)
      return jsonRes(isList ? [] : {})
    }
    // 非 API 路径（懒加载 JS、字体等）→ 走原始 fetch
    return original(input, init)
  }

  // ── WebSocket 拦截 ──
  class DemoWS {
    url: string
    onopen: ((e: Event) => void) | null = null
    onclose: ((e: CloseEvent) => void) | null = null
    onerror: ((e: Event) => void) | null = null
    onmessage: ((e: MessageEvent) => void) | null = null
    readyState = 1
    private gid = 1
    constructor(url: string) {
      this.url = url
      this.gid = parseInt(url.match(/\/(\d+)\b/)?.[1] || '0') || 1
      queueMicrotask(() => this.onopen?.(new Event('open')))
    }
    close() { this.readyState = 3 }
    send(data: string) {
      try {
        const p = JSON.parse(data)
        const gid = p.group_id || this.gid
        const content = p.content || p.text || ''
        if (!content) return
        addMessage(gid, { sender_type: 'human', sender_id: 1, sender_name: 'Demo', content })
        // 监听 demo_msg_update 让 ChatView 刷新
        if (document.visibilityState === 'visible') {
          window.dispatchEvent(new CustomEvent('CHAT_REFRESH_EVENT', { detail: { type: 'message_sent' } }))
        }
        // AI 回复
        const key = getApiKey()
        if (key && !_processing) {
          _processing = true
          fetch('https://api.deepseek.com/v1/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${key}` },
            body: JSON.stringify({ model: 'deepseek-chat', messages: [{ role: 'user', content }] }),
          }).then(r => r.json()).then(d => {
            const reply = d?.choices?.[0]?.message?.content || '（无回复）'
            addMessage(gid, { sender_type: 'agent', sender_id: 2, sender_name: 'AI 助手', content: reply })
            window.dispatchEvent(new CustomEvent('CHAT_REFRESH_EVENT', { detail: { type: 'message_sent' } }))
          }).catch(e => {
            addMessage(gid, { sender_type: 'system', sender_id: 0, sender_name: '系统', content: `❌ API 请求失败: ${e.message}` })
            window.dispatchEvent(new CustomEvent('CHAT_REFRESH_EVENT', { detail: { type: 'message_sent' } }))
          }).finally(() => { _processing = false })
        }
      } catch {}
    }
  }
  window.WebSocket = DemoWS as any

  // ── Logo 修正 ──
  new MutationObserver(() => {
    document.querySelectorAll('img[src="/logo.png"]').forEach(el => {
      if (el instanceof HTMLImageElement) el.src = '/Copree/logo.png'
    })
  }).observe(document.body || document.documentElement, { childList: true, subtree: true })
}

function jsonRes(data: any, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
}
