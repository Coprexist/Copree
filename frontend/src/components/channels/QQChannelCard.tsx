import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, ExternalLink, Loader2, MessageSquare, Plug, UserCheck } from 'lucide-react'
import { api } from '../../api/client'
import { useT } from '../../i18n/I18nContext'
import { Button, Card, Input, Select, confirmAsync } from '../ui'

/**
 * 「QQ 通道」弹窗里的内容 —— 用户给自己的 AI 接一个 QQ 机器人。
 *
 * 三种身份要分清：AI 主人（配置这条通道的人）、QQ 上的对话者（官方只给 openid，拿不到 QQ 号）、
 * 机器人本身（AppID/Secret 由主人自己去腾讯开放平台申请）。所以「认人」只能靠配对：
 * 陌生人私聊只领一个配对码，主人在这里批准。
 *
 * 排版：整块拆成几张 Card（凭据 / 消息落点 / 私聊 / 配对），卡内统一用 Row 做
 * "左标签右控件"的两列对齐——它现在有自己的弹窗，不用再为省地方挤成一列。
 *
 * 凭据输入框要挡住浏览器的账号密码自动填充（Chrome 会把第一个文本框当用户名、密码框当密码自动填）：
 * 靠 autoComplete + 非凭据类 name + 常见密码管理器的忽略标记。
 */
