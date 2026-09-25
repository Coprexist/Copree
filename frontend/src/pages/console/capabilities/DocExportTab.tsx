/**
 * 文档导出（pandoc）管理：状态 + 在线安装（可选能力）
 *
 * pandoc 用于世界接口文档 md→docx 导出；部署时可不装，管理员在这里在线安装。
 */
import { useCallback, useEffect, useState } from 'react'
import { FileText, CheckCircle2, XCircle, Loader2, PackagePlus } from 'lucide-react'
import { api } from '../../../api/client'

interface ExportStatus {
  docx_available: boolean
  is_admin: boolean
  installing: boolean
  install_error: string | null
}

export default function DocExportTab() {
  const [status, setStatus] = useState<ExportStatus | null>(null)
  const [installing, setInstalling] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    try {
      const s = await api.get<ExportStatus>('/kb/status')
      setStatus(s)
      if (!s.installing) setInstalling(false)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { load() }, [load])

  // 安装中 → 轮询直到完成
  useEffect(() => {
    if (!installing) return
    const timer = setInterval(load, 2500)
    return () => clearInterval(timer)
  }, [installing, load])

  const doInstall = async () => {
    setInstalling(true)
    setMsg('')
    try {
      await api.post('/kb/install')
    } catch (e: any) {
      setMsg(`安装失败: ${e?.message || e}`)
      setInstalling(false)
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <div className="text-sm font-semibold text-textPrimary mb-1">文档导出（md → docx）</div>
        <div className="text-xs text-textMuted">
          世界设计页的接口文档可导出 Word（docx），依赖 <code className="text-primary-400">pandoc</code>。
          可选能力：部署时可不装，在这里在线安装；未安装时文档只提供 .md 下载。
        </div>
      </div>

      {/* 状态卡 */}
      {!status && (
        <div className="rounded-card border border-accent-500/30 bg-accent-500/10 p-4 text-xs text-accent-400">
          状态加载失败——后端可能未重启（新接口 /kb/status 未生效）。
          <button onClick={load} className="ml-2 text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors">重试</button>
        </div>
      )}
      {status && (
      <div className="rounded-card border border-border bg-elevated/40 p-4">
        <div className="flex items-center gap-2">
          <FileText size={16} className="text-primary-400 shrink-0" />
          <span className="text-sm text-textPrimary">pandoc 状态</span>
          {status?.installing || installing ? (
            <span className="inline-flex items-center gap-1 text-xs text-accent-400"><Loader2 size={12} className="animate-spin" /> 安装中…（apt 下载可能需要几分钟）</span>
          ) : status?.docx_available ? (
            <span className="inline-flex items-center gap-1 text-xs text-mint-400"><CheckCircle2 size={12} /> 已安装，docx 导出可用</span>
          ) : (
            <span className="inline-flex items-center gap-1 text-xs text-textMuted"><XCircle size={12} /> 未安装（仅 .md 下载）</span>
          )}
        </div>
        {status?.install_error && (
          <div className="mt-2 text-xs text-rose-400">{status.install_error}</div>
        )}
        {msg && <div className="mt-2 text-xs text-accent-400">{msg}</div>}
      </div>
      )}

      {/* 安装按钮（管理员） */}
      {status && !status.docx_available && (
        <button
          onClick={doInstall}
          disabled={installing || status.installing}
          className="btn btn-sm btn-primary gap-1.5"
        >
          {installing || status.installing ? <Loader2 size={14} className="animate-spin" /> : <PackagePlus size={14} />}
          {installing || status.installing ? '安装中…' : '安装 pandoc'}
        </button>
      )}
      {status?.docx_available && (
        <div className="text-xs text-textMuted">安装完成。刷新世界设计页的接口文档弹窗即可使用 docx 下载。</div>
      )}
    </div>
  )
}
