import { useState, useCallback, useEffect, useRef, useMemo, forwardRef, useImperativeHandle } from 'react'
import { Send, Paperclip } from 'lucide-react'
import { MenuPanel, MenuItem } from './ui'

/** @提及 的终止字符：与后端 app/utils/text.py 的 _MENTION_STOP 同一套口径
 *  （两处必须一致，否则前端提醒的名字和后端认的名字会对不上） */
const MENTION_STOP_CHARS = new Set<string>([
  ' ', '\t', '\n', '@', '\\',
  ...'，。！？、；：\u201c\u201d\u2018\u2019「」『』【】（）()[]{}<>#+*&^%$!~`|/'.split(''),
])

/** 找出文本里所有 @提及 的短名（到终止字符为止） */
function findMentionTokens(text: string): string[] {
  const tokens: string[] = []
  let at = text.indexOf('@')
  while (at !== -1) {
    let end = at + 1
    while (end < text.length && !MENTION_STOP_CHARS.has(text[end])) end++
    if (end > at + 1) tokens.push(text.slice(at + 1, end))
    at = text.indexOf('@', at + 1)
  }
  return tokens
}

interface ChatInputProps {
  conversationType: string
  conversationId: number | string
  t: (key: string, vars?: Record<string, string | number>) => string
  onSend: (text: string) => void
  onSendFile?: () => void
  connected: boolean
  hasAttachments?: boolean
  groupMembers?: Array<{ type: string; id: number; name: string; state?: string }>
  inputHeight?: number | null
  /** 自动高度变化时通知父组件（用于补偿拖拽高度） */
  onAutoHeight?: (ah: number) => void
}

/**
 * 独立输入框。管理自身 value 和 @mention 状态，打字不触发父组件重渲染。
 */
