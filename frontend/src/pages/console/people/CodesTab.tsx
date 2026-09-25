// 兑换码：生成与列表

import { useState, useEffect } from 'react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'

export default function CodesTab() {
  const t = useT()
  const [codes, setCodes] = useState<any[]>([])
  const [quota, setQuota] = useState(3)
  const [days, setDays] = useState(30)
  const [codeType, setCodeType] = useState('ai_quota')
  const [note, setNote] = useState('')              // v0.1.5
  const [maxUsage, setMaxUsage] = useState<number | null>(null)  // v0.1.5
  const [isApiPool, setIsApiPool] = useState(false)  // v0.1.5
  const [generatedCode, setGeneratedCode] = useState('')
  const [generating, setGenerating] = useState(false)

  const CODE_TYPES: Record<string, string> = {
    ai_quota: t('admin.codeTypeDefault'),
    api_credit: t('admin.creditApi'),
    agent_bundle: t('admin.creditBundle'),
    file_quota: t('admin.creditFile'),
  }

  const loadCodes = async () => {
    try {
      const data = await api.get('/admin/redemption-codes')
      setCodes(data)
    } catch (err) { console.error(err) }
  }

  useEffect(() => { loadCodes() }, [])

  const handleGenerate = async () => {
    setGenerating(true)
    setGeneratedCode('')
    try {
      const data = await api.post('/admin/redemption-codes', {
        quota_amount: quota,
        expires_in_days: days,
        code_type: codeType,
        note: note.trim() || null,
        max_usage: maxUsage ?? null,
        is_api_pool: isApiPool,
      })
      setGeneratedCode(data.code)
      loadCodes()
    } catch (err: any) {
      console.error('生成兑换码失败:', err)
      alert(err?.message || err?.detail || t('admin.generateFailed'))
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* 生成兑换码 */}
      <div className="bg-surface rounded-card border border-border p-5">
        <h3 className="font-semibold text-textPrimary mb-3">{t('admin.generateCode')}</h3>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs mb-1 text-textSecondary">{t('admin.codeType')}</label>
            <select value={codeType} onChange={(e) => setCodeType(e.target.value)}
              className="px-2 py-1.5 border border-border bg-canvas rounded-card text-sm text-textPrimary">
              {Object.entries(CODE_TYPES).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs mb-1 text-textSecondary">{t('me.quota')}</label>
            <input type="number" value={quota} onChange={(e) => setQuota(parseInt(e.target.value) || 0)}
              min={1} max={(codeType === 'file_size' || codeType === 'file_quota') ? 1024 : 100}
              className="w-24 px-2 py-1.5 border border-border bg-canvas rounded-card text-sm text-textPrimary" />
          </div>
          <div>
            <label className="block text-xs mb-1 text-textSecondary">{t('admin.expiresDays')}</label>
            <input type="number" value={days} onChange={(e) => setDays(parseInt(e.target.value) || 1)}
              min={1} max={365}
              className="w-20 px-2 py-1.5 border border-border bg-canvas rounded-card text-sm text-textPrimary" />
          </div>
          <div className="flex items-center gap-1.5 self-end mb-1">
            <input type="checkbox" id="isApiPool" checked={isApiPool} onChange={(e) => setIsApiPool(e.target.checked)}
              className="w-4 h-4 rounded border-border bg-canvas text-primary-500" />
            <label htmlFor="isApiPool" className="text-xs text-textSecondary">{t('admin.isApiPool')}</label>
          </div>
          <button
            onClick={handleGenerate}
            disabled={generating || quota < 1 || days < 1}
            className="btn btn-xs btn-primary"
          >
            {generating ? t('admin.generating') : t('admin.generate')}
          </button>
        </div>
        {/* 详细选项 */}
        <div className="flex flex-wrap items-end gap-3 mt-3 pt-3 border-t border-border/40">
          <div>
            <label className="block text-xs mb-1 text-textSecondary">{t('admin.codeNote')}</label>
            <input type="text" value={note} onChange={(e) => setNote(e.target.value)}
              placeholder={t('admin.codeNotePlaceholder')}
              className="w-48 px-2 py-1.5 border border-border bg-canvas rounded-card text-sm text-textPrimary placeholder:text-textMuted" />
          </div>
          <div>
            <label className="block text-xs mb-1 text-textSecondary">{t('admin.maxUsageLabel')}</label>
            <input type="number" value={maxUsage ?? ''} onChange={(e) => setMaxUsage(e.target.value ? parseInt(e.target.value) : null)}
              min={1}
              className="w-28 px-2 py-1.5 border border-border bg-canvas rounded-card text-sm text-textPrimary" />
          </div>
          <span className="text-2xs text-textMuted pb-1.5">
            {t('admin.balanceHint')}
          </span>
        </div>
        {generatedCode && (
          <div className="mt-3 p-3 bg-mint-400/10 border border-mint-400/20 rounded-card">
            <p className="text-sm font-mono text-mint-400 break-all">{generatedCode}</p>
            <p className="text-xs text-mint-400 mt-1">{t('admin.codeOneTimeWarning')}</p>
          </div>
        )}
      </div>

      {/* 已生成的兑换码 */}
      <div className="bg-surface rounded-card border border-border p-5">
        <h3 className="font-semibold text-textPrimary mb-3">{t('admin.codeList')}</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-textPrimary">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.codesColCode')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.codesColType')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.codesColAmount')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.codesColNote')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.codesColExpires')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.codesColStatus')}</th>
              </tr>
            </thead>
            <tbody>
              {codes.map((c: any) => (
                <tr key={c.code} className="border-b border-border/50">
                  <td className="py-2 px-3 font-mono text-xs text-textPrimary">
                    {c.code}
                    {c.is_api_pool && <span className="ml-1 px-1 py-0.5 bg-accent-400/10 text-accent-400 rounded text-3xs">{t('admin.pool')}</span>}
                  </td>
                  <td className="py-2 px-3 text-xs text-textSecondary">{CODE_TYPES[c.code_type] || c.code_type || t('admin.codeTypeDefault')}</td>
                  <td className="py-2 px-3 text-textPrimary">{c.quota_amount}{(c.code_type === 'file_size' || c.code_type === 'file_quota') ? ' MB' : ''}</td>
                  <td className="py-2 px-3 text-xs text-textMuted max-w-[120px] truncate" title={c.note || ''}>{c.note || '-'}</td>
                  <td className="py-2 px-3 text-xs text-textSecondary">{c.expires_at ? new Date(c.expires_at).toLocaleDateString('zh-CN') : '-'}</td>
                  <td className="py-2 px-3">
                    {c.used_by ? (
                      <span className="text-xs text-textMuted">{t('admin.usedBy')} (uid:{c.used_by})</span>
                    ) : (
                      <span className="text-xs text-mint-400">{t('admin.available')}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

/* ================================================================
   数据库备份/恢复
   ================================================================ */
