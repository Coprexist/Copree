import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, ExternalLink, History, KeyRound, Loader2, MessageSquare, Plug, QrCode, RefreshCw, UserCheck } from 'lucide-react'
import { api } from '../../api/client'
import { useLang, useT } from '../../i18n/I18nContext'
import type { Lang } from '../../i18n/languages'
import { Button, Card, Input, SecretInput, Select, UnderlineTabs, confirmAsync, usePaged } from '../ui'
import ChannelTokenBudgetCard from './ChannelTokenBudgetCard'

/**
 * 「通道」页签 —— 一个 AI 接几条外部通道，由**插件自己声明**（后端 catalog.channels()）：
 * 现在有 QQ 官方与 NapCat 两条，将来第三方通道插件装上就多一条子项。
 *
 * 表单不写死字段：按插件给的 config_schema 渲染，平台只保留三个"约定字段"的特殊控件
 * （target_agent 由平台填、copree_group_id 选落点群、dm_policy 选私聊策略）。
 * 这样 NapCat 那种完全不同的凭据（ws_url / access_token）不用在平台里再加一套代码。
 */
interface ShowIfCond {
  field: string
  equals?: string
  in?: string[]
}

interface FieldSpec {
  type?: string
  title?: string
  title_en?: string
  title_ja?: string
  description?: string
  description_en?: string
  description_ja?: string
  secret?: boolean
  required?: boolean
  managed?: boolean
  /** 连接凭据：单独成块并排在配对之前（先连上，再决定谁能跟它说话） */
  credential?: boolean
  /** 枚举字段：插件给选项，卡片统一渲染成下拉（不给就按普通文本框） */
  options?: { value: string; label?: string; label_en?: string; label_ja?: string }[]
  /** 插件声明的默认值：没保存过的新字段按它预填（否则新设置项会以空框/错勾状态示人） */
  default?: string | number | boolean | null
  /** 联动显隐由插件声明：只有依赖字段满足条件时才画这个输入框（平台不写死字段名）。
   *  给数组表示"全都要满足"（例如：开关打开 且 周期是每天/每周）。 */
  show_if?: ShowIfCond | ShowIfCond[]

  /**
   * 行内分组：同一个 key 的字段画在同一行里，读起来像一句话
   * （「额度自动重置 [✓ 开启] [每天▾] [04:00] 重置」）。
   * label / hint / prefix / suffix 由组内任意成员声明，平台取第一个非空的。
   */
  /** 分块：同一个 key 的字段落在同一个设置块里（块头由组内任意成员声明） */
  section?: { key: string; title: string; title_en?: string; title_ja?: string }
  inline?: {
    key: string
    label?: string
    label_en?: string
    label_ja?: string
    hint?: string
    hint_en?: string
    hint_ja?: string
    prefix?: string
    suffix?: string
  }
}

interface GuideStep {
  text: string
  text_en?: string
  text_ja?: string
  url?: string
}

interface ChannelView {
  plugin_id: string
  kind: string
  label: string
  label_en: string
  label_ja: string
  desc: string
  desc_en: string
  desc_ja: string
  guide: GuideStep[]
  /** 插件自己声明的能力与限制（腾讯下线主动推送、私聊额度、封号风险…） */
  limits: { text: string; text_en?: string; text_ja?: string }[]
  pairing: boolean
  supports_group: boolean
  instance: string
  enabled: boolean
  schema: Record<string, FieldSpec>
  values: Record<string, unknown>
  secrets: Record<string, boolean>
  running: boolean
  /** 这条通道能不能自测（插件声明的能力，平台不猜） */
  self_test: boolean
  detail: Record<string, any>
  missing_required: string[]
  configured: boolean
  owner: { origin: string; nickname: string; approved_at: string | null } | null
  pending: { id: number; origin: string; nickname: string; code: string; created_at: string | null }[]
  approved: { id: number; origin: string; nickname: string; approved_at: string | null }[]
  blocked: { id: number; origin: string; nickname: string }[]
  group_options: { id: number; name: string }[]
}

const POLICY_KEYS = ['pairing', 'owner', 'open', 'off'] as const
// 显式映射而不是拼字符串：i18n 静态检查要能看见字面量 key
const POLICY_LABEL_KEY: Record<string, string> = {
  pairing: 'tool:channel.policy_pairing',
  owner: 'tool:channel.policy_owner',
  open: 'tool:channel.policy_open',
  off: 'tool:channel.policy_off',
}
const POLICY_HINT_KEY: Record<string, string> = {
  pairing: 'tool:channel.policy_pairingHint',
  owner: 'tool:channel.policy_ownerHint',
  open: 'tool:channel.policy_openHint',
  off: 'tool:channel.policy_offHint',
}

const DOT: Record<string, string> = { mint: 'bg-mint-400', accent: 'bg-primary-400', muted: 'bg-gray-400' }
const TEXT: Record<string, string> = { mint: 'text-mint-400', accent: 'text-primary-400', muted: 'text-textMuted' }

/** 插件文案自带三语，缺哪个就退回中文那一份 */
const pick = (lang: Lang, zh?: string, en?: string, ja?: string) => {
  const text = lang === 'en' ? en || zh : lang === 'ja' ? ja || zh : zh
  return text || ''
}

const tail = (origin: string) => (origin.length > 6 ? '…' + origin.slice(-6) : origin)

/** 挡浏览器自动填充：autocomplete 关掉，name 起得不像账号密码，再给常用密码管理器一个忽略标记 */
const NO_AUTOFILL = {
  autoComplete: 'off',
  spellCheck: false,
  'data-form-type': 'other',
  'data-1p-ignore': 'true',
  'data-lpignore': 'true',
} as const

/** 左标签右控件：卡片里所有字段共用这一套对齐，不再各写各的 */
function Row({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="grid gap-x-4 gap-y-1 sm:grid-cols-[7rem_1fr] sm:items-start">
      <div className="text-xs text-textSecondary sm:pt-2 pt-0">{label}</div>
      <div className="min-w-0 space-y-1.5">
        {children}
        {hint && <p className="text-3xs text-textMuted leading-relaxed">{hint}</p>}
      </div>
    </div>
  )
}

/** 一次给一页的条数：待批准的行高一点（带操作按钮），已批准/已拉黑是细行，能多放几条 */
const PAIR_PAGE = 5
const GRANT_PAGE = 6
const RECENT_PAGE = 4

interface PairRow { id: number; origin: string; nickname: string }

/** 插件给的三语展示文案；参数项可带 fmt（值的人话格式，{} 是占位） */
interface TrailText { text: string; text_en?: string; text_ja?: string; fmt?: string; fmt_en?: string; fmt_ja?: string }

/**
 * 按插件声明的 section 分组：没有分块的字段平铺在上（落点、白名单这类每张卡都先要看的），
 * 有分块的按声明顺序生成页签。分块顺序 = 字段在 schema 里第一次出现的顺序，不是字典序。
 */
function groupBySection(fields: [string, FieldSpec][], lang: Lang) {
  const plain: [string, FieldSpec][] = []
  const tabs: { key: string; label: string }[] = []
  const buckets: Record<string, [string, FieldSpec][]> = {}
  for (const [key, spec] of fields) {
    const sectionKey = spec.section?.key
    if (!sectionKey) {
      plain.push([key, spec])
      continue
    }
    if (!buckets[sectionKey]) {
      buckets[sectionKey] = []
      tabs.push({
        key: sectionKey,
        label: pick(lang, spec.section!.title, spec.section!.title_en, spec.section!.title_ja) || sectionKey,
      })
    }
    buckets[sectionKey].push([key, spec])
  }
  return { plain, tabs, buckets }
}