interface ChannelView {
  plugin_id: string
  instance: string
  enabled: boolean
  schema: Record<string, { title?: string; description?: string; secret?: boolean; required?: boolean; managed?: boolean }>
  values: Record<string, unknown>
  secrets: Record<string, boolean>
  running: boolean
  detail: Record<string, any>
  missing_required: string[]
  configured: boolean
  owner: { openid: string; nickname: string; approved_at: string | null } | null
  pending: { id: number; openid: string; nickname: string; code: string; created_at: string | null }[]
  approved: { id: number; openid: string; nickname: string; approved_at: string | null }[]
  blocked: { id: number; openid: string; nickname: string }[]
  /** 能接的 Copree 群：你管理的 + 这个 AI 已经在里面的（后端校验，前端只做选项） */
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
const DOT: Record<string, string> = { mint: 'bg-mint-400', muted: 'bg-gray-400', accent: 'bg-amber-400', rose: 'bg-rose-400' }
const TEXT: Record<string, string> = { mint: 'text-mint-400', muted: 'text-textMuted', accent: 'text-amber-600 dark:text-amber-400', rose: 'text-rose-400' }
const tail = (openid: string) => (openid.length > 6 ? '…' + openid.slice(-6) : openid)

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
 * :param showTitle 卡片自己要不要亮出"QQ 通道"这块牌子。
 *   页面里（AI 详情 → 通道页签）要，不然只看到一堆字段不知道在看哪条通道；
 *   弹窗里不要——弹窗标题栏已经写了，再写一遍是重复。
 */
export default function QQChannelCard({ agentId, showTitle = true }: { agentId: number; showTitle?: boolean }) {
  const t = useT()
  const [view, setView] = useState<ChannelView | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)
  const [appId, setAppId] = useState('')
  const [secret, setSecret] = useState('')
  const [policy, setPolicy] = useState('pairing')
  const [groupMode, setGroupMode] = useState<'existing' | 'new'>('existing')
  const [groupId, setGroupId] = useState('')
  const [newGroupName, setNewGroupName] = useState('')
  const [qqAllow, setQqAllow] = useState('')
  const [codeInput, setCodeInput] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api.get<{ channels: ChannelView[] }>('/me/agents/' + agentId + '/channels')
      const first = (r.channels || [])[0] || null
      setView(first)
      if (first) {
        setAppId(String(first.values?.app_id || ''))
        setPolicy(String(first.values?.dm_policy || 'pairing'))
        setGroupId(String(first.values?.copree_group_id || ''))
        setGroupMode(first.values?.copree_group_id ? 'existing' : 'new')
        setQqAllow(String(first.values?.qq_group_allowlist || ''))
      }
    } catch (e: any) {
      setMsg({ tone: 'err', text: e?.message || String(e) })
    } finally {
      setLoading(false)
    }
  }, [agentId])

  useEffect(() => { load() }, [load])

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

  const base = '/me/agents/' + agentId + '/channels/qq'

  /** 保存：选了「新建群」就先在 Copree 建一个当落点，再写进通道配置 */
  const save = () => act('save', async () => {
    let landing = groupMode === 'existing' ? groupId.trim() : ''
    if (groupMode === 'new') {
      const created = await api.post<{ id: number; name: string }>(base + '/landing-group', { name: newGroupName.trim() })
      landing = String(created.id)
      setGroupId(landing)
      setGroupMode('existing')
    }
    const values: Record<string, string> = {
      app_id: appId.trim(),
      dm_policy: policy,
      copree_group_id: landing,        // 空串 = 只做私聊
      qq_group_allowlist: qqAllow.trim(),
    }
    // 机密留空 = 不改：后端的语义是"空串=清除"，把空值回传过去会把已存的 Secret 抹掉
    if (secret.trim()) values.client_secret = secret.trim()
    return api.put(base, { values })
  }, t('tool:channel.saved'))

  /** 一键加白名单：openid 并进输入框，再按当前配置存一次（不触发"新建群"那类副作用） */
  const addToAllowlist = (openid: string) => {
    const list = qqAllow.split(/[,，\s]+/).filter(Boolean)
    if (list.includes(openid)) return
    const next = [...list, openid].join(',')
    setQqAllow(next)
    // 还没配好或正打算新建群时，只填进输入框，让用户自己点保存（别顺手改了落点）
    if (!view?.configured || groupMode === 'new') return
    // 只交这一个键：部分保存不碰凭据、不碰落点（后端 set_config 是"没传的键不动"）
    return act('allow:' + openid, () => api.put(base, {
      values: { qq_group_allowlist: next },
    }), t('tool:channel.allowAdded'))
  }

  const start = () => act('start', () => api.post(base + '/start'), t('tool:channel.started'))
  const stop = () => act('stop', () => api.post(base + '/stop'), t('tool:channel.stopped'))
  const approve = (id: number) => act('pair' + id, () => api.post(base + '/pairings/approve', { pairing_id: id }), t('tool:channel.approved'))
  const approveByCode = () => act('code', () => api.post(base + '/pairings/approve', { code: codeInput.trim() }), t('tool:channel.approved'))
  const block = (openid: string) => act('block', () => api.post(base + '/pairings/block', { openid }), t('tool:channel.blocked'))
  const forget = async (openid: string, name: string) => {
    const ok = await confirmAsync({ title: t('tool:channel.forget'), message: t('tool:channel.forgetConfirm', { name }), danger: true })
    if (ok) await act('forget', () => api.post(base + '/pairings/forget', { openid }), t('tool:channel.forgotten'))
  }

  if (loading && !view) {
    return <div className="text-xs text-textMuted py-6 flex items-center justify-center gap-2"><Loader2 size={13} className="animate-spin" />{t('tool:channel.loading')}</div>
  }
  if (!view) {
    return <div className="text-xs text-textMuted py-4">{t('tool:channel.loadFailed')}</div>
  }

  const status = !view.enabled ? { tone: 'muted', text: t('tool:channel.disabled') }
    : !view.configured ? { tone: 'accent', text: t('tool:channel.statusUnconfigured') }
    : view.running ? { tone: 'mint', text: t('tool:channel.statusRunning') }
    : { tone: 'muted', text: t('tool:channel.statusStopped') }
  // "还缺什么"用人话（schema 的 title）：用户看到 app_id 这种字段名只会更懵
  const missingText = view.missing_required.map(k => view.schema?.[k]?.title || k).join(' / ')
  const groups = view.group_options || []
  const recent: { openid: string; last_at: number; count: number; allowed: boolean }[] =
    (view.detail?.recent_groups as any[]) || []
  const allowList = qqAllow.split(/[,，\s]+/).filter(Boolean)

  return (
    <div className="space-y-4">
      {showTitle && (
        <div className="flex items-center gap-2 flex-wrap">
          <MessageSquare size={15} className="text-primary-400 shrink-0" />
          <span className="text-sm font-semibold text-textPrimary">{t('tool:channel.title')}</span>
          <span className="text-3xs text-textMuted truncate">{t('tool:channel.desc')}</span>
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

      {/* 还没配过才显示引导；配好就收起来，别长期占地方 */}
      {view.enabled && !view.configured && (
        <Card title={t('tool:channel.stepTitle')} icon={<Plug size={14} className="text-primary-400" />}>
          <ol className="text-xs text-textSecondary space-y-2 list-decimal list-inside">
            <li>
              <a href="https://q.qq.com/" target="_blank" rel="noreferrer" className="text-primary-400 hover:text-primary-500 inline-flex items-center gap-1">
                {t('tool:channel.step1')} <ExternalLink size={11} />
              </a>
            </li>
            <li>{t('tool:channel.step2')}</li>
            <li>{t('tool:channel.step3')}</li>
          </ol>
        </Card>
      )}

      <Card title={t('tool:channel.credTitle')} hint={t('tool:channel.credHint')}>
        <Row label={t('tool:channel.appIdLabel')}>
          <Input
            value={appId}
            onChange={e => setAppId(e.target.value)}
            placeholder={t('tool:channel.appIdPlaceholder')}
            name="copree-qq-app-id"
            inputMode="numeric"
            fieldSize="sm"
            disabled={!view.enabled}
            {...NO_AUTOFILL}
          />
        </Row>
        <Row label={t('tool:channel.secretLabel')}>
          <Input
            type="password"
            value={secret}
            onChange={e => setSecret(e.target.value)}
            placeholder={view.secrets?.client_secret ? t('tool:channel.secretSaved') : t('tool:channel.secretPlaceholder')}
            name="copree-qq-app-secret"
            autoComplete="new-password"
            spellCheck={false}
            data-form-type="other"
            data-1p-ignore="true"
            data-lpignore="true"
            fieldSize="sm"
            disabled={!view.enabled}
          />
        </Row>
      </Card>

      <Card title={t('tool:channel.landingTitle')} hint={t('tool:channel.landingHint')}>
        <Row label={t('tool:channel.landingLabel')}>
          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex gap-1 bg-elevated rounded-control p-0.5 w-fit shrink-0">
              {(['existing', 'new'] as const).map(mode => (
                <button
                  key={mode}
                  onClick={() => setGroupMode(mode)}
                  disabled={!view.enabled}
                  className={'text-2xs px-3 py-1.5 rounded-control transition-colors ' +
                    (groupMode === mode ? 'bg-surface text-textPrimary shadow-sm' : 'text-textSecondary hover:text-textPrimary')}
                >
                  {t(mode === 'existing' ? 'tool:channel.groupModeExisting' : 'tool:channel.groupModeNew')}
                </button>
              ))}
            </div>
            {groupMode === 'existing' ? (
              <Select
                value={groupId}
                onChange={e => setGroupId(e.target.value)}
                options={[{ value: '', label: t('tool:channel.groupNone') },
                          ...groups.map(g => ({ value: String(g.id), label: g.name }))]}
                fieldSize="sm"
                className="w-56"
                disabled={!view.enabled}
              />
            ) : (
              <Input
                value={newGroupName}
                onChange={e => setNewGroupName(e.target.value)}
                placeholder={t('tool:channel.newGroupPlaceholder')}
                name="copree-qq-new-group"
                fieldSize="sm"
                className="w-56"
                disabled={!view.enabled}
                {...NO_AUTOFILL}
              />
            )}
          </div>
          {groupMode === 'new' && <p className="text-3xs text-textMuted leading-relaxed">{t('tool:channel.newGroupHint')}</p>}
          {groupMode === 'existing' && groups.length === 0 && (
            <p className="text-3xs text-amber-600 dark:text-amber-400">{t('tool:channel.groupEmptyHint')}</p>
          )}
        </Row>
        <Row label={t('tool:channel.allowLabel')} hint={t('tool:channel.allowHint')}>
          <Input
            value={qqAllow}
            onChange={e => setQqAllow(e.target.value)}
            placeholder={t('tool:channel.qqAllowPlaceholder')}
            name="copree-qq-allow"
            fieldSize="sm"
            disabled={!view.enabled}
            {...NO_AUTOFILL}
          />
        </Row>
        {/* 最近见到过的 QQ 群：卡片直接列出 openid，一键进白名单——不用再去别处抄 */}
        <div className="space-y-1.5">
          <div className="text-3xs text-textMuted">{t('tool:channel.recentGroups')}</div>
          {recent.length === 0 ? (
            <div className="text-3xs text-textMuted">{t('tool:channel.recentGroupsEmpty')}</div>
          ) : recent.slice(0, 6).map(g => {
            const listed = allowList.includes(g.openid)
            return (
              <div key={g.openid} className="grid grid-cols-[auto_1fr_auto] items-center gap-2 bg-canvas border border-border rounded-control px-3 py-2">
                <span className={'w-1.5 h-1.5 rounded-full shrink-0 ' + (g.allowed ? 'bg-mint-400' : 'bg-amber-400')} />
                <div className="min-w-0">
                  <div className="text-3xs text-textPrimary truncate font-mono">{g.openid}</div>
                  <div className="text-3xs text-textMuted">
                    {t('tool:channel.recentGroupMeta', { count: String(g.count), time: new Date(g.last_at * 1000).toLocaleString() })}
                  </div>
                </div>
                {listed
                  ? <span className="text-3xs text-mint-400 shrink-0">{t('tool:channel.allowListed')}</span>
                  : <Button size="xs" variant="secondary" loading={busy === 'allow:' + g.openid} onClick={() => addToAllowlist(g.openid)}>{t('tool:channel.allowAdd')}</Button>}
              </div>
            )
          })}
        </div>
        <p className="text-3xs text-textMuted leading-relaxed">{t('tool:channel.qqPullHint')}</p>
      </Card>

      <Card title={t('tool:channel.policyTitle')}>
        <Row label={t('tool:channel.policyLabel')} hint={t(POLICY_HINT_KEY[policy] || POLICY_HINT_KEY.pairing)}>
          <Select
            value={policy}
            onChange={e => setPolicy(e.target.value)}
            options={POLICY_KEYS.map(k => ({ value: k, label: t(POLICY_LABEL_KEY[k]) }))}
            fieldSize="sm"
            className="w-56"
            disabled={!view.enabled}
          />
        </Row>
      </Card>

      {/* 动作条：保存 + 缺口 / 报错，都在按钮这一行，视线不用来回跳 */}
      <div className="flex items-center gap-3 flex-wrap">
        <Button
          size="sm"
          variant="primary"
          loading={busy === 'save'}
          onClick={save}
          disabled={!view.enabled || (!appId.trim() && !secret.trim())}
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

      <Card title={t('tool:channel.pairTitle')} icon={<UserCheck size={14} className="text-primary-400" />} hint={t('tool:channel.pairHint')}>
        {view.pending.length === 0 ? (
          <div className="text-3xs text-textMuted">{t('tool:channel.pendingEmpty')}</div>
        ) : (
          <div className="space-y-1.5">
            {view.pending.map(p => (
              <div key={p.id} className="grid grid-cols-[1fr_auto_auto] items-center gap-2 bg-canvas border border-border rounded-control px-3 py-2">
                <div className="min-w-0">
                  <div className="text-xs text-textPrimary truncate">{p.nickname || t('tool:channel.unknownUser')}</div>
                  <div className="text-3xs text-textMuted">{t('tool:channel.codeLabel', { code: p.code, openid: tail(p.openid) })}</div>
                </div>
                <Button size="xs" variant="primary" loading={busy === 'pair' + p.id} onClick={() => approve(p.id)}>{t('tool:channel.approve')}</Button>
                <Button size="xs" variant="ghost" onClick={() => block(p.openid)}>{t('tool:channel.block')}</Button>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center gap-2">
          <Input
            value={codeInput}
            onChange={e => setCodeInput(e.target.value.toUpperCase())}
            placeholder={t('tool:channel.byCodePlaceholder')}
            name="copree-qq-pair-code"
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
                <span className="min-w-0 truncate">{t('tool:channel.ownerRow', { name: a.nickname || tail(a.openid) })}</span>
                <button onClick={() => forget(a.openid, a.nickname || tail(a.openid))} className="text-textMuted hover:text-rose-400 transition-colors">{t('tool:channel.forget')}</button>
              </div>
            ))}
            {view.blocked.map(b => (
              <div key={b.id} className="grid grid-cols-[auto_1fr_auto] items-center gap-2 text-3xs text-textMuted">
                <span className="w-1.5 h-1.5 rounded-full bg-gray-400" />
                <span className="min-w-0 truncate">{t('tool:channel.blockedRow', { name: b.nickname || tail(b.openid) })}</span>
                <button onClick={() => forget(b.openid, b.nickname || tail(b.openid))} className="hover:text-primary-400 transition-colors">{t('tool:channel.unblock')}</button>
              </div>
            ))}
          </div>
        )}

        <p className="text-3xs text-textMuted leading-relaxed">{t('tool:channel.privacy')}</p>
      </Card>
    </div>
  )
}
