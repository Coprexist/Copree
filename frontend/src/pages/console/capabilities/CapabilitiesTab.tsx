import { useState, useEffect, useMemo } from 'react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'
import { STATE_LABELS, STATE_TAG_COLORS } from '../../../constants'
import { Wrench, Brain, Backpack, Plug } from 'lucide-react'
import SkillBackpack from '../../../components/SkillBackpack'
import PluginManager from './PluginManager'

// ─── 类型 ────────────────────────────────────────────

interface ToolInfo {
  name: string
  description: string
  segment: string
  segment_name: string
  states: string[]
  parameters: Record<string, unknown>
  required: string[]
}

interface ToolsResponse {
  tools: ToolInfo[]
  segments: { key: string; name: string; description: string; tool_count: number; tools: string[] }[]
  total: number
}

interface SkillInfo {
  id: number
  agent_id: number
  name: string
  skill_type: string
  is_enabled: boolean
  config: Record<string, unknown>
  priority: number
  created_at: string | null
  updated_at: string | null
  type_hint?: string
}

interface AgentOption {
  id: number
  name: string
}

// ─── 组件 ────────────────────────────────────────────

const SKILL_TYPE_LABELS: Record<string, string> = {
  delay_reply: '延迟回复',
  typing_indicator: '打字指示器',
  scene_trigger: '场景匹配',
  inject_prompt: '注入提示词',
}

type SubTab = 'plugins' | 'registry' | 'skills' | 'backpack'

