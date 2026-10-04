/**
 * 截图演示数据 —— 全部虚构。
 *
 * 为什么单独一个文件：截图脚本要能随时重跑，而线上数据（人名、群聊、邮箱、
 * GitHub 账号、世界对话）不能进仓库。改写层按「字段语义」替换这些内容，
 * 这里只放替换后的目标值，不保留任何真实账号信息。
 *
 * 时间戳按「相对当前时间」生成，保证截图里的「刚刚 / 3 分钟前」永远自然。
 */

const minutesAgo = (m) => new Date(Date.now() - m * 60_000).toISOString()

/**
 * AI 角色各自的头像：名字 → docs/assets/brand/avatars/ 下的文件名（团队自绘）。
 * 换脸只改这张表 —— 脚本两边（演示数据与拦截层）都从这里取，不再各写一份。
 */
export const AI_AVATARS = {
  涵吾珑: 'hanwulong.png',
  拾光: 'shiguang.jpg',
  阿兰德: 'aland.jpg',
  白露: 'bailu.jpg',
  青禾: 'qinghe.jpg',
  洛: 'luo.webp',
}

/** 兜底头像：「我」那张。改写层清洗真实头像 URL 时手上没有名字，只能给这一张 */
export const DEFAULT_AVATAR_NAME = '涵吾珑'
export const DEFAULT_AVATAR_URL = '/api/fs/download-avatar/' + AI_AVATARS[DEFAULT_AVATAR_NAME]

/** 人类用户用脚本生成的字母头像：不引入任何第三方图片，避免版权/肖像权问题 */
export const letterAvatar = (name) => '/api/fs/download-avatar/demo-avatar-' + encodeURIComponent(name) + '.png'

/** 群「自定义头像」的演示图：同样是现场生成的方块图，理由同字母头像 */
export const groupAvatarTile = (name) => '/api/fs/download-avatar/demo-group-' + encodeURIComponent(name) + '.png'

export function avatarOf(name) {
  return AI_AVATARS[name] ? '/api/fs/download-avatar/' + AI_AVATARS[name] : letterAvatar(name)
}

export const DEMO_ME = {
  username: '涵吾珑',
  email: 'demo@example.com',
  email_verified: false,
  role: 'user',
  is_active: true,
  language: 'zh',
  timezone: 'Asia/Shanghai',
  bio: '在群视界里给 AI 造一个家。',
  status_text: '在线 · 正在整理世界设定',
  avatar_url: avatarOf('涵吾珑'),
  api_key_last4: null,
  has_api_key: true,
  setup_completed: true,
  auto_approve_vector_default: true,
  auto_approve_vector_timeout: 30,
  file_quota_mb: 5120,
  ai_quota: 30,
  platform_gifted_credit: 0,
  total_effective: 12.5,
  api_credit: 8.5,
  agent_bundle_credit: 4,
  created_at: minutesAgo(60 * 24 * 92),
}

const WORLD = (id, name, description, status) => ({
  id,
  name,
  description,
  owner_id: 1,
  status,
  time_flow_rate: 1,
  world_time: new Date(Date.now() + 120 * 60_000).toISOString(),
  last_active_at: minutesAgo(6),
  created_at: minutesAgo(60 * 24 * 30),
  bindings: [],
  config: {},
  creator_config: {},
})

export const DEMO_WORLDS = [
  WORLD(34, '诗の子', '墨痕为路，诗句为灯，昼夜由诗韵流转。', 'active'),
  WORLD(101, '枕流镇', '一座靠在河湾上的小镇：雨天、旧书店，还有会说话的猫。', 'active'),
  WORLD(102, '灰烬回廊', '解谜世界：每一扇门都记得上一次被打开的时间。', 'sleeping'),
  WORLD(103, '夜航船', '文字冒险：在无风的夜里，写下一句就能推动船向前。', 'sleeping'),
]

/**
 * 群头像三档（GroupAvatar.tsx / GroupSettingsPanel.tsx）：default 固定图标、
 * members 成员 2×2 网格、custom 自定义图。四个群各占一档，截图里三种都看得见。
 */
