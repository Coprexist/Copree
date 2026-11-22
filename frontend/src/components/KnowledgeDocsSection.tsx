/**
 * 创建表单里的知识库（虚拟）
 *
 * 这时 AI 还没有文件空间：这里的文档只是表单上的一篇篇内容，跟着草稿快照走
 * （form.knowledge）。建号成功那一刻才由后端落成真正的 knowledge/ 文件 + 索引行
 * （见 routers/agents.py 的 _persist_knowledge_docs），之后它与用户在详情页里
 * 手动加的资料完全同构——所以这里不做任何"读写知识库"的动作。
 */
import { useState } from 'react'
import { FileText, Pencil, Plus, Trash2 } from 'lucide-react'
import Modal from './ui/Modal'
import Button from './ui/Button'
import { useT } from '../i18n/I18nContext'
import type { KnowledgeDoc } from './agent-create/types'

const EMPTY_DOC: KnowledgeDoc = { title: '', summary: '', content: '' }

export default function KnowledgeDocsSection({
  docs,
  onChange,
}: {
  docs: KnowledgeDoc[]
  onChange: (docs: KnowledgeDoc[]) => void
}) {
  const t = useT()
  const [editing, setEditing] = useState<{ index: number; doc: KnowledgeDoc } | null>(null)

  const patchEditing = (p: Partial<KnowledgeDoc>) =>
    setEditing((cur) => (cur ? { ...cur, doc: { ...cur.doc, ...p } } : cur))

  const save = () => {
    if (!editing) return
    const doc = { ...editing.doc, title: editing.doc.title.trim() }
    if (!doc.content.trim()) {
      setEditing(null)
      return
    }
    const next = [...docs]
    if (editing.index < 0) next.push(doc)
    else next[editing.index] = doc
    onChange(next)
    setEditing(null)
  }

  const remove = (index: number) => onChange(docs.filter((_, i) => i !== index))

  return (
    <div className="mb-5">
      <div className="mb-1.5 flex items-center justify-between">
        <label className="text-sm font-medium text-textSecondary">{t('modal:createAgentKnowledge')}</label>
        <Button
          variant="outline"
          size="xs"
          icon={<Plus size={13} />}
          onClick={() => setEditing({ index: -1, doc: { ...EMPTY_DOC } })}
        >
          {t('modal:createAgentKnowledgeAdd')}
        </Button>
      </div>
      <p className="mb-2 text-xs text-textSecondary">{t('modal:createAgentKnowledgeHint')}</p>

      {docs.length === 0 ? (
        <div className="rounded-control border border-dashed border-border px-3 py-4 text-center text-xs text-textSecondary">
          {t('modal:createAgentKnowledgeEmpty')}
        </div>
      ) : (
        <ul className="space-y-2">
          {docs.map((doc, i) => (
            <li key={i} className="flex items-start gap-2 rounded-control border border-border px-3 py-2">
              <FileText size={15} className="mt-0.5 shrink-0 text-textSecondary" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-textPrimary">
                  {doc.title || t('modal:createAgentKnowledgeUntitled')}
                </div>
                <div className="truncate text-xs text-textSecondary">
                  {doc.summary || t('modal:createAgentKnowledgeNoSummary', { n: doc.content.length })}
                </div>
              </div>
              <Button
                variant="ghost"
                size="xs"
                icon={<Pencil size={13} />}
                aria-label={t('modal:createAgentKnowledgeEdit')}
                onClick={() => setEditing({ index: i, doc: { ...doc } })}
              />
              <Button
                variant="ghost"
                size="xs"
                icon={<Trash2 size={13} />}
                aria-label={t('modal:createAgentKnowledgeRemove')}
                onClick={() => remove(i)}
              />
            </li>
          ))}
        </ul>
      )}

      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing && editing.index >= 0 ? t('modal:createAgentKnowledgeEdit') : t('modal:createAgentKnowledgeAdd')}
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditing(null)}>{t('common:cancel')}</Button>
            <Button variant="primary" onClick={save}>{t('common:save')}</Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-textSecondary">{t('modal:createAgentKnowledgeTitle')}</label>
            <input
              className="field w-full"
              value={editing?.doc.title ?? ''}
              onChange={(e) => patchEditing({ title: e.target.value })}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-textSecondary">{t('modal:createAgentKnowledgeSummary')}</label>
            <input
              className="field w-full"
              value={editing?.doc.summary ?? ''}
              onChange={(e) => patchEditing({ summary: e.target.value })}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-textSecondary">{t('modal:createAgentKnowledgeContent')}</label>
            <textarea
              className="field min-h-[220px] w-full resize-y text-xs"
              value={editing?.doc.content ?? ''}
              onChange={(e) => patchEditing({ content: e.target.value })}
            />
          </div>
        </div>
      </Modal>
    </div>
  )
}