function ToolRegistryTab() {
  const t = useT()
  const [data, setData] = useState<ToolsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [filterSegment, setFilterSegment] = useState('all')
  const [filterState, setFilterState] = useState('all')
  const [browserStatus, setBrowserStatus] = useState<'loading' | 'running' | 'stopped'>('loading')

  useEffect(() => {
    api.get<ToolsResponse>('/admin/tools')
      .then(r => { setData(r); setLoading(false) })
      .catch(() => setLoading(false))
    // 检查浏览器服务状态
    api.get<{running: boolean}>('/admin/browser-status')
      .then(r => setBrowserStatus(r.running ? 'running' : 'stopped'))
      .catch(() => setBrowserStatus('stopped'))
  }, [])

  const filtered = useMemo(() => {
    if (!data) return []
    return data.tools.filter(tool => {
      if (filterSegment !== 'all' && tool.segment !== filterSegment) return false
      if (filterState !== 'all' && !tool.states.includes(filterState)) return false
      return true
    })
  }, [data, filterSegment, filterState])

  if (loading) return <div className="p-6 text-textSecondary text-sm">加载中...</div>
  if (!data) return <div className="p-6 text-rose-500 text-sm">加载失败</div>

  const stateOptions = ['all', 'active', 'dnd', 'inactive', 'blocked']

  return (
    <div className="space-y-4">
      {/* 统计概览 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {data.segments.map(seg => (
          <div key={seg.key} className="bg-surface rounded-control border border-border p-3">
            <div className="text-xs text-textSecondary">{seg.name}</div>
            <div className="text-lg font-semibold text-textPrimary mt-0.5">{seg.tool_count}</div>
          </div>
        ))}
      </div>

      {/* 浏览器服务状态提示 */}
      <div className={`rounded-control border px-3.5 py-2.5 text-xs flex items-center gap-2 ${
        browserStatus === 'running'
          ? 'bg-mint-500/10 border-mint-500/20 text-mint-600 dark:text-mint-400'
          : browserStatus === 'loading'
            ? 'bg-accent-500/10 border-accent-500/20 text-accent-600 dark:text-accent-400'
            : 'bg-rose-500/10 border-rose-500/20 text-rose-600 dark:text-rose-400'
      }`}>
        <span className={`w-2 h-2 rounded-full shrink-0 ${
          browserStatus === 'running' ? 'bg-mint-500' : browserStatus === 'loading' ? 'bg-accent-500 animate-pulse' : 'bg-rose-500'
        }`} />
        <span>
          {browserStatus === 'running'
            ? 'Chromium 浏览器服务运行中，browser 命令可用'
            : browserStatus === 'loading'
              ? '正在检查浏览器服务状态...'
              : 'Chromium 浏览器服务未运行，browser 命令不可用。请改用 web_search（搜索）和 web_fetch（获取网页）工具'}
        </span>
      </div>

      {/* 筛选 */}
      <div className="flex flex-wrap gap-3 items-center">
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-textSecondary">{t('admin.toolsFilterSegment')}:</label>
          <select
            value={filterSegment}
            onChange={e => setFilterSegment(e.target.value)}
            className="text-sm border border-border rounded-control px-2.5 py-1.5 bg-surface text-textPrimary"
          >
            <option value="all">{t('admin.toolsFilterAll')}</option>
            {data.segments.map(seg => (
              <option key={seg.key} value={seg.key}>{seg.name}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-textSecondary">{t('admin.toolsFilterState')}:</label>
          <select
            value={filterState}
            onChange={e => setFilterState(e.target.value)}
            className="text-sm border border-border rounded-control px-2.5 py-1.5 bg-surface text-textPrimary"
          >
            <option value="all">{t('admin.toolsFilterAll')}</option>
            {stateOptions.filter(s => s !== 'all').map(s => (
              <option key={s} value={s}>{STATE_LABELS[s] || s}</option>
            ))}
          </select>
        </div>
        <span className="text-xs text-textSecondary ml-auto">
          {filtered.length} / {data.total} {t('admin.total')}
        </span>
      </div>

      {/* 工具列表 */}
      <div className="space-y-1.5 max-h-[60vh] overflow-y-auto">
        {filtered.map(tool => (
          <details key={tool.name} className="group bg-surface rounded-control border border-border">
            <summary className="px-4 py-3 cursor-pointer hover:bg-elevated transition-colors flex items-center gap-3 select-none">
              <span className="font-mono text-sm font-medium text-textPrimary min-w-[140px]">{tool.name}</span>
              <span className="text-xs px-2 py-0.5 rounded bg-primary-50 text-primary-600 dark:bg-primary-900/20 dark:text-primary-400">
                {tool.segment_name}
              </span>
              <span className="flex gap-1 ml-auto">
                {tool.states.map(s => (
                  <span key={s} className={`text-xs px-1.5 py-0.5 rounded ${STATE_TAG_COLORS[s] || ''}`}>
                    {STATE_LABELS[s] || s}
                  </span>
                ))}
              </span>
            </summary>
            <div className="px-4 pb-3 border-t border-border pt-2 space-y-2">
              <p className="text-xs text-textSecondary leading-relaxed">{tool.description}</p>
              <div>
                <span className="text-xs font-medium text-textPrimary">{t('admin.toolsParamSchema')}:</span>
                <pre className="mt-1 text-xs bg-canvas rounded p-2 overflow-x-auto text-textSecondary max-h-48">
                  {JSON.stringify({ properties: tool.parameters, required: tool.required }, null, 2)}
                </pre>
              </div>
            </div>
          </details>
        ))}
        {filtered.length === 0 && (
          <div className="text-center py-8 text-textSecondary text-sm">无匹配工具</div>
        )}
      </div>
    </div>
  )
}

function SkillManagementTab() {
  const t = useT()
  const [agents, setAgents] = useState<AgentOption[]>([])
  const [selectedAgent, setSelectedAgent] = useState<number | null>(null)
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [loadingAgents, setLoadingAgents] = useState(true)
  const [loadingSkills, setLoadingSkills] = useState(false)

  // 加载 AI 列表
  useEffect(() => {
    api.get<{agents: {id:number; name:string}[]}>('/admin/agents')
      .then(r => {
        const list = (r.agents || []).map(a => ({ id: a.id, name: a.name }))
        setAgents(list)
        setLoadingAgents(false)
        if (list.length > 0) setSelectedAgent(list[0].id)
      })
      .catch(() => setLoadingAgents(false))
  }, [])

  // 加载技能列表
  useEffect(() => {
    if (!selectedAgent) { setSkills([]); return }
    setLoadingSkills(true)
    api.get<{skills: SkillInfo[]}>(`/admin/skills/agents/${selectedAgent}`)
      .then(r => {
        setSkills(r.skills || [])
        setLoadingSkills(false)
      })
      .catch(() => {
        setSkills([])
        setLoadingSkills(false)
      })
  }, [selectedAgent])

  const handleToggle = async (skillId: number, currentEnabled: boolean) => {
    try {
      await api.put(`/admin/skills/agents/${selectedAgent}/${skillId}`, {
        is_enabled: !currentEnabled,
      })
      setSkills(prev => prev.map(s =>
        s.id === skillId ? { ...s, is_enabled: !currentEnabled } : s
      ))
    } catch {
      alert(t('admin.skillsToggleFailed'))
    }
  }

  const agentName = agents.find(a => a.id === selectedAgent)?.name || ''

  return (
    <div className="space-y-4">
      {/* AI 选择 */}
      <div className="flex items-center gap-3">
        <label className="text-sm text-textSecondary whitespace-nowrap">{t('admin.skillsSelectAgent')}:</label>
        <select
          value={selectedAgent ?? ''}
          onChange={e => setSelectedAgent(Number(e.target.value) || null)}
          className="text-sm border border-border rounded-control px-3 py-2 bg-surface text-textPrimary min-w-[200px]"
          disabled={loadingAgents}
        >
          {loadingAgents && <option value="">加载中...</option>}
          {agents.map(a => (
            <option key={a.id} value={a.id}>{a.name} (ID:{a.id})</option>
          ))}
        </select>
      </div>

      {/* 技能列表 */}
      {!selectedAgent ? (
        <div className="text-center py-8 text-textSecondary text-sm">{t('admin.skillsNoAgent')}</div>
      ) : loadingSkills ? (
        <div className="text-center py-8 text-textSecondary text-sm">加载中...</div>
      ) : skills.length === 0 ? (
        <div className="text-center py-8 text-textSecondary text-sm">此 AI 没有技能</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-textSecondary text-xs uppercase tracking-wider">
                <th className="px-3 py-2">{t('admin.skillsColName')}</th>
                <th className="px-3 py-2">{t('admin.skillsColType')}</th>
                <th className="px-3 py-2">{t('admin.skillsColEnabled')}</th>
                <th className="px-3 py-2">{t('admin.skillsColConfig')}</th>
                <th className="px-3 py-2">{t('admin.skillsColAction')}</th>
              </tr>
            </thead>
            <tbody>
              {skills.map(skill => (
                <tr key={skill.id} className="border-b border-border hover:bg-elevated transition-colors">
                  <td className="px-3 py-2.5 font-medium text-textPrimary">{skill.name}</td>
                  <td className="px-3 py-2.5">
                    <span className="text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400">
                      {SKILL_TYPE_LABELS[skill.skill_type] || skill.skill_type}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={`text-xs px-2 py-0.5 rounded ${skill.is_enabled
                      ? 'bg-mint-100 text-mint-700 dark:bg-mint-900/30 dark:text-mint-400'
                      : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-500'
                    }`}>
                      {skill.is_enabled ? '已启用' : '已禁用'}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 max-w-[200px]">
                    <pre className="text-xs text-textSecondary truncate max-h-10 overflow-hidden">
                      {JSON.stringify(skill.config)}
                    </pre>
                  </td>
                  <td className="px-3 py-2.5">
                    <button
                      onClick={() => handleToggle(skill.id, skill.is_enabled)}
                      className={`text-xs px-2.5 py-1 rounded-control border transition-colors ${skill.is_enabled
                        ? 'border-rose-200 text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400'
                        : 'border-mint-200 text-mint-600 hover:bg-mint-50 dark:border-mint-800 dark:text-mint-400'
                      }`}
                    >
                      {skill.is_enabled ? '禁用' : '启用'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}


/** 能力面板：插件（可装卸的扩展） / 工具（内置工具注册表） / 技能（AI 技能） / 技能背包，四处都是"给 AI 装能力"，合并成一个入口 */
export default function CapabilitiesTab() {
  const t = useT()
  const [subTab, setSubTab] = useState<SubTab>('plugins')

  const subTabs: { key: SubTab; label: string; icon: React.ElementType }[] = [
    { key: 'plugins', label: t('admin.capabilitiesPlugins'), icon: Plug },
    { key: 'registry', label: t('admin.toolRegistry'), icon: Wrench },
    { key: 'skills', label: t('admin.skillManagement'), icon: Brain },
    { key: 'backpack', label: t('admin.skillBackpack'), icon: Backpack },
  ]

  return (
    <div className="space-y-5">
      {/* 子页签 */}
      <div className="flex gap-1 bg-elevated rounded-control p-1 w-fit">
        {subTabs.map(st => (
          <button
            key={st.key}
            onClick={() => setSubTab(st.key)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-control text-sm transition-colors ${subTab === st.key
              ? 'bg-surface text-textPrimary shadow-sm'
              : 'text-textSecondary hover:text-textPrimary'
            }`}
          >
            <st.icon size={15} />
            {st.label}
          </button>
        ))}
      </div>

      {/* 内容 */}
      {subTab === 'plugins' ? <PluginManager />
        : subTab === 'registry' ? <ToolRegistryTab />
        : subTab === 'skills' ? <SkillManagementTab />
        : <SkillBackpack />}
    </div>
  )
}
