import { useState, useEffect } from 'react'
import { api } from '../../../api/client'
import { useT, useLang } from '../../../i18n/I18nContext';
import { getLangMeta } from '../../../i18n/languages'
import { Eraser, Clock, File, FileX2 } from 'lucide-react'

export default function CleanupTab() {
  const t = useT()
  const locale = getLangMeta(useLang()).locale
  const [stats, setStats] = useState<{
    cleaned_files: number
    cleaned_refs: number
    orphan_cleaned: number
    run_at: string | null
  } | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')

  const loadStats = async () => {
    try {
      const data = await api.get('/admin/cleanup/stats')
      setStats(data)
    } catch {
      // silently fail — first load before any run
    }
  }

  useEffect(() => { loadStats() }, [])

  const handleRun = async () => {
    setRunning(true)
    setError('')
    try {
      const result = await api.post('/admin/cleanup/files')
      setStats(result)
    } catch (e: any) {
      setError(e?.detail || e?.message || t('common.error'))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="space-y-5">
      {/* 运行清理 */}
      <div className="bg-surface rounded-card border border-border p-5">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-card bg-primary-500/10 flex items-center justify-center">
            <Eraser size={20} className="text-primary-400" />
          </div>
          <div>
            <h3 className="font-semibold text-textPrimary">{t('admin.cleanup')}</h3>
            <p className="text-xs text-textMuted mt-0.5">{t('admin.cleanupDesc')}</p>
          </div>
        </div>

        <button
          onClick={handleRun}
          disabled={running}
          className="btn btn-md btn-primary gap-2"
        >
          {running ? (
            <>
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              {t('admin.cleanupRunning')}
            </>
          ) : (
            <>
              <Eraser size={16} />
              {t('admin.cleanupRun')}
            </>
          )}
        </button>

        {error && (
          <div className="mt-3 p-3 bg-rose-400/10 border border-rose-400/20 rounded-card">
            <p className="text-sm text-rose-400">{error}</p>
          </div>
        )}
      </div>

      {/* 上次清理统计 */}
      <div className="bg-surface rounded-card border border-border p-5">
        <div className="flex items-center gap-2 mb-4">
          <Clock size={16} className="text-textSecondary" />
          <h3 className="font-semibold text-textPrimary">{t('admin.cleanupLastRun')}</h3>
        </div>

        {!stats ? (
          <p className="text-sm text-textMuted">{t('admin.cleanupNever')}</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="bg-canvas rounded-card border border-border p-4">
              <div className="flex items-center gap-2 mb-2">
                <File size={16} className="text-mint-400" />
                <span className="text-xs text-textMuted">{t('admin.cleanupFilesDeleted')}</span>
              </div>
              <p className="text-2xl font-bold text-textPrimary">{stats.cleaned_files ?? 0}</p>
            </div>

            <div className="bg-canvas rounded-card border border-border p-4">
              <div className="flex items-center gap-2 mb-2">
                <FileX2 size={16} className="text-accent-400" />
                <span className="text-xs text-textMuted">{t('admin.cleanupOrphansDeleted')}</span>
              </div>
              <p className="text-2xl font-bold text-textPrimary">{stats.orphan_cleaned ?? 0}</p>
            </div>

            <div className="bg-canvas rounded-card border border-border p-4">
              <div className="flex items-center gap-2 mb-2">
                <Clock size={16} className="text-textSecondary" />
                <span className="text-xs text-textMuted">{t('admin.cleanupLastRun')}</span>
              </div>
              <p className="text-sm font-medium text-textPrimary">
                {stats.run_at
                  ? new Date(stats.run_at).toLocaleString(locale)
                  : t('admin.cleanupNever')}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
