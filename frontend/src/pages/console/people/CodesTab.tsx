// 兑换码：生成与列表（一次性 / 不限人次的活动码）

import { Fragment, useState, useEffect } from 'react'
import { api } from '../../../api/client'
import { useT, useLang } from '../../../i18n/I18nContext'
import { fmtDate } from '../../../utils/time'

interface CodeRow {
  code: string
  quota_amount: number
  code_type: string
  expires_at: string | null
  note: string | null
  usage_limit: number | null      // null = 不限人次；1 = 一次性
  redeemed_count: number
  last_redeemed_at: string | null
  state: 'available' | 'exhausted' | 'expired'
  is_api_pool: boolean
  created_at: string | null
}

interface RedemptionRow {
  user_id: number
  username: string | null
  quota_amount: number
  code_type: string
  redeemed_at: string | null
}

// 状态由后端算（含过期判断），前端只负责上色
const STATE_COLORS: Record<CodeRow['state'], string> = {
  available: 'text-mint-400',
  exhausted: 'text-textMuted',
  expired: 'text-rose-400',
}

export default function CodesTab() {
  const t = useT()
  const lang = useLang()
  const [codes, setCodes] = useState<CodeRow[]>([])
  const [quota, setQuota] = useState(3)
  const [days, setDays] = useState(30)
  const [usageLimit, setUsageLimit] = useState('1')   // 字符串：空 = 不限人次
  const [codeType, setCodeType] = useState('ai_quota')
  const [note, setNote] = useState('')              // v0.1.5
  const [maxUsage, setMaxUsage] = useState<number | null>(null)  // v0.1.5
  const [isApiPool, setIsApiPool] = useState(false)  // v0.1.5
  const [generated, setGenerated] = useState<CodeRow | null>(null)
  const [generating, setGenerating] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [records, setRecords] = useState<Record<string, RedemptionRow[] | undefined>>({})

  const CODE_TYPES: Record<string, string> = {
    ai_quota: t('admin:codeTypeDefault'),
    api_credit: t('admin:creditApi'),
    agent_bundle: t('admin:creditBundle'),
    file_quota: t('admin:creditFile'),
  }

  const isFileQuota = (type: string) => type === 'file_size' || type === 'file_quota'
  const limitLabel = (limit: number | null) => (limit === null ? '∞' : String(limit))

  const loadCodes = async () => {
    try {
      const data = await api.get('/admin/redemption-codes')
      setCodes(data)
    } catch (err) { console.error(err) }
  }

  useEffect(() => { loadCodes() }, [])

  const handleGenerate = async () => {
    setGenerating(true)
    setGenerated(null)
    const limit = usageLimit.trim() === '' ? null : Math.max(1, parseInt(usageLimit) || 1)
    try {
      const data = await api.post('/admin/redemption-codes', {
        quota_amount: quota,
        expires_in_days: days,
        code_type: codeType,
        note: note.trim() || null,
        max_usage: maxUsage ?? null,
        is_api_pool: isApiPool,
        usage_limit: limit,
      })
      setGenerated({ ...data, redeemed_count: 0, state: 'available' } as CodeRow)
      loadCodes()
    } catch (err: any) {
      console.error('生成兑换码失败:', err)
      alert(err?.message || err?.detail || t('admin:generateFailed'))
    } finally {
      setGenerating(false)
    }
  }

  const toggleRecords = async (code: string) => {
    if (expanded === code) { setExpanded(null); return }
    setExpanded(code)
    if (records[code]) return
    try {
      const data = await api.get(`/admin/redemption-codes/${code}/redemptions`)
      setRecords((prev) => ({ ...prev, [code]: data }))
    } catch (err) { console.error(err) }
  }

  return (
    <div className="space-y-6">
      {/* 生成兑换码 */}
 <div className="card p-5">
        <h3 className="font-semibold text-textPrimary mb-3">{t('admin:generateCode')}</h3>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs mb-1 text-textSecondary">{t('admin:codeType')}</label>
            <select value={codeType} onChange={(e) => setCodeType(e.target.value)}
              className="field field-inline">
              {Object.entries(CODE_TYPES).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs mb-1 text-textSecondary">{t('me:quota')}</label>
            <input type="number" value={quota} onChange={(e) => setQuota(parseInt(e.target.value) || 0)}
              min={1} max={isFileQuota(codeType) ? 1024 : 100}
              className="field field-sm w-24" />
          </div>
          <div>
            <label className="block text-xs mb-1 text-textSecondary">{t('admin:expiresDays')}</label>
            <input type="number" value={days} onChange={(e) => setDays(parseInt(e.target.value) || 1)}
              min={1} max={365}
              className="w-20 px-2 py-1.5 border border-border bg-canvas rounded-card text-sm text-textPrimary" />
          </div>
          <div>
            <label className="block text-xs mb-1 text-textSecondary">{t('admin:codeUsageLimit')}</label>
            <input type="number" value={usageLimit} onChange={(e) => setUsageLimit(e.target.value)}
              min={1} placeholder={t('admin:codeUsageUnlimited')}
              className="field field-sm w-28" />
          </div>
          <div className="flex items-center gap-1.5 self-end mb-1">
            <input type="checkbox" id="isApiPool" checked={isApiPool} onChange={(e) => setIsApiPool(e.target.checked)}
              className="w-4 h-4 rounded border-border bg-canvas text-primary-500" />
            <label htmlFor="isApiPool" className="text-xs text-textSecondary">{t('admin:isApiPool')}</label>
          </div>
          <button
            onClick={handleGenerate}
            disabled={generating || quota < 1 || days < 1}
            className="btn btn-md btn-primary"
          >
            {generating ? t('admin:generating') : t('admin:generate')}
          </button>
        </div>
        {/* 详细选项 */}
        <div className="flex flex-wrap items-end gap-3 mt-3 pt-3 border-t border-border/40">
          <div>
            <label className="block text-xs mb-1 text-textSecondary">{t('admin:codeNote')}</label>
            <input type="text" value={note} onChange={(e) => setNote(e.target.value)}
              placeholder={t('admin:codeNotePlaceholder')}
              className="w-48 px-2 py-1.5 border border-border bg-canvas rounded-card text-sm text-textPrimary placeholder:text-textMuted" />
          </div>
          <div>
            <label className="block text-xs mb-1 text-textSecondary">{t('admin:maxUsageLabel')}</label>
            <input type="number" value={maxUsage ?? ''} onChange={(e) => setMaxUsage(e.target.value ? parseInt(e.target.value) : null)}
              min={1}
              className="w-28 px-2 py-1.5 border border-border bg-canvas rounded-card text-sm text-textPrimary" />
          </div>
          <span className="text-2xs text-textMuted pb-1.5">{t('admin:codeUsageHintWithLabel')}</span>
          <span className="text-2xs text-textMuted pb-1.5">{t('admin:balanceHint')}</span>
        </div>
        {generated && (
          <div className="mt-3 p-3 bg-mint-400/10 border border-mint-400/20 rounded-card">
            <p className="text-sm font-mono text-mint-400 break-all">{generated.code}</p>
            <p className="text-xs text-mint-400 mt-1">
              {generated.usage_limit === null ? t('admin:codeCampaignWarning') : t('admin:codeSingleWarning')}
            </p>
          </div>
        )}
      </div>

      {/* 已生成的兑换码 */}
 <div className="card p-5">
        <h3 className="font-semibold text-textPrimary mb-3">{t('admin:codeList')}</h3>
        <div className="max-h-[60vh] overflow-auto">
          <table className="w-full min-w-[820px] text-sm text-textPrimary">
            <thead className="sticky top-0 z-10 bg-surface">
              <tr className="border-b border-border">
                <th className="text-left py-2 px-3 font-medium text-textSecondary whitespace-nowrap">{t('admin:codesColCode')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary whitespace-nowrap">{t('admin:codesColType')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary whitespace-nowrap">{t('admin:codesColAmount')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary whitespace-nowrap">{t('admin:codesColNote')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary whitespace-nowrap">{t('admin:codesColRedeemed')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary whitespace-nowrap">{t('admin:codesColExpires')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary whitespace-nowrap">{t('admin:codesColStatus')}</th>
              </tr>
            </thead>
            <tbody>
              {codes.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-6 text-center text-xs text-textMuted">{t('admin:codesRecordsEmpty')}</td>
                </tr>
              )}
              {codes.map((c) => (
                <Fragment key={c.code}>
                  <tr className="border-b border-border/50">
                    <td className="py-2 px-3 font-mono text-xs text-textPrimary whitespace-normal break-all align-top">
                      {c.code}
                      {c.is_api_pool && <span className="ml-1 px-1 py-0.5 bg-accent-400/10 text-accent-400 rounded text-3xs">{t('admin:pool')}</span>}
                    </td>
                    <td className="py-2 px-3 text-xs text-textSecondary align-top whitespace-nowrap">{CODE_TYPES[c.code_type] || c.code_type || t('admin:codeTypeDefault')}</td>
                    <td className="py-2 px-3 text-textPrimary align-top whitespace-nowrap tabular-nums">{c.quota_amount}{isFileQuota(c.code_type) ? ' MB' : ''}</td>
                    <td className="py-2 px-3 text-xs text-textMuted align-top">
                      <div className="whitespace-normal break-words max-w-[200px]">{c.note || '-'}</div>
                    </td>
                    <td className="py-2 px-3 text-xs text-textSecondary align-top whitespace-nowrap tabular-nums">
                      {c.redeemed_count > 0 ? (
                        <button onClick={() => toggleRecords(c.code)}
                          className="inline-flex items-center gap-1 hover:text-textPrimary transition-colors"
                          title={t('admin:codesRecordsTitle')}>
                          {c.redeemed_count} / {limitLabel(c.usage_limit)}
                          <span className="text-3xs text-textMuted ml-1">{expanded === c.code ? '▾' : '▸'}</span>
                        </button>
                      ) : (
                        <span className="text-textMuted">{c.redeemed_count} / {limitLabel(c.usage_limit)}</span>
                      )}
                    </td>
                    <td className="py-2 px-3 text-xs text-textSecondary align-top whitespace-nowrap">{c.expires_at ? fmtDate(c.expires_at, lang) : '-'}</td>
                    <td className={`py-2 px-3 text-xs align-top whitespace-nowrap ${STATE_COLORS[c.state] || 'text-textMuted'}`}>
                      {c.state === 'available' && t('admin:available')}
                      {c.state === 'exhausted' && t('admin:codeStatusExhausted')}
                      {c.state === 'expired' && t('admin:codeStatusExpired')}
                    </td>
                  </tr>
                  {expanded === c.code && (
                    <tr className="border-b border-border/50 bg-canvas/50">
                      <td colSpan={7} className="px-3 py-3">
                        <div className="text-xs font-medium text-textSecondary mb-2">{t('admin:codesRecordsTitle')}</div>
                        {!records[c.code] ? (
                          <div className="text-xs text-textMuted">…</div>
                        ) : records[c.code]!.length === 0 ? (
                          <div className="text-xs text-textMuted">{t('admin:codesRecordsEmpty')}</div>
                        ) : (
                          <div className="max-h-56 overflow-auto rounded-card border border-border/50">
                            <table className="w-full min-w-[460px] text-xs">
                              <thead className="sticky top-0 z-10 bg-canvas">
                                <tr className="text-textMuted border-b border-border/50">
                                  <th className="text-left py-1.5 px-2 font-medium">{t('admin:codesRecordsUser')}</th>
                                  <th className="text-left py-1.5 px-2 font-medium">{t('admin:codesColAmount')}</th>
                                  <th className="text-left py-1.5 px-2 font-medium">{t('admin:codesRecordsTime')}</th>
                                </tr>
                              </thead>
                              <tbody>
                                {records[c.code]!.map((r) => (
                                  <tr key={`${r.user_id}-${r.redeemed_at}`} className="border-b border-border/30 last:border-0">
                                    <td className="py-1.5 px-2 align-top text-textPrimary whitespace-normal break-words">
                                      {r.username || `uid:${r.user_id}`}
                                    </td>
                                    <td className="py-1.5 px-2 align-top text-textSecondary whitespace-nowrap tabular-nums">
                                      {r.quota_amount}{isFileQuota(r.code_type) ? ' MB' : ''}
                                    </td>
                                    <td className="py-1.5 px-2 align-top text-textSecondary whitespace-nowrap">
                                      {r.redeemed_at ? fmtDate(r.redeemed_at, lang) : '-'}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
