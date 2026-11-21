/**
 * 创建 AI 的入口：继续上一次 / 开始新的 / 未完成的创建。
 *
 * 「一次创建」在后端是一行 agent_creation_drafts（表单快照）+ 一段对话日志
 * （conversation_type="creator"，session_id = 草稿 id），所以这里的列表就是那些草稿。
 *
 * 宽屏与创建编辑器同一个度量（1120 居中）：左边两个入口竖着排，右边是旧草稿列表；
 * 窄屏退回单列，顺序与原来一致（继续上一次 → 开始新的 → 未完成列表）。
 */
import { useEffect, useState } from 'react'
import { CheckCircle2, Clock, Plus, Sparkles, Trash2 } from 'lucide-react'
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

  // 已经建好的草稿不能当"继续上一次"——再点一次就是重复建一个 AI（线上真出过）。
  // 它们只作为"已成功创建"的记录留在右边；重开这条记录也是"再建一个"，
  // 编辑器里保存时会先弹窗说清楚，并另起一张草稿承接这次创建。
  const open = (drafts || []).find((d) => d.status === 'open') || null
  const pending = (drafts || []).filter((d) => d.status !== 'created' && d !== open)
  const created = (drafts || []).filter((d) => d.status === 'created')

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
    <div className="flex h-full flex-col bg-canvas">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="w-full px-4 py-5 md:px-6 md:py-6">
          <h2 className="mb-4 text-lg font-semibold text-textPrimary">{t('modal:createStartTitle')}</h2>

          {drafts === null ? (
            <p className="py-6 text-center text-xs text-textMuted">{t('modal:createStartLoading')}</p>
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)] lg:items-start">
              {/* 左：两个入口 */}
              <div className="space-y-3">
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

              {/* 右：未完成的创建 + 已建好的记录 */}
              <div className="space-y-4">
                <div>
                  <p className="mb-1.5 text-xs font-medium text-textSecondary">{t('modal:createStartUnfinished')}</p>
                  {pending.length > 0 ? (
                    <div className="space-y-1.5">
                      {pending.map((d) => (
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
                  ) : (
                    <p className="py-4 text-center text-xs text-textMuted">{t('modal:createStartEmpty')}</p>
                  )}
                </div>

                {created.length > 0 && (
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-textSecondary">{t('modal:createStartCreatedTitle')}</p>
                    <div className="space-y-1.5">
                      {created.map((d) => (
                        // 渐变填充从左边薄荷绿散到右边透明：一眼认出"这条已经落地了"
                        <div key={d.id} className="flex items-center gap-2 rounded-control border border-mint-500/30 bg-gradient-to-r from-mint-500/20 via-mint-500/8 to-transparent px-3 py-2">
                          <button onClick={() => onPick(d)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                            <CheckCircle2 size={14} className="shrink-0 text-mint-400" />
                            <div className="min-w-0 flex-1">
                              <span className="block truncate text-sm text-textPrimary">{d.title || t('modal:createStartUntitled')}</span>
                              <span className="block text-3xs text-mint-400">
                                {t('modal:createStartCreated')} · {t('modal:createStartCreatedHint')}
                              </span>
                            </div>
                          </button>
                          <button onClick={() => remove(d.id)} className="icon-btn-sm text-textMuted hover:text-rose-400" title={t('modal:createStartDelete')}>
                            <Trash2 size={14} />
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="mt-6 flex justify-center">
            <button onClick={onPlain} className="text-xs text-textMuted transition-colors hover:text-textSecondary">
              {t('modal:createStartPlain')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
