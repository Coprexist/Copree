/**
 * 导入灵魂档案（Copree 自己的 v2.0 格式）——「从文件导入」那一支。
 *
 * 这个弹窗原先长在 AgentsPage 里，只有 AI 列表能调。创建流程现在也要它
 *（创建 AI 的两个导入入口之一），所以抽成独立组件：两处同一份实现，
 * 免得"列表里能导、创建页里导不了"或反过来两边行为不一致。
 *
 * 导入成功就是"AI 已经建好了"，imported 返回后由调用方决定去哪。
 */
import { useState } from 'react'
import { X } from 'lucide-react'
import { useT } from '../../i18n/I18nContext'
import { Dialog } from '../ui'

export default function SoulImportDialog({ onClose, onImported }: {
  onClose: () => void
  onImported: () => void
}) {
  const t = useT()
  const [file, setFile] = useState<File | null>(null)
  const [importMemories, setImportMemories] = useState(true)
  const [importKnowledge, setImportKnowledge] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<any>(null)
  // 导入结果：只回一句"成功"看不出知识库到底跟过来没有，把搬了多少摊开给用户看
  const [result, setResult] = useState<{ memories?: number; memory_details?: number; knowledge?: number } | null>(null)

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    setFile(f)
    setError('')
    setResult(null)
    try {
      const text = await f.text()
      const data = JSON.parse(text)
      setPreview(data)
    } catch {
      setError(t('agents:invalidFile'))
      setPreview(null)
    }
  }

  const handleImport = async () => {
    if (!file) return
    setLoading(true)
    setError('')
    try {
      const token = localStorage.getItem('access_token')
      const formData = new FormData()
      formData.append('file', file)
      const res = await fetch(`/api/agents/import?import_memories=${importMemories}&import_knowledge=${importKnowledge}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` },
        body: formData,
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || t('error:operationFailed'))
      }
      // 先进结果态：让用户看见知识库/记忆搬了多少，再点"完成"收尾
      setResult(data.imported || { memories: 0, knowledge: 0 })
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog onClose={onClose} className="flex items-center justify-center">
      <div className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-md mx-4 shadow-2xl pb-[var(--safe-bottom)] md:pb-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-textPrimary">{t('agents:importSoulTitle')}</h2>
          <button onClick={onClose} className="icon-btn-sm text-textMuted">
            <X size={18} />
          </button>
        </div>

        {/* ui-standard-ignore: 文件选择控件（file: 伪元素按钮），不是输入框形态 */}
        <input
          type="file"
          accept=".json"
          onChange={handleFileChange}
          className="block w-full text-sm text-textPrimary file:mr-3 file:py-2 file:px-4 file:rounded-card file:border-0 file:text-sm file:bg-elevated file:text-textPrimary hover:file:bg-border mb-3"
        />

        {preview && (
          <div className="bg-canvas rounded-card p-3 mb-3 text-sm space-y-1">
            <p className="font-medium text-textPrimary">{preview.agent_name || t('agents:unnamed')}</p>
            <p className="text-xs text-textSecondary">{t('agents:memoryCount')} {preview.memories?.length || 0}</p>
            <p className="text-xs text-textSecondary">{t('agents:knowledgeCount')} {preview.knowledge?.length || 0}</p>
            <p className="text-xs text-textSecondary">{t('agents:configHistoryCount')} {preview.config_history?.length || 0}</p>
            {preview.export_version && (
              <p className="text-xs text-textMuted">{t('agents:archiveVersion')} {preview.export_version}</p>
            )}
          </div>
        )}

        {result ? (
          <div className="bg-canvas rounded-card p-3 mb-4 text-sm space-y-1">
            <p className="font-medium text-textPrimary">{t('agents:importedTitle')}</p>
            <p className="text-xs text-textSecondary">
              {t('agents:importedMemories')} {result.memories || 0}
              <span className="text-textMuted">
                （{t('agents:importedDetails')} {result.memory_details || 0}）
              </span>
            </p>
            <p className="text-xs text-textSecondary">{t('agents:importedKnowledge')} {result.knowledge || 0}</p>
          </div>
        ) : (
          <div className="space-y-1 mb-4">
            <label className="flex items-center gap-2 text-sm text-textSecondary cursor-pointer">
              <input
                type="checkbox"
                checked={importMemories}
                onChange={(e) => setImportMemories(e.target.checked)}
                className="rounded"
              />
              {t('agents:importMemories')}
            </label>
            <label className="flex items-center gap-2 text-sm text-textSecondary cursor-pointer">
              <input
                type="checkbox"
                checked={importKnowledge}
                onChange={(e) => setImportKnowledge(e.target.checked)}
                className="rounded"
              />
              {t('agents:importKnowledge')}
            </label>
          </div>
        )}

        {error && <div className="text-sm text-rose-400 mb-3">{error}</div>}

        <div className="flex gap-2">
          {result ? (
            <button onClick={onImported} className="btn btn-md btn-primary flex-1">
              {t('agents:importedDone')}
            </button>
          ) : (
            <>
              <button onClick={onClose} className="btn btn-md btn-outline flex-1">
                {t('agents:cancel')}
              </button>
              <button
                onClick={handleImport}
                disabled={!file || loading}
                className="btn btn-md btn-primary flex-1"
              >
                {loading ? t('agents:importing') : t('agents:importButton')}
              </button>
            </>
          )}
        </div>
      </div>
    </Dialog>
  )
}