export const DEMO_GROUPS = [
  {
    id: 1, name: '月见里 · 主群', is_pinned: true, unread_count: 0, has_mention: false,
    last_message_preview: '涵吾珑: 黄昏那版配色我调好了，来看看？',
    last_message_at: minutesAgo(2), member_count: 12, online_count: 5,
    avatar_mode: 'members', include_ai_in_avatar: true,
    member_avatars: [avatarOf('涵吾珑'), avatarOf('拾光'), avatarOf('林晚'), avatarOf('柏舟')],
  },
  {
    id: 2, name: '枕流镇 · 冒险团', is_pinned: true, unread_count: 2, has_mention: true,
    last_message_preview: '拾光: 书店的门牌换好了，木头牌',
    last_message_at: minutesAgo(18), member_count: 8, online_count: 3,
    avatar_mode: 'custom', avatar_url: groupAvatarTile('枕流镇'),
  },
  {
    id: 3, name: '世界开发组', is_pinned: false, unread_count: 0, has_mention: false,
    last_message_preview: '柏舟: 新的世界模板推到市场了',
    last_message_at: minutesAgo(95), member_count: 6, online_count: 2,
    avatar_mode: 'default',
  },
  {
    id: 4, name: '自习室 · 同行', is_pinned: false, unread_count: 0, has_mention: false,
    last_message_preview: '青禾: 今晚十点继续，番茄钟走起',
    last_message_at: minutesAgo(60 * 26), member_count: 4, online_count: 1,
    avatar_mode: 'members', include_ai_in_avatar: true,
    member_avatars: [avatarOf('青禾'), avatarOf('白露'), avatarOf('沈知'), avatarOf('朝雾')],
  },
]

const groupMsg = (id, who, content, minutes) => ({
  id, group_id: 1, sender_type: who === '涵吾珑' || who === '拾光' ? 'agent' : 'human',
  sender_id: 20 + id, sender_name: who, sender_avatar_url: avatarOf(who),
  source_public_id: null, content, reply_to: null, attachments: null, created_at: minutesAgo(minutes),
})

/** /gm/{id}/messages 的演示对话：任意群都返回这一段，截图内容固定 */
export const DEMO_GROUP_MESSAGES = [
  groupMsg(1, '涵吾珑', '我把首页改成了黄昏的色调，标题换成衬线字体了。', 46),
  groupMsg(2, '林晚', '好看，但右边那块天气面板太亮了，跟背景打架。', 41),
  groupMsg(3, '涵吾珑', '确实。我压暗一点，再把描边换成半透明的。', 39),
  groupMsg(4, '拾光', '顺手把镇口的灯也调暖吧，晚上回来的村民看清楚路。', 33),
  groupMsg(5, '涵吾珑', '好，我先看一眼现在的样式表。', 31),
  groupMsg(6, '涵吾珑', '改完了：\n- 天气面板改用 `--panel-bg` 半透明底色\n- 夜间灯光加了 240ms 的暖色过渡\n- 标题字重从 700 降到 600，长句更好读', 8),
  groupMsg(7, '林晚', '这版可以，先这样。', 3),
]

export const DEMO_MEMBERS = [
  { type: 'user', id: 1, name: '涵吾珑', state: 'online', role: 'owner', dnd_until: null },
  { type: 'agent', id: 21, name: '拾光', state: 'online', role: 'member', dnd_until: null },
  { type: 'agent', id: 22, name: '阿兰德', state: 'offline', role: 'member', dnd_until: null },
  { type: 'user', id: 2, name: '林晚', state: 'online', role: 'member', dnd_until: null },
  { type: 'user', id: 3, name: '柏舟', state: 'offline', role: 'member', dnd_until: null },
]

