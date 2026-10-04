/**
 * 群视界世界列表页 — 创建世界 / 绑定群聊 / 进入设计页（2026-08-07 适配日夜主题）
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronRight, Store, Trash2, Plus, Eye } from 'lucide-react'
import { useT } from '../i18n/I18nContext'
import { api } from '../api/client'
import BindGroupModal from '../components/world/BindGroupModal'
import { Button, PageShell } from '../components/ui'

interface World {
  id: number
  name: string
  description: string
  status: string
  time_flow_rate: number
  bindings: { entity_type: string; entity_id: number }[]
}

export default function WorldsPage() {
  const navigate = useNavigate()
  const t = useT()
  const [worlds, setWorlds] = useState<World[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [msg, setMsg] = useState('')

  const load = async () => {
    try {
      const list = await api.get<World[]>('/worlds')
      setWorlds(list || [])
    } catch (e: any) {
      setMsg(t('tool:world.editor.loadFailed', { error: e?.message || e }))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const create = async () => {
    if (!name.trim()) return
    try {
      const w = await api.post<World>('/worlds', { name, description, time_flow_rate: 1.0 })
      setShowCreate(false)
      setName('')
      setDescription('')
      navigate(`/worlds/${w.id}/design`)
    } catch (e: any) {
      setMsg(t('tool:world.editor.createFailed', { error: e?.message || e }))
    }
  }

  const [bindWorld, setBindWorld] = useState<{ world: World; tab?: 'group' | 'agent' } | null>(null)

  const openBindModal = (world: World, tab?: 'group' | 'agent') => setBindWorld({ world, tab })

  const toggleStatus = async (world: World) => {
    try {
      await api.post(`/worlds/${world.id}/${world.status === 'active' ? 'sleep' : 'wake'}`)
      load()
    } catch { /* ignore */ }
  }

  const deleteWorld = async (world: World) => {
    if (!confirm(t('tool:world.list.confirmDelete', { name: world.name }))) return
    try {
      await api.delete(`/worlds/${world.id}`)
      setMsg(t('tool:world.list.deleted', { name: world.name }))
      load()
    } catch (e: any) {
      setMsg(t('tool:world.editor.deleteFailed', { error: e?.message || e }))
    }
  }

  if (loading) return <div className="flex items-center justify-center h-screen text-textMuted">{t('common:loading')}</div>

  return (
    <PageShell
      title={t('nav:worlds')}
      subtitle={t('tool:world.list.subtitle')}
      width="content"
      contentClassName=""
      actions={
        <>
          <Button size="sm" variant="secondary" icon={<Store size={14} />} title={t('tool:world.list.marketTitle')} onClick={() => navigate('/market')}>
            {t('tool:world.list.market')}
          </Button>
          <Button size="sm" icon={<Plus size={14} />} onClick={() => setShowCreate(!showCreate)}>
            {t('tool:world.list.create')}
          </Button>
        </>
      }
    >
          {msg && <div className="text-sm text-accent-400 mb-4">{msg}</div>}

          {showCreate && (
            <div className="bg-surface border border-border rounded-control p-4 mb-6 space-y-3">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('tool:world.list.namePlaceholder')}
                className="w-full bg-elevated text-textPrimary px-3 py-2 rounded text-sm outline-none border border-border focus:border-primary-500/50"
              />
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t('tool:world.list.descPlaceholder')}
                rows={2}
                className="w-full bg-elevated text-textPrimary px-3 py-2 rounded text-sm outline-none resize-none border border-border focus:border-primary-500/50"
              />
              <button onClick={create} className="btn btn-sm btn-primary">{t('tool:world.list.createAndDesign')}</button>
            </div>
          )}

          {worlds.length === 0 && !showCreate && (
            <div className="text-center text-textMuted py-20 text-sm">
              {t('tool:world.list.empty')}<br />
              <span className="text-textSecondary text-xs">{t('tool:world.list.emptyHint')}</span>
            </div>
          )}

          <div className="space-y-3">
            {worlds.map((w) => (
              <div key={w.id} className="bg-surface border border-border rounded-control p-4 flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-4 hover:border-primary-500/40 transition-colors">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium text-textPrimary truncate">{w.name}</span>
                    <span className={`text-3xs px-2 py-0.5 rounded-full shrink-0 ${w.status === 'active' ? 'bg-mint-500/20 text-mint-400' : 'bg-elevated text-textMuted'}`}>
                      {w.status === 'active' ? t('tool:world.top.status.active') : t('tool:world.top.status.hibernating')}
                    </span>
                  </div>
                  {w.description && <div className="text-xs text-textSecondary truncate mt-0.5">{w.description}</div>}
                  <div className="text-3xs text-textMuted mt-1 truncate">
                    {t('tool:world.list.entry')} {w.bindings?.length ? w.bindings.map((b) => `${b.entity_type}#${b.entity_id}`).join(', ') : t('tool:world.list.unbound')}
                  </div>
                </div>
                {/* 操作按钮：统一 .btn .btn-sm 语义类（固定高度严格等高） */}
                <div className="flex items-center gap-2 shrink-0 flex-wrap">
                  <button onClick={() => openBindModal(w, 'group')} className="btn btn-sm btn-secondary">{t('tool:world.list.bindGroup')}</button>
                  <button onClick={() => openBindModal(w, 'agent')} className="btn btn-sm btn-secondary">{t('tool:world.list.bindAgent')}</button>
                  <button onClick={() => toggleStatus(w)} className="btn btn-sm btn-secondary">
                    {w.status === 'active' ? t('tool:world.top.status.hibernating') : t('tool:world.list.wake')}
                  </button>
                  <button
                    onClick={() => window.open(`/world/${w.id}/preview`, '_blank')}
                    className="btn btn-sm btn-secondary"
                    title={t('tool:world.list.previewTitle')}
                  >
                    <Eye size={13} /> {t('tool:world.list.open')}
                  </button>
                  <button
                    onClick={() => navigate(`/worlds/${w.id}/design`)}
                    className="btn btn-sm btn-primary"
                  >
                    {t('tool:world.list.design')} <ChevronRight size={13} />
                  </button>
                  <button
                    onClick={() => deleteWorld(w)}
                    className="btn btn-sm btn-danger !w-8 !px-0"
                    title={t('tool:world.list.deleteTitle')}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>

      {/* 绑定群弹窗：选类型 → 勾选群批量绑定（BindGroupModal 自包含） */}
      {bindWorld && (
        <BindGroupModal
          worldId={bindWorld.world.id}
          initialTab={bindWorld.tab}
          onClose={() => setBindWorld(null)}
          onBound={() => { setBindWorld(null); load() }}
        />
      )}
    </PageShell>
  )
}
