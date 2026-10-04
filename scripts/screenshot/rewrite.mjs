/**
 * 接口改写层：把真实响应换成演示数据。
 *
 * 两条原则：
 *   1. 白名单式替换 —— 按「字段语义」改写，不认识的一律不信任；仓库里不存在
 *      任何真实用户名/邮箱/token 的对照表，所以改名靠哈希映射到虚构人名池。
 *   2. 幂等 —— 已经是演示数据的值不会被再次改写（KNOWN 集合），所以
 *      「先按端点覆盖、再统一兜底清洗」两遍顺序无所谓。
 */
import {
  DEFAULT_AVATAR_URL, DEMO_AGENTS, DEMO_DM_SESSIONS, DEMO_FRIENDS, DEMO_GITHUB_BIND, DEMO_GITHUB_ITEMS,
  DEMO_GROUP_MESSAGES, DEMO_GROUPS, DEMO_ME, DEMO_MEMBERS, DEMO_MARKET_ITEMS, DEMO_SUGGESTIONS,
  DEMO_USER_STATS, DEMO_USER_STORAGE, DEMO_USAGE_OVERVIEW, DEMO_WORLDS, DEMO_WORLD_CHAT,
} from './demo-data.mjs'

/** 虚构人名池：任何未知的人名/昵称都会稳定地映射到这里的一个 */
const NAME_POOL = ['林晚', '柏舟', '青禾', '沈知', '朝雾', '拾光', '阿兰德', '白露', '洛']
const DEMO_TEXT = new Set([
  ...NAME_POOL,
  ...DEMO_GROUPS.map((g) => g.name),
  ...DEMO_WORLDS.map((w) => w.name),
  ...DEMO_AGENTS.map((a) => a.name),
  ...DEMO_MEMBERS.map((m) => m.name),
  ...DEMO_FRIENDS.map((f) => f.friend_name),
  ...DEMO_MARKET_ITEMS.map((i) => i.title),
  ...DEMO_GITHUB_ITEMS.map((i) => i.title),
  'Copree 官方', 'Copree 社区', 'copree-demo',
])

const NAME_KEY = /^(username|sender_name|friend_name|author_name|author_github|nickname|display_name|creator_name|owner_name|name)$/
const PREVIEW_KEY = /^(last_message_preview|preview)$/
const SECRET_KEY = /(token|secret|password|api_key)$/i
const PROMPT_KEY = /(system_prompt|forced_prompt)$/
const EMAIL_RE = /^[\w.+-]+@[\w-]+\.[\w.]+$/

/** 列表里的消息摘要不保留原文，按原值稳定映射到演示句子 */
const PREVIEW_POOL = [
  '黄昏那版配色我调好了，来看看？',
  '明天那格我补上了，读的是天气状态。',
  '镇口的灯明天能装，木料到了。',
  '世界模板推到市场了，可以导入试试。',
  '今晚十点继续，番茄钟走起。',
]

function hashOf(value) {
  let hash = 0
  for (const ch of String(value)) hash = (hash * 31 + ch.codePointAt(0)) >>> 0
  return hash
}

/** 兜底清洗：逐层遍历，按 key 语义替换个人数据 */
function scrub(node, key = '') {
  if (Array.isArray(node)) return node.map((item) => scrub(item))
  if (node && typeof node === 'object') {
    const out = {}
    for (const [k, v] of Object.entries(node)) out[k] = scrub(v, k)
    return out
  }
  if (typeof node !== 'string') return node
  if (DEMO_TEXT.has(node)) return node
  if (EMAIL_RE.test(node)) return DEMO_ME.email
  if (node.includes('/download-avatar/')) return DEFAULT_AVATAR_URL
  if (key === 'email') return DEMO_ME.email
  if (NAME_KEY.test(key)) return NAME_POOL[hashOf(node) % NAME_POOL.length]
  if (PREVIEW_KEY.test(key)) return PREVIEW_POOL[hashOf(node) % PREVIEW_POOL.length]
  if (SECRET_KEY.test(key)) return 'demo'
  if (PROMPT_KEY.test(key)) return '你是这个世界的 AI，负责让世界自己长下去。'
  return node
}

const at = (minutes) => new Date(Date.now() - minutes * 60_000).toISOString()
const route = (pattern, apply) => ({ pattern, apply })