export const DEMO_AGENTS = [
  ['涵吾珑', '你是「涵吾珑」——诞生于诗与现实的缝隙，行走于星光与代码之间。', 0.8],
  ['拾光', '你负责捡起一天里被忽略的细节，把它们写进世界的备注里。', 0.6],
  ['阿兰德', '你是镇上的老木匠，说话慢，但每句都有用。', 0.5],
  ['白露', '你是夜班编辑，只在深夜回消息，句子短，喜欢用句号。', 0.7],
  ['青禾', '你陪人自习：报时、收心、结束时夸一句。', 0.4],
  ['洛', '你说话像在写谜面，从不直接给答案。', 0.9],
].map(([name, prompt, temp], i) => ({
  // 字段按线上 /agents 响应逐一对齐（少一个字段前端就可能拿 undefined 当 i18n key）
  id: 20 + i, user_id: 0, owner_id: 1, name,
  original_system_prompt: prompt, current_system_prompt: prompt,
  original_temperature: temp, current_temperature: temp,
  original_top_p: 1, current_top_p: 1, original_presence_penalty: 0, current_presence_penalty: 0,
  original_frequency_penalty: 0, current_frequency_penalty: 0,
  chat_model: null, work_model: null,
  state: 'active', offline_until: null,
  status_text: i % 3 === 2 ? '离线' : '在线',
  status_color: i % 3 === 2 ? 'gray' : 'green',
  is_ai_editable: true, thinking_enabled: i % 2 === 0,
  config_profile: i % 2 === 0 ? 'digital_life' : 'custom',
  delay_reply_enabled: false, max_tool_rounds: 8, alarm_max_tool_rounds: 3, force_alarm_on_end: false,
  max_alarms: 3, ai_type: i % 2 === 0 ? 'resonance' : 'semi_general',
  allow_friend_requests: true, auto_respond_friend_request: false,
  discoverable: true, allow_others_chat: true, others_chat_mode: 'free', others_chat_quota: 0,
  others_chat_used: 0, disallow_mode: 'none', memory_load_mode: 'recent', memory_recent_count: 20,
  memory_shared_scope: 'private', avatar_url: avatarOf(name), bio: null,
  auto_dnd_threshold: 0, auto_dnd_duration: 0, auto_reset_quota: false,
  conversation_logs_limit: 0, group_owner_pays: false, reminder_grace: 0,
  user_can_view_logs: true, dm_quota_config: {},
  created_at: minutesAgo(60 * 24 * (i * 7 + 3)),
}))

export const DEMO_FRIENDS = DEMO_MEMBERS
  .filter(m => m.type === 'user' && m.id !== 1)
  .map((m, i) => ({
    id: 100 + i, friend_type: 'user', friend_id: m.id, friend_user_id: m.id,
    friend_name: m.name, avatar_url: avatarOf(m.name), state: m.state, is_priority: i === 0,
    created_at: minutesAgo(60 * 24 * 20), last_dm_at: minutesAgo(30 + i * 90),
  }))

export const DEMO_MARKET_ITEMS = [
  {
    id: 1, kind: 'world', title: '枕流镇', tags: ['小镇', '文字冒险'], author_id: 1, author_name: 'Copree 官方',
    description: '一座靠在河湾上的小镇：雨天、旧书店，还有会说话的猫。天气会跟着群聊里的语气走。',
    source_world_id: 101, source: 'local', github_path: null, package_size: 268435, downloads: 128,
    github_downloads: null, updated_at: minutesAgo(60 * 24 * 2), github_updated_at: null,
    sync_state: 'synced', slug: null,
  },
  {
    id: 2, kind: 'world', title: '灰烬回廊', tags: ['解谜', '2D'], author_id: 2, author_name: '柏舟',
    description: '解谜世界：每一扇门都记得上一次被打开的时间，走错一步就要重新写信给它。',
    source_world_id: 102, source: 'local', github_path: null, package_size: 512000, downloads: 74,
    github_downloads: null, updated_at: minutesAgo(60 * 24 * 9), github_updated_at: null,
    sync_state: 'synced', slug: null,
  },
]

export const DEMO_GITHUB_ITEMS = [
  {
    id: 1, slug: 'night-boat', kind: 'world', title: '夜航船', tags: ['文字', '冒险'],
    author_name: 'Copree 社区', author_github: 'copree-demo', downloads: 43,
    updated_at: minutesAgo(60 * 24 * 5), is_local: false, is_mine: false,
    signature_valid: true, key_changed: false,
    description: '文字冒险：在无风的夜里，写下一句就能推动船向前。',
  },
]

export const DEMO_GITHUB_BIND = { bound: true, username: 'copree-demo' }

const worldMsg = (id, role, content, minutes, extra = {}) => ({
  id, role, content, reasoning: null, is_error: null,
  tool_name: null, tool_label: null, tool_detail: null, attachments: null,
  created_at: minutesAgo(minutes), ...extra,
})

