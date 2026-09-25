/**
 * 世界商城 — 本地 / GitHub 双板块（2026-08-08）
 *
 * - 本地板块：本实例发布的商品（DB source=local），标注同步状态
 *   （未同步 / 已同步🟢 / 同步过🟡），显示本地+云端下载数与更新时间
 * - GitHub 板块：GitHub 索引快照（refresh 缓存），标注来源（本地/远程），
 *   非本地资源可一键导入
 * - GitHub 绑定：用户绑定自己的 GitHub 账户，同步时以本人身份推送
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Search, Download, Upload, X, Trash2, Globe, Tag, User, Clock,
  Package, Store, Edit3, CheckCircle2, RefreshCw, Github, Link2, Unlink, ArrowUpCircle, Database, Settings,
} from 'lucide-react'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { Dialog, PageHeader } from '../components/ui'
import MarketGithubTab from '../components/MarketGithubTab'

interface MarketItem {
  id: number
  kind: string
  title: string
  description: string
  tags: string[]
  author_id: number
  author_name: string
  source_world_id: number | null
  package_size: number
  downloads: number            // 本地下载数
  github_downloads?: number | null  // 云端下载数
  updated_at: string | null    // 本地更新时间
  github_updated_at?: string | null // 云端更新时间
  sync_state: 'unsynced' | 'synced' | 'stale'
  github_path: string | null
  slug?: string
}

interface GithubItem {
  id: number
  slug: string
  title: string
  description: string
  tags: string[]
  author_name: string
  author_github?: string | null
  downloads: number | null
  updated_at: string | null
  is_local: boolean
  is_mine?: boolean
  signature_valid?: boolean | null
}

type Tab = 'local' | 'github'

const fmtSize = (n: number) => n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`
const fmtDate = (s: string | null | undefined) => s ? s.slice(0, 16).replace('T', ' ') : '—'

export default function MarketPage() {
  const t = useT()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('local')

  // ── 本地板块 ──
  const [items, setItems] = useState<MarketItem[]>([])
  const [total, setTotal] = useState(0)
  const [q, setQ] = useState('')
  const [tag, setTag] = useState('')
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')

  // ── GitHub 板块 ──
  const [ghItems, setGhItems] = useState<GithubItem[]>([])
  const [ghSyncedAt, setGhSyncedAt] = useState<string | null>(null)
  const [ghLoading, setGhLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  // ── GitHub 绑定 ──
  const [bindState, setBindState] = useState<{ bound: boolean; username: string | null } | null>(null)

  // ── 发布/编辑/导入 ──
  const [importingId, setImportingId] = useState<number | null>(null)
  // 实例 GitHub 配置（管理员）：点击先探测权限（非管理员 403）
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsDenied, setSettingsDenied] = useState(false)
  const [syncingId, setSyncingId] = useState<number | null>(null)
  const [editItem, setEditItem] = useState<MarketItem | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editDesc, setEditDesc] = useState('')
  const [editTags, setEditTags] = useState('')
  const [editing, setEditing] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [myId, setMyId] = useState<number | null>(null)

  // ── 卡片详情（桌面弹窗 / 手机全屏）──
  const [detail, setDetail] = useState<{ kind: 'local' | 'github'; item: MarketItem | GithubItem } | null>(null)

  // ── 数据加载 ──
  const loadLocal = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (q.trim()) params.set('q', q.trim())
      if (tag.trim()) params.set('tag', tag.trim())
      params.set('kind', 'world')
      const r = await api.get<{ total: number; items: MarketItem[] }>(`/market/items?${params}`)
      setItems(r.items || [])
      setTotal(r.total || 0)
    } catch (e: any) {
      setMsg(`加载失败: ${e?.message || e}`)
    } finally {
      setLoading(false)
    }
  }, [q, tag])

  const loadGithub = useCallback(async () => {
    setGhLoading(true)
    try {
      const r = await api.get<{ synced_at: string | null; items: GithubItem[] }>(`/market/github/items`)
      setGhItems(r.items || [])
      setGhSyncedAt(r.synced_at || null)
    } catch (e: any) {
      setMsg(`GitHub 板块加载失败: ${e?.message || e}`)
    } finally {
      setGhLoading(false)
    }
  }, [])

  const loadBind = useCallback(async () => {
    try {
      const r = await api.get<{ bound: boolean; username: string | null }>(`/market/github/bind`)
      setBindState(r)
    } catch { /* 非致命 */ }
  }, [])

  // 绑定状态同步：监听其他页面（如“我的”页）的绑定变化
  useEffect(() => {
    const sync = () => loadBind()
    window.addEventListener('gh-bind-changed', sync)
    return () => window.removeEventListener('gh-bind-changed', sync)
  }, [loadBind])

  useEffect(() => { loadLocal() }, [loadLocal])
  useEffect(() => { loadBind() }, [loadBind])
  // 挂载即预加载 GitHub 快照（tab 徽标显示真实数量，而不是初始 0）；切到 GitHub tab 时再刷一次
  useEffect(() => { loadGithub() }, [loadGithub])
  useEffect(() => { if (tab === 'github') loadGithub() }, [tab, loadGithub])

  // 当前用户 id（操作权限判断）
  useEffect(() => {
    try {
      const me = localStorage.getItem('user_info')
      if (me) setMyId(JSON.parse(me).id ?? null)
    } catch { /* ignore */ }
  }, [])

  // ── GitHub 绑定：统一到「我的」页绑定；状态跨页同步 ──
  const doUnbind = async () => {
    if (!confirm('解绑 GitHub 账户？（同步将回退为管理员 token）')) return
    try {
      await api.delete('/market/github/bind')
      setBindState({ bound: false, username: null })
      window.dispatchEvent(new Event('gh-bind-changed'))
      setToast('已解绑 GitHub 账户')
      setTimeout(() => setToast(null), 2500)
    } catch (e: any) {
      setMsg(`解绑失败: ${e?.message || e}`)
    }
  }

  // ── 刷新 GitHub 快照（管理员）──
  const openGithubSettings = async () => {
    try {
      await api.get('/market/settings')
      setSettingsDenied(false)
      setSettingsOpen(true)
    } catch {
      setSettingsDenied(true)
    }
  }

  const doRefreshGithub = async () => {
    setRefreshing(true)
    setMsg('')
    try {
      const r = await api.post<{ added: number; updated: number; removed: number }>('/market/github/refresh')
      setToast(`GitHub 刷新完成: +${r.added} 新增, ${r.updated} 更新, -${r.removed} 移除`)
      setTimeout(() => setToast(null), 3000)
      loadGithub()
      loadLocal() // 本地同步状态可能变化
    } catch (e: any) {
      setMsg(`刷新失败: ${e?.message || e}`)
    } finally {
      setRefreshing(false)
    }
  }

  // ── 发布：统一跳转独立发布页 /market/publish ──
  const openPublish = () => navigate('/market/publish')

  // ── 同步到 GitHub ──
  const doSync = async (item: MarketItem) => {
    // 未绑定 GitHub → 拦截并引导去绑定
    if (!bindState?.bound) {
      if (confirm('同步到 GitHub 需要先绑定你自己的 GitHub 账户。现在去绑定？')) {
        navigate('/me?bind=github')
      }
      return
    }
    setSyncingId(item.id)
    setMsg('')
    try {
      const r = await api.post<{ success: boolean; path: string }>(`/market/items/${item.id}/sync-github`)
      setToast(`已同步到 GitHub: ${r.path}`)
      setTimeout(() => setToast(null), 3000)
      setSyncingId(null)
      loadLocal()
      loadGithub()
    } catch (e: any) {
      const m = e?.message || String(e)
      // token 无效/过期 → 明确提示重新绑定
      if (/token|401|无效|过期/i.test(m)) {
        setMsg(`${m} —— 请到「我的」页重新绑定 GitHub`)
      } else {
        setMsg(`同步失败: ${m}`)
      }
      setSyncingId(null)
    }
  }

  // ── 导入（本地商品 / GitHub 远程资源）──
  const doImport = async (item: MarketItem | GithubItem) => {
    setImportingId(item.id)
    setMsg('')
    try {
      const r = await api.post<{ world_id: number; name: string; imported: number }>(
        item && 'is_local' in item ? `/market/github/import` : `/market/items/${item.id}/import`,
        item && 'is_local' in item ? { id: item.id } : undefined,
      )
      setToast(`已导入「${r.name}」（${r.imported} 个文件），正在打开…`)
      setTimeout(() => navigate(`/worlds/${r.world_id}/design`), 900)
    } catch (e: any) {
      setMsg(`导入失败: ${e?.message || e}`)
      setImportingId(null)
    }
  }

  // ── 编辑/下架 ──
  const openEdit = (item: MarketItem) => {
    setEditItem(item)
    setEditTitle(item.title)
    setEditDesc(item.description || '')
    setEditTags((item.tags || []).join(','))
  }

  const doEdit = async () => {
    if (!editItem) return
    setEditing(true)
    try {
      const updated = await api.put<MarketItem>(`/market/items/${editItem.id}`, {
        title: editTitle.trim(),
        description: editDesc.trim(),
        tags: editTags.split(/[,，]/).map(s => s.trim()).filter(Boolean),
      })
      setEditItem(null)
      setToast(`商品「${updated.title}」已更新`)
      setTimeout(() => setToast(null), 2500)
      loadLocal()
    } catch (e: any) {
      setMsg(`保存失败: ${e?.message || e}`)
    } finally {
      setEditing(false)
    }
  }

  const doUnpublish = async (item: MarketItem) => {
    if (!confirm(`下架「${item.title}」？（不影响已导入的世界）`)) return
    try {
      await api.delete(`/market/items/${item.id}`)
      setMsg('已下架')
      loadLocal()
    } catch (e: any) {
      setMsg(`下架失败: ${e?.message || e}`)
    }
  }

  // ── 同步状态徽标 ──
  const SyncBadge = ({ item }: { item: MarketItem }) => {
    if (item.sync_state === 'synced') {
      return <span className="inline-flex items-center gap-1 text-3xs px-1.5 py-0.5 rounded bg-mint-500/15 text-mint-400"><CheckCircle2 size={9} /> 已同步</span>
    }
    if (item.sync_state === 'stale') {
      return <span className="inline-flex items-center gap-1 text-3xs px-1.5 py-0.5 rounded bg-accent-500/15 text-accent-400"><RefreshCw size={9} /> 同步过（有改动）</span>
    }
    return null
  }

  // ── 详情弹窗：桌面居中卡片，手机全屏 ──
  const DetailModal = () => {
    if (!detail) return null
    const isLocal = detail.kind === 'local'
    const it = detail.item as any
    const isMine = isLocal && myId !== null && it.author_id === myId
    return (
      <div className="fixed inset-0 z-modal bg-black/60 flex items-end md:items-center justify-center" onClick={() => setDetail(null)}>
        <div
          className="w-full md:max-w-lg bg-surface border-t md:border border-border md:rounded-dialog rounded-t-2xl max-h-[85vh] flex flex-col"
          onClick={(e) => e.stopPropagation()}
        >
          {/* 头部 */}
          <div className="flex items-center gap-3 p-4 pb-2 shrink-0">
            <div className={`w-10 h-10 rounded-card flex items-center justify-center shrink-0 ${isLocal ? 'bg-primary-500/15 text-primary-400' : 'bg-[#24292F]/10 dark:bg-white/10 text-[#24292F] dark:text-white'}`}>
              {isLocal ? <Globe size={18} /> : <Github size={18} />}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-base font-semibold text-textPrimary truncate">{it.title}</span>
                {isLocal ? <SyncBadge item={it} /> : it.is_local
                  ? <span className="inline-flex items-center gap-1 text-3xs px-1.5 py-0.5 rounded bg-primary-500/15 text-primary-400 shrink-0"><Store size={9} /> 本地</span>
                  : <span className="inline-flex items-center gap-1 text-3xs px-1.5 py-0.5 rounded bg-elevated text-textMuted shrink-0"><Github size={9} /> 远程</span>}
              </div>
              <div className="text-3xs text-textMuted mt-0.5">
                <span className="inline-flex items-center gap-1"><User size={10} /> {it.author_name || (isLocal ? `#${it.author_id}` : 'GitHub')}</span>
                {isLocal && it.github_path && <span className="ml-2">{it.github_path}</span>}
                {!isLocal && it.slug && <span className="ml-2">worlds/{it.slug}</span>}
              </div>
            </div>
            <button onClick={() => setDetail(null)} className="p-1.5 text-textMuted hover:text-textPrimary transition-colors shrink-0"><X size={16} /></button>
          </div>

          {/* 正文 */}
          <div className="flex-1 overflow-y-auto px-4 pb-2 space-y-3">
            <p className="text-sm text-textSecondary leading-relaxed whitespace-pre-wrap">{it.description || '（无介绍）'}</p>
            {it.tags?.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {it.tags.map((tg: string) => (
                  <span key={tg} className="inline-flex items-center gap-0.5 text-3xs px-1.5 py-0.5 rounded bg-elevated text-textMuted"><Tag size={9} /> {tg}</span>
                ))}
              </div>
            )}
            {/* 信息网格 */}
            <div className="grid grid-cols-2 gap-2 text-2xs">
              <div className="rounded-control bg-elevated px-3 py-2">
                <div className="text-textMuted">更新时间</div>
                <div className="text-textPrimary mt-0.5">{isLocal ? fmtDate(it.updated_at) : fmtDate(it.updated_at)}</div>
              </div>
              {isLocal && (
                <div className="rounded-control bg-elevated px-3 py-2">
                  <div className="text-textMuted">云端更新</div>
                  <div className="text-textPrimary mt-0.5">{it.github_updated_at ? fmtDate(it.github_updated_at) : '—'}</div>
                </div>
              )}
              <div className="rounded-control bg-elevated px-3 py-2">
                <div className="text-textMuted">{isLocal ? '本地下载' : '云端下载'}</div>
                <div className="text-textPrimary mt-0.5">{isLocal ? `${it.downloads} 次导入` : `${it.downloads ?? 0} 次`}</div>
              </div>
              {isLocal && (
                <div className="rounded-control bg-elevated px-3 py-2">
                  <div className="text-textMuted">云端下载</div>
                  <div className="text-textPrimary mt-0.5">{it.github_downloads != null ? `${it.github_downloads} 次` : '—'}</div>
                </div>
              )}
              <div className="rounded-control bg-elevated px-3 py-2">
                <div className="text-textMuted">包大小</div>
                <div className="text-textPrimary mt-0.5">{fmtSize(it.package_size)}</div>
              </div>
              <div className="rounded-control bg-elevated px-3 py-2">
                <div className="text-textMuted">来源</div>
                <div className="text-textPrimary mt-0.5">{isLocal ? '本地发布' : 'GitHub 仓库'}</div>
              </div>
            </div>
          </div>

          {/* 操作 */}
          <div className="p-4 pt-2 shrink-0 flex items-center gap-2">
            {isLocal && isMine && (
              <button
                onClick={() => { doSync(it); setDetail(null) }}
                disabled={syncingId === it.id || it.sync_state === 'synced'}
                className="flex-1 inline-flex items-center justify-center gap-1 text-xs px-3 py-2 rounded-control bg-primary-500/15 text-primary-400 hover:bg-primary-500/25 transition-colors disabled:opacity-40"
              >
                <ArrowUpCircle size={12} /> {it.sync_state === 'synced' ? '已同步' : '同步到 GitHub'}
              </button>
            )}
            {isLocal && isMine && (
              <button
                onClick={() => { setEditItem(it); setDetail(null) }}
                className="flex-1 inline-flex items-center justify-center gap-1 text-xs px-3 py-2 rounded-control bg-elevated text-textSecondary hover:text-primary-400 transition-colors"
              ><Edit3 size={12} /> 编辑介绍</button>
            )}
            {isLocal && isMine && (
              <button
                onClick={() => { doUnpublish(it); setDetail(null) }}
                className="inline-flex items-center gap-1 text-xs px-3 py-2 rounded-control bg-elevated text-textMuted hover:text-rose-400 transition-colors"
              ><Trash2 size={12} /> 下架</button>
            )}
            {(!isLocal && !it.is_local) && (
              <button
                onClick={() => { doImport(it); setDetail(null) }}
                disabled={importingId === it.id}
                className="flex-1 inline-flex items-center justify-center gap-1 text-xs px-3 py-2 rounded-control bg-primary-500 text-white hover:bg-primary-600 transition-colors disabled:opacity-40"
              >
                <Download size={12} /> {importingId === it.id ? '导入中…' : '导入到我的世界'}
              </button>
            )}
            {isLocal && !isMine && (
              <button
                onClick={() => { doImport(it); setDetail(null) }}
                disabled={importingId === it.id}
                className="flex-1 inline-flex items-center justify-center gap-1 text-xs px-3 py-2 rounded-control bg-primary-500 text-white hover:bg-primary-600 transition-colors disabled:opacity-40"
              >
                <Download size={12} /> {importingId === it.id ? '导入中…' : '一键导入'}
              </button>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col bg-canvas">
      {/* 标题栏（统一底座） */}
      <PageHeader title="世界商城" subtitle="本地发布 / GitHub 资源共享" onBack={() => navigate(-1)}>
        <button
          onClick={openPublish}
          className="btn btn-xs btn-primary gap-1.5 shrink-0"
        >
          <Upload size={13} /> 发布世界
        </button>
      </PageHeader>

      {/* 板块切换 */}
      <div className="px-4 pt-2 shrink-0">
        <div className="flex items-center gap-1 bg-elevated rounded-control p-0.5 w-fit">
          <button
            onClick={() => setTab('local')}
            className={`inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-control transition-colors ${tab === 'local' ? 'bg-primary-500/15 text-primary-400' : 'text-textSecondary hover:text-textPrimary'}`}
          >
            <Database size={12} /> 本地 <span className="text-3xs opacity-70">({total})</span>
          </button>
          <button
            onClick={() => setTab('github')}
            className={`inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-control transition-colors ${tab === 'github' ? 'bg-primary-500/15 text-primary-400' : 'text-textSecondary hover:text-textPrimary'}`}
          >
            <Github size={12} /> GitHub <span className="text-3xs opacity-70">({ghItems.length})</span>
          </button>
        </div>
      </div>

      {/* GitHub 绑定条（统一到「我的」页绑定，状态跨页同步） */}
      <div className="px-4 pt-2 shrink-0">
        <div className="flex items-center gap-2 text-xs rounded-control bg-surface border border-border px-3 py-2">
          <Link2 size={12} className="text-textMuted shrink-0" />
          {bindState?.bound ? (
            <>
              <span className="text-textSecondary">GitHub：<span className="text-primary-400 font-semibold">@{bindState.username}</span>（同步以你的身份推送）</span>
              <button onClick={() => navigate('/me?bind=github')} className="ml-auto text-3xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors shrink-0">更换</button>
              <button onClick={doUnbind} className="inline-flex items-center gap-1 text-3xs px-2 py-1 rounded bg-elevated text-textMuted hover:text-rose-400 transition-colors shrink-0">
                <Unlink size={10} /> 解绑
              </button>
            </>
          ) : (
            <>
              <span className="text-textMuted">未绑定 GitHub（同步时用你的身份推送）</span>
              <button
                onClick={() => navigate('/me?bind=github')}
                className="ml-auto inline-flex items-center gap-1 text-3xs px-2 py-1 rounded bg-primary-500/15 text-primary-400 hover:bg-primary-500/25 transition-colors shrink-0"
              >
                <Link2 size={10} /> 去绑定 GitHub →
              </button>

            </>
          )}
        </div>
      </div>

      {/* 搜索 / 刷新 */}
      <div className="px-4 pt-2 pb-2 bg-canvas shrink-0">
        <div className="flex items-center gap-2 flex-wrap">
          {tab === 'local' ? (
            <>
              <div className="relative flex-1 min-w-[140px]">
                <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-textMuted" />
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') loadLocal() }}
                  placeholder="搜索世界标题 / 描述…"
                  className="w-full bg-elevated text-sm pl-8 pr-3 py-1.5 rounded-control border border-border outline-none focus:border-primary-500/50 text-textPrimary"
                />
              </div>
              <input
                value={tag}
                onChange={(e) => setTag(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') loadLocal() }}
                placeholder="标签，如 2d冒险"
                className="w-32 sm:w-36 bg-elevated text-sm px-3 py-1.5 rounded-control border border-border outline-none focus:border-primary-500/50 text-textPrimary shrink-0"
              />
              <button onClick={loadLocal} className="text-xs px-3 py-1.5 rounded-control bg-elevated hover:bg-border text-textSecondary transition-colors shrink-0">
                搜索
              </button>
            </>
          ) : (
            <>
              <div className="flex-1 text-3xs text-textMuted">
                {ghSyncedAt ? `快照时间: ${fmtDate(ghSyncedAt)}（点击刷新获取最新）` : 'GitHub 快照为空，点击刷新获取'}
              </div>
              <button
                onClick={doRefreshGithub}
                disabled={refreshing}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-control bg-elevated hover:bg-border text-textSecondary transition-colors shrink-0 disabled:opacity-40"
              >
                <RefreshCw size={11} className={refreshing ? 'animate-spin' : ''} /> {refreshing ? '刷新中…' : '刷新'}
              </button>
              <button
                onClick={openGithubSettings}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-control bg-elevated hover:bg-border text-textSecondary transition-colors shrink-0"
                title="实例 GitHub 配置（管理员）"
              >
                <Settings size={11} /> 实例配置
              </button>
              {settingsDenied && <span className="text-3xs text-textMuted shrink-0">仅管理员可配置实例 GitHub</span>}
            </>
          )}
        </div>
        {msg && <div className="text-xs text-accent-400 mt-2">{msg}</div>}
      </div>

      {/* 列表 */}
      <div className="flex-1 overflow-y-auto p-4">
        {tab === 'local' ? (
          loading ? (
            <div className="text-center text-textMuted text-sm py-16">加载中…</div>
          ) : items.length === 0 ? (
            <div className="text-center text-textMuted text-sm py-16 space-y-2">
              <Package size={32} className="mx-auto opacity-40" />
              <div>本地商城还没有世界。把做好的世界发布出来吧。</div>
              <button onClick={openPublish} className="text-xs px-3 py-1.5 rounded-control bg-elevated hover:bg-border text-primary-400 transition-colors">
                + 发布第一个世界
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {items.map((item) => (
                <div key={item.id} className="rounded-card bg-surface border border-border p-3 flex flex-col gap-2 hover:border-primary-500/40 transition-colors cursor-pointer" onClick={() => setDetail({ kind: 'local', item })}>
                  <div className="flex items-start gap-2">
                    <div className="w-9 h-9 rounded-control bg-primary-500/15 text-primary-400 flex items-center justify-center shrink-0">
                      <Globe size={16} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm font-semibold text-textPrimary truncate">{item.title}</span>
                        <SyncBadge item={item} />
                      </div>
                      <div className="text-3xs text-textMuted flex items-center gap-1 mt-0.5">
                        <User size={10} /> {item.author_name || `#${item.author_id}`}
                        <span className="mx-0.5">·</span>
                        <Clock size={10} /> 更新 {fmtDate(item.updated_at)}
                      </div>
                    </div>
                    {myId !== null && item.author_id === myId && (
                      <div className="flex items-center shrink-0">
                        <button
                          onClick={(e) => { e.stopPropagation(); openEdit(item) }}
                          className="p-1 text-textMuted hover:text-primary-400 transition-colors"
                          title="编辑介绍"
                        ><Edit3 size={13} /></button>
                        <button
                          onClick={(e) => { e.stopPropagation(); doUnpublish(item) }}
                          className="p-1 text-textMuted hover:text-rose-400 transition-colors"
                          title="下架"
                        ><Trash2 size={13} /></button>
                      </div>
                    )}
                  </div>
                  {item.description && (
                    <div className="text-xs text-textSecondary line-clamp-2 min-h-[2em]">{item.description}</div>
                  )}
                  {item.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {item.tags.map((tg) => (
                        <button
                          key={tg}
                          onClick={(e) => { e.stopPropagation(); setTag(tg); loadLocal() }}
                          className="inline-flex items-center gap-0.5 text-3xs px-1.5 py-0.5 rounded bg-elevated text-textMuted hover:text-primary-400 transition-colors"
                        ><Tag size={9} /> {tg}</button>
                      ))}
                    </div>
                  )}
                  <div className="flex items-center justify-between mt-auto pt-1 border-t border-border/50">
                    <span className="text-3xs text-textMuted space-x-2">
                      <span>本地 {item.downloads} 次导入</span>
                      {item.github_downloads != null && <span>云端 {item.github_downloads} 次</span>}
                      {item.sync_state === 'stale' && <span className="text-accent-400/80">云端 {fmtDate(item.github_updated_at)}</span>}
                    </span>
                    {myId !== null && item.author_id === myId && (
                      <button
                        onClick={(e) => { e.stopPropagation(); doSync(item) }}
                        disabled={syncingId === item.id || item.sync_state === 'synced'}
                        title={item.sync_state === 'synced' ? '已是最新' : '同步到 GitHub'}
                        className="inline-flex items-center gap-1 text-3xs px-2 py-1 rounded bg-elevated text-textMuted hover:text-primary-400 transition-colors disabled:opacity-40 shrink-0"
                      >
                        <ArrowUpCircle size={11} />
                        {syncingId === item.id ? '同步中…' : item.sync_state === 'synced' ? '已同步' : '同步'}
                      </button>
                    )}
                    {!(myId !== null && item.author_id === myId) && (
                      <button
                        onClick={(e) => { e.stopPropagation(); doImport(item) }}
                        disabled={importingId === item.id}
                        className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-control bg-primary-500/15 text-primary-400 hover:bg-primary-500/25 transition-colors disabled:opacity-40"
                      >
                        <Download size={11} />
                        {importingId === item.id ? '导入中…' : '一键导入'}
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )
        ) : (
          ghLoading ? (
            <div className="text-center text-textMuted text-sm py-16">加载中…</div>
          ) : ghItems.length === 0 ? (
            <div className="text-center text-textMuted text-sm py-16 space-y-2">
              <Github size={32} className="mx-auto opacity-40" />
              <div>GitHub 快照为空。点击右上「刷新」从仓库拉取世界列表。</div>
              <div className="text-3xs opacity-60">仓库: Coprexist/Copree-Community · 管理员可在后台配置仓库与自动获取</div>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {ghItems.map((item) => (
                <div key={item.id} className="rounded-card bg-surface border border-border p-3 flex flex-col gap-2 hover:border-primary-500/40 transition-colors cursor-pointer" onClick={() => setDetail({ kind: 'github', item })}>
                  <div className="flex items-start gap-2">
                    <div className="w-9 h-9 rounded-control bg-[#24292F]/10 dark:bg-white/10 text-[#24292F] dark:text-white flex items-center justify-center shrink-0">
                      <Github size={16} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm font-semibold text-textPrimary truncate">{item.title}</span>
                        {item.is_mine ? (
                          <span className="inline-flex items-center gap-1 text-3xs px-1.5 py-0.5 rounded bg-mint-500/15 text-mint-400 shrink-0" title="GitHub 数字 id 与你绑定的账户一致">我的</span>
                        ) : item.is_local ? (
                          <span className="inline-flex items-center gap-1 text-3xs px-1.5 py-0.5 rounded bg-primary-500/15 text-primary-400 shrink-0"><Store size={9} /> 本地</span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-3xs px-1.5 py-0.5 rounded bg-elevated text-textMuted shrink-0"><Github size={9} /> 远程</span>
                        )}
                      </div>
                      <div className="text-3xs text-textMuted flex items-center gap-1 mt-0.5">
                        <User size={10} /> {item.author_github ? `@${item.author_github}` : (item.author_name || 'GitHub')}
                        {item.signature_valid === true && <span className="text-mint-400/80">· 机器人已验证</span>}
                        {item.signature_valid === false && <span className="text-rose-400/80">· 签名无效</span>}
                        <span className="mx-0.5">·</span>
                        <Clock size={10} /> 更新 {fmtDate(item.updated_at)}
                      </div>
                    </div>
                  </div>
                  {item.description && (
                    <div className="text-xs text-textSecondary line-clamp-2 min-h-[2em]">{item.description}</div>
                  )}
                  {item.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {item.tags.map((tg) => (
                        <span key={tg} className="inline-flex items-center gap-0.5 text-3xs px-1.5 py-0.5 rounded bg-elevated text-textMuted"><Tag size={9} /> {tg}</span>
                      ))}
                    </div>
                  )}
                  <div className="flex items-center justify-between mt-auto pt-1 border-t border-border/50">
                    <span className="text-3xs text-textMuted">
                      {item.downloads != null ? `云端 ${item.downloads} 次下载` : ''}
                      {item.slug ? ` · ${item.slug}` : ''}
                    </span>
                    {!item.is_local && (
                      <button
                        onClick={(e) => { e.stopPropagation(); doImport(item) }}
                        disabled={importingId === item.id}
                        className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-control bg-primary-500/15 text-primary-400 hover:bg-primary-500/25 transition-colors disabled:opacity-40"
                      >
                        <Download size={11} />
                        {importingId === item.id ? '导入中…' : '导入到我的世界'}
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )
        )}
        {!loading && tab === 'local' && items.length > 0 && (
          <div className="text-center text-3xs text-textMuted mt-3">共 {total} 个世界</div>
        )}
      </div>

      {/* 居中 toast */}
      {toast && (
        <div className="fixed top-6 left-1/2 -translate-x-1/2 z-toast flex items-center gap-2 px-4 py-2.5 rounded-card bg-surface border border-border shadow-2xl text-sm text-mint-400">
          <CheckCircle2 size={16} className="shrink-0" />
          <span className="whitespace-nowrap">{toast}</span>
        </div>
      )}

      {/* 卡片详情（桌面弹窗 / 手机全屏） */}
      <DetailModal />

      {/* 编辑弹窗 */}
      {editItem && (
        <Dialog onClose={() =>  setEditItem(null)} className="flex items-center justify-center p-4">
          <div className="w-full max-w-md rounded-dialog bg-surface border border-border p-4 space-y-3" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-textPrimary">编辑商品介绍</span>
              <button onClick={() => setEditItem(null)} className="p-1 text-textMuted hover:text-textPrimary"><X size={15} /></button>
            </div>
            <div>
              <div className="text-3xs text-textMuted mb-1">标题</div>
              <input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                className="w-full bg-elevated text-sm p-2 rounded-control border border-border outline-none focus:border-primary-500/50 text-textPrimary"
              />
            </div>
            <div>
              <div className="text-3xs text-textMuted mb-1">描述</div>
              <textarea
                value={editDesc}
                onChange={(e) => setEditDesc(e.target.value)}
                rows={3}
                className="w-full bg-elevated text-sm p-2 rounded-control border border-border outline-none resize-none focus:border-primary-500/50 text-textPrimary"
              />
            </div>
            <div>
              <div className="text-3xs text-textMuted mb-1">标签（逗号分隔）</div>
              <input
                value={editTags}
                onChange={(e) => setEditTags(e.target.value)}
                className="w-full bg-elevated text-sm p-2 rounded-control border border-border outline-none focus:border-primary-500/50 text-textPrimary"
              />
            </div>
            <button
              onClick={doEdit}
              disabled={editing}
              className="btn btn-sm btn-primary w-full"
            >
              {editing ? '保存中…' : '保存'}
            </button>
          </div>
        </Dialog>
      )}


      {/* 实例 GitHub 配置（管理员） */}
      {settingsOpen && (
        <Dialog onClose={() =>  setSettingsOpen(false)} className="flex items-center justify-center p-4">
          <div className="w-full max-w-lg bg-surface border border-border rounded-dialog max-h-[85vh] overflow-y-auto shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="sticky top-0 bg-surface border-b border-border px-4 py-3 flex items-center justify-between">
              <span className="text-sm font-semibold text-textPrimary">实例 GitHub 配置</span>
              <button onClick={() => setSettingsOpen(false)} className="p-1 text-textMuted hover:text-textPrimary transition-colors"><X size={16} /></button>
            </div>
            <div className="p-4">
              <MarketGithubTab />
            </div>
          </div>
        </Dialog>
      )}
    </div>
  )
}
