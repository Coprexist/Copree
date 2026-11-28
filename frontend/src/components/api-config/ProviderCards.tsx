/**
 * 厂商卡片列表 —— 一张卡一家厂商，默认折叠，点开看它名下的配置档。
 *
 * 同一副骨架两处用，差别只有展开后那些行的行为：
 *   mode="manage"（设置页）：行只提供 ⋯（编辑 / 设为默认 / 删除）
 *   mode="pick"（AI 的 API 页签）：整行可点＝选中这套配置，右侧保留 ⋯
 * 抽成一个组件而不是写两遍：卡片头、折叠、⋯ 菜单这些一处改就行了，
 * 两处各写一份迟早漂成两种长相。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { Check, ChevronDown, MoreHorizontal, Plus } from 'lucide-react'
import { api, errorText } from '../../api/client'
import { useT } from '../../i18n/I18nContext'
import { AutoHeight, Badge, Button, IconButton, MenuItem, MenuPanel, Modal } from '../ui'
import KeyMark from './KeyMark'
import type { ApiKind, ApiProvider, ApiProfile, BuiltinPreset } from './types'

interface MenuAction {
  label: string
  onSelect: () => void
  danger?: boolean
}

// 菜单离视口边缘的最小留白，以及触发器与菜单之间的间距
const MENU_MARGIN = 8
const MENU_GAP = 4

/** 行内菜单：点外面 / ESC 收起，浮层 portal 到 body 并按视口自动翻转 */
function RowMenu({
  label,
  items,
  trigger = 'icon',
  direction = 'down',
}: {
  label: string
  items: MenuAction[]
  /** icon = ⋯ 图标按钮（卡片头用）；text = 文字按钮（底部那排用） */
  trigger?: 'icon' | 'text'
  /** 底部那排的菜单要向上展开，否则会被视口切掉 */
  direction?: 'down' | 'up'
}) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState({ left: 0, top: 0, ready: false })
  const rootRef = useRef<HTMLDivElement | null>(null)
  const panelRef = useRef<HTMLDivElement | null>(null)

  // 点外面 / ESC 收起。portal 之后浮层不在触发器子树里，两棵都要看
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node
      if (rootRef.current?.contains(target) || panelRef.current?.contains(target)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // 定位：先按触发器算，量完实际尺寸再夹进视口、上下自动翻转。
  // 为什么必须量：菜单宽度随文案变，高度随条目数变，任何写死的估算都会在
  // "条目多/窄屏"时出界——而一出去就被视口切掉一半。
  const place = useCallback(() => {
    const anchor = rootRef.current?.getBoundingClientRect()
    if (!anchor) return
    const panel = panelRef.current
    const width = panel?.offsetWidth || 160
    const height = panel?.offsetHeight || 0
    let left = trigger === 'text' ? anchor.left : anchor.right - width
    left = Math.min(Math.max(MENU_MARGIN, left), window.innerWidth - width - MENU_MARGIN)
    let top = direction === 'up' ? anchor.top - height - MENU_GAP : anchor.bottom + MENU_GAP
    if (top < MENU_MARGIN) top = anchor.bottom + MENU_GAP                     // 上方不够翻到下方
    if (top + height > window.innerHeight - MENU_MARGIN) {                    // 下方不够翻回上方
      top = Math.max(MENU_MARGIN, anchor.top - height - MENU_GAP)
    }
    setPos({ left, top, ready: true })
  }, [trigger, direction])

  useLayoutEffect(() => {
    if (!open) { setPos({ left: 0, top: 0, ready: false }); return }
    place()
    // 滚动与缩放都要重算：fixed 不跟随祖先滚动，卡片会从菜单底下走开
    const onMove = () => place()
    window.addEventListener('resize', onMove)
    window.addEventListener('scroll', onMove, true)
    return () => {
      window.removeEventListener('resize', onMove)
      window.removeEventListener('scroll', onMove, true)
    }
  }, [open, place])

  return (
    <>
      <div className="relative shrink-0" ref={rootRef}>
        {trigger === 'icon' ? (
          <IconButton icon={<MoreHorizontal size={14} />} label={label} size="sm"
            onClick={() => setOpen((v) => !v)} />
        ) : (
          <Button variant="ghost" size="sm" onClick={() => setOpen((v) => !v)}>
            {label}
          </Button>
        )}
      </div>
      {/* 挂到 body：菜单与卡片同为 surface 色，叠在卡片里只剩 1px 边框的差别，
          阴影又被卡片自身的阴影淹没——看上去像没弹出来。portal 出去背景变 canvas，
          对比度才出得来；顺带摆脱祖先 overflow / 层叠上下文。 */}
      {open && createPortal(
        <div
          ref={panelRef}
          style={{
            position: 'fixed',
            left: pos.left,
            top: pos.top,
            width: 'max-content',
            visibility: pos.ready ? 'visible' : 'hidden',
          }}
          className="z-modal"
        >
          <MenuPanel role="menu" aria-label={label} className="min-w-[10rem] py-1">
            {items.map((it) => (
              <MenuItem key={it.label}
                className={it.danger ? 'text-rose-400 hover:text-rose-300' : ''}
                onClick={() => { setOpen(false); it.onSelect() }}>
                {it.label}
              </MenuItem>
            ))}
          </MenuPanel>
        </div>,
        document.body,
      )}
    </>
  )
}

