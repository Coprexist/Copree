/**
 * 绑定群弹窗（世界设计页 / 世界列表页共用）
 *
 * 流程（对齐需求）：先选群类型 → 勾选群聊（只显示「我是群主」的群，可改绑）→ 批量绑定
 * 自动创建群助手（按类型模板），绑定结果逐群反馈。
 */
import { useEffect, useMemo, useState } from 'react'
import { X, Link2, Users, CheckSquare, Loader2, AlertCircle, Bot } from 'lucide-react'
import { api } from '../../api/client'
import { Dialog } from '../ui'

interface GroupType {
  slug: string
  name: string
  description: string
  bind_limit: number
  bound_count?: number
}

interface GroupItem {
  id: number
  name: string
  owner_type?: string
  owner_id?: number
  is_pinned?: boolean
}

type BindTab = 'group' | 'agent'

interface BindGroupModalProps {
  worldId: number
  /** 预选类型（群类型卡片点「绑定群」时传入） */
  initialTypeSlug?: string
  /** 初始 tab：group=绑定群聊 / agent=绑定 AI（世界列表页「绑定 AI」按钮用） */
  initialTab?: BindTab
  onClose: () => void
  /** 绑定成功后回调（刷新类型绑定数/群助手） */
  onBound: () => void
}

export default function BindGroupModal({ worldId, initialTypeSlug, initialTab = 'group', onClose, onBound }: BindGroupModalProps) {
  const [tab, setTab] = useState<BindTab>(initialTab)
  const [types, setTypes] = useState<GroupType[]>([])
  const [groups, setGroups] = useState<GroupItem[]>([])
  const [agents, setAgents] = useState<{ id: number; name: string; owner_id?: number; user_id?: number }[]>([])
  const [boundGroupIds, setBoundGroupIds] = useState<Set<number>>(new Set())
  // AI 绑定统一用 user_id（全平台 AI 对外标识一律 user_id，杜绝与 agent.id 撞车）
  const [boundAgentIds, setBoundAgentIds] = useState<Set<number>>(new Set())
  const [agentUserIdByAgentId, setAgentUserIdByAgentId] = useState<Map<number, number>>(new Map())
  const [typeSlug, setTypeSlug] = useState<string>(initialTypeSlug || '')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [msg, setMsg] = useState('')
  const [binding, setBinding] = useState(false)
  const myId = useMemo(() => {
    try { return JSON.parse(localStorage.getItem('user_info') || '{}').id ?? null } catch { return null }
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [t, g, w, a] = await Promise.all([
          api.get<{ types: GroupType[] }>(`/worlds/${worldId}/group-types?entity_type=${tab}`),
          api.get<GroupItem[]>('/groups'),
          api.get<{ bindings: { entity_type: string; entity_id: number }[] }>(`/worlds/${worldId}`),
          api.get<{ id: number; name: string; owner_id?: number; user_id?: number }[]>('/agents'),
        ])
        if (cancelled) return
        setTypes(t.types || [])
        setGroups(g || [])
        setAgents(Array.isArray(a) ? a : [])
        // AI 映射：agent.id → user_id（绑定用 user_id）
        const uidMap = new Map<number, number>()
        for (const ag of (Array.isArray(a) ? a : [])) {
          if (ag.user_id) uidMap.set(ag.id, ag.user_id)
        }
        setAgentUserIdByAgentId(uidMap)
        setBoundGroupIds(new Set((w.bindings || []).filter((b) => b.entity_type === 'group').map((b) => b.entity_id)))
        setBoundAgentIds(new Set((w.bindings || []).filter((b) => b.entity_type === 'agent').map((b) => b.entity_id)))
      } catch { /* 失败静默，弹窗可关 */ }
    })()
    return () => { cancelled = true }
  }, [worldId, tab])

  // 可绑定群：仅「我是群主」（owner_type=human 且 owner_id=我）
  const ownedGroups = useMemo(
    () => groups.filter((g) => g.owner_type === 'human' && g.owner_id === myId),
    [groups, myId],
  )
  // 可绑定 AI：仅「我拥有的」（owner_id=我；合作的 AI 不绑我的世界）
  const ownedAgents = useMemo(
    () => agents.filter((a) => a.owner_id === myId || a.owner_id == null),
    [agents, myId],
  )
  // 当前 tab 的已绑集合 + 候选列表
  const boundIds = tab === 'group' ? boundGroupIds : boundAgentIds
  const candidates = tab === 'group' ? ownedGroups : ownedAgents
  const currentType = types.find((t) => t.slug === typeSlug)

  const toggle = (gid: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(gid)) next.delete(gid)
      else next.add(gid)
      return next
    })
  }

  const doBind = async () => {
    if (!typeSlug || selected.size === 0) return
    setBinding(true)
    setMsg('')
    try {
      // AI 绑定统一用 user_id（agent.id → user_id 映射；后端也做归一化防御）
      const idsToSubmit = tab === 'agent'
        ? [...selected].map((id) => agentUserIdByAgentId.get(id) ?? id)
        : [...selected]
      const r = await api.post<{ bound: number; failed: number; results: { entity_id: number; success: boolean; error?: string }[] }>(
        `/worlds/${worldId}/bind-entries`, { entity_type: tab, type_slug: typeSlug, entity_ids: idsToSubmit },
      )
      // 绑定成功的群从勾选移除 + 标记已绑
      const done = new Set((r.results || []).filter((x) => x.success).map((x) => x.entity_id))
      if (tab === 'group') {
        setBoundGroupIds((prev) => { const n = new Set(prev); done.forEach((g) => n.add(g)); return n })
      } else {
        setBoundAgentIds((prev) => { const n = new Set(prev); done.forEach((g) => n.add(g)); return n })
      }
      setSelected((prev) => { const n = new Set(prev); done.forEach((g) => n.delete(g)); return n })
      if (r.failed === 0) {
        // 全部成功：直接关闭（结果已在类型/群列表上体现）
        onBound()
        onClose()
        return
      }
      // 部分失败：留在弹窗，只提示失败的
      const errors = (r.results || []).filter((x) => !x.success).map((x) => x.error).filter(Boolean)
      setMsg(`${r.failed} 个群绑定失败${errors.length ? '：' + errors[0] : ''}`)
      onBound()
    } catch (e: any) {
      setMsg(`绑定失败: ${e?.message || e}`)
    } finally {
      setBinding(false)
    }
  }

  return (
    <Dialog onClose={onClose} className="flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-surface border border-border rounded-dialog max-h-[85vh] flex flex-col shadow-xl" onClick={(e) => e.stopPropagation()}>
        {/* 头部 */}
        <div className="flex items-center justify-between p-4 pb-2 shrink-0">
          <div className="flex items-center gap-2">
            <Link2 size={16} className="text-primary-400" />
            <span className="text-sm font-semibold text-textPrimary">绑定入口</span>
            <span className="text-3xs text-textMuted">选类型 → 勾选 → 批量绑定</span>
          </div>
          {/* Tab：群聊 / AI */}
          <div className="flex items-center gap-1 bg-elevated rounded-control p-0.5 shrink-0">
            <button onClick={() => { setTab('group'); setSelected(new Set()); setTypeSlug('') }} className={`inline-flex items-center gap-1 text-xs px-3 py-1 rounded-control ${tab === 'group' ? 'bg-primary-500/15 text-primary-400' : 'text-textSecondary'}`}>
              <Users size={12} /> 群聊
            </button>
            <button onClick={() => { setTab('agent'); setSelected(new Set()); setTypeSlug('') }} className={`inline-flex items-center gap-1 text-xs px-3 py-1 rounded-control ${tab === 'agent' ? 'bg-primary-500/15 text-primary-400' : 'text-textSecondary'}`}>
              <Bot size={12} /> AI
            </button>
          </div>
          <button onClick={onClose} className="p-1 text-textMuted hover:text-textPrimary transition-colors" title="关闭"><X size={16} /></button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {/* 1. 选类型 */}
          <div>
            <div className="text-3xs text-textSecondary uppercase tracking-wide font-medium mb-1.5 flex items-center gap-1"><Users size={11} className="text-primary-400" /> 1. 选择{tab === 'group' ? '群类型' : 'AI 类型'}</div>
            {types.length === 0 ? (
              <div className="text-xs text-textMuted bg-elevated/40 rounded-card p-3">{tab === 'group' ? '还没有群类型——先去「群类型与群助手」里创建（如 冒险团/商会/座谈会），才能给群分类。' : '还没有类型定义——群与 AI 共用同一套类型，先去「群类型与群助手」里创建。'}</div>
            ) : (
              <div className="space-y-1.5">
                {types.map((t) => (
                  <button
                    key={t.slug}
                    onClick={() => setTypeSlug(t.slug)}
                    className={`w-full text-left px-3 py-2 rounded-card border transition-colors ${typeSlug === t.slug ? 'border-primary-500/50 bg-primary-500/10' : 'border-border bg-elevated/40 hover:bg-elevated'}`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-textPrimary">{t.name}</span>
                      <span className="text-3xs text-textMuted">{t.bound_count ?? '?'}/{t.bind_limit === -1 ? '∞' : t.bind_limit} {tab === 'group' ? '群' : 'AI'}</span>
                    </div>
                    {t.description && <div className="text-xs text-textSecondary line-clamp-1 mt-0.5">{t.description}</div>}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* 2. 勾选群 */}
          {typeSlug && (
            <div>
              <div className="text-3xs text-textSecondary uppercase tracking-wide font-medium mb-1.5 flex items-center gap-1"><CheckSquare size={11} className="text-primary-400" /> 2. 勾选{tab === 'group' ? '群聊（仅显示你群主的群）' : 'AI（仅显示你创建的 AI）'}</div>
              {candidates.length === 0 ? (
                <div className="text-xs text-textMuted bg-elevated/40 rounded-card p-3">{tab === 'group' ? '你没有可绑定的群（需要是你创建的群）。' : '你没有可绑定的 AI（需要是你创建的 AI）。'}</div>
              ) : (
                <div className="space-y-1">
                  {candidates.map((g: any) => {
                    // AI 绑定按 user_id 判断（boundAgentIds 存 user_id）
                    const gid = tab === 'agent' ? (agentUserIdByAgentId.get(g.id) ?? g.id) : g.id
                    const isBound = boundIds.has(gid)
                    const isSelected = selected.has(g.id)
                    return (
                      <label
                        key={g.id}
                        className={`flex items-center gap-2 px-3 py-2 rounded-card border cursor-pointer transition-colors ${isSelected ? 'border-primary-500/50 bg-primary-500/10' : isBound ? 'border-border bg-elevated/30 opacity-60' : 'border-border bg-elevated/40 hover:bg-elevated'}`}
                      >
                        <input
                          type="checkbox"
                          checked={isSelected}
                          disabled={binding || isBound}
                          onChange={() => toggle(g.id)}
                          className="accent-primary-500 shrink-0"
                        />
                        <span className="truncate flex-1 text-sm text-textPrimary">{g.name}</span>
                        <span className="shrink-0 text-3xs text-textMuted">#{g.id}</span>
                        {isBound && <span className="shrink-0 text-3xs text-textMuted">已绑定</span>}
                      </label>
                    )
                  })}
                </div>
              )}
            </div>
          )}
          {msg && <div className="inline-flex items-center gap-1 text-xs text-rose-400"><AlertCircle size={12} className="shrink-0" /> {msg}</div>}
        </div>

        {/* 底部操作 */}
        <div className="p-4 pt-3 shrink-0 border-t border-border flex items-center gap-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-textMuted hover:text-textPrimary transition-colors rounded-control">取消</button>
          <div className="flex-1" />
          <button
            onClick={doBind}
            disabled={!typeSlug || selected.size === 0 || binding}
            className="btn btn-sm btn-primary gap-1.5"
          >
            {binding ? <Loader2 size={14} className="animate-spin" /> : <Link2 size={14} />}
            绑定 {selected.size > 0 ? `${selected.size} 个${tab === 'group' ? '群' : 'AI'}` : ''}
          </button>
        </div>
      </div>
    </Dialog>
  )
}