const ChatInputFunc = ({ conversationType, conversationId, t, onSend, onSendFile, connected, hasAttachments, groupMembers, inputHeight, onAutoHeight }: ChatInputProps, ref: React.ForwardedRef<HTMLTextAreaElement>) => {
  const [value, setValue] = useState('')
  const [autoHeight, setAutoHeight] = useState(0)
  const valueRef = useRef('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  useImperativeHandle(ref, () => textareaRef.current!, [])
  const LINE_H = 23

  // @mention 检测
  const [mentionQuery, setMentionQuery] = useState('')
  const [mentionActive, setMentionActive] = useState(false)
  const [mentionIdx, setMentionIdx] = useState(0)
  const mentionFiltered = useMemo(() =>
    mentionQuery ? (groupMembers || []).filter(m => m.name.toLowerCase().includes(mentionQuery.toLowerCase())) : (groupMembers || [])
  , [mentionQuery, groupMembers])

  useEffect(() => { valueRef.current = value }, [value])

  // 拖拽高度或自动高度变化时同步到 textarea DOM
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = Math.max(40, (inputHeight || 0) + autoHeight) + 'px'
  }, [inputHeight, autoHeight])

  // 草稿恢复 & 离开保存
  useEffect(() => {
    const key = `draft_${conversationType}_${conversationId}`
    const saved = localStorage.getItem(key)
    if (saved) setValue(saved)
    return () => {
      const v = valueRef.current.trim()
      if (v) localStorage.setItem(key, v)
      else localStorage.removeItem(key)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationType, conversationId])

  // @ 检测
  const detectMention = useCallback((val: string, cursorPos: number) => {
    const before = val.slice(0, cursorPos)
    const atIdx = before.lastIndexOf('@')
    if (atIdx >= 0) {
      const after = val.slice(atIdx + 1, cursorPos)
      if (/^[\w\u4e00-\u9fff]*$/.test(after)) {
        setMentionQuery(after)
        setMentionActive(true)
        setMentionIdx(0)
        return
      }
    }
    setMentionActive(false)
  }, [])

  // 插入 @ 名字
  const insertMention = useCallback((name: string) => {
    const ta = textareaRef.current
    if (!ta) return
    const cursorPos = ta.selectionStart
    const before = value.slice(0, cursorPos)
    const atIdx = before.lastIndexOf('@')
    if (atIdx === -1) return
    const newBefore = before.slice(0, atIdx) + '@' + name + ' '
    const newValue = newBefore + value.slice(cursorPos)
    setValue(newValue)
    setMentionActive(false)
    requestAnimationFrame(() => {
      ta.focus()
      const newPos = newBefore.length
      ta.setSelectionRange(newPos, newPos)
    })
  }, [value])

  // 手打 @短名（漏了括号后缀）时提醒一句：@ 是全字匹配，少一个字都喊不醒对方
  const mentionFix = useMemo(() => {
    if (mentionActive || !groupMembers?.length) return null
    const names = groupMembers.map((m) => m.name)
    for (const token of findMentionTokens(value)) {
      if (names.includes(token)) continue
      const candidates = names.filter((n) => n.startsWith(token))
      // 已经打全了就别提醒（提取在括号处截断，"@浮生（人物志1）" 的 token 仍是"浮生"）
      if (candidates.length === 1 && !value.includes(`@${candidates[0]}`)) {
        return { token, name: candidates[0] }
      }
    }
    return null
  }, [value, groupMembers, mentionActive])

  // 一键补全：把 @短名 换成 @完整名字，光标落到名字后面
  const applyMentionFix = useCallback(() => {
    if (!mentionFix) return
    const next = value.split(`@${mentionFix.token}`).join(`@${mentionFix.name} `)
    setValue(next)
    requestAnimationFrame(() => {
      const ta = textareaRef.current
      if (!ta) return
      ta.focus()
      ta.setSelectionRange(next.length, next.length)
    })
  }, [mentionFix, value])

  // 发送
  const doSend = useCallback(() => {
    onSend(value.trim())
    setValue('')
  }, [value, onSend])

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const ta = e.target
    setValue(ta.value)
    detectMention(ta.value, ta.selectionStart)
    // 自动缩放：超出基础高度的部分最多 3 行
    ta.style.height = 'auto'
    const scrollH = ta.scrollHeight
    const base = inputHeight || 40
    const maxAuto = 3 * LINE_H
    const ah = Math.max(0, Math.min(scrollH - base, maxAuto))
    setAutoHeight(ah)
    ta.dataset.autoHeight = String(ah)
    onAutoHeight?.(ah)
    ta.style.height = Math.max(40, (inputHeight || 0) + ah) + 'px'
  }, [detectMention, inputHeight])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    // @mention 导航
    if (mentionActive && mentionFiltered.length > 0) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setMentionIdx(i => (i + 1) % mentionFiltered.length); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); setMentionIdx(i => (i - 1 + mentionFiltered.length) % mentionFiltered.length); return }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); insertMention(mentionFiltered[mentionIdx].name); return }
      if (e.key === 'Escape') { e.preventDefault(); setMentionActive(false); return }
    }
    // Enter 发送
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      doSend()
    }
  }, [mentionActive, mentionFiltered, mentionIdx, insertMention, doSend])

  return (
    <div className="flex items-end gap-2 px-4 py-3 shrink-0">
      {/* 没 @ 到人的提醒：@ 是全字匹配，漏了括号后缀对方根本收不到；给一键补全 */}
      {mentionFix && (
        <div className="absolute bottom-full left-4 mb-1 flex items-center gap-2 rounded-control border border-border bg-elevated px-3 py-1.5 text-2xs text-textSecondary shadow-lg z-modal">
          <span>{t('chat.mentionHint', { token: mentionFix.token, name: mentionFix.name })}</span>
          <button
            type="button"
            className="btn btn-xs btn-outline"
            onMouseDown={(e) => { e.preventDefault(); applyMentionFix() }}
          >
            {t('chat.mentionHintFix')}
          </button>
        </div>
      )}

      {/* @mention 弹出列表 */}
      {mentionActive && mentionFiltered.length > 0 && (
        <MenuPanel className="absolute bottom-full left-4 mb-1 w-56 max-h-40 overflow-y-auto py-1 z-modal">
          {mentionFiltered.map((m, i) => (
            <MenuItem
              key={`${m.type}:${m.id}`}
              active={i === mentionIdx}
              className="py-2 text-xs"
              onMouseDown={(e) => { e.preventDefault(); insertMention(m.name) }}
            >
              {m.name}
            </MenuItem>
          ))}
        </MenuPanel>
      )}

      <button
        onClick={() => onSendFile?.()}
        className="p-2.5 rounded-card border border-border bg-canvas text-textMuted hover:text-textPrimary hover:border-primary-500/30 hover:bg-elevated transition-colors shrink-0"
        title={t('chat.addAttachment')}
      >
        <Paperclip size={18} />
      </button>

      <textarea
        ref={textareaRef}
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={conversationType === 'dm' ? t('chat.dmInputPlaceholder') : t('chat.groupInputPlaceholder')}
        rows={1}
        className="flex-1 min-w-0 resize-none rounded-card border border-border bg-canvas px-4 py-2.5 text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50 focus:border-primary-500/30 transition-shadow min-h-[40px]"
      />
      <button
        onClick={doSend}
        disabled={(!value.trim() && !hasAttachments) || !connected}
        className="icon-btn-lg icon-btn-primary shrink-0"
        title={t('chat.send')}
      >
        <Send size={16} />
      </button>
    </div>
  )
}

const ChatInput = forwardRef(ChatInputFunc)
export default ChatInput
