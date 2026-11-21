/**
 * 通道把平台的富消息拍平成了文本：卡片消息、合并转发的聊天记录都长这样。
 * 主站要把它们还原成看得懂的东西，只能从这里解析——正文本身不动
 * （渲染即落库，改它等于改事实），解析失败返回 null，气泡退回纯文本。
 *
 * 解析器必须是纯函数：格式是平台定的，哪天变了这里失败一次，界面退化而不是出错。
 */

export interface ChannelCard {
  kind: 'card'
  /** 卡片种类，来自首行 "[卡片消息] 图文卡片" 的后半段 */
  label: string
  title: string
  desc: string
  url: string
  /** 来源标签（QQ 卡片里的 tag，如「腾讯频道」「哔哩哔哩」） */
  tag: string
  /** 链接域名是否在白名单里：不在就渲染成不可点的文本 */
  linkable: boolean
}

export interface ForwardAttachment {
  label: string
  name: string
  size: string
  url: string
  isImage: boolean
}

export interface ForwardMessage {
  sender: string
  text: string
  attachments: ForwardAttachment[]
}

export interface ChannelForward {
  kind: 'forward'
  title: string
  messages: ForwardMessage[]
}

export type ChannelContent = ChannelCard | ChannelForward

/** 只放行这些域名的链接与图片：通道来的文本也是外部输入，别把任意地址渲染成可点的 */
const SAFE_HOSTS = ['qq.com', 'pd.qq.com', 'multimedia.nt.qq.com.cn']

export function safeUrl(raw: string): string {
  const url = String(raw || '').trim()
  if (!/^https?:\/\//i.test(url)) return ''
  try {
    const host = new URL(url).hostname.toLowerCase()
    return SAFE_HOSTS.some(h => host === h || host.endsWith('.' + h)) ? url : ''
  } catch {
    return ''
  }
}

function lines(content: string): string[] {
  return String(content || '').replace(/\r\n?/g, '\n').split('\n')
}

/** 单行 "键: 值"；值里可能还有冒号（URL），所以只在第一个冒号处切 */
function field(line: string): [string, string] | null {
  const at = line.indexOf(':')
  if (at <= 0) return null
  return [line.slice(0, at).trim(), line.slice(at + 1).trim()]
}

function parseCard(rows: string[]): ChannelCard | null {
  const head = /^\[卡片消息\]\s*(.*)$/.exec(rows[0] || '')
  if (!head) return null
  const got: Record<string, string> = {}
  for (const row of rows.slice(1)) {
    const f = field(row)
    if (f) got[f[0]] = f[1]
  }
  const title = got.title || got['摘要'] || ''
  const desc = got.desc || got['描述'] || ''
  const url = got.jump_url || got.url || ''
  const tag = got.tag || got['标签'] || ''
  if (!title && !desc) return null
  return { kind: 'card', label: head[1].trim(), title, desc, url, tag, linkable: !!safeUrl(url) }
}

const ATTACHMENT_RE = /^\[附件(\d+)\]\s*(.*)$/
const FORWARD_HEAD_RE = /^===\s*消息\s*\d+\s*===$/
/** "[梦月吟希的聊天记录]"：开头那行就是转发标题，名字在"的"前面（以前这里多写了个"聊"，
 *  必须是"聊的聊天记录"才认，于是所有转发都退化成纯文本） */
const FORWARD_TITLE_RE = /^\[(.+?)的聊天记录\]$/
/** 每条消息正文前那个 "[消息内容] " 是平台加的分节标记，不该显示出来 */
const MESSAGE_BODY_RE = /^\[消息内容\]\s?/

function parseAttachment(row: string): ForwardAttachment | null {
  const m = ATTACHMENT_RE.exec(row)
  if (!m) return null
  const body = m[2]
  const pick = (key: string) => {
    const r = new RegExp(key + ':\\s*(.+?)(?=\\s+[^\\s]+:|$)').exec(body)
    return r ? r[1].trim() : ''
  }
  const url = safeUrl(pick('URL'))
  const label = pick('类型')
  return {
    label,
    name: pick('文件名'),
    size: pick('大小'),
    url,
    isImage: /图片|image/i.test(label) && !!url,
  }
}

function parseForward(rows: string[]): ChannelForward | null {
  const head = FORWARD_TITLE_RE.exec((rows[0] || '').trim())
  if (!head) return null
  const messages: ForwardMessage[] = []
  let cur: ForwardMessage | null = null
  for (const row of rows.slice(1)) {
    const text = row.trim()
    if (!text) continue
    if (FORWARD_HEAD_RE.test(text)) {
      if (cur) messages.push(cur)
      cur = { sender: '', text: '', attachments: [] }
      continue
    }
    if (!cur) continue
    const sender = /^\[发送者\]\s*(.*)$/.exec(text)
    if (sender) {
      cur.sender = sender[1].trim()
      continue
    }
    const att = parseAttachment(text)
    if (att) {
      cur.attachments.push(att)
      continue
    }
    const body = text.replace(MESSAGE_BODY_RE, '')
    cur.text = cur.text ? cur.text + '\n' + body : body
  }
  if (cur) messages.push(cur)
  // 标题用原文整句（去掉方括号）：「梦月吟希的聊天记录」，而不是只剩名字
  const title = head[0].replace(/^\[|\]$/g, '')
  return messages.length ? { kind: 'forward', title, messages } : null
}

/** 平台文本 → 可渲染的结构；认不出就是 null（调用方原样显示纯文本） */
export function parseChannelContent(content: string): ChannelContent | null {
  const rows = lines(content)
  const first = (rows[0] || '').trim()
  if (first.startsWith('[卡片消息]')) return parseCard([first, ...rows.slice(1)])
  if (first.startsWith('[') && first.includes('的聊天记录')) return parseForward([first, ...rows.slice(1)])
  return null
}
