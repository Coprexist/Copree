// 总览：站点统计卡片 + 维护模式开关

import { useState, useEffect } from 'react'
import { api } from '../../../api/client'
import { Users, Bot, MessageCircle, Activity, Wrench } from 'lucide-react'
import { useT } from '../../../i18n/I18nContext'
import MaintenanceMsgEditor from '../../../components/MaintenanceMsgEditor'

export default function OverviewTab() {
  const t = useT()
  const [stats, setStats] = useState<any>(null)
  const [mt, setMt] = useState<{ hard: boolean; soft: boolean; auto: boolean }>({ hard: false, soft: false, auto: false })
  const [mtBusy, setMtBusy] = useState<string | null>(null)
  const [mtError, setMtError] = useState('')
  useEffect(() => {
    api.get('/admin/overview').then(setStats).catch(console.error)
    api.get('/admin/maintenance').then((d: any) => setMt({ hard: !!d.hard, soft: !!d.soft, auto: !!d.auto })).catch(() => {})
  }, [])

  const toggleHard = async () => {
    if (mtBusy) return
    setMtBusy('hard'); setMtError('')
    try { const d: any = await api.post('/admin/maintenance/hard'); setMt(prev => ({ ...prev, hard: !!d.hard })) }
    catch (e: any) { setMtError(e?.detail || e?.message || '操作失败') }
    setMtBusy(null)
  }
  const toggleSoft = async () => {
    if (mtBusy) return
    setMtBusy('soft'); setMtError('')
    try { const d: any = await api.post('/admin/maintenance/soft'); setMt(prev => ({ ...prev, soft: !!d.soft })) }
    catch (e: any) { setMtError(e?.detail || e?.message || '操作失败') }
    setMtBusy(null)
  }

  if (!stats) return <p className="text-textMuted">{t('common.loading')}</p>

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

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label={t('admin.totalUsers')} value={stats.total_users} icon={Users} />
        <StatCard label={t('admin.totalAgents')} value={stats.total_agents} icon={Bot} />
        <StatCard label={t('admin.totalGroups')} value={stats.total_groups} icon={MessageCircle} />
        <StatCard label={t('admin.pendingRequests')} value={stats.pending_vector_requests} icon={Activity} />
      </div>
      {/* ── 状态条 + 双开关 ── */}
      <div className="bg-surface rounded-card border border-border p-4 space-y-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-3">
            <div className={`w-9 h-9 rounded-card flex items-center justify-center ${mt.hard ? 'bg-rose-500/10' : mt.soft || mt.auto ? 'bg-accent-500/10' : 'bg-mint-500/10'}`}>
              <Wrench size={18} className={mt.hard ? 'text-rose-400' : mt.soft || mt.auto ? 'text-accent-400' : 'text-mint-400'} />
            </div>
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
            <button onClick={toggleHard} disabled={!!mtBusy} title="用户看到弹窗或顶栏，API 全部返回 503"
              className={`px-3 py-1.5 rounded-control text-xs font-medium transition-colors disabled:opacity-50 ${
                mt.hard ? 'bg-mint-500 hover:bg-mint-600 text-white' : 'bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30'
              }`}>
              {mtBusy === 'hard' ? '···' : mt.hard ? t('admin.maintenanceResumeService') : t('admin.maintenancePauseService')}
            </button>
            <button onClick={toggleSoft} disabled={!!mtBusy} title="用户看到顶栏或弹窗提示，API 正常运行"
              className={`px-3 py-1.5 rounded-control text-xs font-medium transition-colors disabled:opacity-50 ${
                mt.soft ? 'bg-mint-500 hover:bg-mint-600 text-white' : 'bg-accent-500/10 hover:bg-accent-500/20 text-accent-400 border border-accent-500/30'
              }`}>
              {mtBusy === 'soft' ? '···' : mt.soft ? t('admin.maintenanceCancelTip') : t('admin.maintenanceShowTip')}
            </button>
          </div>
        </div>
        {mtError && <p className="text-2xs text-rose-400">{mtError}</p>}
      </div>

      {/* 文案编辑 */}
      <MaintenanceMsgEditor />
    </div>
  )
}

function StatCard({ label, value, icon: Icon }: { label: string; value: number; icon: React.ElementType }) {
  return (
    <div className="bg-surface rounded-card border border-border p-5 hover:border-primary-500/20 transition-colors">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-card bg-primary-500/10 flex items-center justify-center">
          <Icon size={20} className="text-primary-400" />
        </div>
        <div>
          <p className="text-2xl font-bold text-textPrimary">{value}</p>
          <p className="text-xs text-textSecondary">{label}</p>
        </div>
      </div>
    </div>
  )
}

/** 重置密码弹窗 */
