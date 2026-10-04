import { useState, useEffect, useMemo } from 'react'
import { Search, Send, X, FileIcon, Loader2, Check, Users, MessageSquare } from 'lucide-react'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { CHAT_REFRESH_EVENT } from '../constants'
import { Dialog } from './ui'

interface ForwardTarget {
  type: 'group' | 'dm'
  id: number | string   // group.id 或 dm.session_id
  name: string
  avatar_url?: string
}

interface ForwardFileModalProps {
  file: {
    file_id: number
    name: string
    size: number
    mime_type: string
  }
  onClose: () => void
}

export default function ForwardFileModal({ file, onClose }: ForwardFileModalProps) {
  const t = useT()

  // 可选目标
  const [groups, setGroups] = useState<ForwardTarget[]>([])
  const [dmContacts, setDmContacts] = useState<ForwardTarget[]>([])
  const [loading, setLoading] = useState(true)

  // 选择 & 搜索
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [search, setSearch] = useState('')

  // 发送状态
  const [sending, setSending] = useState(false)
  const [done, setDone] = useState(false)

  useEffect(() => {
    Promise.all([
      api.get<any[]>('/groups').then((list) =>
        (Array.isArray(list) ? list : []).map((g) => ({
          type: 'group' as const, id: g.id, name: g.name,
        })),
      ),
      api.get<any[]>('/dm/sessions').then((list) =>
        (Array.isArray(list) ? list : []).map((s) => ({
          type: 'dm' as const,
          id: s.session_id,
          name: s.partner?.name || `用户${s.partner?.id || ''}`,
          avatar_url: s.partner?.avatar_url,
        })),
      ),
    ])
      .then(([g, d]) => {
        setGroups(g)
        setDmContacts(d)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  // 搜索过滤
  const filteredGroups = useMemo(
    () => (search ? groups.filter((g) => g.name.toLowerCase().includes(search.toLowerCase())) : groups),
    [groups, search],
  )
  const filteredDms = useMemo(
    () => (search ? dmContacts.filter((d) => d.name.toLowerCase().includes(search.toLowerCase())) : dmContacts),
    [dmContacts, search],
  )

  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const handleSend = async () => {
    if (selected.size === 0) return
    setSending(true)

    const attachment = {
      file_id: file.file_id,
      name: file.name,
      size: file.size,
      mime_type: file.mime_type,
    }

    const targets = [...groups, ...dmContacts]
    const results = await Promise.allSettled(
      targets
        .filter((t) => selected.has(`${t.type}:${t.id}`))
        .map((t) => {
          const body = { content: '', attachments: [attachment] }
          return t.type === 'group'
            ? api.post(`/gm/${t.id}/messages`, body)
            : api.post(`/dm/${t.id}/messages`, body)
        }),
    )

    const succeeded = results.filter((r) => r.status === 'fulfilled').length
    const failed = results.filter((r) => r.status === 'rejected').length

    setSending(false)
    setDone(true)

    // 通知聊天界面刷新
    window.dispatchEvent(new CustomEvent(CHAT_REFRESH_EVENT))

    // 短暂显示结果后关闭
    if (failed === 0) {
      setTimeout(onClose, 800)
    }
    // 有失败则留久一点让用户看到
  }

  const targetKey = (t: ForwardTarget) => `${t.type}:${t.id}`

  const renderList = (items: ForwardTarget[], icon: React.ReactNode, label: string) => {
    if (items.length === 0) return null
    return (
      <div className="mb-3">
        <div className="flex items-center gap-1.5 text-2xs font-medium text-textMuted mb-1.5 px-1">
          {icon}
          {label}
        </div>
        <div className="space-y-0.5">
          {items.map((item) => {
            const key = targetKey(item)
            const checked = selected.has(key)
            return (
              <button
                key={key}
                onClick={() => toggle(key)}
                className={`w-full flex items-center gap-2.5 px-2 py-2 rounded-control text-left transition-colors ${
                  checked
                    ? 'bg-primary-500/10 text-primary-600 dark:text-primary-300'
                    : 'hover:bg-elevated text-textPrimary'
                }`}
              >
                <div
                  className={`w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 transition-colors ${
                    checked
                      ? 'bg-primary-500 border-primary-500'
                      : 'border-textMuted/30'
                  }`}
                >
                  {checked && <Check size={12} className="text-white" />}
                </div>
                {/* 头像占位 */}
                <div className="w-6 h-6 rounded-full bg-primary-500/10 flex items-center justify-center shrink-0">
                  {item.avatar_url ? (
                    <img src={item.avatar_url} className="w-6 h-6 rounded-full object-cover" alt="" loading="lazy" />
                  ) : (
                    <span className="text-3xs font-medium text-primary-400">
                      {item.name.slice(0, 1)}
                    </span>
                  )}
                </div>
                <span className="text-sm truncate flex-1">{item.name}</span>
              </button>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <Dialog onClose={onClose} className="flex items-center justify-center p-4">
      <div
        className="bg-surface border border-border rounded-dialog shadow-2xl shadow-black/30 w-full max-w-sm max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-center gap-3 px-4 h-12 border-b border-border shrink-0">
          <FileIcon size={16} className="text-textMuted shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-textPrimary truncate">{file.name}</p>
          </div>
          <button
            onClick={onClose}
            className="icon-btn-sm text-textMuted"
          >
            <X size={16} />
          </button>
        </div>

        {/* 搜索框 */}
        <div className="px-3 py-2 shrink-0">
          <div className="flex items-center gap-2 px-3 py-2 rounded-card bg-canvas border border-border">
            <Search size={14} className="text-textMuted shrink-0" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('forward:searchPlaceholder')}
              className="flex-1 bg-transparent text-sm text-textPrimary placeholder:text-textMuted focus:outline-none"
              autoFocus
            />
          </div>
        </div>

        {/* 目标列表 */}
        <div className="flex-1 overflow-y-auto px-3 py-1 min-h-0">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 size={20} className="animate-spin text-textMuted" />
            </div>
          ) : groups.length === 0 && dmContacts.length === 0 ? (
            <p className="text-center text-sm text-textMuted py-12">{t('forward:noTargets')}</p>
          ) : (
            <>
              {renderList(filteredGroups, <Users size={12} />, t('forward:groupsLabel'))}
              {renderList(filteredDms, <MessageSquare size={12} />, t('forward:dmLabel'))}
            </>
          )}
        </div>

        {/* 底部操作栏 */}
        <div className="px-3 py-2.5 border-t border-border shrink-0 flex items-center gap-2">
          <span className="text-xs text-textMuted flex-1">
            {done
              ? t('forward:sent')
              : selected.size > 0
              ? t('forward:selectedCount').replace('{n}', String(selected.size))
              : t('forward:selectHint')}
          </span>
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded-control text-xs text-textSecondary hover:bg-elevated transition-colors"
          >
            {done ? t('common:close') : t('common:cancel')}
          </button>
          <button
            onClick={handleSend}
            disabled={selected.size === 0 || sending || done}
            className="btn btn-xs btn-primary gap-1.5"
          >
            {sending ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Send size={14} />
            )}
            {t('forward:send')}
          </button>
        </div>
      </div>
    </Dialog>
  )
}
