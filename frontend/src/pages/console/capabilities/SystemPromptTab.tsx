import { useState, useEffect } from 'react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'
import { Save, RotateCcw, Eye, Edit3, ChevronDown, ChevronUp, Loader2, Layers, ArrowRight, ArrowUp, ArrowDown } from 'lucide-react'

interface Segment {
  key: string
  label: string
  description: string
  current: string
  default: string
  is_overridden: boolean
  readonly?: boolean
}

export default function SystemPromptTab() {
  const t = useT()
  const [segments, setSegments] = useState<Segment[]>([])
  const [order, setOrder] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)

  useEffect(() => {
    loadSegments()
  }, [])

  const loadSegments = () => {
    setLoading(true)
    api.get<{ segments: Segment[]; segment_order: string[] }>('/admin/system-prompt')
      .then(r => {
        setSegments(r.segments)
        setOrder(r.segment_order)
      })
      .catch(() => setMessage('加载失败'))
      .finally(() => setLoading(false))
  }

  const startEdit = (seg: Segment) => {
    setEditingKey(seg.key)
    setEditValue(seg.current)
  }

  const cancelEdit = () => {
    setEditingKey(null)
    setEditValue('')
  }

  const saveEdit = async (seg: Segment) => {
    const overrides: Record<string, string> = {}
    overrides[seg.key] = editValue
    // 也保留其他已覆盖的段
    for (const s of segments) {
      if (s.key !== seg.key && s.is_overridden && !s.readonly) {
        overrides[s.key] = s.current
      }
    }
    setSaving(true)
    setMessage(null)
    try {
      await api.put('/admin/system-prompt', { overrides })
      setMessage('已保存')
      setEditingKey(null)
      await loadSegments()
    } catch {
      setMessage('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const resetSegment = async (seg: Segment) => {
    const overrides: Record<string, string> = {}
    for (const s of segments) {
      if (s.key !== seg.key && s.is_overridden && !s.readonly) {
        overrides[s.key] = s.current
      }
    }
    setSaving(true)
    setMessage(null)
    try {
      await api.put('/admin/system-prompt', { overrides })
      setMessage('已恢复默认')
      await loadSegments()
    } catch {
      setMessage('重置失败')
    } finally {
      setSaving(false)
    }
  }

  const previewText = order
    .map(k => {
      const seg = segments.find(s => s.key === k)
      return seg ? `## ${seg.label}\n${seg.current}` : ''
    })
    .filter(Boolean)
    .join('\n\n')

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 size={24} className="animate-spin text-textMuted" />
      </div>
    )
  }

  return (
    <div className="max-w-3xl space-y-4">
      {/* 标题栏 */}
      <div className="flex items-center gap-3 flex-wrap">
        <h3 className="text-sm font-semibold text-textPrimary flex items-center gap-2">
          <Layers size={16} className="text-primary-400" />
          系统提示词
        </h3>
        <span className="text-xs text-textMuted">管理发给 AI 的提示词段和组装顺序</span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setPreviewOpen(!previewOpen)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-control bg-elevated hover:bg-canvas border border-border text-xs text-textSecondary hover:text-textPrimary transition-colors"
          >
            <Eye size={13} />
            {previewOpen ? '隐藏预览' : '预览拼接'}
          </button>
        </div>
      </div>

      {message && (
        <div className={`text-sm px-3 py-2 rounded-card ${
          message.includes('失败') ? 'bg-rose-400/10 text-rose-400' : 'bg-mint-400/10 text-mint-400'
        }`}>{message}</div>
      )}

      {/* 预览面板 */}
      {previewOpen && (
        <div className="bg-canvas border border-border rounded-card p-4 max-h-96 overflow-y-auto">
          <pre className="text-xs text-textSecondary whitespace-pre-wrap font-mono">{previewText || '（空）'}</pre>
        </div>
      )}

      {/* 段列表 */}
      <div className="space-y-2">
        {segments.map((seg, idx) => (
          <div
            key={seg.key}
            className={`bg-surface border rounded-card transition-colors ${
              seg.is_overridden ? 'border-accent-400/30' :
              seg.readonly ? 'border-border/60 opacity-70' : 'border-border'
            }`}
          >
            {/* 段头部 */}
            <div
              className="flex items-center gap-2 px-4 py-3 cursor-pointer select-none"
              onClick={() => setExpandedKey(expandedKey === seg.key ? null : seg.key)}
            >
              <span className="text-3xs w-5 h-5 rounded-full bg-elevated border border-border flex items-center justify-center text-textMuted font-mono shrink-0">
                {idx + 1}
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-textPrimary truncate">{seg.label}</span>
                  {seg.is_overridden && (
                    <span className="text-3xs px-1.5 py-0.5 rounded-full bg-accent-400/10 text-accent-400 border border-accent-400/20 shrink-0">
                      已覆盖
                    </span>
                  )}
                  {seg.readonly && (
                    <span className="text-3xs px-1.5 py-0.5 rounded-full bg-canvas text-textMuted border border-border shrink-0">
                      动态
                    </span>
                  )}
                </div>
                <p className="text-xs text-textMuted mt-0.5 truncate">{seg.description}</p>
              </div>
              {expandedKey === seg.key ? <ChevronUp size={16} className="text-textMuted shrink-0" /> : <ChevronDown size={16} className="text-textMuted shrink-0" />}
            </div>

            {/* 段内容 */}
            {expandedKey === seg.key && (
              <div className="px-4 pb-4 border-t border-border/60 pt-3">
                {editingKey === seg.key ? (
                  <div className="space-y-2">
                    <textarea
                      value={editValue}
                      onChange={e => setEditValue(e.target.value)}
                      className="w-full h-48 px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary font-mono resize-y focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                    />
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => saveEdit(seg)}
                        disabled={saving}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-control bg-mint-400 text-white hover:bg-mint-500 disabled:opacity-40 text-xs font-medium transition-colors"
                      >
                        {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                        保存
                      </button>
                      <button
                        onClick={cancelEdit}
                        className="btn btn-xs btn-outline bg-canvas hover:text-textPrimary"
                      >
                        取消
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <pre className="text-xs text-textSecondary whitespace-pre-wrap bg-canvas rounded-control p-3 max-h-40 overflow-y-auto border border-border/60 font-mono">
                      {seg.current}
                    </pre>
                    {!seg.readonly && (
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => startEdit(seg)}
                          className="flex items-center gap-1.5 px-3 py-1.5 rounded-control bg-primary-500/10 text-primary-400 hover:bg-primary-500/20 text-xs font-medium transition-colors"
                        >
                          <Edit3 size={12} /> 编辑
                        </button>
                        {seg.is_overridden && (
                          <button
                            onClick={() => resetSegment(seg)}
                            disabled={saving}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-control bg-canvas border border-border text-xs text-textMuted hover:text-textPrimary transition-colors"
                          >
                            <RotateCcw size={12} /> 恢复默认
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* 组装顺序（可编辑） */}
      <div className="bg-surface border border-border rounded-card p-4">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-xs font-medium text-textSecondary flex items-center gap-2">
            <Layers size={13} className="text-primary-400" />
            组装顺序（可拖拽调整）
          </h4>
          <button
            onClick={async () => {
              setSaving(true); setMessage(null)
              try {
                await api.put('/admin/system-prompt', { segment_order: order })
                setMessage('顺序已保存')
              } catch { setMessage('保存顺序失败') }
              finally { setSaving(false) }
            }}
            disabled={saving}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-control bg-mint-400 text-white hover:bg-mint-500 disabled:opacity-40 text-xs font-medium transition-colors"
          >
            {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
            保存顺序
          </button>
        </div>
        <div className="space-y-1.5">
          {order.map((k, i) => {
            const seg = segments.find(s => s.key === k)
            const moveUp = () => {
              if (i === 0) return
              const next = [...order];
              [next[i - 1], next[i]] = [next[i], next[i - 1]]
              setOrder(next)
            }
            const moveDown = () => {
              if (i === order.length - 1) return
              const next = [...order];
              [next[i], next[i + 1]] = [next[i + 1], next[i]]
              setOrder(next)
            }
            return (
              <div key={k} className="flex items-center gap-2 px-3 py-2 rounded-control bg-canvas border border-border/60">
                <span className="text-3xs w-5 h-5 rounded-full bg-elevated border border-border flex items-center justify-center text-textMuted font-mono shrink-0">
                  {i + 1}
                </span>
                <span className="text-xs text-textPrimary flex-1">{seg?.label || k}</span>
                <button
                  onClick={moveUp} disabled={i === 0}
                  className="icon-btn-sm text-textMuted"
                  title="上移"
                >
                  <ArrowUp size={14} />
                </button>
                <button
                  onClick={moveDown} disabled={i === order.length - 1}
                  className="icon-btn-sm text-textMuted"
                  title="下移"
                >
                  <ArrowDown size={14} />
                </button>
              </div>
            )
          })}
        </div>
        <p className="text-3xs text-textMuted mt-2">点击 ↑↓ 调整段的拼接顺序，点「保存顺序」持久化到数据库。</p>
      </div>
    </div>
  )
}
