/**
 * 存储管理页面
 * 显示自己上传的文件列表 + 用量概览
 */
import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { HardDrive, FileText, Loader2, ArrowLeft, Forward, Eye } from 'lucide-react'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import FilePreviewModal from '../components/FilePreviewModal'

interface FileItem {
  id: number
  path: string
  size: number
  mime_type: string
  created_at: string
  is_forwarded?: boolean
  owner_type?: string
  owner_id?: number
}

function formatSize(bytes: number): string {
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${bytes} B`
}

export default function StoragePage() {
  const t = useT()
  const navigate = useNavigate()

  const [storage, setStorage] = useState<{
    total_used: number; total_files: number; quota_mb: number; usage_percent: number
  } | null>(null)
  const [files, setFiles] = useState<FileItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [forwardFile, setForwardFile] = useState<FileItem | null>(null)
  const [previewFile, setPreviewFile] = useState<FileItem | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    Promise.all([
      api.get('/user/storage'),
      api.get<FileItem[]>('/fs/list?path=.'),
    ]).then(([s, f]) => {
      setStorage(s)
      setFiles(Array.isArray(f) ? f : [])
    }).catch((e) => {
      setError(e?.message || t('common:error'))
    }).finally(() => setLoading(false))
  }, [])

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('path', '/')
      await api.post('/fs/upload', form)
      // 刷新文件列表
      const [s, f] = await Promise.all([
        api.get('/user/storage'),
        api.get<FileItem[]>('/fs/list?path=.'),
      ])
      setStorage(s)
      setFiles(Array.isArray(f) ? f : [])
    } catch {}
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const openForward = (file: FileItem) => {
    setForwardFile(file)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin text-textMuted" />
      </div>
    )
  }

  return (
    <PageShell title="存储空间" onBack={() => navigate('/me')} width="content" contentClassName="space-y-4">

      {error && (
        <p className="text-sm text-rose-400 bg-rose-500/10 rounded-card px-4 py-3">{error}</p>
      )}

      {/* 用量概览 */}
      {storage && (
        <div className="bg-surface rounded-dialog border border-border p-5">
          <div className="flex items-center gap-2 mb-3">
            <HardDrive size={16} className="text-primary-400" />
            <span className="text-sm font-medium text-textPrimary">用量</span>
          </div>
          <div className="flex items-center justify-between text-xs text-textMuted mb-2">
            <span>{t('me:used')} {formatSize(storage.total_used)}</span>
            <span className={storage.usage_percent > 90 ? 'text-rose-400 font-medium' : ''}>
              {storage.usage_percent}%
            </span>
          </div>
          <div className="w-full h-2 bg-canvas rounded-full overflow-hidden mb-2">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                storage.usage_percent > 90 ? 'bg-rose-400' : storage.usage_percent > 70 ? 'bg-accent-400' : 'bg-primary-400'
              }`}
              style={{ width: `${Math.min(storage.usage_percent, 100)}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-3xs text-textMuted">
            <span>{storage.total_files} 个文件</span>
            <span>配额 {storage.quota_mb}MB</span>
          </div>
          {storage.usage_percent > 90 && (
            <p className="text-xs text-rose-400 mt-2">存储空间不足，请清理文件</p>
          )}
        </div>
      )}

      {/* 上传按钮 */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => fileInputRef.current?.click()}
          className="text-xs px-4 py-2 bg-primary-500 text-white rounded-control hover:bg-primary-600 transition-colors"
        >
          上传文件
        </button>
        <input ref={fileInputRef} type="file" className="hidden" onChange={handleUpload} />
      </div>

      {/* 文件列表 */}
      <div className="bg-surface rounded-dialog border border-border p-5">
        <h3 className="text-sm font-semibold text-textPrimary mb-3">文件列表</h3>
        {files.length === 0 ? (
          <p className="text-sm text-textMuted text-center py-8">暂无文件</p>
        ) : (
          <div className="space-y-0.5">
            {files.map((f) => {
              const name = f.path.split('/').pop() || f.path
              return (
                <div key={f.id} className="flex items-center gap-2 px-2 py-2 rounded-control hover:bg-elevated transition-colors group cursor-pointer"
                  onClick={() => setPreviewFile(f)}>
                  <FileText size={14} className={`shrink-0 ${f.is_forwarded ? 'text-accent-400' : 'text-textMuted'}`} />
                  <span className="text-xs text-textPrimary truncate flex-1" title={name}>
                    {name}
                    {f.is_forwarded && <span className="text-3xs text-accent-400 ml-1.5">转发</span>}
                  </span>
                  <span className="text-3xs text-textMuted shrink-0">{formatSize(f.size)}</span>
                  <button
                    onClick={(e) => { e.stopPropagation(); openForward(f); }}
                    className="p-1 rounded hover:bg-primary-500/10 text-textMuted hover:text-primary-400 transition-colors opacity-0 group-hover:opacity-100"
                    title="转发"
                  >
                    <Forward size={12} />
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* 文件预览弹窗 */}
      {previewFile && (
        <FilePreviewModal
          fileId={previewFile.id}
          fileName={previewFile.path.split('/').pop() || previewFile.path}
          fileSize={previewFile.size}
          mimeType={previewFile.mime_type}
          onClose={() => setPreviewFile(null)}
        />
      )}

      {/* 转发弹窗 */}
      {forwardFile && (
        <ForwardFileModal
          file={{ file_id: forwardFile.id, name: forwardFile.path.split('/').pop() || forwardFile.path, size: forwardFile.size, mime_type: forwardFile.mime_type }}
          onClose={() => setForwardFile(null)}
        />
      )}
    </PageShell>
  )
}

// 复用已有的转发组件
import ForwardFileModal from '../components/ForwardFileModal'
import { PageShell } from '../components/ui'