/**
 * 一条日志的「状态帧身份」—— AI 详情页与管理台日志列表共用（单一实现，勿各自手写）。
 *
 * 后端从存下的请求体里读回当时的栈顶帧：type + label。不同状态的请求体前缀本就不同，
 * 列表按帧身份归堆，才看得出「这段状态的上下文长什么样」；帧实例（每次 push 的随机 id）
 * 会把同一段状态碎成一地，所以只认身份。
 */
import { useT } from '../../i18n/I18nContext'

export interface LogStateFrame {
  type?: string
  label?: string
}

/** 这条日志属于哪段状态；读不到就是空（那轮没注入状态摘要，比如状态栈建起来之前的旧日志） */
export function logStateOf(log: { state_frame?: LogStateFrame } | null | undefined): LogStateFrame | null {
  const frame = log?.state_frame
  return frame && frame.type ? frame : null
}

/**
 * 帧身份的编码：`type|label`（无状态帧时空串）。
 *
 * 它同时是分组键和 URL 参数——同一个身份只有一种写法，刷新回来才对得上。
 * type 是固定标识（group_chat / dm / …），不含 |，所以按第一个 | 切开就能还原。
 */
export function stateKeyOf(frame: LogStateFrame | null): string {
  return frame ? `${frame.type}|${frame.label ?? ''}` : ''
}

/** stateKeyOf 的逆（URL 参数还原用）；空串还原成 null */
export function stateFrameOfKey(key: string | null | undefined): LogStateFrame | null {
  if (!key) return null
  const at = key.indexOf('|')
  const type = at < 0 ? key : key.slice(0, at)
  const label = at < 0 ? '' : key.slice(at + 1)
  return type ? { type, label } : null
}

/**
 * 按状态帧身份分组（空组不返回）。
 * 组序 = 首次出现序：列表是新到旧，所以最近待过的状态排在最前面。
 */
export function groupByState<T>(
  items: T[],
  state: (item: T) => LogStateFrame | null,
): { frame: LogStateFrame | null; items: T[] }[] {
  const groups: { frame: LogStateFrame | null; items: T[] }[] = []
  const index = new Map<string, number>()
  for (const item of items) {
    const frame = state(item)
    const key = stateKeyOf(frame)
    let at = index.get(key)
    if (at === undefined) {
      at = groups.length
      index.set(key, at)
      groups.push({ frame, items: [] })
    }
    groups[at].items.push(item)
  }
  return groups
}

/** 帧类型说人话：group_chat / dm / world 有对应说法，AI 自己压的帧（work 之类）照原样 */
const STATE_TYPE_KEYS: Record<string, string> = {
  group_chat: 'logs:stateTypeGroup',
  dm: 'logs:stateTypeDm',
  world: 'logs:stateTypeWorld',
}

/** 后端把标签拼成「群「名字」」/「私信「名字」」/「世界「名字」」；类型交代过这件事，名字里就不重复 */
function stateName(label: string): string {
  const matched = /^(?:群|私信|世界)「([\s\S]*)」$/.exec(label)
  return matched ? matched[1] : label
}

/** 状态帧身份标签：类型 + 名字，一个胶囊说完 */
export function StateChip({ frame, className = '' }: { frame: LogStateFrame | null; className?: string }) {
  const t = useT()
  if (!frame) {
    return (
      <span className={`text-3xs px-2 py-0.5 rounded-full bg-canvas border border-border text-textMuted whitespace-nowrap ${className}`}>
        {t('logs:stateNone')}
      </span>
    )
  }
  const typeKey = STATE_TYPE_KEYS[frame.type ?? '']
  return (
    <span className={`inline-flex items-baseline gap-1 max-w-full text-3xs px-2 py-0.5 rounded-full bg-primary-500/10 whitespace-nowrap ${className}`}>
      <span className="shrink-0 font-medium text-primary-400">{typeKey ? t(typeKey) : frame.type}</span>
      {frame.label && <span className="truncate text-textPrimary">{stateName(frame.label)}</span>}
    </span>
  )
}