/**
 * 「几个 AI 绑着这条配置」：点浮标开一个标准弹窗列名字，点了跳那个 AI 的详情。
 *
 * 用 Modal 而不是行内浮层：这是「查看一份清单」，不是选一项就走的菜单；Modal 在手机上
 * 会铺满整屏（mobileFullscreen），长名单不会挤在卡片边上——行内浮层还会被 AutoHeight 的
 * overflow-hidden 裁掉。
 */
function UsageMenu({ profile }: { profile: ApiProfile }) {
  const t = useT()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)

  if (!profile.used_by_count) return null
  const users = profile.used_by || []
  return (
    <>
      <button type="button" aria-label={t('apiConfig:usageCount')}
        onClick={() => setOpen(true)}>
        <Badge tone="muted">{profile.used_by_count}</Badge>
      </button>
      <Modal open={open} onClose={() => setOpen(false)} size="sm"
        title={t('apiConfig:usageCount')} mobileFullscreen>
        {users.length === 0 ? (
          <p className="text-sm text-textMuted">{t('apiConfig:usageEmpty')}</p>
        ) : (
          <div className="space-y-1">
            {users.map((a) => (
              <MenuItem key={a.id}
                onClick={() => { setOpen(false); navigate('/agents/' + a.id) }}>
                {a.name}
              </MenuItem>
            ))}
          </div>
        )}
      </Modal>
    </>
  )
}