/** 前端会读、但演示数据不挑的群字段：给个中性默认值，具体头像三档写在 DEMO_GROUPS 里 */
const GROUP_DEFAULTS = {
  owner_type: 'user', owner_id: 1, announcement: null, dnd_until: null,
  speak_limit_per_minute: 0, speak_limit_window_seconds: 0, my_role: 'owner',
  avatar_mode: 'default', avatar_url: null, member_avatars: [], include_ai_in_avatar: true,
}

/** 端点级覆盖：这些响应整体换成演示数据，兜底清洗再统一跑一遍 */
const ROUTES = [
  route(/^\/auth\/me$/, (data) => ({ ...data, ...DEMO_ME })),

  route(/^\/groups$/, () => DEMO_GROUPS.map((g, i) => ({
    ...GROUP_DEFAULTS, created_at: at(60 * 24 * (30 + i)), ...g,
  }))),
  route(/^\/groups\/(\d+)$/, (data, m) => ({
    ...data,
    ...GROUP_DEFAULTS,
    ...(DEMO_GROUPS.find((g) => g.id === Number(m[1])) || DEMO_GROUPS[0]),
    id: Number(m[1]),
  })),
  route(/^\/groups\/\d+\/members$/, () => DEMO_MEMBERS),
  route(/^\/gm\/\d+\/messages/, () => DEMO_GROUP_MESSAGES),

  route(/^\/agents(\/available)?$/, () => DEMO_AGENTS),
  route(/^\/friends$/, () => DEMO_FRIENDS),
  route(/^\/dm\/sessions$/, () => DEMO_DM_SESSIONS),

  route(/^\/user\/stats$/, () => DEMO_USER_STATS),
  route(/^\/user\/storage$/, () => DEMO_USER_STORAGE),
  route(/^\/conversation-log\/usage\/overview/, () => DEMO_USAGE_OVERVIEW),

  route(/^\/worlds$/, () => DEMO_WORLDS),
  route(/^\/worlds\/(\d+)$/, (data, m) => ({
    ...data,
    ...(DEMO_WORLDS.find((w) => w.id === Number(m[1])) || DEMO_WORLDS[0]),
    id: Number(m[1]),
  })),
  route(/^\/worlds\/\d+\/files$/, (data) => ({
    ...data,
    // 截图里不出现 __pycache__ / 备份文件这类开发残留
    files: (data.files || []).filter((f) => !/(__pycache__|\.pyc$|\.bak)/.test(f.path)),
  })),
  route(/^\/worlds\/\d+\/chat$/, (data) => ({
    ...data,
    messages: DEMO_WORLD_CHAT,
    has_more: false,
    current_session: 'demo-session',
    sessions: [{ id: 'demo-session', created_at: at(28), last_active_at: at(4), pinned: true }],
  })),
  route(/^\/worlds\/\d+\/chat\/suggest$/, () => ({ suggestions: DEMO_SUGGESTIONS })),

  route(/^\/market\/items/, () => ({ total: DEMO_MARKET_ITEMS.length, items: DEMO_MARKET_ITEMS })),
  route(/^\/market\/github\/items$/, () => ({ synced_at: at(120), items: DEMO_GITHUB_ITEMS, total: DEMO_GITHUB_ITEMS.length })),
  route(/^\/market\/github\/bind$/, () => DEMO_GITHUB_BIND),
]

/** 流式接口一律放行：SSE/长连接被拦截会挂住页面 */
const PASS_THROUGH = /(\/chat\/stream|\/chat\/status|\/stream|\/sse|\/events)/

export function shouldPassThrough(url) {
  return PASS_THROUGH.test(url)
}

/**
 * 把一个 API 响应体换成演示数据；pathname 带 /api 前缀。
 * 先整体兜底清洗，再按端点整体覆盖 —— 顺序反了的话，端点覆盖出来的演示值
 * 会被兜底清洗再改一遍名字。
 */
export function rewriteApi(pathname, data) {
  const path = pathname.replace(/^\/api/, '')
  let current = scrub(data)
  for (const { pattern, apply } of ROUTES) {
    const m = path.match(pattern)
    if (m) current = apply(current, m)
  }
  return current
}
