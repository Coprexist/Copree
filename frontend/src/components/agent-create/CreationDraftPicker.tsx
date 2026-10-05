/**
 * 创建 AI 的入口：继续上一次 / 未完成的创建列表 / 开始新的。
 *
 * 「一次创建」在后端是一行 agent_creation_drafts（表单快照）+ 一段对话日志
 * （conversation_type="creator"，session_id = 草稿 id），所以这里的列表就是那些草稿。
 */
import { useEffect, useState } from 'react'
import { Clock, Plus, Sparkles, Trash2 } from 'lucide-react'
import { useT } from '../../i18n/I18nContext'
import { createDraft, deleteDraft, listDrafts, type CreationDraft } from './creationApi'

export default function CreationDraftPicker({ onPick, onPlain }: {
  onPick: (draft: CreationDraft) => void
  onPlain: () => void
}) {
  const t = useT()
  const [drafts, setDrafts] = useState<CreationDraft[] | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    listDrafts().then(setDrafts).catch(() => setDrafts([]))
  }, [])

  const open = drafts?.[0]
  const rest = (drafts || []).slice(1)

  const startNew = async () => {
    setBusy(true)
    try { onPick(await createDraft()) } catch { setBusy(false) }
  }

  const remove = async (id: number) => {
    await deleteDraft(id).catch(() => {})
    setDrafts((prev) => (prev || []).filter((d) => d.id !== id))
  }

  const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '')

  return (
    <div className="h-full flex flex-col bg-canvas">
      <div className="flex-1 min-h-0 w-full max-w-lg mx-auto p-4 md:p-6 flex flex-col gap-4">
        <h2 className="text-lg font-semibold text-textPrimary">{t('modal:createStartTitle')}</h2>

        {drafts === null ? (
          <p className="text-xs text-textMuted py-6 text-center">{t('modal:createStartLoading')}</p>
        ) : (
          <div className="overflow-y-auto space-y-3">
            {open && (
              <button
                onClick={() => onPick(open)}
                className="w-full text-left rounded-card border border-primary-400/50 bg-gradient-to-b from-primary-500/15 to-primary-600/5 p-4 hover:border-primary-400 transition-colors"
              >
                <div className="flex items-center gap-1.5 text-sm font-semibold text-textPrimary">
                  <Clock size={14} /> {t('modal:createStartContinue')}
                </div>
                <p className="text-xs text-textSecondary mt-1 truncate">{open.title || t('modal:createStartUntitled')}</p>
                <p className="text-3xs text-textMuted mt-0.5">{when(open.updated_at)}</p>
              </button>
            )}

            {rest.length > 0 && (
              <div>
                <p className="text-xs font-medium text-textSecondary mb-1.5">{t('modal:createStartUnfinished')}</p>
                <div className="space-y-1.5">
                  {rest.map((d) => (
                    <div key={d.id} className="flex items-center gap-2 rounded-control border border-border px-3 py-2 hover:bg-canvas transition-colors">
                      <button onClick={() => onPick(d)} className="flex-1 min-w-0 text-left">
                        <span className="block text-sm text-textPrimary truncate">{d.title || t('modal:createStartUntitled')}</span>
                        <span className="block text-3xs text-textMuted">{when(d.updated_at)}</span>
                      </button>
                      <button onClick={() => remove(d.id)} className="icon-btn-sm text-textMuted hover:text-rose-400" title={t('modal:createStartDelete')}>
                        <Trash2 size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {!open && rest.length === 0 && (
              <p className="text-xs text-textMuted py-4 text-center">{t('modal:createStartEmpty')}</p>
            )}

            <button
              onClick={startNew}
              disabled={busy}
              className="w-full rounded-card border border-border p-4 text-left hover:border-primary-500/40 transition-colors disabled:opacity-60"
            >
              <div className="flex items-center gap-1.5 text-sm font-semibold text-textPrimary">
                <Plus size={14} /> {t('modal:createStartNew')}
              </div>
              <p className="text-xs text-textSecondary mt-1 flex items-center gap-1">
                <Sparkles size={12} /> {t('modal:createStartNewHint')}
              </p>
            </button>
          </div>
        )}

        <button onClick={onPlain} className="text-xs text-textMuted hover:text-textSecondary transition-colors self-center">
          {t('modal:createStartPlain')}
        </button>
      </div>
    </div>
  )
}
