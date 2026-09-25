import { useState, useEffect } from 'react'
import { api } from '../../../api/client'
import { useIsDark } from '../../../hooks/useIsDark'
import { useT } from '../../../i18n/I18nContext'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, PieChart, Pie, Cell,
} from 'recharts'
import { Activity, Clock, AlertTriangle, Layers, Loader2 } from 'lucide-react'

export default function SystemMetricsTab() {
  const t = useT()
  const [metrics, setMetrics] = useState<any>(null)
  const [hours, setHours] = useState(24)
  const [loading, setLoading] = useState(true)
  const isDark = useIsDark()

  useEffect(() => {
    setLoading(true)
    api.get(`/admin/metrics?hours=${hours}`)
      .then(setMetrics)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [hours])

  if (loading) return (
    <div className="flex justify-center py-16">
      <Loader2 className="animate-spin text-textSecondary" size={28} />
    </div>
  )
  if (!metrics) return <p className="text-textMuted py-8 text-center">{t('admin.noMetrics')}</p>

  const live = metrics.live
  const timeline = metrics.timeline || []
  const retentionDays = metrics.retention_days || 30

  const colors = isDark ? {
    primary: '#A78BFA', mint: '#34D399', rose: '#FB7185',
    amber: '#FBBF24', cyan: '#22D3EE', grid: '#374151', text: '#9CA3AF',
  } : {
    primary: '#7C3AED', mint: '#10B981', rose: '#F43F5E',
    amber: '#F59E0B', cyan: '#06B6D4', grid: '#E5E7EB', text: '#6B7280',
  }

  const errorPieData = Object.entries(live.errors || {}).map(([type, count]) => ({
    name: type, value: count as number,
  }))

  return (
    <div className="space-y-5 pb-8">
      {/* 时间选择 + 保留天数 */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex gap-1.5">
          {[1, 6, 24, 72, 168].map(h => (
            <button key={h} onClick={() => setHours(h)}
              className={`px-3.5 py-1.5 rounded-card text-xs font-medium transition-colors ${
                hours === h
                  ? 'bg-primary-500/15 text-primary-600 dark:text-primary-300 border border-primary-400/30'
                  : 'bg-surface border border-border text-textSecondary hover:bg-elevated'
              }`}>
              {h >= 24 ? `${h / 24}d` : `${h}h`}
            </button>
          ))}
        </div>
        <span className="text-2xs text-textMuted">
          {t('admin.metricsRetention').replace('{retentionDays}', String(retentionDays))}
        </span>
      </div>

      {/* 实时指标卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard icon={Activity} label={t('admin.metricsLlmCalls')} value={live.llm?.total_calls ?? 0} color="primary" />
        <MetricCard icon={Clock} label={t('admin.metricsAvgLatency')} value={`${(live.llm?.latency?.avg ?? 0).toFixed(2)}s`} color="mint" />
        <MetricCard icon={AlertTriangle} label={t('admin.metricsErrorRate')} value={`${((live.llm?.error_rate ?? 0) * 100).toFixed(1)}%`} color="rose" />
        <MetricCard icon={Layers} label={t('admin.metricsMaxQueueDepth')} value={live.queue?.max_depth ?? 0} color="amber" />
      </div>

      {/* LLM 延迟趋势图 */}
      {timeline.length > 0 && (
        <div className="bg-surface rounded-dialog border border-border p-5">
          <h3 className="text-sm font-semibold text-textPrimary mb-4">{t('admin.metricsLatencyTrend')}</h3>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={timeline}>
                <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} />
                <XAxis dataKey="at" tick={{ fontSize: 11, fill: colors.text }}
                  tickFormatter={v => v?.slice(11, 16) || ''} />
                <YAxis tick={{ fontSize: 11, fill: colors.text }} />
                <Tooltip
                  contentStyle={{
                    background: isDark ? '#1F2937' : '#FFF',
                    border: `1px solid ${colors.grid}`,
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  labelFormatter={v => v?.slice(0, 19) || ''}
                />
                <Legend />
                <Line type="monotone" dataKey="llm_avg_latency" stroke={colors.primary}
                  name={t('admin.metricsAvgLatencyS')} dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="messages_per_second" stroke={colors.mint}
                  name={t('admin.metricsMsgPerSec')} dot={false} strokeWidth={2} yAxisId={1} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* 错误分布 + 工具延迟表 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {errorPieData.length > 0 && (
          <div className="bg-surface rounded-dialog border border-border p-5">
            <h3 className="text-sm font-semibold text-textPrimary mb-3">{t('admin.metricsErrorDistribution')}</h3>
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={errorPieData} dataKey="value" nameKey="name"
                    cx="50%" cy="50%" outerRadius={70} label={({ name, value }) => `${name}: ${value}`}>
                    {errorPieData.map((_, i) => (
                      <Cell key={i} fill={[colors.rose, colors.amber, colors.primary, colors.cyan][i % 4]} />
                    ))}
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}

        {live.tools && Object.keys(live.tools).length > 0 && (
          <div className="bg-surface rounded-dialog border border-border p-5">
            <h3 className="text-sm font-semibold text-textPrimary mb-3">{t('admin.metricsToolStats')}</h3>
            <div className="overflow-x-auto max-h-56 overflow-y-auto">
              <table className="w-full text-sm text-textPrimary">
                <thead>
                  <tr className="border-b border-border sticky top-0 bg-surface">
                    <th className="text-left py-2 px-3 font-medium text-textSecondary text-xs">{t('admin.toolColName')}</th>
                    <th className="text-right py-2 px-3 font-medium text-textSecondary text-xs">{t('admin.toolColCalls')}</th>
                    <th className="text-right py-2 px-3 font-medium text-textSecondary text-xs">{t('admin.toolColAvg')}</th>
                    <th className="text-right py-2 px-3 font-medium text-textSecondary text-xs">{t('admin.toolColSuccessRate')}</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(live.tools).map(([name, stats]: [string, any]) => {
                    const total = (stats.success || 0) + (stats.error || 0)
                    const rate = total > 0 ? ((stats.success / total) * 100).toFixed(0) + '%' : '-'
                    return (
                      <tr key={name} className="border-b border-border/50">
                        <td className="py-1.5 px-3 font-mono text-xs">{name}</td>
                        <td className="py-1.5 px-3 text-right">{stats.latency?.count || 0}</td>
                        <td className="py-1.5 px-3 text-right">{stats.latency?.avg?.toFixed(2) || '-'}s</td>
                        <td className="py-1.5 px-3 text-right">
                          <span className={stats.error > 0 ? 'text-rose-400' : 'text-mint-400'}>
                            {rate}
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* 意愿评分分布 */}
      {live.willingness && Object.keys(live.willingness).length > 0 && (
        <div className="bg-surface rounded-dialog border border-border p-5">
          <h3 className="text-sm font-semibold text-textPrimary mb-3">{t('admin.willingnessDistribution')}</h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(live.willingness).sort().map(([bucket, count]) => (
              <span key={bucket}
                className="px-2.5 py-1 rounded-control bg-primary-500/10 text-primary-600 dark:text-primary-300 text-xs font-mono">
                {bucket}: {count as number}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function MetricCard({ icon: Icon, label, value, color }: {
  icon: React.ElementType; label: string; value: string | number; color: string
}) {
  const colorMap: Record<string, string> = {
    primary: 'bg-primary-500/10 text-primary-400',
    mint: 'bg-mint-400/10 text-mint-400',
    rose: 'bg-rose-400/10 text-rose-400',
    amber: 'bg-accent-400/10 text-accent-400',
  }
  return (
    <div className="bg-surface rounded-card border border-border p-4 text-center">
      <Icon size={18} className={`mx-auto mb-1.5 ${colorMap[color]?.split(' ')[1] || 'text-textSecondary'}`} />
      <div className="text-xl font-bold text-textPrimary">{value}</div>
      <div className="text-3xs text-textMuted mt-0.5">{label}</div>
    </div>
  )
}
