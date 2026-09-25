import { useState, useEffect } from 'react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'
import { getApiKeyUrl } from '../../../utils/providers'
import { Key, Plus, Trash2, ToggleLeft, ToggleRight, Loader2, BarChart3, X } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts'
import { Dialog } from '../../../components/ui'

interface PoolKey {
  id: number
  name: string
  api_base_url: string
  api_key_preview: string
  is_active: boolean
  priority: number
  concurrent_limit: number | null
  created_at: string | null
  updated_at: string | null
}

interface KeyStats {
  key_id: number
  key_name: string
  overview: {
    total_requests: number
    total_tokens: number
    total_credit: number
    active_users: number
    days: number
  }
  daily: Array<{ day: string; tokens: number; requests: number }>
  model_distribution: Array<{ model: string; count: number; tokens: number }>
}

const COLORS = ['#3b82f6', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#ec4899']

export default function ApiKeyPoolTab() {
  const t = useT()
  const [keys, setKeys] = useState<PoolKey[]>([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [statsKeyId, setStatsKeyId] = useState<number | null>(null)

  const loadKeys = async () => {
    try {
      const data = await api.get('/admin/api-key-pool')
      setKeys(data)
    } catch (err) { console.error(err) }
    finally { setLoading(false) }
  }

  useEffect(() => { loadKeys() }, [])

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(t('admin.confirmDeletePoolKey').replace('{name}', name))) return
    try {
      await api.delete(`/admin/api-key-pool/${id}`)
      loadKeys()
    } catch (err: any) {
      alert(err?.message || t('admin.deleteFailed'))
    }
  }

  const handleToggle = async (id: number, currentActive: boolean) => {
    try {
      await api.put(`/admin/api-key-pool/${id}`, { is_active: !currentActive })
      loadKeys()
    } catch (err: any) {
      alert(err?.message || t('admin.operationFailed'))
    }
  }

  if (loading) return (
    <div className="flex justify-center py-16">
      <Loader2 className="animate-spin text-textSecondary" size={28} />
    </div>
  )

  return (
    <div className="space-y-5 pb-8">
      {/* 头部 */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-textPrimary flex items-center gap-2">
            <Key size={16} className="text-accent-400" /> {t('admin.apiKeyPool')}
          </h3>
          <p className="text-xs text-textMuted mt-0.5">
            {t('admin.apiKeyPoolDesc')}
          </p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-primary-500 text-white rounded-card hover:bg-primary-600 text-xs font-medium transition-colors"
        >
          <Plus size={14} /> {t('admin.addKey')}
        </button>
      </div>

      {/* Key 列表 */}
      {keys.length === 0 ? (
        <div className="bg-surface rounded-card border border-border p-10 text-center">
          <Key size={32} className="mx-auto mb-3 text-textMuted" />
          <p className="text-sm text-textSecondary">{t('admin.noApiKeys')}</p>
          <p className="text-xs text-textMuted mt-1">{t('admin.noKeysDesc')}</p>
          <button
            onClick={() => setShowAdd(true)}
            className="mt-4 px-4 py-2 bg-primary-500 text-white rounded-card hover:bg-primary-600 text-sm font-medium transition-colors"
          >
            {t('admin.addFirstKey')}
          </button>
        </div>
      ) : (
        <div className="bg-surface rounded-card border border-border overflow-x-auto">
          <table className="w-full text-sm text-textPrimary">
            <thead>
              <tr className="border-b border-border bg-canvas">
                <th className="text-left py-2.5 px-3 text-xs font-medium text-textSecondary">{t('admin.keyColName')}</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-textSecondary">{t('admin.keyColApiUrl')}</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-textSecondary">{t('admin.keyColKey')}</th>
                <th className="text-center py-2.5 px-3 text-xs font-medium text-textSecondary">{t('admin.keyColPriority')}</th>
                <th className="text-center py-2.5 px-3 text-xs font-medium text-textSecondary">{t('admin.keyColConcurrent')}</th>
                <th className="text-center py-2.5 px-3 text-xs font-medium text-textSecondary">{t('admin.keyColStatus')}</th>
                <th className="text-center py-2.5 px-3 text-xs font-medium text-textSecondary">{t('admin.keyColStats')}</th>
                <th className="text-right py-2.5 px-3 text-xs font-medium text-textSecondary">{t('admin.keyColAction')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {keys.map(k => (
                <tr key={k.id} className={`hover:bg-elevated/50 transition-colors ${!k.is_active ? 'opacity-50' : ''}`}>
                  <td className="py-2.5 px-3 font-medium">{k.name}</td>
                  <td className="py-2.5 px-3 text-xs text-textMuted font-mono max-w-[160px] truncate">{k.api_base_url}</td>
                  <td className="py-2.5 px-3 text-xs text-textMuted font-mono">
                    <span title={t('admin.keyEncryptedHint')}>{k.api_key_preview || '—'}</span>
                  </td>
                  <td className="py-2.5 px-3 text-center">{k.priority}</td>
                  <td className="py-2.5 px-3 text-center text-xs text-textMuted">
                    {k.concurrent_limit ?? t('admin.keyConcurrentDefault')}
                  </td>
                  <td className="py-2.5 px-3 text-center">
                    <button
                      onClick={() => handleToggle(k.id, k.is_active)}
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-control text-xs font-medium transition-colors ${
                        k.is_active
                          ? 'bg-mint-400/10 text-mint-400'
                          : 'bg-rose-400/10 text-rose-400'
                      }`}
                      title={k.is_active ? t('admin.clickToDisable') : t('admin.clickToEnable')}
                    >
                      {k.is_active ? <ToggleRight size={14} /> : <ToggleLeft size={14} />}
                      {k.is_active ? t('common.enabled') : t('common.disabled')}
                    </button>
                  </td>
                  <td className="py-2.5 px-3 text-center">
                    <button
                      onClick={() => setStatsKeyId(k.id)}
                      className="p-1.5 rounded-control hover:bg-primary-500/10 text-textMuted hover:text-primary-400 transition-colors"
                      title={t('admin.viewKeyStats')}
                    >
                      <BarChart3 size={14} />
                    </button>
                  </td>
                  <td className="py-2.5 px-3 text-right">
                    <button
                      onClick={() => handleDelete(k.id, k.name)}
                      className="p-1.5 rounded-control hover:bg-rose-400/10 text-textMuted hover:text-rose-400 transition-colors"
                      title={t('common.delete')}
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 添加弹窗 */}
      {showAdd && <AddPoolKeyModal onClose={() => setShowAdd(false)} onSaved={() => { setShowAdd(false); loadKeys() }} />}

      {/* Key 统计弹窗 */}
      {statsKeyId && <KeyStatsModal keyId={statsKeyId} onClose={() => setStatsKeyId(null)} />}
    </div>
  )
}

function AddPoolKeyModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const t = useT()
  const [name, setName] = useState('')
  const [apiBaseUrl, setApiBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [priority, setPriority] = useState(0)
  const [concurrentLimit, setConcurrentLimit] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSave = async () => {
    if (!name.trim() || !apiKey.trim()) return
    setLoading(true)
    setError('')
    try {
      await api.post('/admin/api-key-pool', {
        name: name.trim(),
        api_base_url: apiBaseUrl.trim() || null,
        api_key: apiKey.trim(),
        priority,
        concurrent_limit: concurrentLimit,
      })
      onSaved()
    } catch (err: any) {
      setError(err?.message || err?.detail || t('admin.saveFailed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog onClose={onClose} className="flex items-center justify-center">
      <div
        className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-md mx-4 shadow-2xl shadow-black/30"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold mb-1 text-textPrimary flex items-center gap-2">
          <Key size={18} className="text-accent-400" /> {t('admin.addApiKeyModal')}
        </h3>
        <p className="text-xs text-textMuted mb-4">
          {t('admin.addKeyEncryptNote')}
        </p>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium mb-1 text-textSecondary">{t('admin.keyNameRequired')}</label>
            <input
              type="text" value={name} onChange={(e) => setName(e.target.value)}
              placeholder={t('admin.keyNamePlaceholder')}
              className="w-full px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1 text-textSecondary">{t('admin.keyApiBaseUrl')}</label>
            <input
              type="text" value={apiBaseUrl} onChange={(e) => setApiBaseUrl(e.target.value)}
              placeholder={t('admin.keyApiUrlPlaceholder')}
              className="w-full px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
            />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1 text-textSecondary flex items-center gap-2">
              {t('admin.keyApiKey')}
              {apiBaseUrl && getApiKeyUrl(apiBaseUrl) && (
                <a
                  href={getApiKeyUrl(apiBaseUrl)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-3xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 underline underline-offset-2 font-normal"
                >
                  获取 API Key →
                </a>
              )}
            </label>
            <input
              type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-..."
              className="w-full px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
            />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1 text-textSecondary">{t('admin.keyPriorityLabel')}</label>
            <input
              type="number" value={priority} onChange={(e) => setPriority(parseInt(e.target.value) || 0)}
              min={0} max={100}
              className="w-24 px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
            />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1 text-textSecondary">{t('admin.keyConcurrentLimit')}</label>
            <input
              type="number" value={concurrentLimit ?? ''} onChange={(e) => setConcurrentLimit(e.target.value ? parseInt(e.target.value) : null)}
              min={1} placeholder={t('admin.keyConcurrentLimitPlaceholder')}
              className="w-28 px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
            />
          </div>
        </div>

        {error && <div className="mt-3 text-sm text-rose-400">{error}</div>}

        <div className="flex gap-2 mt-4">
          <button onClick={onClose}
            className="btn btn-md btn-outline flex-1">
            {t('common.cancel')}
          </button>
          <button onClick={handleSave} disabled={!name.trim() || !apiKey.trim() || loading}
            className="btn btn-md btn-primary flex-1">
            {loading ? t('admin.adding') : t('admin.add')}
          </button>
        </div>
      </div>
    </Dialog>
  )
}

function KeyStatsModal({ keyId, onClose }: { keyId: number; onClose: () => void }) {
  const t = useT()
  const [stats, setStats] = useState<KeyStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(30)

  const loadStats = async (d: number) => {
    setLoading(true)
    try {
      const data = await api.get(`/admin/api-key-pool/${keyId}/stats?days=${d}`)
      setStats(data)
    } catch (err) { console.error(err) }
    finally { setLoading(false) }
  }

  useEffect(() => { loadStats(days) }, [keyId, days])

  const handleDaysChange = (newDays: number) => {
    setDays(newDays)
    loadStats(newDays)
  }

  return (
    <Dialog onClose={onClose} className="flex items-center justify-center overflow-y-auto">
      <div
        className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-3xl mx-4 my-8 shadow-2xl shadow-black/30 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-textPrimary flex items-center gap-2">
            <BarChart3 size={18} className="text-primary-400" />
            {t('admin.keyStatsTitle').replace('{name}', stats?.key_name || `#${keyId}`)}
          </h3>
          <div className="flex items-center gap-2">
            <select
              value={days}
              onChange={(e) => handleDaysChange(parseInt(e.target.value))}
              className="px-2 py-1 rounded-control border border-border bg-canvas text-xs text-textPrimary"
            >
              <option value={7}>{t('admin.lastNDays').replace('{n}', '7')}</option>
              <option value={30}>{t('admin.lastNDays').replace('{n}', '30')}</option>
              <option value={90}>{t('admin.lastNDays').replace('{n}', '90')}</option>
            </select>
            <button onClick={onClose}
              className="icon-btn-sm text-textMuted">
              <X size={18} />
            </button>
          </div>
        </div>

        {loading ? (
          <div className="flex justify-center py-16">
            <Loader2 className="animate-spin text-textSecondary" size={28} />
          </div>
        ) : !stats ? (
          <p className="text-textMuted text-center py-10">{t('common.loadFailed')}</p>
        ) : (
          <div className="space-y-5">
            {/* 概览卡片 */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="bg-canvas rounded-card border border-border p-3 text-center">
                <div className="text-2xl font-bold text-primary-400">{stats.overview.total_requests.toLocaleString()}</div>
                <div className="text-xs text-textMuted mt-1">{t('admin.keyStatsTotalReqs')}</div>
              </div>
              <div className="bg-canvas rounded-card border border-border p-3 text-center">
                <div className="text-2xl font-bold text-accent-400">{(stats.overview.total_tokens / 1000).toFixed(0)}K</div>
                <div className="text-xs text-textMuted mt-1">{t('admin.keyStatsTotalTokens')}</div>
              </div>
              <div className="bg-canvas rounded-card border border-border p-3 text-center">
                <div className="text-2xl font-bold text-mint-400">{stats.overview.total_credit.toFixed(1)}</div>
                <div className="text-xs text-textMuted mt-1">{t('admin.keyStatsTotalCredit')}</div>
              </div>
              <div className="bg-canvas rounded-card border border-border p-3 text-center">
                <div className="text-2xl font-bold text-textPrimary">{stats.overview.active_users}</div>
                <div className="text-xs text-textMuted mt-1">{t('admin.keyStatsActiveUsers')}</div>
              </div>
            </div>

            {/* Token 消耗趋势 */}
            {stats.daily.length > 0 && (
              <div>
                <h4 className="text-sm font-medium text-textPrimary mb-2">{t('admin.keyStatsTokenTrend')}</h4>
                <div className="bg-canvas rounded-card border border-border p-3">
                  <ResponsiveContainer width="100%" height={220}>
                    <LineChart data={stats.daily}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                      <XAxis dataKey="day" tick={{ fontSize: 11 }} stroke="var(--color-text-muted)" />
                      <YAxis tick={{ fontSize: 11 }} stroke="var(--color-text-muted)" />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: 'var(--color-elevated)',
                          border: '1px solid var(--color-border)',
                          borderRadius: '12px',
                          fontSize: '12px',
                        }}
                        formatter={(value: number) => [(value / 1000).toFixed(0) + 'K', t('admin.tokens')]}
                      />
                      <Line type="monotone" dataKey="tokens" stroke="#3b82f6" strokeWidth={2} dot={false} name={t('admin.tokens')} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}

            {/* 模型分布饼图 */}
            {stats.model_distribution.length > 0 && (
              <div>
                <h4 className="text-sm font-medium text-textPrimary mb-2">{t('admin.keyStatsModelDist')}</h4>
                <div className="bg-canvas rounded-card border border-border p-3">
                  <ResponsiveContainer width="100%" height={220}>
                    <PieChart>
                      <Pie
                        data={stats.model_distribution}
                        dataKey="tokens"
                        nameKey="model"
                        cx="50%"
                        cy="50%"
                        outerRadius={80}
                        label={({ model, percent }) => `${model} ${(percent * 100).toFixed(0)}%`}
                        labelLine
                      >
                        {stats.model_distribution.map((_, idx) => (
                          <Cell key={idx} fill={COLORS[idx % COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip
                        contentStyle={{
                          backgroundColor: 'var(--color-elevated)',
                          border: '1px solid var(--color-border)',
                          borderRadius: '12px',
                          fontSize: '12px',
                        }}
                        formatter={(value: number) => [(value / 1000).toFixed(0) + 'K', t('admin.tokens')]}
                      />
                      <Legend />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </Dialog>
  )
}
