import { useState, useEffect } from 'react'
import { api } from '../../../api/client'
import { useIsDark } from '../../../hooks/useIsDark'
import { useT, useLang } from '../../../i18n/I18nContext'
import { fmtTokenNum, cacheHitRatePct } from '../../../utils/format'
import { formatRelativeTime } from '../../../utils/time'
import { UnderlineTabs } from '../../../components/ui'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, Area, Line, ComposedChart
} from 'recharts'
import { Loader2, ChevronDown, ChevronRight, BarChart3, Users, Activity, Percent, Globe2 } from 'lucide-react'

type UsageView = 'residents' | 'worlds'

interface GlobalStats {
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens: number
  cached_tokens: number
  total_calls: number
  unique_agents: number
  unique_users: number
  cache_hit_rate_pct: number
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
  cache_hit_rate_pct: number
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
  cache_hit_rate_pct: number
}

/** 群视界用量行：来自 world_llm_usage（唯一记到 world_id 的用量表） */
interface WorldUsageRow {
  world_id: number
  world_name: string
  owner_name: string
  total_calls: number
  total_tokens: number
  prompt_tokens: number
  cached_tokens: number
  cache_hit_rate_pct: number
  last_at: string | null
}

/** 图表配色：本页三张图共用一份（居民曲线、群视界曲线、单 AI 柱状图） */
function chartColors(isDark: boolean) {
  return isDark ? {
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
}

/** 每日 token 面积图 + 命中率曲线。命中率走右轴——两者量纲差几个数量级。
 *  居民 AI 与群视界两个版面画法完全一样，只差标题与数据源，所以只有这一份实现。 */
function DailyTokenChart({ data, title }: { data: DailyPoint[]; title: string }) {
  const t = useT()
  const lang = useLang()
  const isDark = useIsDark()
  const colors = chartColors(isDark)

  if (data.length === 0) return null
  return (
    <div className="bg-surface rounded-dialog border border-border p-5">
      <h3 className="text-sm font-semibold text-textPrimary mb-4">{title}</h3>
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 5, right: 5, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} />
            <XAxis dataKey="date" tick={{ fontSize: 11, fill: colors.text }} tickFormatter={v => v.slice(5)} />
            <YAxis
              yAxisId="tokens"
              tick={{ fontSize: 11, fill: colors.text }}
              tickFormatter={v => fmtTokenNum(v, lang)}
              width={55}
            />
            <YAxis yAxisId="hit" orientation="right" unit="%" tick={{ fontSize: 11, fill: colors.text }} width={45} />
            <Tooltip
              contentStyle={{
                backgroundColor: isDark ? '#1F2937' : '#FFFFFF',
                border: `1px solid ${colors.grid}`,
                borderRadius: '12px',
                fontSize: '12px',
              }}
              formatter={(value: number, name: string) =>
                name === t('admin:cacheHitRate') ? [`${value}%`, name] : [fmtTokenNum(value, lang), name]
              }
            />
            <Legend wrapperStyle={{ fontSize: '11px', flexWrap: 'wrap' }} />
            <Area yAxisId="tokens" type="monotone" dataKey="total_tokens" stroke={colors.prompt} fill={colors.prompt} fillOpacity={0.15} name={t('admin:totalTokens')} />
            <Line yAxisId="hit" type="monotone" dataKey="cache_hit_rate_pct" stroke={colors.cached} strokeWidth={2} dot={false} name={t('admin:cacheHitRate')} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

export default function UsageDashboardTab() {
  const t = useT()
  const lang = useLang()
  const [view, setView] = useState<UsageView>('residents')
  const [days, setDays] = useState(30)
  const [global, setGlobal] = useState<GlobalStats | null>(null)
  const [userRows, setUserRows] = useState<UserAgentRow[]>([])
  const [dailyData, setDailyData] = useState<DailyPoint[]>([])
  const [worldRows, setWorldRows] = useState<WorldUsageRow[]>([])
  const [worldDaily, setWorldDaily] = useState<DailyPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedUsers, setExpandedUsers] = useState<Set<number>>(new Set())
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null)
  const [agentDaily, setAgentDaily] = useState<DailyPoint[]>([])
  const [agentDailyLoading, setAgentDailyLoading] = useState(false)
  const isDark = useIsDark()

  const loadData = async (d: number) => {
    setLoading(true)
    try {
      // 两个版面的账本是分开的：居民 AI 走 usage_daily（scope=residents，把 agent_id=0
      // 那条群视界零头剔掉），群视界走 world_llm_usage —— 后者是唯一带 world_id 的表。
      const [g, u, w, dd, wd] = await Promise.all([
        api.get<GlobalStats>(`/admin/usage/global?days=${d}&scope=residents`),
        api.get<UserAgentRow[]>(`/admin/usage/by-user?days=${d}`),
        api.get<WorldUsageRow[]>(`/admin/usage/worlds?days=${d}`).catch(() => []),
        // 每日曲线各自独立取：任一端点挂了不该把整页拖成空白
        api.get<DailyPoint[]>(`/admin/usage/global/daily?days=${d}&scope=residents`).catch(() => []),
        api.get<DailyPoint[]>(`/admin/usage/worlds/daily?days=${d}`).catch(() => []),
      ])
      setGlobal(g)
      setUserRows(Array.isArray(u) ? u : [])
      setWorldRows(Array.isArray(w) ? w : [])
      setDailyData(Array.isArray(dd) ? dd : [])
      setWorldDaily(Array.isArray(wd) ? wd : [])
    } catch {
      setGlobal(null); setUserRows([]); setWorldRows([]); setDailyData([]); setWorldDaily([])
    } finally { setLoading(false) }
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

  const colors = chartColors(isDark)

  // 按用户分组：用户之间按用量降序（后端给的是注册顺序 u.id，展示上没意义），
  // 组内 AI 后端已按 total_tokens 降序
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
  const userEntries = [...userGroups.entries()].sort((a, b) => b[1].total - a[1].total)

  // 群视界汇总：列表本身就是区间聚合，卡片直接合计。命中率必须 sum(cached)/sum(prompt)，
  // 拿每天的比例再平均会被小样本天数带偏
  const worldSummary = {
    calls: worldRows.reduce((s, w) => s + (w.total_calls || 0), 0),
    tokens: worldRows.reduce((s, w) => s + (w.total_tokens || 0), 0),
    prompt: worldRows.reduce((s, w) => s + (w.prompt_tokens || 0), 0),
    cached: worldRows.reduce((s, w) => s + (w.cached_tokens || 0), 0),
  }

  return (
    <div className="space-y-5">
      {/* 居民 AI 与群视界分两个版面：两边的账本、量级都不同（群视界 30 天 4 亿 vs 居民 2700 万），
          混在一页里读不出任何结论 */}
      <UnderlineTabs
        items={[
          { key: 'residents', label: t('admin:usageTabResidents'), icon: <Users size={14} /> },
          { key: 'worlds', label: t('admin:usageTabWorlds'), icon: <Globe2 size={14} /> },
        ]}
        value={view}
        onChange={setView}
      />

      {/* 日期选择：两个版面共用 */}
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
            {d} {t('admin:daysSuffix')}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex justify-center py-16"><Loader2 className="animate-spin" size={28} /></div>
      ) : view === 'residents' ? (
        <>
          {/* 居民 AI 汇总 */}
          {global && (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {[
                { label: t('admin:totalTokens'), value: fmtTokenNum(global.total_tokens, lang), icon: Activity },
                { label: t('admin:totalCalls'), value: global.total_calls, icon: BarChart3 },
                { label: t('admin:cacheHitRate'), value: `${global.cache_hit_rate_pct}%`, icon: Percent },
                { label: t('admin:activeAgents'), value: global.unique_agents, icon: Activity },
                { label: t('admin:activeUsers'), value: global.unique_users, icon: Users },
              ].map(item => (
                <div key={item.label} className="bg-surface rounded-card border border-border p-4 text-center">
                  <item.icon size={16} className="text-primary-400 mx-auto mb-1" />
                  <div className="text-lg font-semibold text-textPrimary">{item.value}</div>
                  <div className="text-3xs text-textMuted">{item.label}</div>
                </div>
              ))}
            </div>
          )}

          <DailyTokenChart data={dailyData} title={t('admin:residentsDailyTokens')} />

          {/* 按用户明细 */}
          <div className="bg-surface rounded-dialog border border-border overflow-hidden">
            <div className="px-5 py-3 border-b border-border">
              <h3 className="text-sm font-semibold text-textPrimary">{t('admin:usageByUser')}</h3>
            </div>
            <div className="divide-y divide-border/60">
              {userEntries.map(([uid, ug]) => (
                <div key={uid}>
                  {/* 用户行 */}
                  <button
                    onClick={() => toggleUser(uid)}
                    className="w-full flex items-center gap-3 px-5 py-3 hover:bg-elevated transition-colors text-left"
                  >
                    {expandedUsers.has(uid) ? <ChevronDown size={14} className="text-textMuted" /> : <ChevronRight size={14} className="text-textMuted" />}
                    <span className="text-sm font-medium text-textPrimary">{ug.username}</span>
                    <span className="text-xs text-textMuted ml-auto">{ug.agents.length} {t('admin:aiCount')}</span>
                    <span className="text-sm font-mono text-textPrimary ml-4">{fmtTokenNum(ug.total, lang)} Token</span>
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
                          <span className="text-textMuted ml-auto">{t('admin:cacheHitRate')} {a.cache_hit_rate_pct}%</span>
                          <span className="text-textMuted ml-4">{a.total_calls} {t('admin:callsCount')}</span>
                          <span className="text-textPrimary font-mono ml-4">{fmtTokenNum(a.total_tokens, lang)}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {userEntries.length === 0 && (
                <div className="text-center py-12 text-textMuted text-sm">{t('admin:noData')}</div>
              )}
            </div>
          </div>

          {/* 选中 AI 的每日图表 */}
          {selectedAgentId && (
            <div className="bg-surface rounded-dialog border border-border p-5">
              <h3 className="text-sm font-semibold text-textPrimary mb-4">
                {t('admin:aiDailyTokens').replace('{id}', String(selectedAgentId))}
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
                        formatter={(value: number) => [fmtTokenNum(value, lang), '']}
                      />
                      <Legend wrapperStyle={{ fontSize: '11px', flexWrap: 'wrap' }} />
                      <Bar dataKey="prompt_tokens" stackId="a" fill={colors.prompt} name="Prompt" />
                      <Bar dataKey="completion_tokens" stackId="a" fill={colors.completion} name="Completion" />
                      <Bar dataKey="reasoning_tokens" stackId="a" fill={colors.reasoning} name={t('admin:thinking')} />
                      <Bar dataKey="cached_tokens" stackId="a" fill={colors.cached} name={t('admin:cached')} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <div className="text-center py-8 text-textMuted text-sm">{t('admin:noAiDailyData')}</div>
              )}
            </div>
          )}
        </>
      ) : (
        <>
          {/* 群视界汇总 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { label: t('admin:worldsActive'), value: worldRows.length, icon: Globe2 },
              { label: t('admin:totalCalls'), value: worldSummary.calls, icon: BarChart3 },
              { label: t('admin:totalTokens'), value: fmtTokenNum(worldSummary.tokens, lang), icon: Activity },
              { label: t('admin:cacheHitRate'), value: `${cacheHitRatePct(worldSummary.prompt, worldSummary.cached)}%`, icon: Percent },
            ].map(item => (
              <div key={item.label} className="bg-surface rounded-card border border-border p-4 text-center">
                <item.icon size={16} className="text-primary-400 mx-auto mb-1" />
                <div className="text-lg font-semibold text-textPrimary">{item.value}</div>
                <div className="text-3xs text-textMuted">{item.label}</div>
              </div>
            ))}
          </div>

          <DailyTokenChart data={worldDaily} title={t('admin:worldsDailyTokens')} />

          {/* 按世界明细 */}
          <div className="bg-surface rounded-dialog border border-border overflow-hidden">
            <div className="px-5 py-3 border-b border-border">
              <h3 className="text-sm font-semibold text-textPrimary">{t('admin:worldsUsage')}</h3>
            </div>
            {worldRows.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-xs whitespace-nowrap">
                  <thead className="bg-canvas">
                    <tr className="text-textMuted">
                      <th className="text-left py-2 px-4 font-medium">{t('admin:worldColWorld')}</th>
                      <th className="text-left py-2 px-4 font-medium hidden md:table-cell">{t('admin:worldColOwner')}</th>
                      <th className="text-right py-2 px-4 font-medium">{t('admin:totalCalls')}</th>
                      <th className="text-right py-2 px-4 font-medium">{t('admin:totalTokens')}</th>
                      <th className="text-right py-2 px-4 font-medium">{t('admin:cacheHitRate')}</th>
                      <th className="text-right py-2 px-4 font-medium hidden md:table-cell">{t('admin:worldColLastAt')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/60">
                    {worldRows.map(w => (
                      <tr key={w.world_id} className="hover:bg-elevated transition-colors">
                        <td className="py-2.5 px-4 text-textPrimary font-medium">
                          {w.world_name} <span className="text-textMuted font-normal">#{w.world_id}</span>
                        </td>
                        <td className="py-2.5 px-4 text-textMuted hidden md:table-cell">{w.owner_name || '-'}</td>
                        <td className="py-2.5 px-4 text-right text-textMuted font-mono">{w.total_calls}</td>
                        <td className="py-2.5 px-4 text-right text-textPrimary font-mono">{fmtTokenNum(w.total_tokens, lang)}</td>
                        <td className="py-2.5 px-4 text-right text-textPrimary font-mono">{w.cache_hit_rate_pct}%</td>
                        <td className="py-2.5 px-4 text-right text-textMuted hidden md:table-cell">
                          {w.last_at ? formatRelativeTime(w.last_at, lang) : '-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center py-10 text-textMuted text-sm">{t('admin:worldsUsageEmpty')}</div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
