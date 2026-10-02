// 总览（管理台首页）—— 点进来先看见"要你处理的事"，再看见数字，最后一眼看全所有入口。
//
// 版面按外部控制台首页的共识收口：大厂首页都不是按钮墙，而是
//   待办（Stripe 的通知条 / 阿里云的监控告警 / Sentry 的 Issues）+ 关键数字 + 最近访问；
//   "到任何地方"另有目录（Azure 的 All services、AWS 的 Unified Navigation）。
// 这里仍然平铺一栏「全部入口」，是因为左边栏只列当前工作区的页签——跨工作区要点两次；
// 代价是与左边栏重复，所以按工作区分组、放在最下面。
//
// 颜色只取主站品牌色四支（紫 primary / 金 accent / 薄荷 mint / 玫瑰 rose），并只表示语义：
//   需要处理=金、存量=紫、风险=玫瑰、增长=薄荷。同一种元素一律同一套形状与字号，
//   色相只用来区分"不同类的东西"；渐变一律静态 Tailwind 类，不写运行时算色，避免掉帧。
//
// 数字各自来自它自己的接口（登录/对话/新用户取 /admin/ops/overview 的口径），这里不另算一份。

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle, ArrowRight, Bot, CheckCircle2, Database, Gauge, History, Inbox,
  KeyRound, LayoutGrid, MessageCircle, MessagesSquare, ShieldAlert, UserPlus, Users, Wrench,
} from 'lucide-react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'
import MaintenanceMsgEditor from '../../../components/MaintenanceMsgEditor'
import { CONSOLE_WORKSPACES, findConsoleItem } from '../workspaces'
import { recentTabs } from '../recentTabs'

/** 备份超过这个天数就当"可能没在跑"（每日备份功能的正常间隔是 1 天） */
const BACKUP_STALE_DAYS = 2

type Hue = 'primary' | 'accent' | 'mint' | 'rose'

/** 四支品牌色的色块写法：类名必须是完整字面量，Tailwind 才扫得到 */
const HUE: Record<Hue, { chip: string; wash: string; hoverBorder: string; icon: string }> = {
  primary: { chip: 'bg-primary-500/10 text-primary-400', wash: 'bg-primary-500/[0.05]', hoverBorder: 'hover:border-primary-400', icon: 'text-primary-400' },
  accent: { chip: 'bg-accent-500/12 text-accent-400', wash: 'bg-accent-500/[0.05]', hoverBorder: 'hover:border-accent-400', icon: 'text-accent-400' },
  mint: { chip: 'bg-mint-500/12 text-mint-400', wash: 'bg-mint-500/[0.05]', hoverBorder: 'hover:border-mint-400', icon: 'text-mint-400' },
  rose: { chip: 'bg-rose-500/12 text-rose-400', wash: 'bg-rose-500/[0.05]', hoverBorder: 'hover:border-rose-400', icon: 'text-rose-400' },
}

/** 区块标题：全页同一套（色块 + 标题 + 右侧口径说明），保证"同类元素长得一样" */
function SectionTitle({ icon: Icon, hue, title, hint }: {
  icon: React.ElementType; hue: Hue; title: string; hint?: string
}) {
  return (
    <div className="flex items-center gap-2 mb-2">
      <span className={`w-6 h-6 rounded-control flex items-center justify-center shrink-0 ${HUE[hue].chip}`}>
        <Icon size={13} />
      </span>
      <h3 className="text-sm font-semibold text-textPrimary shrink-0">{title}</h3>
      {hint && <span className="ml-auto text-2xs text-textMuted truncate">{hint}</span>}
    </div>
  )
}