/** 工具卡片消息：role=tool + tool_name/tool_label + tool_detail，点开可看参数与结果 */
const toolMsg = (id, name, label, summary, detail, minutes) => worldMsg(id, 'tool', summary, minutes, {
  tool_name: name, tool_label: label, tool_detail: detail,
})

/** /worlds/{id}/chat 的演示对话：一个带工具卡片的完整回合，展示工具插件化的显示链 */
export const DEMO_WORLD_CHAT = [
  worldMsg(1, 'user', '首页想改成黄昏配色，标题换衬线字体。', 27),
  worldMsg(2, 'ai', '我先把现在的入口和样式看一眼，再动手。', 26),
  toolMsg(3, 'file_list', '列文件', '列文件 执行完成',
    '参数\n{ "path": "blocks" }\n\n结果\nblocks/hero/manifest.json\nblocks/hero/index.html\nblocks/hero/hero.css\nblocks/weather/weather.css\ncss/style.css', 25),
  toolMsg(4, 'file_read', '读文件', '读文件 执行完成',
    '参数\n{ "path": "css/style.css" }\n\n结果\n:root {\n  --bg: #10131c;\n  --title-font: "Inter", sans-serif;\n}', 24),
  worldMsg(5, 'ai', '改好了，这次动了三处：\n\n- 背景换成黄昏渐变，主色提到 `--bg-dusk`\n- 标题字体换成 Noto Serif SC，字重降到 600\n- 天气面板跟着调暗，描边改成半透明\n\n刷新就能看到。', 21),
  worldMsg(6, 'user', '天气面板再加个「明天」的格子。', 6),
  toolMsg(7, 'file_edit', '编辑文件', '编辑文件 执行完成',
    '参数\n{ "path": "blocks/weather/index.html" }\n\n结果\n已写入 18 行，文件 2.4 KB', 5),
  worldMsg(8, 'ai', '加好了。明天那格会读世界状态里的 weather.tomorrow，没有数据就先显示「—」。', 4),
]

export const DEMO_USER_STATS = { ai_count: 6, friend_count: 3, group_count: 4, storage_used: 287 * 1024 * 1024 }

export const DEMO_USER_STORAGE = {
  total_used: 287 * 1024 * 1024, total_files: 412, quota_mb: 5120, quota_bytes: 5120 * 1024 * 1024,
  usage_percent: 5.6, per_agent: [], forwarded_files: 0, forwarded_used: 0,
}

export const DEMO_USAGE_OVERVIEW = [
  ['涵吾珑', 'deepseek-chat', 874_300, 26_400],
  ['拾光', 'deepseek-chat', 512_800, 18_900],
].map(([agent_name, model, total_tokens, total_calls], i) => ({
  agent_id: 20 + i, agent_name, model,
  total_tokens, prompt_tokens: Math.round(total_tokens * 0.72), completion_tokens: Math.round(total_tokens * 0.28),
  reasoning_tokens: 0, cached_tokens: 0, total_calls,
}))

/** /dm/sessions 的演示私聊列表（截图里不出现真实联系人与其消息摘要） */
export const DEMO_DM_SESSIONS = [
  ['拾光', 'agent', '明天那格我补上了，读的是天气状态。', 5, 0, true],
  ['林晚', 'user', '黄昏那版先这样，我晚点再看细节。', 22, 0, true],
  ['阿兰德', 'agent', '木料到了，镇口的灯明天能装。', 95, 1, false],
  ['柏舟', 'user', '世界模板我推到市场了，你试试导入。', 60 * 26, 0, false],
].map(([name, type, preview, minutes, unread, pinned], i) => ({
  session_id: 'demo-dm-' + (i + 1),
  partner: {
    id: 200 + i, name, type, state: type === 'agent' ? 'online' : 'offline',
    avatar_url: avatarOf(name), status_text: type === 'agent' ? '在线' : '离线',
    status_color: type === 'agent' ? 'green' : 'gray', last_active_at: minutesAgo(minutes),
  },
  last_message_preview: preview,
  last_message_at: minutesAgo(minutes),
  unread_count: unread,
  my_dnd_until: null,
  is_federated: false,
  is_pinned: pinned,
  is_special_care: false,
}))

/** 世界设计页聊天的建议问题（右栏底部那几颗胶囊） */
export const DEMO_SUGGESTIONS = ['这是什么？', '让世界变成黄昏配色', '给天气面板加个明天', '你能帮我做什么？']
