import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, ExternalLink, Loader2, MessageSquare, Plug, UserCheck } from 'lucide-react'
import { api } from '../../api/client'
import { useLang, useT } from '../../i18n/I18nContext'
import type { Lang } from '../../i18n/languages'
import { Button, Card, Input, Select, confirmAsync } from '../ui'

/**
 * 「通道」页签 —— 一个 AI 接几条外部通道，由**插件自己声明**（后端 catalog.channels()）：
 * 现在有 QQ 官方与 NapCat 两条，将来第三方通道插件装上就多一条子项。
 *
 * 表单不写死字段：按插件给的 config_schema 渲染，平台只保留三个"约定字段"的特殊控件
 * （target_agent 由平台填、copree_group_id 选落点群、dm_policy 选私聊策略）。
 * 这样 NapCat 那种完全不同的凭据（ws_url / access_token）不用在平台里再加一套代码。
 */
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
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({})
  const [secrets, setSecrets] = useState<Record<string, Record<string, string>>>({})
  const [groupMode, setGroupMode] = useState<Record<string, 'existing' | 'new'>>({})
  const [newGroupName, setNewGroupName] = useState<Record<string, string>>({})
  const [codeInput, setCodeInput] = useState('')

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
          if (spec.secret) secrets[key] = ''
          else fields[key] = String(v.values?.[key] ?? '')
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

  // 托管协议端还没登录时每 5 秒重拉一次：NapCat 的二维码会轮换、登录完成后面板要自己消失，
  // 用户不该为了"码过期了"手动刷新页面
  const hostedPending = Boolean((views.find(v => v.plugin_id === activeId)?.detail?.hosted_endpoint as any)?.held_by_platform
    && !(views.find(v => v.plugin_id === activeId)?.detail?.hosted_endpoint as any)?.logged_in)
  useEffect(() => {
    if (!hostedPending) return
    const timer = setInterval(() => { void load() }, 5000)
    return () => clearInterval(timer)
  }, [hostedPending, load])

  /** 所有动作共用一个出口：忙标记、提示、成功后重拉 */
  const act = async (key: string, fn: () => Promise<any>, okText: string) => {
    setBusy(key)
    setMsg(null)
    try {
      const res: any = await fn()
      setMsg(res?.warning ? { tone: 'err', text: res.warning } : { tone: 'ok', text: okText })
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

  /** 最近见到过的通道侧标识：一键进那个"白名单"字段（字段名由插件在状态里报，平台不猜） */
  const recentField = String(view?.detail?.recent_field || '')
  const recent: { origin: string; last_at: number; count: number; allowed: boolean }[] = (view?.detail?.recent_groups as any[]) || []
  const allowList = recentField ? String(draft[recentField] || '').split(/[,，\s]+/).filter(Boolean) : []
  const addToList = (origin: string) => {
    if (!recentField || allowList.includes(origin)) return
    const next = [...allowList, origin].join(',')
    setField(recentField, next)
    // 还没配好或正打算新建群时只填进输入框，让用户自己点保存（别顺手改了落点）
    if (!view?.configured || mode === 'new') return
    return act('allow:' + origin, () => api.put(base, { values: { [recentField]: next } }), t('tool:channel.allowAdded'))
  }

  const start = () => act('start', () => api.post(base + '/start'), t('tool:channel.started'))
  const stop = () => act('stop', () => api.post(base + '/stop'), t('tool:channel.stopped'))
  const approve = (id: number) => act('pair' + id, () => api.post(base + '/pairings/approve', { pairing_id: id }), t('tool:channel.approved'))
  const approveByCode = () => act('code', () => api.post(base + '/pairings/approve', { code: codeInput.trim() }), t('tool:channel.approved'))
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
      qr_url?: string } | null
  const filled = Object.values(draft).some(v => v.trim()) || Object.values(secretDraft).some(v => v.trim())

  /** 一个字段一个控件：平台只对三个约定字段用特殊控件，其余按 schema 渲染 */
  const renderField = (key: string, spec: FieldSpec) => {
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
            <div className="flex gap-1 bg-elevated rounded-control p-0.5 w-fit shrink-0">
              {(['existing', 'new'] as const).map(m => (
                <button
                  key={m}
                  onClick={() => setGroupMode(prev => ({ ...prev, [activeId]: m }))}
                  disabled={!view.enabled}
                  className={'text-2xs px-3 py-1.5 rounded-control transition-colors ' +
                    (mode === m ? 'bg-surface text-textPrimary shadow-sm' : 'text-textSecondary hover:text-textPrimary')}
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
    if (spec.secret) {
      return (
        <Row key={key} label={label} hint={hint}>
          <Input
            type="password"
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
      {views.length > 1 && (
        <div className="flex items-center gap-1 bg-elevated rounded-control p-0.5 w-fit flex-wrap">
          {views.map(v => (
            <button
              key={v.plugin_id}
              onClick={() => { setActiveId(v.plugin_id); setMsg(null) }}
              className={'flex items-center gap-1.5 text-2xs px-3 py-1.5 rounded-control transition-colors ' +
                (v.plugin_id === view.plugin_id ? 'bg-surface text-textPrimary shadow-sm' : 'text-textSecondary hover:text-textPrimary')}
            >
              <span className={'w-1.5 h-1.5 rounded-full shrink-0 ' + DOT[statusOf(v)]} />
              {pick(lang, v.label, v.label_en, v.label_ja)}
            </button>
          ))}
        </div>
      )}

      {/* 状态条：左边状态，右边启停 */}
      <div className="flex items-center gap-2 flex-wrap rounded-card border border-border bg-canvas/50 px-4 py-2.5">
        <span className={'w-2 h-2 rounded-full shrink-0 ' + DOT[status.tone]} />
        <span className={'text-xs ' + TEXT[status.tone]}>{status.text}</span>
        {view.configured && <span className="chip chip-muted">{view.instance}</span>}
        <span className="flex-1" />
        {view.enabled && view.configured && (view.running
          ? <Button size="sm" variant="secondary" loading={busy === 'stop'} onClick={stop}>{t('tool:channel.stop')}</Button>
          : <Button size="sm" variant="primary" loading={busy === 'start'} onClick={start}>{t('tool:channel.start')}</Button>)}
      </div>

      {!view.enabled && (
        <div className="flex items-start gap-2 text-xs rounded-card border border-border bg-canvas px-4 py-3 text-textMuted">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span>{t('tool:channel.disabledHint')}</span>
        </div>
      )}

      {/* 托管协议端：码由平台画出来，用户不用去别处找登录入口 */}
      {hosted?.held_by_platform && (
        <Card title={t('tool:channel.hostedTitle')} icon={<Plug size={14} className="text-primary-400" />}>
          {hosted.logged_in ? (
            <p className="text-xs text-mint-400">{t('tool:channel.hostedLoggedIn', { name: hosted.bot_name || '—' })}</p>
          ) : hosted.qr_png ? (
            <div className="flex items-start gap-4 flex-wrap">
              <img
                src={'data:image/png;base64,' + hosted.qr_png}
                alt=""
                className="w-40 h-40 rounded-control border border-border bg-white p-1 shrink-0"
              />
              <div className="space-y-2 min-w-0 flex-1">
                <p className="text-xs text-textSecondary leading-relaxed">{t('tool:channel.hostedScan')}</p>
                {hosted.qr_url && (
                  <>
                    <p className="text-3xs text-textMuted leading-relaxed">{t('tool:channel.hostedLinkHint')}</p>
                    <input
                      readOnly
                      value={hosted.qr_url}
                      onFocus={e => e.currentTarget.select()}
                      className="text-3xs font-mono bg-canvas border border-border rounded-control px-2 py-1.5 w-full max-w-[24rem] text-textSecondary"
                    />
                  </>
                )}
              </div>
            </div>
          ) : (
            <p className="text-xs text-textMuted">{t('tool:channel.hostedWaiting')}</p>
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

      <Card title={t('tool:channel.configTitle')}>
        {fields.map(([key, spec]) => renderField(key, spec))}
        {/* 最近见到过的群：插件在状态里报了才画（NapCat 还没报，就没有这一块） */}
        {recentField && (
          <div className="space-y-1.5">
            <div className="text-3xs text-textMuted">{t('tool:channel.recentGroups')}</div>
            {recent.length === 0 ? (
              <div className="text-3xs text-textMuted">{t('tool:channel.recentGroupsEmpty')}</div>
            ) : recent.slice(0, 6).map(g => (
              <div key={g.origin} className="grid grid-cols-[auto_1fr_auto] items-center gap-2 bg-canvas border border-border rounded-control px-3 py-2">
                <span className={'w-1.5 h-1.5 rounded-full shrink-0 ' + (g.allowed ? 'bg-mint-400' : 'bg-amber-400')} />
                <div className="min-w-0">
                  <div className="text-3xs text-textPrimary truncate font-mono">{g.origin}</div>
                  <div className="text-3xs text-textMuted">
                    {t('tool:channel.recentGroupMeta', { count: String(g.count), time: new Date(g.last_at * 1000).toLocaleString() })}
                  </div>
                </div>
                {g.allowed
                  ? <span className="text-3xs text-mint-400 shrink-0">{t('tool:channel.allowListed')}</span>
                  : <Button size="xs" variant="secondary" loading={busy === 'allow:' + g.origin} onClick={() => addToList(g.origin)}>{t('tool:channel.allowAdd')}</Button>}
              </div>
            ))}
          </div>
        )}
      </Card>

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

      {view.pairing && (
        <Card title={t('tool:channel.pairTitle')} icon={<UserCheck size={14} className="text-primary-400" />} hint={t('tool:channel.pairHint')}>
          {view.pending.length === 0 ? (
            <div className="text-3xs text-textMuted">{t('tool:channel.pendingEmpty')}</div>
          ) : (
            <div className="space-y-1.5">
              {view.pending.map(p => (
                <div key={p.id} className="grid grid-cols-[1fr_auto_auto] items-center gap-2 bg-canvas border border-border rounded-control px-3 py-2">
                  <div className="min-w-0">
                    <div className="text-xs text-textPrimary truncate">{p.nickname || t('tool:channel.unknownUser')}</div>
                    <div className="text-3xs text-textMuted">{t('tool:channel.codeLabel', { code: p.code, origin: tail(p.origin) })}</div>
                  </div>
                  <Button size="xs" variant="primary" loading={busy === 'pair' + p.id} onClick={() => approve(p.id)}>{t('tool:channel.approve')}</Button>
                  <Button size="xs" variant="ghost" onClick={() => block(p.origin)}>{t('tool:channel.block')}</Button>
                </div>
              ))}
            </div>
          )}

          <div className="flex items-center gap-2">
            <Input
              value={codeInput}
              onChange={e => setCodeInput(e.target.value.toUpperCase())}
              placeholder={t('tool:channel.byCodePlaceholder')}
              name="copree-channel-pair-code"
              fieldSize="sm"
              className="w-44"
              {...NO_AUTOFILL}
            />
            <Button size="sm" variant="secondary" loading={busy === 'code'} onClick={approveByCode} disabled={!codeInput.trim()}>
              {t('tool:channel.byCode')}
            </Button>
          </div>

          {(view.approved.length > 0 || view.blocked.length > 0) && (
            <div className="grid gap-1">
              {view.approved.map(a => (
                <div key={a.id} className="grid grid-cols-[auto_1fr_auto] items-center gap-2 text-3xs text-textSecondary">
                  <span className="w-1.5 h-1.5 rounded-full bg-mint-400" />
                  <span className="min-w-0 truncate">{t('tool:channel.ownerRow', { name: a.nickname || tail(a.origin) })}</span>
                  <button onClick={() => forget(a.origin, a.nickname || tail(a.origin))} className="text-textMuted hover:text-rose-400 transition-colors">{t('tool:channel.forget')}</button>
                </div>
              ))}
              {view.blocked.map(b => (
                <div key={b.id} className="grid grid-cols-[auto_1fr_auto] items-center gap-2 text-3xs text-textMuted">
                  <span className="w-1.5 h-1.5 rounded-full bg-gray-400" />
                  <span className="min-w-0 truncate">{t('tool:channel.blockedRow', { name: b.nickname || tail(b.origin) })}</span>
                  <button onClick={() => forget(b.origin, b.nickname || tail(b.origin))} className="hover:text-primary-400 transition-colors">{t('tool:channel.unblock')}</button>
                </div>
              ))}
            </div>
          )}

          <p className="text-3xs text-textMuted leading-relaxed">{t('tool:channel.privacy')}</p>
        </Card>
      )}
    </div>
  )
}
