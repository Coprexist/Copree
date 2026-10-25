/**
 * Demo 本地数据层 —— 所有数据读写 localStorage（作为本地数据库）
 * 增删查改 API 路由映射
 */

const DEMO_USER = {
  id: 1, username: 'Demo', role: 'user', is_active: true,
  ai_quota: 99, api_credit: 9999, platform_gifted_credit: 0,
  total_effective: 9999, agent_bundle_credit: 0, file_quota_mb: 500,
  has_api_key: false, api_key_last4: '',
  timezone: 'Asia/Shanghai', language: 'zh',
  ui_prefs: {}, avatar_url: null, bio: null,
  status_text: '⚡ Demo', status_color: '#f59e0b',
  setup_completed: true, created_at: '2026-01-01',
  assigned_pool_key_name: null, email: null, email_verified: false,
}

const DEMO_GROUPS = [{
  id: 1, name: 'AI 演示群', type: 'group', owner_type: 'system', owner_id: 0,
  is_vector_accelerated: false, is_paused: false, is_pinned: false,
  avatar_url: null, avatar_mode: 'default', include_ai_in_avatar: true,
  member_count: 2, online_count: 1, member_avatars: [],
  unread_count: 0, has_mention: false, my_role: 'member',
  last_message_preview: null, last_message_at: null, dnd_until: null,
  announcement: null, speak_limit_per_minute: 0, speak_limit_window_seconds: 120,
  created_at: new Date().toISOString(),
}]

const DEMO_MEMBERS = [
  { group_id: 1, id: 1, type: 'human', name: 'Demo', role: 'owner', avatar_url: null, state: 'online' },
  { group_id: 1, id: 2, type: 'agent', name: 'AI 助手', role: 'member', avatar_url: null, state: 'online' },
]

const DEMO_AGENTS = [
  {
    id: 2, name: 'AI 助手', avatar_url: null, owner_id: 1, user_id: 0,
    ai_type: 'general', is_active: true, state: 'active',
    status_text: '在线', status_color: 'green',
    original_system_prompt: '你是 Copree 演示版里的 AI 助手，说话简短、直接。',
    current_system_prompt: '你是 Copree 演示版里的 AI 助手，说话简短、直接。',
    original_temperature: 0.7, current_temperature: 0.7,
    original_top_p: 1, current_top_p: 1,
    original_presence_penalty: 0, current_presence_penalty: 0,
    original_frequency_penalty: 0, current_frequency_penalty: 0,
    chat_model: null, work_model: null,
    thinking_enabled: false, hide_ai_identity: false, is_ai_editable: true,
    config_profile: 'chat', delay_reply_enabled: null, max_tool_rounds: 8,
    created_at: '2026-10-01T10:00:00.000Z',
  },
]

const DEMO_WELCOME = {
  id: 1, group_id: 1, sender_type: 'system', sender_id: 0,
  sender_name: '系统', sender_avatar_url: null,
  content: '欢迎来到 Copree 演示版。\n\n⚙️ 进入「设置 → API 配置」填入你的 DeepSeek API Key 即可与 AI 对话。\n💾 所有数据存储在浏览器本地。',
  reply_to: null, attachments: null, source_public_id: null,
  created_at: new Date().toISOString(),
}

// ── 读写辅助 ──

export function read<T>(key: string, fallback: T): T {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback } catch { return fallback }
}
export function write(key: string, val: any) { try { localStorage.setItem(key, JSON.stringify(val)) } catch {} }

export function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.onerror = reject
    r.readAsDataURL(blob)
  })
}

// ── 初始化（首次访问时创建默认数据） ──

export function initDemoData() {
  // 早期版本的演示 AI 缺 state 等字段，会让列表页读状态标签时崩溃：检测到就重置这批种子
  const seeded = read<any[]>('demo_agents', [])
  if (seeded.length && seeded.some((a) => !a || !a.state)) write('demo_agents', DEMO_AGENTS)
  if (!localStorage.getItem('demo_inited')) {
    write('demo_groups', DEMO_GROUPS)
    write('demo_members', { '1': DEMO_MEMBERS })
    write('demo_agents', DEMO_AGENTS)
    write('demo_messages', { '1': [DEMO_WELCOME] })
    write('demo_user', DEMO_USER)
    localStorage.setItem('demo_inited', '1')
  }
}

// ── 用户 ──

export function getDemoUser() {
  const cached = read('demo_user', DEMO_USER)
  const apiKey = read('demo_api_key', '')
  return { ...DEMO_USER, ...cached, has_api_key: !!apiKey, api_key_last4: apiKey.slice(-4) }
}

export function updateDemoUser(data: Record<string, any>) {
  const old = read('demo_user', {})
  write('demo_user', { ...old, ...data })
}

// ── API Key ──

export function getApiKey() { return read('demo_api_key', '') }
export function setApiKey(key: string) {
  write('demo_api_key', key)
  updateDemoUser({ has_api_key: !!key, api_key_last4: key.slice(-4) })
}

// ── 群组 ──

export function getGroups() { return read('demo_groups', DEMO_GROUPS) }
export function getGroup(id: number) { return getGroups().find((g: any) => g.id === id) || DEMO_GROUPS[0] }

// ── 成员 ──

export function getMembers(gid: number) {
  const all = read<Record<number, any[]>>('demo_members', {})
  return all[gid] || DEMO_MEMBERS
}

// ── AI Agent ──

export function getAgents() { return read('demo_agents', DEMO_AGENTS) }

// ── 消息 ──

export function getMessages(gid: number): any[] {
  const all = read<Record<number, any[]>>('demo_messages', { '1': [DEMO_WELCOME] })
  return all[gid] || []
}

let _msgId = Date.now()
export function addMessage(gid: number, msg: any) {
  const all = read<Record<number, any[]>>('demo_messages', {})
  const m = { id: ++_msgId, group_id: gid, created_at: new Date().toISOString(), ...msg }
  all[gid] = [...(all[gid] || []), m]
  write('demo_messages', all)
  // 触发 UI 刷新
  window.dispatchEvent(new CustomEvent('demo_msg_update', { detail: { group_id: gid } }))
  return m
}
