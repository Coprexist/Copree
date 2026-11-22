/**
 * 群视界世界列表页 — 创建世界 / 绑定群聊 / 进入设计页（2026-08-07 适配日夜主题）
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronRight, Store, Trash2, Plus, Eye, MoreHorizontal } from 'lucide-react'
import { useT } from '../i18n/I18nContext'
import { api } from '../api/client'
import BindGroupModal from '../components/world/BindGroupModal'
import { Button, PageShell, IconButton, MenuPanel, MenuItem } from '../components/ui'

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
  // 卡片右上「···」菜单：同时只开一张卡的
  const [menuId, setMenuId] = useState<number | null>(null)

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
            <div className="card p-4 mb-6 space-y-3">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('tool:world.list.namePlaceholder')}
                className="field w-full"
              />
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t('tool:world.list.descPlaceholder')}
                rows={2}
                className="field field-noresize w-full"
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

          {/* 世界卡片：与「我的 AI」同一种卡片（.card + .chip + .btn），一屏能看几个 */}
          <div className="grid sm:grid-cols-2 gap-4">
            {worlds.map((w) => (
              <div key={w.id} className="card p-4 flex flex-col gap-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-medium text-textPrimary truncate">{w.name}</div>
                    <div className="text-3xs text-textMuted mt-0.5">#{w.id}</div>
                  </div>
                  <span className={'chip shrink-0 ' + (w.status === 'active' ? 'chip-mint' : 'chip-muted')}>
                    {w.status === 'active' ? t('tool:world.top.status.active') : t('tool:world.top.status.hibernating')}
                  </span>
                </div>

                <p className="text-xs text-textSecondary line-clamp-2 min-h-[2.5rem]">
                  {w.description}
                </p>

                <div className="text-3xs text-textMuted truncate">
                  {t('tool:world.list.entry')}{' '}
                  {w.bindings?.length
                    ? w.bindings.map((b) => b.entity_type + '#' + b.entity_id).join(', ')
                    : t('tool:world.list.unbound')}
                </div>

                <div className="mt-auto flex items-center gap-2 pt-1">
                  <button onClick={() => navigate("/worlds/" + w.id + "/design")} className="btn btn-sm btn-primary">
                    {t('tool:world.list.design')} <ChevronRight size={13} />
                  </button>
                  <button
                    onClick={() => window.open("/world/" + w.id + "/preview", "_blank")}
                    className="btn btn-sm btn-secondary"
                    title={t('tool:world.list.previewTitle')}
                  >
                    <Eye size={13} /> {t('tool:world.list.open')}
                  </button>
                  <div className="relative ml-auto">
                    <IconButton
                      size="sm"
                      icon={<MoreHorizontal size={13} />}
                      label={t('common:more')}
                      onClick={() => setMenuId(menuId === w.id ? null : w.id)}
                    />
                    {menuId === w.id && (
                      <MenuPanel className="absolute right-0 top-full mt-1 w-44 z-modal">
                        <MenuItem onClick={() => { setMenuId(null); openBindModal(w, 'group') }}>{t('tool:world.list.bindGroup')}</MenuItem>
                        <MenuItem onClick={() => { setMenuId(null); openBindModal(w, 'agent') }}>{t('tool:world.list.bindAgent')}</MenuItem>
                        <MenuItem onClick={() => { setMenuId(null); toggleStatus(w) }}>
                          {w.status === 'active' ? t('tool:world.top.status.hibernating') : t('tool:world.list.wake')}
                        </MenuItem>
                        <MenuItem onClick={() => { setMenuId(null); deleteWorld(w) }} className="text-rose-400">
                          <Trash2 size={12} /> {t('tool:world.list.delete')}
                        </MenuItem>
                      </MenuPanel>
                    )}
                  </div>
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
