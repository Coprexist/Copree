/**
 * 左栏「会话」页签内容：会话列表（当前高亮）+ 新对话入口 + 每行 ⋯ 溢出菜单
 *
 * 效率约定（左栏会被对话流式渲染带着重渲，这里不能当放大器）：
 *  - 列表与每一行都 memo，行 props 只有字符串/布尔/稳定回调——父级不必为它现造对象
 *  - 显示名与相对时间在列表层算成字符串，行内不做重复的 i18n / 时间格式化
 *  - key 用会话 id（稳定），不用下标
 *
 * 只做展示与回调：会话状态与动作仍归 WorldChatPanel（useWorldChat 全页只有那一个实例），
 * 父组件从 onSessionsChange 收数据、通过 ref 调动作——不重复拉接口，也不复制一份状态。
 */
import { memo, useEffect, useRef, useState } from 'react'
import { Plus, MoreHorizontal, Pin } from 'lucide-react'
import { MenuPanel, MenuItem } from '../ui'
import { useLang, useT } from '../../i18n/I18nContext'
import { formatRelativeTime } from '../../utils/time'

export interface WorldSessionInfo {
  id: string
  title?: string
  last_active_at?: string
  pinned?: boolean
}

interface SessionRowProps {
  id: string
  /** 原始名字：改名与下载文件名要以它为准 */
  title: string
  /** 列表里显示的名字（列表层已算好） */
  label: string
  /** 已经格式化好的相对时间；空串 = 不显示 */
  timeLabel: string
  active: boolean
  pinned: boolean
  onSelect: (id: string) => void
  onRename: (id: string, title: string) => void
  onTogglePin: (id: string) => void
  onExport: (id: string, fmt: 'md' | 'json', title: string) => void
}

/** 单行：props 全是原始值/稳定回调，memo 才真的挡得住重渲 */
const SessionRow = memo(function SessionRow({
  id, title, label, timeLabel, active, pinned, onSelect, onRename, onTogglePin, onExport,
}: SessionRowProps) {
  const t = useT()
  // 菜单开关是行内状态：点一行 ⋯ 只重渲这一行
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // 点外面收起用 document 监听，而不是别处那套 fixed 全屏透明遮罩：
  // 左栏做了进场动效（transition-[opacity,transform] 在 enter 后仍是 transform: translate(0)），
  // 祖先一旦有 transform 就会成为 fixed 的包含块，遮罩会缩成左栏那么大，点外面关不掉
  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menuOpen])

  return (
    <div
      className={`group flex items-center rounded-control transition-colors ${active ? 'bg-primary-500/15 text-primary-300' : 'text-textSecondary hover:bg-elevated'}`}
    >
      <button
        onClick={() => onSelect(id)}
        className="flex items-center gap-1.5 min-w-0 flex-1 px-2 py-1.5 text-xs text-left"
        title={`${label}\n${id}`}
      >
        <span className="truncate min-w-0 flex-1">{label}</span>
        {pinned && <Pin size={10} className="shrink-0 text-accent-400 fill-current" />}
        {timeLabel && <span className="shrink-0 text-3xs text-textMuted">{timeLabel}</span>}
      </button>
      <div className="relative shrink-0" ref={menuRef}>
        <button
          onClick={() => setMenuOpen((v) => !v)}
          // 悬停才显形，触屏没有 hover 就常显——否则这行操作在手机上永远点不到
          className={`icon-btn-sm text-textMuted ${menuOpen ? '' : 'opacity-0 group-hover:opacity-100 focus:opacity-100 [@media(hover:none)]:opacity-100'}`}
          title={t('tool:world.session.more')}
          aria-label={t('tool:world.session.more')}
        >
          <MoreHorizontal size={14} />
        </button>
        {menuOpen && (
          <>
            <MenuPanel className="absolute right-0 top-full mt-0.5 w-44 py-1 z-toast">
              <MenuItem onClick={() => { setMenuOpen(false); onRename(id, title) }}>{t('tool:world.session.rename')}</MenuItem>
              {active && (
                <MenuItem onClick={() => { setMenuOpen(false); onTogglePin(id) }}>
                  {pinned ? t('tool:world.session.unpin') : t('tool:world.session.pin')}
                </MenuItem>
              )}
              <MenuItem onClick={() => { setMenuOpen(false); onExport(id, 'md', title) }}>{t('tool:world.session.exportMd')}</MenuItem>
              <MenuItem onClick={() => { setMenuOpen(false); onExport(id, 'json', title) }}>{t('tool:world.session.exportJson')}</MenuItem>
            </MenuPanel>
          </>
        )}
      </div>
    </div>
  )
})

interface WorldSessionListProps {
  sessions: WorldSessionInfo[]
  /** 当前会话 id：整行高亮，不写"当前"两个字 */
  current: string
  onSelect: (id: string) => void
  onNew: () => void
  onRename: (id: string, title: string) => void
  /** 只能收藏当前会话（后端的 pin 是按当前会话落库的） */
  onTogglePin: (id: string) => void
  onExport: (id: string, fmt: 'md' | 'json', title: string) => void
}

const WorldSessionList = memo(function WorldSessionList({
  sessions, current, onSelect, onNew, onRename, onTogglePin, onExport,
}: WorldSessionListProps) {
  const t = useT()
  const lang = useLang()
  return (
    <div className="p-2 space-y-0.5">
      <button
        onClick={onNew}
        className="w-full inline-flex items-center gap-1.5 px-2 py-1.5 rounded-control text-xs text-primary-400 hover:bg-elevated transition-colors"
        title={t('tool:world.session.new')}
      >
        <Plus size={13} />
        {t('tool:world.session.new')}
      </button>
      {sessions.map((s) => (
        <SessionRow
          key={s.id}
          id={s.id}
          title={s.title || ''}
          label={s.title || (s.id === 'default' ? t('tool:world.session.default') : s.id)}
          timeLabel={s.last_active_at ? formatRelativeTime(s.last_active_at, lang) : ''}
          active={s.id === current}
          pinned={!!s.pinned}
          onSelect={onSelect}
          onRename={onRename}
          onTogglePin={onTogglePin}
          onExport={onExport}
        />
      ))}
      {sessions.length === 0 && <div className="px-2 py-3 text-2xs text-textMuted">{t('tool:world.session.empty')}</div>}
      {/* 末尾留白：末行的 ⋯ 菜单往下展开需要空间，否则会被滚动容器裁掉 */}
      <div className="h-24" aria-hidden />
    </div>
  )
})

export default WorldSessionList
