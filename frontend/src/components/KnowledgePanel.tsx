/**
 * AI 知识库面板 — 看得到、能开关、能改
 *
 * 一份文档 = 文件空间里 knowledge/<path> 的正文 + 一条元数据（标题 / 摘要 / 开关）。
 * 这里只做"人"这一侧：清单、新增/编辑、开关、预览、删除；
 * "AI"那一侧是系统提示里的一段索引 + 它自己的 file_read（见 services/agent/knowledge_service.py）。
 * 关掉开关只是不进索引，正文还在，随时可以再打开。
 */
import { useCallback, useEffect, useState } from 'react'
import { BookOpen, Eye, FileText, Loader2, Plus, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import Toggle from './Toggle'
import { Dialog, EmptyState } from './ui'

interface KnowledgeEntry {
  id: number
  title: string
  summary: string
  path: string
  size: number
  enabled: boolean
  created_at?: string | null
  updated_at?: string | null
}

interface KnowledgeDraft {
  id?: number
  title: string
  summary: string
  content: string
}

/** 文件大小：知识库里的文档是人贴进来的，B/KB 两档够用 */
function formatSize(n: number): string {
  if (!n) return '0 B'
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1024 / 1024).toFixed(1) + ' MB'
}

export default function KnowledgePanel({ agentId }: { agentId: number }) {
  const t = useT()
  const [items, setItems] = useState<KnowledgeEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState<KnowledgeDraft | null>(null)
  const [saving, setSaving] = useState(false)
  const [preview, setPreview] = useState<{ title: string; content: string } | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeEntry | null>(null)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.get<{ items: KnowledgeEntry[] }>(`/agents/${agentId}/knowledge`)
      setItems(data.items || [])
      setError('')
    } catch (e: any) {
      setError(e?.message || t('agentDetail:knowledgeFailed'))
    } finally {
      setLoading(false)
    }
  }, [agentId, t])

  useEffect(() => { load() }, [load])

  const openEdit = async (entry: KnowledgeEntry) => {
    setError('')
    try {
      // 列表里不带正文（可能几十 KB），编辑时按需取一次
      const full = await api.get<KnowledgeEntry & { content: string }>(`/agents/${agentId}/knowledge/${entry.id}`)
      setDraft({ id: entry.id, title: full.title, summary: full.summary || '', content: full.content || '' })
    } catch (e: any) {
      setError(e?.message || t('agentDetail:knowledgeFailed'))
    }
  }

  const openPreview = async (entry: KnowledgeEntry) => {
    setError('')
    try {
      const full = await api.get<{ content: string }>(`/agents/${agentId}/knowledge/${entry.id}`)
      setPreview({ title: entry.title, content: full.content || '' })
    } catch (e: any) {
      setError(e?.message || t('agentDetail:knowledgeFailed'))
    }
  }

  const save = async () => {
    if (!draft || saving) return
    if (!draft.title.trim() || !draft.content.trim()) {
      setError(t('agentDetail:knowledgeRequired'))
      return
    }
    setSaving(true)
    setError('')
    try {
      const body = { title: draft.title.trim(), summary: draft.summary.trim(), content: draft.content }
      if (draft.id) await api.patch(`/agents/${agentId}/knowledge/${draft.id}`, body)
      else await api.post(`/agents/${agentId}/knowledge`, body)
      setDraft(null)
      await load()
    } catch (e: any) {
      setError(e?.message || t('agentDetail:knowledgeFailed'))
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (entry: KnowledgeEntry, enabled: boolean) => {
    // 先动界面：开关是"立刻生效"的动作，失败再翻回去
    setItems((prev) => prev.map((x) => (x.id === entry.id ? { ...x, enabled } : x)))
    try {
      await api.patch(`/agents/${agentId}/knowledge/${entry.id}`, { enabled })
    } catch (e: any) {
      setItems((prev) => prev.map((x) => (x.id === entry.id ? { ...x, enabled: !enabled } : x)))
      setError(e?.message || t('agentDetail:knowledgeFailed'))
    }
  }

  const remove = async () => {
    if (!deleteTarget || deleting) return
    setDeleting(true)
    try {
      await api.delete(`/agents/${agentId}/knowledge/${deleteTarget.id}`)
      setDeleteTarget(null)
      await load()
    } catch (e: any) {
      setError(e?.message || t('agentDetail:knowledgeFailed'))
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="bg-surface rounded-card border border-border p-4">
      <div className="flex items-center gap-2 mb-1">
        <BookOpen size={16} className="text-primary-400" />
        <h3 className="font-medium text-textPrimary text-sm">
          {t('agentDetail:knowledgeTitle')}
          <span className="text-textMuted font-normal ml-1.5">{items.length}</span>
        </h3>
        <button
          onClick={() => { setError(''); setDraft({ title: '', summary: '', content: '' }) }}
          className="btn btn-sm btn-primary gap-1 ml-auto"
        >
          <Plus size={14} />
          {t('agentDetail:knowledgeAdd')}
        </button>
      </div>
      <p className="text-xs text-textMuted mb-3">{t('agentDetail:knowledgeHint')}</p>

      {error && <p className="text-xs text-rose-400 mb-2">{error}</p>}

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-8 text-sm text-textMuted">
          <Loader2 size={14} className="animate-spin" />
          {t('agentDetail:knowledgeLoading')}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={BookOpen}
          title={t('agentDetail:knowledgeEmptyTitle')}
          description={t('agentDetail:knowledgeEmptyDesc')}
        />
      ) : (
        <div className="space-y-1.5">
          {items.map((entry) => (
            <div
              key={entry.id}
              className="flex items-start gap-3 p-2.5 rounded-control border border-border hover:border-primary-500/30 transition-colors"
            >
              <FileText size={15} className={(entry.enabled ? 'text-primary-400' : 'text-textMuted/50') + ' mt-0.5 shrink-0'} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => openEdit(entry)}
                    className="text-sm font-medium text-textPrimary hover:text-primary-400 truncate text-left"
                  >
                    {entry.title}
                  </button>
                  <span className="text-3xs text-textMuted shrink-0">{formatSize(entry.size)}</span>
                </div>
                {entry.summary && <p className="text-xs text-textMuted mt-0.5 line-clamp-1">{entry.summary}</p>}
                <p className="text-3xs text-textMuted/60 font-mono mt-0.5 truncate">knowledge/{entry.path}</p>
              </div>
              <div className="flex items-center gap-1 shrink-0 mt-0.5">
                <Toggle
                  size="sm"
                  checked={entry.enabled}
                  onChange={(v) => toggle(entry, v)}
                  ariaLabel={entry.title}
                />
                <button
                  onClick={() => openPreview(entry)}
                  className="p-1.5 rounded-control text-textMuted hover:text-primary-400 hover:bg-primary-500/10"
                  aria-label={t('agentDetail:knowledgePreview')}
                >
                  <Eye size={14} />
                </button>
                <button
                  onClick={() => setDeleteTarget(entry)}
                  className="p-1.5 rounded-control text-textMuted hover:text-rose-400 hover:bg-rose-500/10"
                  aria-label={t('agentDetail:knowledgeDelete')}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 新增 / 编辑 */}
      {draft && (
        <Dialog onClose={() => { if (!saving) setDraft(null) }} layer="toast" className="flex items-center justify-center p-4">
          <div
            className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-2xl shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="font-semibold text-textPrimary text-base mb-4">
              {draft.id ? t('agentDetail:knowledgeEditTitle') : t('agentDetail:knowledgeAddTitle')}
            </h3>
            <label className="block text-xs font-medium text-textSecondary mb-1">{t('agentDetail:knowledgeTitleLabel')}</label>
            <input
              className="input w-full mb-3"
              value={draft.title}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              placeholder={t('agentDetail:knowledgeTitlePlaceholder')}
            />
            <label className="block text-xs font-medium text-textSecondary mb-1">{t('agentDetail:knowledgeSummaryLabel')}</label>
            <input
              className="input w-full mb-3"
              value={draft.summary}
              onChange={(e) => setDraft({ ...draft, summary: e.target.value })}
              placeholder={t('agentDetail:knowledgeSummaryPlaceholder')}
            />
            <label className="block text-xs font-medium text-textSecondary mb-1">{t('agentDetail:knowledgeContentLabel')}</label>
            <textarea
              className="input w-full font-mono text-xs leading-relaxed"
              rows={14}
              value={draft.content}
              onChange={(e) => setDraft({ ...draft, content: e.target.value })}
              placeholder={t('agentDetail:knowledgeContentPlaceholder')}
            />
            <div className="flex items-center gap-2 justify-end mt-4">
              <button className="btn btn-sm btn-outline" onClick={() => setDraft(null)} disabled={saving}>
                {t('agentDetail:knowledgeCancel')}
              </button>
              <button className="btn btn-sm btn-primary" onClick={save} disabled={saving}>
                {saving ? t('agentDetail:knowledgeSaving') : t('agentDetail:knowledgeSave')}
              </button>
            </div>
          </div>
        </Dialog>
      )}

      {/* 预览 */}
      {preview && (
        <Dialog onClose={() => setPreview(null)} layer="toast" className="flex items-center justify-center p-4">
          <div
            className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-3xl shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 mb-3">
              <FileText size={16} className="text-primary-400" />
              <h3 className="font-semibold text-textPrimary text-base truncate">{preview.title}</h3>
              <button onClick={() => setPreview(null)} className="ml-auto text-xs text-textMuted hover:text-textPrimary">
                {t('agentDetail:knowledgeCancel')}
              </button>
            </div>
            <pre className="whitespace-pre-wrap break-words text-xs leading-relaxed text-textSecondary bg-canvas rounded-control border border-border p-3 max-h-[60vh] overflow-auto">
              {preview.content || t('agentDetail:knowledgeEmptyContent')}
            </pre>
          </div>
        </Dialog>
      )}

      {/* 删除确认 */}
      {deleteTarget && (
        <Dialog onClose={() => { if (!deleting) setDeleteTarget(null) }} layer="toast" className="flex items-center justify-center p-4">
          <div
            className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-md shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 mb-3">
              <div className="w-8 h-8 rounded-full bg-rose-500/10 flex items-center justify-center">
                <Trash2 size={16} className="text-rose-400" />
              </div>
              <h3 className="font-semibold text-textPrimary text-base">{t('agentDetail:knowledgeDelete')}</h3>
            </div>
            <p className="text-sm text-textSecondary mb-4">
              {t('agentDetail:knowledgeDeleteConfirm')} <span className="font-medium text-textPrimary">「{deleteTarget.title}」</span>？
            </p>
            <div className="flex items-center gap-2 justify-end">
              <button className="btn btn-sm btn-outline" onClick={() => setDeleteTarget(null)} disabled={deleting}>
                {t('agentDetail:knowledgeCancel')}
              </button>
              <button className="btn btn-sm btn-outline-danger" onClick={remove} disabled={deleting}>
                {deleting ? t('agentDetail:knowledgeSaving') : t('agentDetail:knowledgeDelete')}
              </button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  )
}
