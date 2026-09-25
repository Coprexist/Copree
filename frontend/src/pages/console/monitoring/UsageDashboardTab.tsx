import { useState, useEffect } from 'react'
import { api } from '../../../api/client'
import { useIsDark } from '../../../hooks/useIsDark'
import { useT, useLang } from '../../../i18n/I18nContext'
import { fmtTokenNum } from '../../../utils/format'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, AreaChart, Area
} from 'recharts'
import { Loader2, ChevronDown, ChevronRight, BarChart3, Users, Activity } from 'lucide-react'

interface GlobalStats {
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens: number
  cached_tokens: number
  total_calls: number
  unique_agents: number
  unique_users: number
}

interface UserAgentRow {
  user_id: number
  username: string
  agent_id: number
  agent_name: string
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens: number
  cached_tokens: number
  total_calls: number
  model?: string | null
}

interface DailyPoint {
  date: string
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens: number
  cached_tokens: number
  request_count: number
}

export default function UsageDashboardTab() {
  const t = useT()
  const lang = useLang()
  const [days, setDays] = useState(30)
  const [global, setGlobal] = useState<GlobalStats | null>(null)
  const [userRows, setUserRows] = useState<UserAgentRow[]>([])
  const [dailyData, setDailyData] = useState<DailyPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedUsers, setExpandedUsers] = useState<Set<number>>(new Set())
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null)
  const [agentDaily, setAgentDaily] = useState<DailyPoint[]>([])
  const [agentDailyLoading, setAgentDailyLoading] = useState(false)
  const isDark = useIsDark()

  const loadData = async (d: number) => {
    setLoading(true)
    try {
      const [g, u] = await Promise.all([
        api.get<GlobalStats>(`/admin/usage/global?days=${d}`),
        api.get<UserAgentRow[]>(`/admin/usage/by-user?days=${d}`),
      ])
      setGlobal(g)
      setUserRows(Array.isArray(u) ? u : [])

      // 构建全站每日汇总（从 by-user 数据聚合）
      // 暂时用首条数据作为示意；后续可加全站 daily 端点
      const firstAgentId = Array.isArray(u) && u.length > 0 ? u[0].agent_id : null
      if (firstAgentId) {
        try {
          const dd = await api.get<DailyPoint[]>(`/admin/usage/agents/${firstAgentId}/daily?days=${d}`)
          setDailyData(Array.isArray(dd) ? dd : [])
        } catch { setDailyData([]) }
      }
    } catch { setGlobal(null); setUserRows([]) }
    finally { setLoading(false) }
  }

  useEffect(() => { loadData(days) }, [days])

  const loadAgentDaily = async (agentId: number) => {
    setSelectedAgentId(agentId)
    setAgentDailyLoading(true)
    try {
      const dd = await api.get<DailyPoint[]>(`/admin/usage/agents/${agentId}/daily?days=${days}`)
      setAgentDaily(Array.isArray(dd) ? dd : [])
    } catch { setAgentDaily([]) }
    finally { setAgentDailyLoading(false) }
  }

  const toggleUser = (uid: number) => {
    setExpandedUsers(prev => {
      const next = new Set(prev)
      if (next.has(uid)) next.delete(uid); else next.add(uid)
      return next
    })
  }

  // fmtTokenNum 从 ../utils/format 引入

  const colors = isDark ? {
    prompt: '#A78BFA',
    completion: '#34D399',
    reasoning: '#FBBF24',
    cached: '#22D3EE',
    grid: '#374151',
    text: '#9CA3AF',
  } : {
    prompt: '#7C3AED',
    completion: '#10B981',
    reasoning: '#F59E0B',
    cached: '#06B6D4',
    grid: '#E5E7EB',
    text: '#6B7280',
  }

  // 按用户分组
  const userGroups = new Map<number, { username: string; agents: UserAgentRow[]; total: number }>()
  userRows.forEach(r => {
    const g = userGroups.get(r.user_id)
    if (g) {
      g.agents.push(r)
      g.total += r.total_tokens || 0
    } else {
      userGroups.set(r.user_id, { username: r.username, agents: [r], total: r.total_tokens || 0 })
    }
  })

  return (
    <div className="space-y-5">
      {loading ? (
        <div className="flex justify-center py-16"><Loader2 className="animate-spin" size={28} /></div>
      ) : (
        <>
          {/* 日期选择 */}
          <div className="flex gap-2">
            {[7, 30, 60, 90].map(d => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`px-4 py-2 rounded-card text-xs font-medium transition-colors ${
                  days === d
                    ? 'bg-primary-500/15 text-primary-600 dark:text-primary-300 border border-primary-400/30'
                    : 'bg-surface border border-border text-textSecondary hover:bg-elevated'
                }`}
              >
                {d} {t('admin.daysSuffix')}
              </button>
            ))}
          </div>

          {/* 全站汇总 */}
          {global && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                { label: t('admin.totalTokens'), value: fmtTokenNum(global.total_tokens), icon: Activity },
                { label: t('admin.totalCalls'), value: global.total_calls, icon: BarChart3 },
                { label: t('admin.activeAgents'), value: global.unique_agents, icon: Activity },
                { label: t('admin.activeUsers'), value: global.unique_users, icon: Users },
              ].map(item => (
                <div key={item.label} className="bg-surface rounded-card border border-border p-4 text-center">
                  <item.icon size={16} className="text-primary-400 mx-auto mb-1" />
                  <div className="text-lg font-semibold text-textPrimary">{item.value}</div>
                  <div className="text-3xs text-textMuted">{item.label}</div>
                </div>
              ))}
            </div>
          )}

          {/* 全站每日图表 */}
          {dailyData.length > 0 && (
            <div className="bg-surface rounded-dialog border border-border p-5">
              <h3 className="text-sm font-semibold text-textPrimary mb-4">{t('admin.dailyTokenConsumption')}</h3>
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={dailyData} margin={{ top: 5, right: 5, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} />
                    <XAxis dataKey="date" tick={{ fontSize: 11, fill: colors.text }} tickFormatter={v => v.slice(5)} />
                    <YAxis
                      tick={{ fontSize: 11, fill: colors.text }}
                      tickFormatter={v => fmtTokenNum(v, lang)}
                      width={55}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: isDark ? '#1F2937' : '#FFFFFF',
                        border: `1px solid ${colors.grid}`,
                        borderRadius: '12px',
                        fontSize: '12px',
                      }}
                      formatter={(value: number) => [fmtTokenNum(value), '']}
                    />
                    <Area type="monotone" dataKey="total_tokens" stroke={colors.prompt} fill={colors.prompt} fillOpacity={0.15} name={t('admin.totalTokens')} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* 按用户明细 */}
          <div className="bg-surface rounded-dialog border border-border overflow-hidden">
            <div className="px-5 py-3 border-b border-border">
              <h3 className="text-sm font-semibold text-textPrimary">{t('admin.usageByUser')}</h3>
            </div>
            <div className="divide-y divide-border/60">
              {Array.from(userGroups.entries()).map(([uid, ug]) => (
                <div key={uid}>
                  {/* 用户行 */}
                  <button
                    onClick={() => toggleUser(uid)}
                    className="w-full flex items-center gap-3 px-5 py-3 hover:bg-elevated transition-colors text-left"
                  >
                    {expandedUsers.has(uid) ? <ChevronDown size={14} className="text-textMuted" /> : <ChevronRight size={14} className="text-textMuted" />}
                    <span className="text-sm font-medium text-textPrimary">{ug.username}</span>
                    <span className="text-xs text-textMuted ml-auto">{ug.agents.length} {t('admin.aiCount')}</span>
                    <span className="text-sm font-mono text-textPrimary ml-4">{fmtTokenNum(ug.total)} Token</span>
                  </button>
                  {/* AI 子行 */}
                  {expandedUsers.has(uid) && (
                    <div className="bg-canvas/50">
                      {ug.agents.map(a => (
                        <button
                          key={a.agent_id}
                          onClick={() => loadAgentDaily(a.agent_id)}
                          className={`w-full flex items-center gap-4 px-10 py-2.5 text-xs hover:bg-elevated transition-colors ${
                            selectedAgentId === a.agent_id ? 'bg-primary-500/5' : ''
                          }`}
                        >
                          <span className="text-textPrimary font-medium">{a.agent_name}</span>
                          <span className="text-textMuted hidden md:inline">{a.model || '-'}</span>
                          <span className="text-textMuted ml-auto">{a.total_calls} {t('admin.callsCount')}</span>
                          <span className="text-textPrimary font-mono ml-4">{fmtTokenNum(a.total_tokens)}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {userGroups.size === 0 && (
                <div className="text-center py-12 text-textMuted text-sm">{t('admin.noData')}</div>
              )}
            </div>
          </div>

          {/* 选中 AI 的每日图表 */}
          {selectedAgentId && (
            <div className="bg-surface rounded-dialog border border-border p-5">
              <h3 className="text-sm font-semibold text-textPrimary mb-4">
                {t('admin.aiDailyTokens').replace('{id}', String(selectedAgentId))}
              </h3>
              {agentDailyLoading ? (
                <div className="flex justify-center py-8"><Loader2 className="animate-spin" size={20} /></div>
              ) : agentDaily.length > 0 ? (
                <div className="h-72">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={agentDaily} margin={{ top: 5, right: 5, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} />
                      <XAxis dataKey="date" tick={{ fontSize: 11, fill: colors.text }} tickFormatter={v => v.slice(5)} />
                      <YAxis
                        tick={{ fontSize: 11, fill: colors.text }}
                        tickFormatter={v => fmtTokenNum(v, lang)}
                        width={55}
                      />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: isDark ? '#1F2937' : '#FFFFFF',
                          border: `1px solid ${colors.grid}`,
                          borderRadius: '12px',
                          fontSize: '12px',
                        }}
                        formatter={(value: number) => [fmtTokenNum(value), '']}
                      />
                      <Legend wrapperStyle={{ fontSize: '11px', flexWrap: 'wrap' }} />
                      <Bar dataKey="prompt_tokens" stackId="a" fill={colors.prompt} name="Prompt" />
                      <Bar dataKey="completion_tokens" stackId="a" fill={colors.completion} name="Completion" />
                      <Bar dataKey="reasoning_tokens" stackId="a" fill={colors.reasoning} name={t('admin.thinking')} />
                      <Bar dataKey="cached_tokens" stackId="a" fill={colors.cached} name={t('admin.cached')} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <div className="text-center py-8 text-textMuted text-sm">{t('admin.noAiDailyData')}</div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
