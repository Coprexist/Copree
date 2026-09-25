/**
 * 接口文档分区管理（部署者/开发者表单注入）
 *
 * md 文件是源（随 git），DB 是运行时快照：
 * - 编辑标题/介绍 → 存 DB 即时生效
 * - 勾选「同步更新文档」→ 同时写回 md（行1 标题 / 行2 区介绍）
 * - 「从文档中更新」→ md → DB 全量同步（开发者改完 md 后点一下）
 */
import { useCallback, useEffect, useState } from 'react'
import { BookOpen, RefreshCw, Save, CheckCircle2 } from 'lucide-react'
import { api } from '../../../api/client'

interface SectionRow {
  id: string
  title: string
  intro: string
  doc_title?: string | null
  doc_intro?: string | null
  title_changed?: boolean
  intro_changed?: boolean
}

export default function ApiDocSectionsTab() {
  const [sections, setSections] = useState<SectionRow[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [writeBack, setWriteBack] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api.get<{ sections: SectionRow[] }>('/admin/api-doc-sections')
      setSections(r.sections || [])
    } catch { /* ignore */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const update = (id: string, field: 'title' | 'intro', value: string) => {
    setSections((prev) => prev.map((s) => (s.id === id ? { ...s, [field]: value } : s)))
  }

  const save = async () => {
    setSaving(true)
    setMsg('')
    try {
      const r = await api.put<{ saved: number; write_back: boolean }>('/admin/api-doc-sections', {
        sections: sections.map((s) => ({ id: s.id, title: s.title, intro: s.intro })),
        write_back: writeBack,
      })
      setMsg(`已保存 ${r.saved} 个分区${r.write_back ? '（已同步写回文档）' : ''}`)
      load()
    } catch (e: any) { setMsg(`保存失败: ${e?.message || e}`) } finally { setSaving(false) }
  }

  const syncFromDocs = async () => {
    if (!confirm('从文档同步将用 md 文件覆盖 DB 快照（含删除文档中已不存在的分区），确认？')) return
    setSyncing(true)
    setMsg('')
    try {
      const r = await api.post<{ created: number; updated: number; removed: number }>('/admin/api-doc-sections/sync-from-docs')
      setMsg(`已从文档同步：新增 ${r.created}，更新 ${r.updated}，移除 ${r.removed}`)
      load()
    } catch (e: any) { setMsg(`同步失败: ${e?.message || e}`) } finally { setSyncing(false) }
  }

  return (
    <div className="space-y-3">
      <div>
        <div className="text-sm font-semibold text-textPrimary mb-1">接口文档分区（标题 / 介绍）</div>
        <div className="text-xs text-textMuted">
          md 文件是源（随 git 分发），DB 是运行时快照。编辑即时生效；勾选「同步更新文档」会写回 md（行1 标题 / 行2 区介绍）；
          「从文档中更新」用 md 覆盖 DB（开发者改完文档后同步）。
        </div>
      </div>

      {loading ? (
        <div className="text-xs text-textMuted py-6 text-center">加载中…</div>
      ) : (
        <div className="space-y-2">
          {sections.map((s) => (
            <div key={s.id} className="rounded-card border border-border bg-elevated/30 p-3 space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-3xs font-mono text-primary-400 bg-primary-500/10 rounded px-1.5 py-0.5">{s.id}</span>
                <input
                  value={s.title}
                  onChange={(e) => update(s.id, 'title', e.target.value)}
                  className="flex-1 bg-transparent text-sm text-textPrimary border border-border rounded-control px-2 py-1 focus:border-primary-500/50 focus:outline-none"
                  placeholder="标题"
                />
                {s.title_changed && <span className="shrink-0 text-3xs text-accent-400">已改（与文档不同）</span>}
              </div>
              <textarea
                value={s.intro}
                onChange={(e) => update(s.id, 'intro', e.target.value)}
                rows={2}
                className="w-full bg-transparent text-xs text-textSecondary border border-border rounded-control px-2 py-1.5 focus:border-primary-500/50 focus:outline-none resize-none"
                placeholder="区介绍"
              />
              {s.intro_changed && <div className="text-3xs text-accent-400 -mt-1">介绍与文档不同</div>}
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          onClick={save}
          disabled={saving}
          className="btn btn-xs btn-primary gap-1.5"
        >
          <Save size={13} /> {saving ? '保存中…' : '保存'}
        </button>
        <label className="inline-flex items-center gap-1.5 text-xs text-textSecondary cursor-pointer">
          <input type="checkbox" checked={writeBack} onChange={(e) => setWriteBack(e.target.checked)} className="accent-primary-500" />
          同步更新文档（写回 md）
        </label>
        <div className="flex-1" />
        <button
          onClick={syncFromDocs}
          disabled={syncing}
          className="inline-flex items-center gap-1.5 text-xs px-3 py-2 rounded-control bg-elevated hover:bg-border text-textSecondary transition-colors disabled:opacity-50"
        >
          <RefreshCw size={13} /> {syncing ? '同步中…' : '从文档中更新'}
        </button>
      </div>

      {msg && <div className="inline-flex items-center gap-1 text-xs text-mint-400"><CheckCircle2 size={12} /> {msg}</div>}
      <div className="flex items-center gap-1 text-3xs text-textMuted"><BookOpen size={11} /> 变更即时生效：管理页/设计页的接口文档列表读取 DB 快照</div>
    </div>
  )
}