/**
 * 配对：待批准 / 已批准 / 已拉黑。
 *
 * 三份名单都会变长（拉一次群能同时进来一串人），所以一律分页——列表不铺开，卡片就
 * 不会被十几行顶成一整屏。页码存在这里，主卡只管把数据和动作递进来。
 * 行也压成一行（昵称与配对码并排），一页 5 条正好是一屏之内的高度。
 */
function PairingBlock({ pending, approved, blocked, busy, approve, approveByCode, block, forget }: {
  pending: { id: number; origin: string; nickname: string; code: string }[]
  approved: PairRow[]
  blocked: PairRow[]
  busy: string
  approve: (id: number) => void
  approveByCode: (code: string) => void
  block: (origin: string) => void
  forget: (origin: string, name: string) => void
}) {
  const t = useT()
  const [code, setCode] = useState('')
  // 已批准与已拉黑合成一份名单：行只差一个圆点颜色和右侧动作
  const grants = [
    ...approved.map(a => ({ ...a, kind: 'approved' as const })),
    ...blocked.map(b => ({ ...b, kind: 'blocked' as const })),
  ]
  const pendingList = usePaged(pending, PAIR_PAGE)
  const grantList = usePaged(grants, GRANT_PAGE)
  return (
    <>
      {pending.length === 0 ? (
        <div className="text-3xs text-textMuted">{t('tool:channel.pendingEmpty')}</div>
      ) : (
        <div className="space-y-1.5">
          {pendingList.rows.map(p => (
            <div key={p.id} className="grid grid-cols-[1fr_auto_auto] items-center gap-2 bg-canvas border border-border rounded-control px-2.5 py-1.5">
              {/* 一个人一行：昵称与配对码并排，不再各占一行 */}
              <div className="flex items-baseline gap-2 min-w-0">
                <span className="text-xs text-textPrimary truncate">{p.nickname || t('tool:channel.unknownUser')}</span>
                <span className="text-3xs text-textMuted truncate">{t('tool:channel.codeLabel', { code: p.code, origin: tail(p.origin) })}</span>
              </div>
              <Button size="xs" variant="primary" loading={busy === 'pair' + p.id} onClick={() => approve(p.id)}>{t('tool:channel.approve')}</Button>
              <Button size="xs" variant="ghost" onClick={() => block(p.origin)}>{t('tool:channel.block')}</Button>
            </div>
          ))}
          {pendingList.pager}
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        <Input
          value={code}
          onChange={e => setCode(e.target.value.toUpperCase())}
          placeholder={t('tool:channel.byCodePlaceholder')}
          name="copree-channel-pair-code"
          fieldSize="sm"
          className="w-44"
          {...NO_AUTOFILL}
        />
        <Button size="sm" variant="secondary" loading={busy === 'code'} onClick={() => approveByCode(code.trim())} disabled={!code.trim()}>
          {t('tool:channel.byCode')}
        </Button>
      </div>

      {grants.length > 0 && (
        <div className="space-y-1">
          {grantList.rows.map(g => (
            <div key={g.kind + ':' + g.id} className={'grid grid-cols-[auto_1fr_auto] items-center gap-2 text-3xs ' + (g.kind === 'approved' ? 'text-textSecondary' : 'text-textMuted')}>
              <span className={'w-1.5 h-1.5 rounded-full ' + (g.kind === 'approved' ? 'bg-mint-400' : 'bg-gray-400')} />
              <span className="min-w-0 truncate">
                {t(g.kind === 'approved' ? 'tool:channel.ownerRow' : 'tool:channel.blockedRow', { name: g.nickname || tail(g.origin) })}
              </span>
              <button
                onClick={() => forget(g.origin, g.nickname || tail(g.origin))}
                className={'transition-colors ' + (g.kind === 'approved' ? 'text-textMuted hover:text-rose-400' : 'hover:text-primary-400')}
              >
                {t(g.kind === 'approved' ? 'tool:channel.forget' : 'tool:channel.unblock')}
              </button>
            </div>
          ))}
          {grantList.pager}
        </div>
      )}

      <p className="text-3xs text-textMuted leading-relaxed">{t('tool:channel.privacy')}</p>
    </>
  )
}

/**
 * :param showTitle 卡片自己要不要亮出通道名与说明。
 *   页面里（AI 详情 → 通道页签）要，不然只看到一堆字段不知道在看哪条通道；
 *   弹窗里不要——弹窗标题栏已经写了，再写一遍是重复。
 */
export default function ChannelCard({ agentId, showTitle = true }: { agentId: number; showTitle?: boolean }) {
  const t = useT()
  const lang = useLang()
  const [views, setViews] = useState<ChannelView[]>([])
  const [activeId, setActiveId] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)
  // 草稿按通道分开存：切换子项时，另一条通道填了一半的内容不会被带过去
  const [configSection, setConfigSection] = useState('')   // 配置块的当前页签（接入/回复/额度/私聊）
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({})
  const [secrets, setSecrets] = useState<Record<string, Record<string, string>>>({})
  const [groupMode, setGroupMode] = useState<Record<string, 'existing' | 'new'>>({})
  const [newGroupName, setNewGroupName] = useState<Record<string, string>>({})
  /** 使用轨迹：默认看人话，点一下看模型实际传的原文 */
  const [activityRaw, setActivityRaw] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api.get<{ channels: ChannelView[] }>('/me/agents/' + agentId + '/channels')
      const list = r.channels || []
      setViews(list)
      setActiveId(prev => (list.some(v => v.plugin_id === prev) ? prev : list[0]?.plugin_id || ''))
      const nextDrafts: Record<string, Record<string, string>> = {}
      const nextSecrets: Record<string, Record<string, string>> = {}
      const nextMode: Record<string, 'existing' | 'new'> = {}
      for (const v of list) {
        const fields: Record<string, string> = {}
        const secrets: Record<string, string> = {}
        for (const [key, spec] of Object.entries(v.schema || {})) {
          if (spec.managed) continue
          if (spec.secret) { secrets[key] = ''; continue }
          const saved = v.values?.[key]
          // 没保存过的字段用 schema 的默认值：新加的设置项不该以空输入框/错误勾选状态示人
          const fallback = spec.default === undefined || spec.default === null
            ? ''
            : (typeof spec.default === 'boolean' ? (spec.default ? 'true' : 'false') : String(spec.default))
          fields[key] = String(saved ?? fallback)
        }
        nextDrafts[v.plugin_id] = fields
        nextSecrets[v.plugin_id] = secrets
        nextMode[v.plugin_id] = v.values?.copree_group_id ? 'existing' : 'new'
      }
      setDrafts(nextDrafts)
      setSecrets(nextSecrets)
      setGroupMode(nextMode)
    } catch (e: any) {
      setMsg({ tone: 'err', text: e?.message || String(e) })
    } finally {
      setLoading(false)
    }
  }, [agentId])

  useEffect(() => { load() }, [load])

  // 托管协议端每 5 秒重拉一次，直到卡片落定：没登录（码会轮换、登录完成后要自己切走），
  // 或登录了但还没拿到昵称（扫码确认到写回配置之间有个空窗——这个空窗里停轮询，
  // 卡片就会永远停在「已登录：—」，只能手动刷新页面）
  const hostedEndpoint = views.find(v => v.plugin_id === activeId)?.detail?.hosted_endpoint as
    | { held_by_platform?: boolean; logged_in?: boolean; bot_name?: string
        qr_state?: 'live' | 'expired' | 'stalled'; qr_expires_in?: number
        qr_png?: string; qr_at?: number; qr_url?: string }
    | undefined
  const hostedPending = Boolean(hostedEndpoint?.held_by_platform
    && (!hostedEndpoint?.logged_in || !hostedEndpoint?.bot_name))
  useEffect(() => {
    if (!hostedPending) return
    const timer = setInterval(() => { void load() }, 5000)
    return () => clearInterval(timer)
  }, [hostedPending, load])

  // 二维码：默认不摆码，点一下才向协议端要一张。拿到的那张做成快照留在这里，
  // 过期也不收起（用户可能正扫到一半），只是倒计时归零后标"已过期"；
  // 刷新按钮常驻，一分钟内最多点三次。快照只在点击时更新——协议端自己换了新码，
  // 也不会把手里这张顶掉，否则等于替用户自动申请了。
  const QR_CLICK_LIMIT = 3
  const QR_CLICK_WINDOW_MS = 60_000
  const [qrShot, setQrShot] = useState<{ png: string; url: string; expiresAt: number } | null>(null)
  const [qrPending, setQrPending] = useState(false)   // 点过了，正在等协议端给下一张
  const [qrWaited, setQrWaited] = useState(0)
  const [qrLeft, setQrLeft] = useState(0)
  const [qrClicks, setQrClicks] = useState<number[]>([])
  const [qrCooldown, setQrCooldown] = useState(0)
  const [qrError, setQrError] = useState('')

  const adoptQr = (png: string, url: string, expiresIn: number) => {
    setQrShot({ png, url, expiresAt: Date.now() + Math.max(0, expiresIn) * 1000 })
    setQrPending(false)
  }

  // 一次点击 = 一次申请：直接让协议端向腾讯要一张新的码，倒计时从这一刻算起。
  // 不拿"协议端手上那张"——那张码上挂的时间已经过去一截，剩余秒数会是假的。
  const requestQr = async () => {
    const now = Date.now()
    const recent = qrClicks.filter(at => now - at < QR_CLICK_WINDOW_MS)
    if (recent.length >= QR_CLICK_LIMIT) {
      setQrCooldown(Math.ceil((QR_CLICK_WINDOW_MS - (now - recent[0])) / 1000))
      return
    }
    setQrClicks([...recent, now])
    setQrPending(true)
    setQrWaited(0)
    setQrError('')
    try {
      const res = await api.post<{ qr_png?: string; qr_url?: string; qr_expires_in?: number }>(
        '/me/agents/' + agentId + '/channels/' + activeId + '/login-qr', {}
      )
      if (res?.qr_png) {
        adoptQr(res.qr_png, res.qr_url || '', Number(res.qr_expires_in) || 0)
      } else {
        setQrError(t('tool:channel.hostedQrFailed'))
        setQrPending(false)
      }
    } catch (e: any) {
      setQrError(e?.message || t('tool:channel.hostedQrFailed'))
      setQrPending(false)
    }
  }

  useEffect(() => {
    setQrShot(null); setQrPending(false); setQrWaited(0); setQrClicks([]); setQrCooldown(0); setQrError('')
  }, [activeId])
  useEffect(() => {
    if (!hostedEndpoint?.logged_in) return
    setQrShot(null); setQrPending(false)
  }, [hostedEndpoint?.logged_in])

  // 只有"用户点出来的这一次申请"会自动采纳新码；已经显示的那张不会被替换
  useEffect(() => {
    if (!qrPending) return
    if (hostedEndpoint?.qr_state === 'live' && hostedEndpoint?.qr_png) {
      adoptQr(hostedEndpoint.qr_png, hostedEndpoint.qr_url || '', Number(hostedEndpoint.qr_expires_in) || 0)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qrPending, hostedEndpoint?.qr_state, hostedEndpoint?.qr_png, hostedEndpoint?.qr_url, hostedEndpoint?.qr_expires_in])

  useEffect(() => {
    if (!qrPending) { setQrWaited(0); return }
    const timer = setInterval(() => setQrWaited(v => v + 1), 1000)
    return () => clearInterval(timer)
  }, [qrPending])

  // 倒计时按快照自己算，不看轮询结果：手里这张的寿命只由它自己出生时间决定
  useEffect(() => {
    if (!qrShot) { setQrLeft(0); return }
    const tick = () => setQrLeft(Math.max(0, Math.round((qrShot.expiresAt - Date.now()) / 1000)))
    tick()
    const timer = setInterval(tick, 1000)
    return () => clearInterval(timer)
  }, [qrShot])

  useEffect(() => {
    if (qrCooldown <= 0) return
    const timer = setInterval(() => setQrCooldown(v => Math.max(0, v - 1)), 1000)
    return () => clearInterval(timer)
  }, [qrCooldown])

  /** 所有动作共用一个出口：忙标记、提示、成功后重拉
   *  okText 允许按响应生成：有些提示要等发出去才知道（自测走的是被动回复还是主动消息） */
  const act = async (key: string, fn: () => Promise<any>, okText: string | ((res: any) => string)) => {
    setBusy(key)
    setMsg(null)
    try {
      const res: any = await fn()
      setMsg(res?.warning
        ? { tone: 'err', text: res.warning }
        : { tone: 'ok', text: typeof okText === 'function' ? okText(res) : okText })
      await load()
    } catch (e: any) {
      setMsg({ tone: 'err', text: e?.message || String(e) })
    } finally {
      setBusy('')
    }
  }

  const view = views.find(v => v.plugin_id === activeId) || null
  const base = view ? '/me/agents/' + agentId + '/channels/' + view.plugin_id : ''
  const draft = drafts[activeId] || {}
  const secretDraft = secrets[activeId] || {}
  const mode = groupMode[activeId] || 'existing'
  const fields = Object.entries(view?.schema || {}).filter(([, spec]) => !spec.managed)
  const credentialFields = fields.filter(([, spec]) => spec.credential)
  const configFields = fields.filter(([, spec]) => !spec.credential)
  // 分块字段走页签：无分块的字段照旧平铺（落点、白名单这类"每张卡都要先看的"）
  const { plain: plainFields, tabs: sectionTabs, buckets: sectionFields } = groupBySection(configFields, lang)
  const activeSection = sectionTabs.some(t => t.key === configSection)
    ? configSection
    : (sectionTabs[0]?.key || '')

  const setField = (key: string, value: string) =>
    setDrafts(prev => ({ ...prev, [activeId]: { ...(prev[activeId] || {}), [key]: value } }))
  const setSecret = (key: string, value: string) =>
    setSecrets(prev => ({ ...prev, [activeId]: { ...(prev[activeId] || {}), [key]: value } }))

  /** 保存：选了「新建群」就先在 Copree 建一个当落点，再写进通道配置 */
  const save = () => act('save', async () => {
    const schema = view?.schema || {}
    let landing = mode === 'existing' ? (draft.copree_group_id || '').trim() : ''
    if ('copree_group_id' in schema && mode === 'new') {
      const created = await api.post<{ id: number; name: string }>(base + '/landing-group', { name: (newGroupName[activeId] || '').trim() })
      landing = String(created.id)
      setField('copree_group_id', landing)
      setGroupMode(prev => ({ ...prev, [activeId]: 'existing' }))
    }
    const values: Record<string, string> = {}
    for (const [key, spec] of Object.entries(schema)) {
      if (spec.managed || spec.secret) continue
      values[key] = key === 'copree_group_id' ? landing : (draft[key] || '').trim()
    }
    // 机密留空 = 不改：后端的语义是"空串=清除"，把空值回传过去会把已存的凭据抹掉
    for (const [key, value] of Object.entries(secretDraft)) {
      if (value.trim()) values[key] = value.trim()
    }
    return api.put(base, { values })
  }, t('tool:channel.saved'))

  /** 给某个 QQ 群指定落点 = 合并到这个群：走后端一处收尾（AI 入群 + 源落点群归档）。
   *  只有"选回默认落点"才是纯配置动作（把这个群从映射表里删掉，不涉及成员）。 */
  const saveLanding = (origin: string, value: string) => {
    setMapDraft(prev => ({ ...prev, [activeId]: { ...(prev[activeId] || {}), [origin]: value } }))
    setNewFor(prev => ({ ...prev, [origin]: '' }))
    if (value) return mergeInto(origin, Number(value))
    const next: Record<string, string> = { ...((view?.detail?.group_map as Record<string, string>) || {}) }
    delete next[origin]
    return act('map:' + origin, () => api.put(base, { values: { group_map: JSON.stringify(next) } }), t('tool:channel.landingSaved'))
  }

  /** 为这个 QQ 群新建一个 Copree 群，并直接指定成它的落点 */
  const createLandingFor = (origin: string) => act('newmap:' + origin, async () => {
    const created = await api.post<{ id: number; name: string }>(
      base + '/landing-group', { name: (newFor[origin] || '').trim() },
    )
    setMapDraft(prev => ({ ...prev, [activeId]: { ...(prev[activeId] || {}), [origin]: String(created.id) } }))
    setNewFor(prev => ({ ...prev, [origin]: '' }))
    // 新建出来的群已经是成员（create_landing_group 加的），再走一次合并只是把落点固定下来——
    // 与"选已有群"同一条后端路径，少一套写法
    await mergeInto(origin, created.id)
  }, t('tool:channel.landingSaved'))

  /** 凭据单独存：换 AppID/Secret 等于"换一个机器人重新连"，与落点/策略不是一件事。
   *  后端允许部分保存，只交这两个键不会动其它配置；机密留空 = 不改（空串是"清除"）。 */
  const saveCredential = () => {
    const values: Record<string, string> = {}
    for (const [key, spec] of credentialFields) {
      const typed = spec.secret ? (secretDraft[key] || '').trim() : (draft[key] || '').trim()
      if (typed) values[key] = typed
    }
    if (Object.keys(values).length === 0) return
    return act('cred', () => api.put(base, { values }), t('tool:channel.saved'))
  }

  /** 最近见到过的通道侧标识：一键进那个"白名单"字段（字段名由插件在状态里报，平台不猜） */
  const recentField = String(view?.detail?.recent_field || '')
  const recent: {
    origin: string; last_at: number; count: number; allowed: boolean;
    copree_group_id?: number | null; mapped?: boolean;
    // 插件今天从通道那边拉到的群名/人数（没拉到就是空）：有就显示真名，比 openid 好认
    name?: string; member_num?: number;
    // 「可能是同一个群」：同一个主人的别的机器人把同名的通道群接在哪儿（只读候选）
    possible_group?: { id: number; name: string } | null;
  }[] = (view?.detail?.recent_groups as any[]) || []
  /** 最近见过的群也分页：一次只给一页，卡不会被群列表撑长 */
  const recentList = usePaged(recent, RECENT_PAGE)
  /** 每个 QQ 群选了哪个落点（未提交前的本地值）：键是群 openid */
  const [mapDraft, setMapDraft] = useState<Record<string, Record<string, string>>>({})
  /** 正在为哪个群起名新建（openid → 名字，空串 = 面板已打开） */
  const [newFor, setNewFor] = useState<Record<string, string>>({})
  const allowList = recentField ? String(draft[recentField] || '').split(/[,，\s]+/).filter(Boolean) : []
  const addToList = (origin: string) => {
    if (!recentField || allowList.includes(origin)) return
    const next = [...allowList, origin].join(',')
    setField(recentField, next)
    // 还没配好或正打算新建群时只填进输入框，让用户自己点保存（别顺手改了落点）
    if (!view?.configured || mode === 'new') return
    return act('allow:' + origin, () => api.put(base, { values: { [recentField]: next } }), t('tool:channel.allowAdded'))
  }

  /** 通道自测：让后端在这条通道的真实出口上发一条（"没发出去"是自测的答案，原样显示原因） */
  const selfTest = () => act('self-test', async () => {
    const res = await api.post<{ sent: boolean; mode?: string; reason?: string }>(base + '/self-test')
    if (!res?.sent) throw new Error(res?.reason || t('tool:channel.selfTestFailed'))
    return res
  }, res => t('tool:channel.selfTestSent', {
    mode: t(res.mode === 'active' ? 'tool:channel.selfTestModeActive' : 'tool:channel.selfTestModePassive'),
  }))

  /** 合并到「可能是同一个群」那个 Copree 群：落点指过去、这个 AI 也搬进去（收尾在后端一处） */
  const mergeInto = (origin: string, groupId: number) =>
    act('merge:' + origin, () => api.post(base + '/merge', { origin, group_id: groupId }), t('tool:channel.merged'))

  const start = () => act('start', () => api.post(base + '/start'), t('tool:channel.started'))
  const stop = () => act('stop', () => api.post(base + '/stop'), t('tool:channel.stopped'))
  const approve = (id: number) => act('pair' + id, () => api.post(base + '/pairings/approve', { pairing_id: id }), t('tool:channel.approved'))
  const approveByCode = (code: string) => act('code', () => api.post(base + '/pairings/approve', { code }), t('tool:channel.approved'))
  const block = (origin: string) => act('block', () => api.post(base + '/pairings/block', { origin }), t('tool:channel.blocked'))
  const forget = async (origin: string, name: string) => {
    const ok = await confirmAsync({ title: t('tool:channel.forget'), message: t('tool:channel.forgetConfirm', { name }), danger: true })
    if (ok) await act('forget', () => api.post(base + '/pairings/forget', { origin }), t('tool:channel.forgotten'))
  }

  if (loading && views.length === 0) {
    return <div className="text-xs text-textMuted py-6 flex items-center justify-center gap-2"><Loader2 size={13} className="animate-spin" />{t('tool:channel.loading')}</div>
  }
  if (!view) {
    return <div className="text-xs text-textMuted py-4">{t('tool:channel.loadFailed')}</div>
  }

  const statusOf = (v: ChannelView) => !v.enabled ? 'muted' : !v.configured ? 'accent' : v.running ? 'mint' : 'muted'
  const status = statusOf(view) === 'muted' && view.enabled ? { tone: 'muted', text: t('tool:channel.statusStopped') }
    : statusOf(view) === 'muted' ? { tone: 'muted', text: t('tool:channel.disabled') }
    : statusOf(view) === 'accent' ? { tone: 'accent', text: t('tool:channel.statusUnconfigured') }
    : { tone: 'mint', text: t('tool:channel.statusRunning') }
  // "还缺什么"用人话（schema 的 title）：用户看到 app_id 这种字段名只会更懵
  const missingText = view.missing_required.map(k => {
    const spec = view.schema?.[k]
    return pick(lang, spec?.title, spec?.title_en, spec?.title_ja) || k
  }).join(' / ')
  const groups = view.group_options || []
  // 平台自带协议端时，登录入口在卡片里（后端已经把二维码读出来一起报过来了）
  const hosted = (view.detail?.hosted_endpoint || null) as
    { held_by_platform: boolean; logged_in: boolean; bot_name: string; qr_png?: string; qr_at?: number
      qr_state?: 'ready' | 'waiting' | 'stalled'; qr_expires_in?: number
      qr_url?: string } | null
  // 使用轨迹：插件自己决定记什么、怎么记（B 站记工具调用/推送/登录），平台只按形状渲染。
  // 插件不报就不画——没有自带工具的通道本来就没有轨迹可看。
  const activity = (view.detail?.activity || null) as
    {
      count: number
      /** 动作名与参数名的三语字典：插件声明，界面按当前语言取——库里只存工具 id 与参数值 */
      i18n?: {
        actions?: Record<string, TrailText>
        args?: Record<string, TrailText>
      }
      items: { at: string; action: string; raw: string; ok: boolean; ms: number }[]
    } | null
  const filled = Object.values(draft).some(v => v.trim()) || Object.values(secretDraft).some(v => v.trim())

  /** 插件字典里的三语文案：缺项退回 fallback（不猜、不翻译机器名） */
  const trailText = (dict: Record<string, TrailText> | undefined, key: string, fallback: string) => {
    const item = dict?.[key]
    return (item ? pick(lang, item.text, item.text_en, item.text_ja) : '') || fallback
  }
  /** 轨迹的动作名：插件声明 → 按语言翻译；缺了就显示工具 id 本身 */
  const trailLabel = (action: string) => trailText(activity?.i18n?.actions, action, action)
  /** 轨迹的参数：参数名也翻译（keyword → 关键词），值原样；不是 JSON 的老行直接显示 */
  const trailArgs = (action: string, raw: string) => {
    const text = (raw || '').trim()
    if (!text) return ''
    try {
      const parsed = JSON.parse(text)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return Object.entries(parsed as Record<string, unknown>)
          .map(([k, v]) => {
            const meta = activity?.i18n?.args?.[k]
            const value = typeof v === 'boolean' ? t(v ? 'common:yes' : 'common:no')
              : typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)
            // 有格式模板就用它（如"最多 2 条"）；没有就退回"名字=值"
            const fmt = meta ? pick(lang, meta.fmt, meta.fmt_en, meta.fmt_ja) : ''
            if (fmt.includes('{}')) return fmt.replace('{}', value)
            return trailText(activity?.i18n?.args, k, k) + '=' + value
          })
          .join(' · ')
      }
    } catch {
      // 不是 JSON（老轨迹里的中文说明）就原样显示
    }
    return text
  }

  /** 一个字段一个控件：平台只对三个约定字段用特殊控件，其余按 schema 渲染 */
  /** 联动条件判定：多个条件为"且" */
  const matchesCond = (cond: ShowIfCond, values: Record<string, string>): boolean => {
    const value = values[cond.field] ?? ''
    if (cond.equals !== undefined) return value === cond.equals
    if (cond.in) return cond.in.includes(value)
    return true
  }
  const isVisible = (spec: FieldSpec, values: Record<string, string>): boolean => {
    const rule = spec.show_if
    if (!rule) return true
    return Array.isArray(rule) ? rule.every(c => matchesCond(c, values)) : matchesCond(rule, values)
  }

  /** 行内分组：key → 成员字段（按声明的先后顺序） */
  const inlineGroups: Record<string, string[]> = {}
  for (const [key, spec] of Object.entries(view.schema || {})) {
    if (!spec.inline?.key) continue
    ;(inlineGroups[spec.inline.key] ||= []).push(key)
  }
  const renderedGroups = new Set<string>()

  /** 组内单个控件：紧凑、无独立 label（组标题已经说明这行是什么） */
  const renderInlineControl = (key: string, spec: FieldSpec) => {
    const inline = spec.inline!
    if (spec.type === 'boolean') {
      return (
        <label key={key} className="inline-flex items-center gap-1.5 text-xs text-textSecondary cursor-pointer select-none">
          <input
            type="checkbox"
            className="w-3.5 h-3.5 rounded-control border border-border bg-canvas accent-primary-500"
            checked={(draft[key] || 'true') === 'true'}
            onChange={e => setField(key, e.target.checked ? 'true' : 'false')}
            disabled={!view.enabled}
          />
          {t('tool:channel.switchOn')}
        </label>
      )
    }
    if (spec.options && spec.options.length > 0) {
      return (
        <Select
          key={key}
          value={draft[key] || ''}
          onChange={e => setField(key, e.target.value)}
          options={spec.options.map(o => ({
            value: o.value,
            label: pick(lang, o.label, o.label_en, o.label_ja) || o.value,
          }))}
          fieldSize="sm"
          className="w-32"
          disabled={!view.enabled}
        />
      )
    }
    return (
      <span key={key} className="inline-flex items-center gap-1.5">
        {inline.prefix && <span className="text-xs text-textSecondary">{inline.prefix}</span>}
        <Input
          value={draft[key] || ''}
          onChange={e => setField(key, e.target.value)}
          name={'channel-field-' + key}
          fieldSize="sm"
          className="w-24"
          disabled={!view.enabled}
          {...NO_AUTOFILL}
        />
      </span>
    )
  }

  const renderField = (key: string, spec: FieldSpec) => {
    // 联动显隐：插件说"这个字段什么时候才有意义"（比如重置时间只在每天/每周时显示），
    // 平台只按声明判断，不在核心里写死字段名
    // 行内分组：整组只画一行（成员各自保留 show_if，隐藏的成员不占位）
    if (spec.inline) {
      const groupKey = spec.inline.key
      if (renderedGroups.has(groupKey)) return null
      renderedGroups.add(groupKey)
      const members = (inlineGroups[groupKey] || [key])
        .filter(k => isVisible(view.schema[k], draft))
      if (members.length === 0) return null
      const groupLabel = pick(lang, spec.inline.label, spec.inline.label_en, spec.inline.label_ja) || ''
      const groupHint = pick(lang, spec.inline.hint, spec.inline.hint_en, spec.inline.hint_ja) || ''
      return (
        <Row key={groupKey} label={groupLabel} hint={groupHint}>
          <div className="flex items-center gap-2 flex-wrap">
            {members.map(k => renderInlineControl(k, view.schema[k]))}
          </div>
        </Row>
      )
    }
    if (!isVisible(spec, draft)) return null
    const label = pick(lang, spec.title, spec.title_en, spec.title_ja) || key
    const hint = pick(lang, spec.description, spec.description_en, spec.description_ja)
    if (key === 'dm_policy') {
      return (
        <Row key={key} label={t('tool:channel.policyLabel')} hint={t(POLICY_HINT_KEY[draft[key] || 'pairing'] || POLICY_HINT_KEY.pairing)}>
          <Select
            value={draft[key] || 'pairing'}
            onChange={e => setField(key, e.target.value)}
            options={POLICY_KEYS.map(k => ({ value: k, label: t(POLICY_LABEL_KEY[k]) }))}
            fieldSize="sm"
            className="w-56"
            disabled={!view.enabled}
          />
        </Row>
      )
    }
    if (key === 'copree_group_id' && view.supports_group) {
      return (
        <Row key={key} label={t('tool:channel.landingLabel')} hint={t('tool:channel.groupHint')}>
          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex gap-1 w-fit shrink-0">
              {(['existing', 'new'] as const).map(m => (
                <button
                  key={m}
                  onClick={() => setGroupMode(prev => ({ ...prev, [activeId]: m }))}
                  disabled={!view.enabled}
                  className={`btn btn-xs btn-outline ${mode === m ? 'is-active' : ''}`}
                >
                  {t(m === 'existing' ? 'tool:channel.groupModeExisting' : 'tool:channel.groupModeNew')}
                </button>
              ))}
            </div>
            {mode === 'existing' ? (
              <Select
                value={draft[key] || ''}
                onChange={e => setField(key, e.target.value)}
                options={[{ value: '', label: t('tool:channel.groupNone') },
                          ...groups.map(g => ({ value: String(g.id), label: g.name }))]}
                fieldSize="sm"
                className="w-56"
                disabled={!view.enabled}
              />
            ) : (
              <Input
                value={newGroupName[activeId] || ''}
                onChange={e => setNewGroupName(prev => ({ ...prev, [activeId]: e.target.value }))}
                placeholder={t('tool:channel.newGroupPlaceholder')}
                name="copree-channel-new-group"
                fieldSize="sm"
                className="w-56"
                disabled={!view.enabled}
                {...NO_AUTOFILL}
              />
            )}
          </div>
          {mode === 'new' && <p className="text-3xs text-textMuted leading-relaxed">{t('tool:channel.newGroupHint')}</p>}
          {mode === 'existing' && groups.length === 0 && (
            <p className="text-3xs text-amber-600 dark:text-amber-400">{t('tool:channel.groupEmptyHint')}</p>
          )}
        </Row>
      )
    }
    // 布尔字段：勾选框。独立维度用它（比如"引用回复"），跟单选的下拉互不影响
    if (spec.type === 'boolean') {
      return (
        <Row key={key} label={label} hint={hint}>
          <label className="inline-flex items-center gap-2 text-xs text-textSecondary cursor-pointer select-none">
            <input
              type="checkbox"
              className="w-3.5 h-3.5 rounded-control border border-border bg-canvas accent-primary-500"
              checked={(draft[key] || 'true') === 'true'}
              onChange={e => setField(key, e.target.checked ? 'true' : 'false')}
              disabled={!view.enabled}
            />
            {t('tool:channel.switchOn')}
          </label>
        </Row>
      )
    }
    // 插件声明了选项（enum）就用下拉：平台不认识这个字段，也不该猜它的取值
    if (spec.options && spec.options.length > 0) {
      return (
        <Row key={key} label={label} hint={hint}>
          <Select
            value={draft[key] ?? ''}
            onChange={e => setField(key, e.target.value)}
            options={spec.options.map(o => ({
              value: o.value,
              label: pick(lang, o.label, o.label_en, o.label_ja) || o.value,
            }))}
            fieldSize="sm"
            className="w-56"
            disabled={!view.enabled}
          />
        </Row>
      )
    }
    if (spec.secret) {
      return (
        <Row key={key} label={label} hint={hint}>
          <SecretInput
            value={secretDraft[key] || ''}
            onChange={e => setSecret(key, e.target.value)}
            placeholder={view.secrets?.[key] ? t('tool:channel.secretSaved') : ''}
            name={'copree-channel-' + key}
            fieldSize="sm"
            disabled={!view.enabled}
            {...NO_AUTOFILL}
            autoComplete="new-password"
          />
        </Row>
      )
    }
    return (
      <Row key={key} label={label} hint={hint}>
        <Input
          value={draft[key] || ''}
          onChange={e => setField(key, e.target.value)}
          name={'copree-channel-' + key}
          fieldSize="sm"
          disabled={!view.enabled}
          {...NO_AUTOFILL}
        />
      </Row>
    )
  }

  return (
    <div className="space-y-4">
      {showTitle && (
        <div className="flex items-center gap-2 flex-wrap">
          <MessageSquare size={15} className="text-primary-400 shrink-0" />
          <span className="text-sm font-semibold text-textPrimary">{pick(lang, view.label, view.label_en, view.label_ja)}</span>
          <span className="text-3xs text-textMuted truncate">{pick(lang, view.desc, view.desc_en, view.desc_ja)}</span>
        </div>
      )}

      {/* 子项：一个 AI 接了几条通道就有几个（几条由插件声明，不是写死的） */}
      {/* 通道切换：统一用 UnderlineTabs（与商城/能力/OpenCLI 页签同一套画法，
          切换时那条下划线是共享指示器，会平移过去而不是各自闪一下） */}
      {views.length > 1 && (
        <UnderlineTabs
          size="sm"
          items={views.map(v => ({
            key: v.plugin_id,
            label: pick(lang, v.label, v.label_en, v.label_ja),
            icon: <span className={'w-1.5 h-1.5 rounded-full shrink-0 ' + DOT[statusOf(v)]} />,
          }))}
          value={view.plugin_id}
          onChange={(k) => { setActiveId(k); setMsg(null) }}
        />
      )}

      {/* 状态条：左边状态，右边启停 */}
      <div className="flex items-center gap-2 flex-wrap rounded-card border border-border bg-canvas/50 px-4 py-2.5">
        <span className={'w-2 h-2 rounded-full shrink-0 ' + DOT[status.tone]} />
        <span className={'text-xs ' + TEXT[status.tone]}>{status.text}</span>
        {view.configured && <span className="chip chip-muted">{view.instance}</span>}
        <span className="flex-1" />
        {view.self_test && view.running && (
          <Button size="sm" variant="secondary" loading={busy === 'self-test'} onClick={selfTest}>
            {t('tool:channel.selfTest')}
          </Button>
        )}
        {view.enabled && view.configured && (view.running
          ? <Button size="sm" variant="secondary" loading={busy === 'stop'} onClick={stop}>{t('tool:channel.stop')}</Button>
          : <Button size="sm" variant="primary" loading={busy === 'start'} onClick={start}>{t('tool:channel.start')}</Button>)}
      </div>

      {view.self_test && view.running && (
        <p className="text-3xs text-textMuted leading-relaxed px-1">{t('tool:channel.selfTestHint')}</p>
      )}

      {!view.enabled && (
        <div className="flex items-start gap-2 text-xs rounded-card border border-border bg-canvas px-4 py-3 text-textMuted">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span>{t('tool:channel.disabledHint')}</span>
        </div>
      )}

      {/* 托管协议端：码由平台画出来，用户不用去别处找登录入口 */}
      {hosted?.held_by_platform && (
        <Card title={t('tool:channel.hostedTitle')} icon={<Plug size={14} className="text-primary-400" />}>
          {qrError && <p className="text-xs text-rose-400 leading-relaxed mb-2">{qrError}</p>}
          {hosted.logged_in ? (
            <p className="text-xs text-mint-400">
              {hosted.bot_name?.trim()
                ? t('tool:channel.hostedLoggedIn', { name: hosted.bot_name.trim() })
                : t('tool:channel.hostedLoggedInNoName')}
            </p>
          ) : qrShot ? (
            // 手里这一张一直留着：过期只标"已过期"并压暗，不收起——收起会让人以为要重来
            <div className="flex items-start gap-4 flex-wrap">
              <img
                src={'data:image/png;base64,' + qrShot.png}
                alt=""
                className={'w-40 h-40 rounded-control border border-border bg-white p-1 shrink-0 transition-opacity ' +
                  (qrLeft > 0 ? '' : 'opacity-40')}
              />
              <div className="space-y-2 min-w-0 flex-1">
                <p className="text-xs leading-relaxed">
                  <span className={qrLeft > 0 ? 'text-textSecondary' : 'text-amber-400'}>
                    {qrLeft > 0 ? t('tool:channel.hostedScan') : t('tool:channel.hostedQrExpired')}
                  </span>
                  {qrLeft > 0 && (
                    <span className={'ml-1 ' + (qrLeft <= 15 ? 'text-amber-400' : 'text-textMuted')}>
                      {t('tool:channel.hostedQrLeft', { s: String(qrLeft) })}
                    </span>
                  )}
                </p>
                {qrShot.url && (
                  <>
                    <p className="text-3xs text-textMuted leading-relaxed">{t('tool:channel.hostedLinkHint')}</p>
                    <input
                      readOnly
                      value={qrShot.url}
                      onFocus={e => e.currentTarget.select()}
                      className="field field-sm font-mono w-full max-w-[24rem] text-textSecondary"
                    />
                  </>
                )}
                <div className="flex items-center gap-2 flex-wrap pt-0.5">
                  <Button size="sm" variant="secondary" onClick={requestQr} disabled={qrCooldown > 0}>
                    <RefreshCw size={13} className="mr-1.5" />
                    {qrCooldown > 0
                      ? t('tool:channel.hostedQrCooldown', { s: String(qrCooldown) })
                      : t('tool:channel.hostedQrRefresh')}
                  </Button>
                  <span className="text-3xs text-textMuted">{t('tool:channel.hostedQrLimit')}</span>
                </div>
                {hosted.qr_state === 'stalled' && (
                  <p className="text-xs text-amber-400 leading-relaxed">{t('tool:channel.hostedQrStalled')}</p>
                )}
              </div>
            </div>
          ) : hosted.qr_state === 'stalled' ? (
            // 协议端自己停了出码（扫码被拒 ErrCode 5 / 会话异常 ErrCode 1）：这时候点也等不到
            <div className="space-y-2">
              <p className="text-xs text-amber-400 leading-relaxed">{t('tool:channel.hostedQrStalled')}</p>
              <div className="flex items-center gap-2 flex-wrap">
                <input
                  readOnly
                  value="docker restart napcat"
                  onFocus={e => e.currentTarget.select()}
                  className="field field-sm font-mono w-full max-w-[24rem]"
                />
                <Button size="sm" variant="ghost" onClick={requestQr} disabled={qrCooldown > 0}>
                  {t('tool:channel.hostedQrRetry')}
                </Button>
              </div>
            </div>
          ) : qrPending ? (
            <p className="text-xs text-textMuted flex items-center gap-1.5">
              <Loader2 size={13} className="animate-spin" />
              {t('tool:channel.hostedWaiting', { s: String(qrWaited) })}
            </p>
          ) : (
            // 默认不摆码：先让用户自己决定什么时候去要一张
            <div className="space-y-2">
              <p className="text-xs text-textSecondary leading-relaxed">{t('tool:channel.hostedGetQrHint')}</p>
              <Button size="sm" variant="secondary" onClick={requestQr} disabled={qrCooldown > 0}>
                <QrCode size={14} className="mr-1.5" />
                {qrCooldown > 0
                  ? t('tool:channel.hostedQrCooldown', { s: String(qrCooldown) })
                  : t('tool:channel.hostedGetQr')}
              </Button>
            </div>
          )}
        </Card>
      )}

      {/* 还没配过才显示开通指引；配好、或协议端由平台托管（那段话是"自己跑一个"）就不显示 */}
      {view.enabled && !view.configured && !hosted?.held_by_platform && view.guide.length > 0 && (
        <Card title={t('tool:channel.guideTitle')} icon={<Plug size={14} className="text-primary-400" />}>
          <ol className="text-xs text-textSecondary space-y-2 list-decimal list-inside">
            {view.guide.map((step, i) => (
              <li key={i}>
                {step.url ? (
                  <a href={step.url} target="_blank" rel="noreferrer" className="text-primary-400 hover:text-primary-500 inline-flex items-center gap-1">
                    {pick(lang, step.text, step.text_en, step.text_ja)} <ExternalLink size={11} />
                  </a>
                ) : pick(lang, step.text, step.text_en, step.text_ja)}
              </li>
            ))}
          </ol>
        </Card>
      )}

      {credentialFields.length > 0 && (
        <Card title={t('tool:channel.credentialTitle')} icon={<KeyRound size={14} className="text-primary-400" />}>
          {credentialFields.map(([key, spec]) => renderField(key, spec))}
          <div className="flex justify-end">
            <Button size="sm" variant="primary" loading={busy === 'cred'} onClick={saveCredential}>
              {t('tool:channel.saveCredential')}
            </Button>
          </div>
        </Card>
      )}

      {/* 配对：三份名单各自分页，人再多也不把卡顶长 */}
      {view.pairing && (
        <Card title={t('tool:channel.pairTitle')} icon={<UserCheck size={14} className="text-primary-400" />} hint={t('tool:channel.pairHint')}>
          <PairingBlock
            pending={view.pending}
            approved={view.approved}
            blocked={view.blocked}
            busy={busy}
            approve={approve}
            approveByCode={approveByCode}
            block={block}
            forget={forget}
          />
        </Card>
      )}

      <Card title={t('tool:channel.configTitle')}>
        {plainFields.map(([key, spec]) => renderField(key, spec))}
        {/* 分块走页签：一次只看一块，页签本身就是"你在看哪一段" */}
        {sectionTabs.length > 0 && (
          <div className="pt-1 mt-1 border-t border-border/60">
            <UnderlineTabs
              items={sectionTabs}
              value={activeSection}
              onChange={setConfigSection}
              size="sm"
            />
          </div>
        )}
        {(sectionFields[activeSection] || []).map(([key, spec]) => renderField(key, spec))}
        {/* 最近见到过的群：插件在状态里报了才画（NapCat 还没报，就没有这一块） */}
        {recentField && (
          <div className="space-y-1.5">
            <div className="text-3xs text-textMuted">{t('tool:channel.recentGroups')}</div>
            {recent.length === 0 ? (
              <div className="text-3xs text-textMuted">{t('tool:channel.recentGroupsEmpty')}</div>
            ) : recentList.rows.map(g => (
              <div key={g.origin} className="grid grid-cols-[auto_1fr_auto_auto_auto] items-center gap-2 bg-canvas border border-border rounded-control px-3 py-2">
                <span className={'w-1.5 h-1.5 rounded-full shrink-0 ' + (g.allowed ? 'bg-mint-400' : 'bg-amber-400')} />
                <div className="min-w-0">
                  {/* 通道那边报过群名就显示真名（下一行留 openid 当身份）；没有就只显示 openid */}
                  <div className={g.name ? 'text-3xs text-textPrimary truncate' : 'text-3xs text-textPrimary truncate font-mono'}>
                    {g.name || g.origin}
                  </div>
                  <div className="text-3xs text-textMuted truncate">
                    {g.count > 0
                      ? t('tool:channel.recentGroupMeta', { count: String(g.count), time: new Date(g.last_at * 1000).toLocaleString() })
                      : t('tool:channel.recentGroupNoMessages')}
                    {g.name ? ' · ' + g.origin : ''}
                    {g.member_num ? ' · ' + t('tool:channel.recentGroupMembers', { n: String(g.member_num) }) : ''}
                  </div>
                </div>
                {/* 这个群落到哪个 Copree 群：一个群一个落点，选完立即保存（映射表说了算） */}
                <Select
                  value={mapDraft[activeId]?.[g.origin]
                    ?? (newFor[g.origin] !== undefined
                      ? '__new__'
                      : String((view.detail?.group_map as Record<string, string> | undefined)?.[g.origin] || ''))}
                  onChange={e => (e.target.value === '__new__'
                    ? setNewFor(prev => ({ ...prev, [g.origin]: '' }))
                    : saveLanding(g.origin, e.target.value))}
                  options={[{ value: '', label: t('tool:channel.landingDefault') },
                            ...groups.map(x => ({ value: String(x.id), label: x.name })),
                            { value: '__new__', label: t('tool:channel.groupModeNew') }]}
                  fieldSize="sm"
                  className="w-44"
                  disabled={!view.enabled}
                />
                {/* 「可能是同一个群」子列：认到就地合并过去（同主人的别的机器人已经把同名的群接在哪儿） */}
                <div className="flex items-center gap-2 justify-end">
                  {g.possible_group ? (
                    <>
                      <span className="text-3xs text-textSecondary truncate max-w-[9rem]" title={g.possible_group.name}>
                        {t('tool:channel.possibleSame')}：{g.possible_group.name}
                      </span>
                      <Button size="xs" variant="secondary" loading={busy === 'merge:' + g.origin}
                        onClick={() => mergeInto(g.origin, g.possible_group?.id ?? 0)}>
                        {t('tool:channel.mergeInto')}
                      </Button>
                    </>
                  ) : (
                    <span className="text-3xs text-textMuted">—</span>
                  )}
                </div>
                {g.allowed
                  ? <span className="text-3xs text-mint-400 shrink-0">{t('tool:channel.allowListed')}</span>
                  : <Button size="xs" variant="secondary" loading={busy === 'allow:' + g.origin} onClick={() => addToList(g.origin)}>{t('tool:channel.allowAdd')}</Button>}
                {newFor[g.origin] !== undefined && (
                  <div className="col-span-full flex items-center gap-2">
                    <Input
                      value={newFor[g.origin]}
                      onChange={e => setNewFor(prev => ({ ...prev, [g.origin]: e.target.value }))}
                      placeholder={t('tool:channel.newGroupPlaceholder')}
                      name="qq-group-new-copree-group"
                      fieldSize="sm"
                      className="w-56"
                      disabled={!view.enabled}
                      {...NO_AUTOFILL}
                    />
                    <Button size="xs" variant="primary" loading={busy === 'newmap:' + g.origin}
                      onClick={() => createLandingFor(g.origin)}>{t('tool:channel.groupModeNew')}</Button>
                  </div>
                )}
              </div>
            ))}
            {recentList.pager}
          </div>
        )}
      </Card>

      {/* 对话额度：设置属于插件（上面 schema 表单），这里展示每个对话的账与单独调整 */}
      <ChannelTokenBudgetCard agentId={agentId} pluginId={view.plugin_id} />

      {/* 使用轨迹：这个 AI 拿它那个账号做过什么（插件自己记的，平台只渲染） */}
      {activity && (
        <Card title={t('tool:channel.activity.title')} icon={<History size={14} className="text-primary-400" />}>
          {activity.items.length === 0 ? (
            <p className="text-3xs text-textMuted">{t('tool:channel.activity.empty')}</p>
          ) : (
            <>
              <div className="max-h-72 overflow-auto pr-1 space-y-1.5">
                {activity.items.map((row, i) => (
                  <div key={i} className="flex items-start gap-2 text-3xs">
                    <span
                      className={'mt-1 w-1.5 h-1.5 rounded-full shrink-0 ' + (row.ok ? 'bg-mint-400' : 'bg-rose-400')}
                      title={t(row.ok ? 'tool:channel.activity.ok' : 'tool:channel.activity.failed')}
                    />
                    <span className="text-textMuted tabular-nums whitespace-nowrap">{row.at}</span>
                    <span className="text-textSecondary whitespace-nowrap">
                      {activityRaw ? row.action : trailLabel(row.action)}
                    </span>
                    <span className={(activityRaw ? 'text-textMuted font-mono' : 'text-textPrimary') + ' break-words min-w-0 flex-1'}>
                      {(activityRaw ? row.raw : trailArgs(row.action, row.raw)) || '—'}
                    </span>
                    {row.ms > 0 && (
                      <span className="text-textMuted tabular-nums whitespace-nowrap">{row.ms} ms</span>
                    )}
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-between gap-2 mt-2">
                <p className="text-3xs text-textMuted">{t('tool:channel.activity.count', { n: activity.count })}</p>
                <button
                  type="button"
                  onClick={() => setActivityRaw(v => !v)}
                  className="text-3xs text-textMuted hover:text-primary-400 transition-colors"
                >
                  {t(activityRaw ? 'tool:channel.activity.hideRaw' : 'tool:channel.activity.showRaw')}
                </button>
              </div>
            </>
          )}
        </Card>
      )}

      {/* 能力与限制：插件自带（平台不替它总结），用户配之前就该知道能做到什么 */}
      {view.limits.length > 0 && (
        <Card title={t('tool:channel.limitsTitle')} icon={<AlertTriangle size={14} className="text-amber-500" />}>
          <ul className="text-3xs text-textSecondary space-y-1.5 list-disc list-inside">
            {view.limits.map((item, i) => (
              <li key={i}>{pick(lang, item.text, item.text_en, item.text_ja)}</li>
            ))}
          </ul>
        </Card>
      )}

      {/* 动作条：保存 + 缺口 / 报错，都在按钮这一行，视线不用来回跳 */}
      <div className="flex items-center gap-3 flex-wrap">
        <Button
          size="sm"
          variant="primary"
          loading={busy === 'save'}
          onClick={save}
          disabled={!view.enabled || !filled}
        >
          {t('tool:channel.save')}
        </Button>
        {missingText && <span className="text-3xs text-amber-600 dark:text-amber-400">{t('tool:channel.missing', { fields: missingText })}</span>}
        {!!view.detail?.last_error && <span className="text-3xs text-rose-400">{t('tool:channel.lastError', { error: String(view.detail.last_error) })}</span>}
      </div>

      {msg && (
        <div className={'flex items-center gap-2 text-xs rounded-card border px-3 py-2 ' + (msg.tone === 'ok' ? 'border-mint-500/30 bg-mint-500/10 text-mint-400' : 'border-rose-500/30 bg-rose-500/10 text-rose-400')}>
          {msg.tone === 'ok' ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
          <span className="min-w-0 truncate">{msg.text}</span>
        </div>
      )}

    </div>
  )
}
