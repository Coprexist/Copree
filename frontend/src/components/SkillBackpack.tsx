import { useState, useEffect, useMemo } from 'react'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { ExpandPanel } from './ui'
import { STATE_LABELS, STATE_TAG_COLORS } from '../constants'
import {
  MessagesSquare, Folder, Brain, Users, Settings, Clock,
  ChevronDown, ChevronUp, Puzzle, X } from 'lucide-react'

// ─── 类型 ────────────────────────────────────────────

interface ToolInfo {
  name: string
  description: string
  admin_description: string
  trigger_condition: string
  states: string[]
  available_in_current_state?: boolean
}

interface SegmentInfo {
  name: string
  description: string
  admin_description: string
  trigger_conditions: string[]
  icon: string
  tool_count: number
  tools: ToolInfo[]
}

interface BackpackResponse {
  segments: SegmentInfo[]
  agent_state: string | null
  agent_thinking: boolean | null
}

// ─── 图标映射 ─────────────────────────────────────────

const ICON_MAP: Record<string, React.ElementType> = {
  'messages-square': MessagesSquare,
  'folder': Folder,
  'brain': Brain,
  'users': Users,
  'settings': Settings,
  'clock': Clock,
  'puzzle': Puzzle,
}

// ─── Props ────────────────────────────────────────────

interface Props {
  agentId?: number | null
  className?: string
}

// ─── 组件 ─────────────────────────────────────────────

export default function SkillBackpack({ agentId, className = '' }: Props) {
  const t = useT()
  const [data, setData] = useState<BackpackResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [expandedSegment, setExpandedSegment] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    const params = agentId ? `?agent_id=${agentId}` : ''
    api.get<BackpackResponse>(`/admin/tools/backpack${params}`)
      .then(r => { setData(r); setLoading(false) })
      .catch(() => setLoading(false))
  }, [agentId])

  const segments = useMemo(() => data?.segments ?? [], [data])

  if (loading) {
    return <div className={`text-center py-8 text-textSecondary text-sm ${className}`}>加载中...</div>
  }
  if (!data || segments.length === 0) {
    return <div className={`text-center py-8 text-rose-500 text-sm ${className}`}>加载失败</div>
  }

  const expandedSeg = expandedSegment ? segments.find(s => s.name === expandedSegment) : null

  return (
    <div className={`space-y-3 ${className}`}>
      {/* 当前状态指示 */}
      {data.agent_state && (
        <div className="flex items-center gap-2 text-xs text-textSecondary mb-1">
          <span>{t('backpack.currentState')}:</span>
          <span className={`px-2 py-0.5 rounded ${STATE_TAG_COLORS[data.agent_state] || ''}`}>
            {STATE_LABELS[data.agent_state] || data.agent_state}
          </span>
          {data.agent_thinking !== null && (
            <span className="text-textMuted">· 深度推理: {data.agent_thinking ? '开' : '关'}</span>
          )}
        </div>
      )}

      {/* 段卡片网格 */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {segments.map(seg => {
          const Icon = ICON_MAP[seg.icon] || Puzzle
          const isExpanded = expandedSegment === seg.name

          return (
            <button
              key={seg.name}
              onClick={() => setExpandedSegment(isExpanded ? null : seg.name)}
              className={`bg-surface rounded-card border border-border text-left p-3.5 transition-all ${
                isExpanded
                  ? 'ring-2 ring-primary-500/30 shadow-lg border-primary-500/30'
                  : 'hover:border-primary-500/20 hover:shadow-sm'
              }`}
            >
              <div className="flex items-center gap-2.5 mb-2">
                <div className="w-8 h-8 rounded-control bg-primary-500/10 flex items-center justify-center shrink-0">
                  <Icon size={16} className="text-primary-500" />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-sm font-semibold text-textPrimary">{seg.name}</h3>
                  <p className="text-3xs text-textMuted">{seg.tool_count} {t('backpack.toolCount')}</p>
                </div>
                <div className="shrink-0 text-textMuted">
                  {isExpanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                </div>
              </div>

              {seg.admin_description && (
                <p className="text-2xs text-textSecondary leading-relaxed line-clamp-2 mb-2">{seg.admin_description}</p>
              )}

              {seg.trigger_conditions.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {seg.trigger_conditions.slice(0, 2).map(tc => (
                    <span key={tc} className="text-3xs px-1.5 py-0.5 rounded bg-primary-500/8 text-primary-600 dark:text-primary-400" title={tc}>
                      {tc.length > 12 ? tc.slice(0, 12) + '…' : tc}
                    </span>
                  ))}
                  {seg.trigger_conditions.length > 2 && (
                    <span className="text-3xs px-1.5 py-0.5 rounded bg-canvas text-textMuted" title={seg.trigger_conditions.slice(2).join('、')}>
                      +{seg.trigger_conditions.length - 2}
                    </span>
                  )}
                </div>
              )}
            </button>
          )
        })}
      </div>

      {/* 展开区域 — 通栏显示被选中段落的工具（与插件页共用同一块 ExpandPanel） */}
      {expandedSeg && (
        <ExpandPanel
          title={`${expandedSeg.name} — ${t('backpack.toolsInSkill')} (${expandedSeg.tool_count})`}
          collapseLabel={t('tool:ui.collapse')}
          onCollapse={() => setExpandedSegment(null)}
        >
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {expandedSeg.tools.map(tool => (
              <div key={tool.name} className="rounded-control border border-border/60 p-3 hover:border-primary-500/30 hover:bg-primary-500/[0.02] transition-colors">
                <div className="flex items-center gap-2 flex-wrap min-w-0">
                  <span className="font-mono text-xs font-medium text-textPrimary">{tool.name}</span>
                  {/* 可用性 */}
                  {tool.available_in_current_state !== undefined && (
                    <span className={`text-3xs px-1.5 py-0.5 rounded ${
                      tool.available_in_current_state
                        ? 'bg-mint-100 text-mint-700 dark:bg-mint-900/30 dark:text-mint-400'
                        : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
                    }`}>
                      {tool.available_in_current_state ? t('backpack.availableNow') : t('backpack.unavailableNow')}
                    </span>
                  )}
                  {/* 状态标签 */}
                  {tool.states.map(s => (
                    <span key={s} className={`text-3xs px-1.5 py-0.5 rounded ${STATE_TAG_COLORS[s] || 'bg-canvas text-textMuted'}`}>
                      {STATE_LABELS[s] || s}
                    </span>
                  ))}
                </div>
                {tool.admin_description && (
                  <p className="text-2xs text-textSecondary leading-relaxed mt-1.5">{tool.admin_description}</p>
                )}
                {tool.trigger_condition && (
                  <div className="mt-1 text-3xs text-primary-500/70">{tool.trigger_condition}</div>
                )}
              </div>
            ))}
          </div>
        </ExpandPanel>
      )}
    </div>
  )
}
