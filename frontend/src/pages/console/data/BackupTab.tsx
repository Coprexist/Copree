// 备份与恢复

import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../../../api/client'
import { Database, AlertTriangle } from 'lucide-react'
import { useT } from '../../../i18n/I18nContext'

export default function BackupTab() {
  const t = useT()
  const [downloading, setDownloading] = useState(false)
  const [downloadingFull, setDownloadingFull] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [restoringFull, setRestoringFull] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const fullFileInputRef = useRef<HTMLInputElement>(null)

  // ── 备份后端信息（当前数据库类型等）──
  const [backupInfo, setBackupInfo] = useState<{ db_backend: string; backup_extension: string; warning: string } | null>(null)

  // ── 本地自动备份（每日备份功能产生，可选回档）──
  const [localBackups, setLocalBackups] = useState<{ name: string; size_bytes: number; mtime: string }[]>([])
  const [restoringLocal, setRestoringLocal] = useState('')

  const dbLabel = backupInfo?.db_backend === 'sqlite' ? 'SQLite' : 'PostgreSQL'
  const dbExt = backupInfo?.backup_extension || '.sql'

  const loadBackupInfo = useCallback(async () => {
    try {
      const token = localStorage.getItem('access_token')
      const res = await fetch('/api/admin/backup/info', {
        headers: { 'Authorization': `Bearer ${token}` },
      })
      if (res.ok) {
        setBackupInfo(await res.json())
      }
    } catch { /* 信息拉不到不阻塞 */ }
  }, [])

  const loadLocalBackups = useCallback(async () => {
    try {
      const token = localStorage.getItem('access_token')
      const res = await fetch('/api/admin/backups', {
        headers: { 'Authorization': `Bearer ${token}` },
      })
      if (res.ok) {
        const data = await res.json()
        setLocalBackups(data.backups || [])
      }
    } catch { /* 列表拉不到不阻塞 */ }
  }, [])

  useEffect(() => { loadBackupInfo(); loadLocalBackups() }, [loadBackupInfo, loadLocalBackups])

  const formatBytes = (n: number) => {
    if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
    if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`
    return `${n} B`
  }

  const handleRestoreLocal = async (filename: string) => {
    if (!confirm(t('admin.restoreWarning'))) return
    setRestoringLocal(filename)
    setError('')
    setMessage('')
    try {
      const token = localStorage.getItem('access_token')
      const res = await fetch('/api/admin/backup/restore-local', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename }),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || t('admin.restoreFailed'))
      }
      const data = await res.json()
      setMessage(data.restart_required ? t('admin.dbRestoreRestart') : t('admin.dbRestoreSuccess'))
    } catch (e: any) {
      setError(e.message)
    } finally {
      setRestoringLocal('')
    }
  }

  // ── 仅数据库备份 ──
  const handleBackup = async () => {
    setDownloading(true)
    setError('')
    setMessage('')
    try {
      // 文件名（含 .sql/.db 扩展名）由后端 Content-Disposition 决定，前端不再猜
      await api.download('/admin/backup/download',
                         `copree_backup_${new Date().toISOString().slice(0, 10)}${dbExt}`)
      setMessage(t('admin.dbBackupSuccess'))
    } catch (e: any) {
      setError(e.message)
    } finally {
      setDownloading(false)
    }
  }

  // ── 完整备份（数据库 + 文件）──
  const handleFullBackup = async () => {
    setDownloadingFull(true)
    setError('')
    setMessage('')
    try {
      await api.download('/admin/backup/full/download',
                         `copree_full_${new Date().toISOString().slice(0, 10)}.tar.gz`)
      setMessage(t('admin.fullBackupSuccess'))
    } catch (e: any) {
      setError(e.message)
    } finally {
      setDownloadingFull(false)
    }
  }

  // ── 仅数据库恢复 ──
  const handleRestore = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (!confirm(t('admin.restoreWarning'))) return
    setRestoring(true)
    setError('')
    setMessage('')
    try {
      const token = localStorage.getItem('access_token')
      const formData = new FormData()
      formData.append('file', file)
      const res = await fetch('/api/admin/backup/restore', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` },
        body: formData,
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || t('admin.restoreFailed'))
      }
      const data = await res.json()
      setMessage(data.restart_required ? t('admin.dbRestoreRestart') : t('admin.dbRestoreSuccess'))
    } catch (e: any) {
      setError(e.message)
    } finally {
      setRestoring(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  // ── 完整恢复（数据库 + 文件）──
  const handleFullRestore = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (!confirm(t('admin.restoreWarning'))) return
    setRestoringFull(true)
    setError('')
    setMessage('')
    try {
      const token = localStorage.getItem('access_token')
      const formData = new FormData()
      formData.append('file', file)
      const res = await fetch('/api/admin/backup/full/restore', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` },
        body: formData,
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || t('admin.restoreFailed'))
      }
      const data = await res.json()
      setMessage(data.restart_required ? t('admin.fullRestoreRestart') : t('admin.fullRestoreSuccess'))
    } catch (e: any) {
      setError(e.message)
    } finally {
      setRestoringFull(false)
      if (fullFileInputRef.current) fullFileInputRef.current.value = ''
    }
  }

  return (
    <div className="space-y-5">
      {/* ========== 当前数据库后端信息 ========== */}
      {backupInfo && (
        <div className="bg-amber-400/5 border border-amber-400/20 rounded-card p-4 flex items-start gap-3">
          <AlertTriangle size={18} className="text-amber-400 shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <p className="text-sm text-textPrimary">
              {t('admin.currentDbBackend')}: <span className="font-semibold text-amber-400">{dbLabel}</span>
              <span className="text-xs text-textMuted ml-2">({t('admin.backupFileExt')}: {dbExt})</span>
            </p>
            <p className="text-xs text-amber-400/80 mt-1">{backupInfo.warning}</p>
          </div>
        </div>
      )}

      {/* ========== 导出区 ========== */}
      <div className="bg-surface rounded-card border border-border p-5">
        <h3 className="font-semibold text-textPrimary mb-1">{t('admin.exportTitle')}</h3>
        <p className="text-sm text-textMuted mb-5">{t('admin.exportDesc')}</p>

        {/* 完整备份 */}
        <div className="bg-mint-400/5 border border-mint-400/20 rounded-card p-4 mb-3">
          <div className="flex items-start gap-3">
            <Database size={20} className="text-mint-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-semibold text-mint-400">{t('admin.fullBackup')}</h4>
              <p className="text-xs text-textSecondary mt-1" dangerouslySetInnerHTML={{ __html: t('admin.fullBackupDesc') }} />
            </div>
            <button
              onClick={handleFullBackup}
              disabled={downloadingFull}
              className="shrink-0 px-4 py-2 bg-mint-400 text-white rounded-card hover:bg-mint-500 disabled:opacity-40 text-sm font-medium transition-colors"
            >
              {downloadingFull ? t('admin.packing') : t('admin.downloadFullBackup')}
            </button>
          </div>
        </div>

        {/* 仅数据库 */}
        <div className="bg-canvas border border-border rounded-card p-4">
          <div className="flex items-start gap-3">
            <Database size={20} className="text-textSecondary shrink-0" />
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-semibold text-textPrimary">{t('admin.dbOnly')}</h4>
              <p className="text-xs text-textSecondary mt-1" dangerouslySetInnerHTML={{ __html: t('admin.dbOnlyDesc') }} />
            </div>
            <button
              onClick={handleBackup}
              disabled={downloading}
              className="btn btn-sm btn-primary shrink-0"
            >
              {downloading ? t('admin.exporting') : t('admin.downloadDbOnly')}
            </button>
          </div>
        </div>
      </div>

      {/* ========== 本地自动备份（每日备份 + 回档） ========== */}
      <div className="bg-surface rounded-card border border-border p-5">
        <div className="flex items-center justify-between mb-1">
          <h3 className="font-semibold text-textPrimary">本地自动备份</h3>
          <button onClick={loadLocalBackups} className="text-xs text-textSecondary hover:text-textPrimary">刷新</button>
        </div>
        <p className="text-sm text-textMuted mb-4">每日备份功能产生的文件（服务器 data/backups/），可选择一个回档（覆盖当前所有数据）</p>
        {localBackups.length === 0 ? (
          <div className="text-sm text-textMuted bg-canvas border border-border rounded-card p-4">
            暂无本地备份——在「系统设置」里开启每日备份后，每天会自动生成
          </div>
        ) : (
          <div className="space-y-2">
            {localBackups.map((b) => (
              <div key={b.name} className="flex items-center gap-3 bg-canvas border border-border rounded-card px-4 py-2.5">
                <Database size={16} className="text-textSecondary shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-textPrimary truncate">{b.name}</div>
                  <div className="text-xs text-textMuted">{formatBytes(b.size_bytes)} · {new Date(b.mtime).toLocaleString()}</div>
                </div>
                <button
                  onClick={() => handleRestoreLocal(b.name)}
                  disabled={restoringLocal === b.name}
                  className="shrink-0 px-3 py-1.5 text-sm bg-rose-500/80 text-white rounded-control hover:bg-rose-500 disabled:opacity-40 transition-colors"
                >
                  {restoringLocal === b.name ? '回档中...' : '回档'}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ========== 导入区 ========== */}
      <div className="bg-surface rounded-card border border-rose-500/30 p-5">
        <h3 className="font-semibold text-textPrimary mb-1">{t('admin.restoreTitle')}</h3>
        <p className="text-sm text-rose-400 mb-5">{t('admin.restoreWarning')}</p>

        {/* 完整恢复 */}
        <div className="bg-rose-400/5 border border-rose-400/20 rounded-card p-4 mb-3">
          <div className="flex items-start gap-3">
            <Database size={20} className="text-rose-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-semibold text-rose-400">{t('admin.fullRestore')}</h4>
              <p className="text-xs text-textSecondary mt-1" dangerouslySetInnerHTML={{ __html: t('admin.fullRestoreDesc') }} />
              <input
                ref={fullFileInputRef}
                type="file"
                accept=".tar.gz,.tgz"
                onChange={handleFullRestore}
                disabled={restoringFull}
                className="block mt-2 text-sm text-textPrimary file:mr-3 file:py-2 file:px-4 file:rounded-card file:border-0 file:text-sm file:bg-elevated file:text-textPrimary hover:file:bg-border"
              />
              {restoringFull && <p className="text-sm text-textMuted mt-2">{t('admin.restoringFull')}</p>}
            </div>
          </div>
        </div>

        {/* 仅数据库恢复 */}
        <div className="bg-canvas border border-border rounded-card p-4">
          <div className="flex items-start gap-3">
            <Database size={20} className="text-textSecondary shrink-0" />
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-semibold text-textPrimary">{t('admin.dbRestore')}</h4>
              <p className="text-xs text-textSecondary mt-1" dangerouslySetInnerHTML={{ __html: t('admin.dbRestoreDesc') }} />
              <input
                ref={fileInputRef}
                type="file"
                accept=".sql,.db"
                onChange={handleRestore}
                disabled={restoring}
                className="block mt-2 text-sm text-textPrimary file:mr-3 file:py-2 file:px-4 file:rounded-card file:border-0 file:text-sm file:bg-elevated file:text-textPrimary hover:file:bg-border"
              />
              {restoring && <p className="text-sm text-textMuted mt-2">{t('admin.restoring')}</p>}
            </div>
          </div>
        </div>
      </div>

      {message && (
        <div className="text-sm text-mint-400 bg-mint-400/10 px-3 py-2 rounded-control">{message}</div>
      )}
      {error && (
        <div className="text-sm text-rose-400 bg-rose-400/10 px-3 py-2 rounded-control">{error}</div>
      )}
    </div>
  )
}