export default function OverviewTab() {
  const t = useT()
  const navigate = useNavigate()
  const [ready, setReady] = useState(false)
  const [ov, setOv] = useState<any>(null)
  const [ops, setOps] = useState<any>(null)
  const [pending, setPending] = useState<number | null>(null)
  const [pool, setPool] = useState<any[] | null>(null)
  const [backups, setBackups] = useState<any[] | null>(null)
  const [mt, setMt] = useState<{ hard: boolean; soft: boolean; auto: boolean }>({ hard: false, soft: false, auto: false })
  const [mtBusy, setMtBusy] = useState<string | null>(null)
  const [mtError, setMtError] = useState('')

  useEffect(() => {
    let alive = true
    // 一次性并发取齐首页要用的六处数据：任一处失败只让它那一段空着，不连累整页
    const get = (p: string) => api.get(p).catch(() => null)
    Promise.all([
      get('/admin/overview'),
      get('/admin/maintenance'),
      get('/admin/ops/overview?days=1'),
      get('/requests/pending'),
      get('/admin/api-key-pool'),
      get('/admin/backups'),
    ]).then(([o, m, op, pe, po, bk]) => {
      if (!alive) return
      setOv(o)
      if (m) setMt({ hard: !!m.hard, soft: !!m.soft, auto: !!m.auto })
      setOps(op)
      setPending(Array.isArray(pe) ? pe.length : null)
      setPool(Array.isArray(po) ? po : null)
      setBackups(bk && Array.isArray(bk.backups) ? bk.backups : null)
      setReady(true)
    })
    return () => { alive = false }
  }, [])

  const toggleHard = async () => {
    if (mtBusy) return
    setMtBusy('hard'); setMtError('')
    try { const d: any = await api.post('/admin/maintenance/hard'); setMt(prev => ({ ...prev, hard: !!d.hard })) }
    catch (e: any) { setMtError(e?.detail || e?.message || t('common.error')) }
    setMtBusy(null)
  }
  const toggleSoft = async () => {
    if (mtBusy) return
    setMtBusy('soft'); setMtError('')
    try { const d: any = await api.post('/admin/maintenance/soft'); setMt(prev => ({ ...prev, soft: !!d.soft })) }
    catch (e: any) { setMtError(e?.detail || e?.message || t('common.error')) }
    setMtBusy(null)
  }

  /** 首页的目标有两种：控制台内的页签（走 ?tab=）与应用里的页面（/list） */
  const goto = (target: string) => {
    if (target.startsWith('/')) navigate(target)
    else navigate({ search: target })
  }

  // ── 需要你处理：只放"现在不点就会出事"的事，宁少勿滥（红点一多就没人看了） ──
  type Task = { key: string; text: string; hint: string; icon: React.ElementType; to: string }
  const tasks: Task[] = []
  if (pending) tasks.push({
    key: 'pending', text: t('admin.homePending', { n: pending }), hint: t('admin.homePendingHint'),
    icon: Inbox, to: '/list',
  })
  const suspects = ops?.attack?.suspects || 0
  if (suspects) tasks.push({
    key: 'suspects', text: t('admin.homeSuspects', { n: suspects }),
    hint: t('admin.homeSuspectsHint', { min: ops?.attack?.min_failures ?? 5 }),
    icon: ShieldAlert, to: 'tab=logs&log_type=login_failed',
  })
  const lockouts = ops?.lockouts || 0
  if (lockouts) tasks.push({
    key: 'lockouts', text: t('admin.homeLockouts', { n: lockouts }), hint: t('admin.homeLockoutsHint'),
    icon: ShieldAlert, to: 'tab=logs&log_type=login_failed',
  })
  if (pool && pool.filter(k => k.is_active).length === 0) tasks.push({
    key: 'pool', text: t('admin.homeNoPoolKey'), hint: t('admin.homeNoPoolKeyHint'),
    icon: KeyRound, to: 'tab=apipool',
  })
  const lastBackupAt = backups && backups.length
    ? Math.max(...backups.map((b: any) => Date.parse(b.mtime) || 0)) : 0
  const backupAgeDays = lastBackupAt ? Math.floor((Date.now() - lastBackupAt) / 86400000) : null
  if (backups && (backupAgeDays === null || backupAgeDays >= BACKUP_STALE_DAYS)) tasks.push({
    key: 'backup',
    text: backupAgeDays === null ? t('admin.homeNoBackup') : t('admin.homeBackupStale', { n: backupAgeDays }),
    hint: backupAgeDays === null ? t('admin.homeNoBackupHint') : t('admin.homeBackupStaleHint'),
    icon: Database, to: 'tab=backup',
  })
  if (mt.auto || mt.hard || mt.soft) tasks.push({
    key: 'maintenance', text: t('admin.homeMaintenanceOn'), hint: t('admin.homeMaintenanceOnHint'),
    icon: Wrench, to: 'tab=system',
  })
  const hasTasks = tasks.length > 0

  // ── 平台现状：同一套形状，色相只用来分组——存量=紫，近 24 小时=金（活跃）/玫瑰（风险）/薄荷（增长）
  const numbers: Array<{ key: string; label: string; value: any; to: string; hue: Hue; icon: React.ElementType; fresh?: boolean }> = [
    { key: 'users', label: t('admin.totalUsers'), value: ov?.total_users, to: 'tab=users', hue: 'primary', icon: Users },
    { key: 'agents', label: t('admin.totalAgents'), value: ov?.total_agents, to: 'tab=agents', hue: 'primary', icon: Bot },
    { key: 'groups', label: t('admin.totalGroups'), value: ov?.total_groups, to: 'tab=groups', hue: 'primary', icon: MessageCircle },
    { key: 'turns', label: t('admin.opsTurns'), value: ops?.conversation?.turns, to: 'tab=convlog', hue: 'accent', icon: MessagesSquare, fresh: true },
    { key: 'loginFail', label: t('admin.opsLoginFail'), value: ops?.login?.failed, to: 'tab=logs&log_type=login_failed', hue: 'rose', icon: ShieldAlert, fresh: true },
    { key: 'newUsers', label: t('admin.opsNewUsers'), value: ops?.people?.new_local, to: 'tab=logs&log_type=register', hue: 'mint', icon: UserPlus, fresh: true },
  ]

  const recent = recentTabs().filter(k => k !== 'overview').slice(0, 5).map(findConsoleItem)

  const statusBadge = mt.auto
    ? { cls: 'bg-accent-500/15 text-accent-400', label: t('admin.maintenanceStarting') }
    : mt.hard
      ? { cls: 'bg-rose-500/15 text-rose-400', label: t('admin.maintenanceStatusPaused') }
      : mt.soft
        ? { cls: 'bg-accent-500/15 text-accent-400', label: t('admin.maintenanceStatusTip') }
        : { cls: 'bg-mint-500/15 text-mint-400', label: t('admin.maintenanceStatusNormal') }
  const statusDesc = mt.auto
    ? t('admin.maintenanceDescAuto')
    : mt.hard
      ? t('admin.maintenanceDescHard')
      : mt.soft
        ? t('admin.maintenanceDescSoft')
        : t('admin.maintenanceDescNormal')
  const statusDot = mt.auto ? 'bg-accent-400 animate-pulse' : mt.hard ? 'bg-rose-400' : mt.soft ? 'bg-accent-400' : 'bg-mint-400'
  const statusHue: Hue = mt.hard ? 'rose' : mt.soft || mt.auto ? 'accent' : 'mint'

  return (
    <div className="space-y-5">
      {/* ── 需要你处理：有事项时整块用琥珀金（要动手），清空后转薄荷（没事） ── */}
      <section>
        <div className={`bg-surface rounded-card border overflow-hidden ${
          hasTasks ? 'border-accent-500/30' : 'border-border'
        }`}>
          <div className={`flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r to-transparent ${
            hasTasks ? 'from-accent-500/12 border-b border-accent-500/20' : 'from-mint-500/12 border-b border-border/60'
          }`}>
            <span className={`w-6 h-6 rounded-control flex items-center justify-center shrink-0 ${HUE[hasTasks ? 'accent' : 'mint'].chip}`}>
              {hasTasks ? <AlertTriangle size={13} /> : <CheckCircle2 size={13} />}
            </span>
            <h3 className="text-sm font-semibold text-textPrimary">{t('admin.homeAttention')}</h3>
            {ready && hasTasks && (
              <span className="ml-auto text-2xs font-medium px-2 py-0.5 rounded-full bg-accent-500/15 text-accent-400">
                {tasks.length}
              </span>
            )}
          </div>
          {!ready ? (
            <p className="px-4 py-3 text-xs text-textMuted">{t('common.loading')}</p>
          ) : !hasTasks ? (
            <p className="px-4 py-3 text-xs text-mint-400">{t('admin.homeAllClear')}</p>
          ) : tasks.map(({ icon: Icon, ...task }) => (
            <button
              key={task.key}
              onClick={() => goto(task.to)}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-left border-b border-border/50 last:border-b-0 hover:bg-accent-500/[0.06] transition-colors"
            >
              <span className={`w-7 h-7 rounded-control flex items-center justify-center shrink-0 ${HUE.accent.chip}`}>
                <Icon size={14} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-textPrimary">{task.text}</span>
                <span className="block text-2xs text-textMuted mt-0.5">{task.hint}</span>
              </span>
              <span className="shrink-0 flex items-center gap-1 text-xs font-medium text-primary-400">
                {t('admin.homeGo')}<ArrowRight size={12} />
              </span>
            </button>
          ))}
        </div>
      </section>

      {/* ── 平台现状 ── */}
      <section>
        <SectionTitle icon={Gauge} hue="primary" title={t('admin.homeNumbers')} hint={t('admin.homeNumbersHint')} />
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
          {numbers.map(({ icon: Icon, ...n }) => (
            <button
              key={n.key}
              onClick={() => goto(n.to)}
              title={n.fresh ? t('admin.homeLast24h') : undefined}
              className={`group relative rounded-card border border-border px-3 py-2.5 text-left transition-colors ${HUE[n.hue].wash} ${HUE[n.hue].hoverBorder}`}
            >
              <span className="flex items-center gap-1.5">
                <span className={`w-5 h-5 rounded-control flex items-center justify-center shrink-0 ${HUE[n.hue].chip}`}>
                  <Icon size={11} />
                </span>
                <span className="text-2xs text-textSecondary truncate">{n.label}</span>
              </span>
              <span className="block mt-1 text-xl font-semibold text-textPrimary tabular-nums">{n.value ?? '—'}</span>
              {n.fresh && (
                <span className="absolute top-1.5 right-2 text-3xs text-textMuted">{t('admin.homeLast24h')}</span>
              )}
            </button>
          ))}
        </div>
      </section>

      {/* ── 最近访问：虚线块，和下面实线的入口块区分开（同一栏，不同性质） ── */}
      {recent.length > 0 && (
        <section>
          <SectionTitle icon={History} hue="primary" title={t('admin.homeRecent')} />
          <div className="flex flex-wrap gap-2">
            {recent.map(({ icon: Icon, ...item }) => (
              <button
                key={item.key}
                onClick={() => goto(`tab=${item.key}`)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-control border border-dashed border-primary-400/40 bg-primary-500/[0.06] text-xs text-textSecondary hover:text-primary-400 hover:border-primary-400 transition-colors"
              >
                <Icon size={13} />{t(item.labelKey)}
              </button>
            ))}
          </div>
        </section>
      )}

      {/* ── 系统状态 + 维护开关 ── */}
      <section>
        <div className="bg-surface rounded-card border border-border p-4 space-y-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-3">
              <span className={`w-9 h-9 rounded-card flex items-center justify-center shrink-0 ${HUE[statusHue].chip}`}>
                <Wrench size={17} />
              </span>
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-textPrimary">{t('admin.maintenanceMode')}</span>
                  <span className={`text-2xs px-2 py-0.5 rounded-full ${statusBadge.cls}`}>{statusBadge.label}</span>
                </div>
                <div className="flex items-center gap-1.5 text-2xs text-textMuted mt-0.5">
                  <span className={`w-1.5 h-1.5 rounded-full ${statusDot}`} />
                  {statusDesc}
                </div>
              </div>
            </div>
            <div className="flex gap-2">
              <button onClick={toggleHard} disabled={!!mtBusy} title={t('admin.homeMaintenanceOnHint')}
                className={`px-3 py-1.5 rounded-control text-xs font-medium transition-colors disabled:opacity-50 ${
                  mt.hard ? 'bg-mint-500 hover:bg-mint-600 text-white' : 'bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30'
                }`}>
                {mtBusy === 'hard' ? '···' : mt.hard ? t('admin.maintenanceResumeService') : t('admin.maintenancePauseService')}
              </button>
              <button onClick={toggleSoft} disabled={!!mtBusy} title={t('admin.homeMaintenanceOnHint')}
                className={`px-3 py-1.5 rounded-control text-xs font-medium transition-colors disabled:opacity-50 ${
                  mt.soft ? 'bg-mint-500 hover:bg-mint-600 text-white' : 'bg-accent-500/10 hover:bg-accent-500/20 text-accent-400 border border-accent-500/30'
                }`}>
                {mtBusy === 'soft' ? '···' : mt.soft ? t('admin.maintenanceCancelTip') : t('admin.maintenanceShowTip')}
              </button>
            </div>
          </div>
          {mtError && <p className="text-2xs text-rose-400">{mtError}</p>}
        </div>
      </section>

      {/* ── 全部入口：工作区用小色块区分，入口本身保持同一形状（一致性），只在悬停时上品牌紫 ── */}
      <section className="space-y-3">
        <SectionTitle icon={LayoutGrid} hue="primary" title={t('admin.homeAllEntries')} hint={t('admin.homeAllEntriesHint')} />
        {CONSOLE_WORKSPACES.map(({ icon: WorkspaceIcon, ...workspace }, wi) => {
          const hue: Hue = (['primary', 'mint', 'accent', 'rose', 'primary'] as Hue[])[wi % 5]
          return (
            <div key={workspace.key}>
              <div className="flex items-center gap-1.5 mb-1.5">
                <span className={`w-4 h-4 rounded-control flex items-center justify-center shrink-0 ${HUE[hue].chip}`}>
                  <WorkspaceIcon size={10} />
                </span>
                <span className="text-2xs font-medium text-textSecondary tracking-wide">{t(workspace.labelKey)}</span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
                {workspace.items.map(({ icon: Icon, ...item }) => {
                  const active = item.key === 'overview'
                  return (
                    <button
                      key={item.key}
                      title={t(item.descKey)}
                      onClick={() => goto(`tab=${item.key}`)}
                      className={`group flex items-center gap-2 rounded-card border bg-surface px-3 py-2 text-left text-xs transition-colors ${
                        active
                          ? 'border-primary-400/50 bg-primary-500/[0.06] text-textPrimary'
                          : 'border-border text-textSecondary hover:text-textPrimary hover:border-primary-400'
                      }`}
                    >
                      <Icon size={14} className={`shrink-0 ${active ? HUE.primary.icon : 'text-textMuted group-hover:text-primary-400'}`} />
                      <span className="truncate">{t(item.labelKey)}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          )
        })}
      </section>

      {/* ── 维护文案 ── */}
      <MaintenanceMsgEditor />
    </div>
  )
}