export default function ProviderCards({
  kind,
  providers,
  mode = 'manage',
  selectedId = null,
  liveProfileId = null,
  onSelect,
  presets = [],
  onPickPreset,
  onCustomProvider,
  onFillProviderKey,
  onEditProvider,
  onNewProfile,
  onEditProfile,
  onChanged,
}: {
  kind: ApiKind
  providers: ApiProvider[]
  /** pick = 这些卡是给"这个 AI 用哪套"挑的；manage = 设置页管理 */
  mode?: 'manage' | 'pick'
  selectedId?: number | null
  /** 解析实际命中的那条（只有 pick 模式有）：用来区分「已选」与「生效中」 */
  liveProfileId?: number | null
  onSelect?: (profileId: number) => void
  /** 内置厂商清单：底部铺成胶囊，点一下就接入 */
  presets?: BuiltinPreset[]
  onPickPreset?: (presetKey: string) => void
  onCustomProvider?: () => void
  /** 打开"填厂商默认 Key"的厂商选择弹窗 */
  onFillProviderKey?: () => void
  onEditProvider: (id: number, focusKey?: boolean) => void
  /** 不传 providerId = 从底部入口新建（厂商在编辑器里选） */
  onNewProfile: (providerId?: number) => void
  onEditProfile: (id: number) => void
  onChanged: () => void
}) {
  const t = useT()
  const storeKey = 'api-config-expanded-' + kind + '-' + mode
  const [expanded, setExpanded] = useState<number[]>(() => {
    try {
      const raw = localStorage.getItem(storeKey)
      return raw ? (JSON.parse(raw) as number[]) : []
    } catch {
      return []
    }
  })
  const [error, setError] = useState('')

  // 挑模式：默认展开"当前选中的那条所属的厂商"，否则用户看不到自己选了什么
  useEffect(() => {
    if (mode !== 'pick' || !selectedId) return
    const owner = providers.find((p) => p.profiles.some((x) => x.id === selectedId))
    if (owner && !expanded.includes(owner.id)) setExpanded((prev) => [...prev, owner.id])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, selectedId, providers])

  const toggle = (id: number) => {
    setExpanded((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
      try { localStorage.setItem(storeKey, JSON.stringify(next)) } catch { /* 忽略 */ }
      return next
    })
  }

  const act = async (fn: () => Promise<unknown>) => {
    setError('')
    try {
      await fn()
      onChanged()
    } catch (e) {
      setError(errorText(e, t('apiConfig:loadFailed')))
    }
  }

  const removeProvider = (provider: ApiProvider) => {
    if (!window.confirm(t('apiConfig:deleteProviderConfirm', {
      name: provider.name, n: provider.profiles.length,
    }))) return
    act(() => api.delete('/user/api-config/providers/' + provider.id + '?force=true'))
  }

  const restoreProvider = (provider: ApiProvider) => {
    if (!window.confirm(t('apiConfig:restoreConfirm'))) return
    act(() => api.post('/user/api-config/providers/' + provider.id + '/restore'))
  }

  const removeProfile = (name: string, id: number) => {
    if (!window.confirm(t('apiConfig:deleteProfileConfirm', { name }))) return
    act(() => api.delete('/user/api-config/profiles/' + id))
  }

  // 底部：把「还能接入哪些内置厂商」直接铺出来。
  // 以前这一步藏在一个"接入厂商"弹窗里，用户根本找不到厂商清单。
  const takenKeys = new Set(
    providers.map((p) => p.preset_key).filter(Boolean) as string[],
  )
  const available = presets.filter((p) => !takenKeys.has(p.key))

  const footer = (
    <div className="space-y-1.5 pt-1">
      {available.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-3xs text-textMuted mr-1">{t('apiConfig:builtinProviders')}</span>
          {available.map((p) => (
            // 有边框才看得出是"可点的选项"；ghost 在这里就是一堆裸文字
            <Button key={p.key} variant="outline" size="sm"
              onClick={() => onPickPreset?.(p.key)}>
              {p.label}
            </Button>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1">
        <Button variant="secondary" size="sm" onClick={() => onNewProfile()}>
          {t('apiConfig:newProfile')}
        </Button>
        <Button variant="ghost" size="sm" onClick={onCustomProvider}>
          {t('apiConfig:customProvider')}
        </Button>
        {/* 主动去填某家的默认 Key：打开选择弹窗（我的厂商 + 内置未接入的都在里面） */}
        <Button variant="ghost" size="sm" onClick={onFillProviderKey}>
          {t('apiConfig:fillProviderKeyBtn')}
        </Button>
      </div>
    </div>
  )

  if (providers.length === 0) {
    return (
      <div className="space-y-2">
        <div className="card p-6 text-center space-y-2">
          <p className="text-sm font-medium text-textPrimary">{t('apiConfig:emptyTitle')}</p>
          <p className="text-3xs text-textMuted">{t('apiConfig:emptyDesc')}</p>
        </div>
        {footer}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {error && <p className="text-xs text-rose-400">{error}</p>}
      {providers.map((provider) => {
        const open = expanded.includes(provider.id)
        const holdsSelected = provider.profiles.some((x) => x.id === selectedId)
        const providerMenu: MenuAction[] = [
          { label: t('apiConfig:editProvider'), onSelect: () => onEditProvider(provider.id) },
        ]
        if (provider.preset_key) {
          providerMenu.push({ label: t('apiConfig:restore'), onSelect: () => restoreProvider(provider) })
        }
        providerMenu.push({
          label: t('apiConfig:deleteProvider'),
          danger: true,
          onSelect: () => removeProvider(provider),
        })

        return (
          <div key={provider.id} className="card">
            <div className="flex items-center gap-2 pr-3">
              {/* 展开箭头在左、内容紧跟其后：与「默认配置」页的场景卡片同一副头部——
                  两页都是「一行一个东西、点开看明细」，箭头位置不该一左一右。 */}
              <Button
                variant="ghost"
                size="sm"
                className="flex-1 min-w-0 !justify-start !gap-3 !h-auto !pl-3 !pr-0 !py-2.5"
                onClick={() => toggle(provider.id)}
                aria-expanded={open}
              >
                <ChevronDown
                  size={14}
                  className={'shrink-0 text-textMuted transition-transform ' + (open ? 'rotate-180' : '')}
                />
                {/* 钥匙挂在厂商名右上角：绝对定位、不参与布局，长名字也不会把它挤走。
                    图标、说明文案与打开方式（悬停 + 点击）都在 KeyMark 里，别在这里重写。 */}
                <span className="relative shrink-0 max-w-full">
                  {/* 厂商是卡片的标题级：用品牌主色把这一层和下面的配置档行（中性色）分开，
                      不是强调按钮，所以只要一点点颜色，别加粗也别改字号。 */}
                  <span className="text-sm font-medium text-primary-400 truncate block pr-2.5">
                    {provider.name}
                  </span>
                  {provider.has_key && (
                    <KeyMark last4={provider.key_last4} placement="corner" />
                  )}
                </span>
                <span className="text-3xs text-textMuted truncate">
                  {provider.has_key ? '' : t('apiConfig:noKeyYet') + ' · '}
                  {t('apiConfig:profileCount', { n: provider.profiles.length })}
                </span>
                {mode === 'pick' && holdsSelected && !open && (
                  <Badge tone="primary">{t('apiConfig:inUse')}</Badge>
                )}
              </Button>
              <IconButton label={t('apiConfig:newProfile')} size="sm"
                onClick={() => onNewProfile(provider.id)} icon={<Plus size={14} />} />
              <RowMenu label={t('apiConfig:editProvider')} items={providerMenu} />
            </div>

            {/* 展开/收起走 AutoHeight：内容一直在，高度收放才有动画（条件渲染只能硬切） */}
            <AutoHeight open={open} fadeKey={provider.id}>
              <div className="border-t border-border/50 px-2 py-1.5 space-y-1">
                {/* 这家没填默认 Key → 就地给个入口，点一下直接打开这家编辑器并聚焦 Key 框。
                    不塞进卡片头：那一整行本身是"展开/收起"按钮，里面再放一个按钮会嵌套。 */}
                {!provider.has_key && (
                  <button type="button"
                    className="text-3xs text-accent-400 hover:underline"
                    onClick={() => onEditProvider(provider.id, true)}>
                    {t('apiConfig:fillProviderKey')}
                  </button>
                )}
                {provider.profiles.length === 0 && (
                  // ui-standard-ignore: 空态引导的文字链接，不是按钮形态
                  <button type="button" className="text-3xs text-primary-500 hover:underline px-1"
                    onClick={() => onNewProfile(provider.id)}>
                    {t('apiConfig:newProfile')}
                  </button>
                )}
                {provider.profiles.map((profile) => {
                  const picked = mode === 'pick' && profile.id === selectedId
                  const body = (
                    <>
                      <span className="text-sm text-textPrimary truncate">{profile.name}</span>
                      {/* 已选是声明，生效中是事实：只有已经选中的那条才会出现这两种 */}
                      {picked && liveProfileId !== null && liveProfileId !== undefined && (
                        profile.id === liveProfileId
                          ? <Badge tone="mint">{t('apiConfig:badgeLive')}</Badge>
                          : <Badge tone="accent">{t('apiConfig:badgeNotLive')}</Badge>
                      )}
                      <span className="text-3xs text-textMuted truncate">
                        {profile.primary_model || provider.default_primary || ''}
                        {profile.has_key ? ' · ' + t('apiConfig:keyEndsWith', { last4: profile.key_last4 }) : ''}
                      </span>
                      <UsageMenu profile={profile} />
                      {picked && <Check size={14} className="ml-auto shrink-0 text-primary-500" />}
                    </>
                  )
                  return (
                    <div key={profile.id}
                      className={'flex items-center gap-2 rounded-control ' +
                        (picked ? 'bg-primary-500/10' : '')}>
                      {mode === 'pick' ? (
                        // ui-standard-ignore: 整行可点的选中行（配套右侧 ⋯ 菜单），不是按钮形态
                        <button type="button"
                          className="flex flex-1 items-center gap-2 min-w-0 px-2 py-1.5 text-left rounded-control hover:bg-elevated transition-colors"
                          onClick={() => onSelect?.(profile.id)}>
                          {body}
                        </button>
                      ) : (
                        <div className="flex flex-1 items-center gap-2 min-w-0 px-2 py-1.5">{body}</div>
                      )}
                      <RowMenu label={t('apiConfig:editProfile')} items={[
                        { label: t('apiConfig:editProfile'), onSelect: () => onEditProfile(profile.id) },
                        {
                          label: t('apiConfig:deleteProfile'),
                          danger: true,
                          onSelect: () => removeProfile(profile.name, profile.id),
                        },
                      ]} />
                    </div>
                  )
                })}
              </div>
            </AutoHeight>
          </div>
        )
      })}
      {footer}
    </div>
  )
}
