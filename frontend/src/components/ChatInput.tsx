import { useState, useCallback, useEffect, useRef, useMemo, forwardRef, useImperativeHandle } from 'react'
import { Send, Paperclip, Smile } from 'lucide-react'
import { MenuPanel, MenuItem } from './ui'
import { useEmojiPacks } from '../hooks/useEmojiPacks'
import { faceAssetUrl, faceMarkup } from '../utils/emojiPacks'

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

// 输入框高度：MIN_H = 单行（与 textarea 的 min-h-[40px] 一致），自动增长最多再给 3 行。
// 导出给拖拽手柄用——两处的最小高度必须是同一个数，否则拖到底会和自动高度差几像素
export const CHAT_INPUT_MIN_H = 40
const LINE_H = 23
const MAX_H = CHAT_INPUT_MIN_H + 3 * LINE_H

interface ChatInputProps {
  conversationType: string
  conversationId: number | string
  t: (key: string, vars?: Record<string, string | number>) => string
  onSend: (text: string) => void
  onSendFile?: () => void
  connected: boolean
  hasAttachments?: boolean
  groupMembers?: Array<{ type: string; id: number; name: string; state?: string }>
  /** 用户拖出来的最低高度（总高）；null = 只按内容自动长 */
  inputHeight?: number | null
}

/**
 * 独立输入框。管理自身 value 和 @mention 状态，打字不触发父组件重渲染。
 */
const ChatInputFunc = ({ conversationType, conversationId, t, onSend, onSendFile, connected, hasAttachments, groupMembers, inputHeight }: ChatInputProps, ref: React.ForwardedRef<HTMLTextAreaElement>) => {
  const [value, setValue] = useState('')
  // 内容自然高度（已按单行下限与 3 行上限裁剪）：输入框高度 = max(它, 用户拖出来的高度)
  const [contentH, setContentH] = useState(CHAT_INPUT_MIN_H)
  const [emojiOpen, setEmojiOpen] = useState(false)
  const emojiPacks = useEmojiPacks()
  const valueRef = useRef('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  useImperativeHandle(ref, () => textareaRef.current!, [])

  // @mention 检测
  const [mentionQuery, setMentionQuery] = useState('')
  const [mentionActive, setMentionActive] = useState(false)
  const [mentionIdx, setMentionIdx] = useState(0)
  const mentionFiltered = useMemo(() =>
    mentionQuery ? (groupMembers || []).filter(m => m.name.toLowerCase().includes(mentionQuery.toLowerCase())) : (groupMembers || [])
  , [mentionQuery, groupMembers])

  useEffect(() => { valueRef.current = value }, [value])

  // 拖拽高度或内容高度变化时同步到 textarea DOM
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = Math.max(CHAT_INPUT_MIN_H, inputHeight || 0, contentH) + 'px'
  }, [inputHeight, contentH])

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

  // 有字符的表情插入字符（输入框即为所见），仅图片的插入 :id: 短码
  const insertEmoji = useCallback((markup: string) => {
    const ta = textareaRef.current
    if (!ta) return
    const cursorPos = ta.selectionStart
    const before = value.slice(0, cursorPos)
    const token = markup
    setValue(before + token + value.slice(cursorPos))
    requestAnimationFrame(() => {
      ta.focus()
      const next = before.length + token.length
      ta.setSelectionRange(next, next)
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
    // 自动缩放：量内容高度再夹到 [单行, 3 行上限]。
    // textarea 的 auto 高度只认 rows，不认折行后的内容，所以必须读 scrollHeight；
    // 而 scrollHeight 不含上下边框，不补这 2px 就会永远差一点——多行时底部挂着一条细滚动条
    const borderY = ta.offsetHeight - ta.clientHeight
    ta.style.height = 'auto'
    const next = Math.min(Math.max(ta.scrollHeight + borderY, CHAT_INPUT_MIN_H), MAX_H)
    setContentH(next)
    ta.style.height = Math.max(CHAT_INPUT_MIN_H, inputHeight || 0, next) + 'px'
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
          <span>{t('chat:mentionHint', { token: mentionFix.token, name: mentionFix.name })}</span>
          <button
            type="button"
            className="btn btn-xs btn-outline"
            onMouseDown={(e) => { e.preventDefault(); applyMentionFix() }}
          >
            {t('chat:mentionHintFix')}
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
        title={t('chat:addAttachment')}
      >
        <Paperclip size={18} />
      </button>

      {/* 装表情包插件才有这个入口：没装就不显示，输入框保持原样 */}
      {emojiPacks.length > 0 && (
        <div className="relative shrink-0">
          <button
            onClick={() => setEmojiOpen((open) => !open)}
            className={`p-2.5 rounded-card border transition-colors ${
              emojiOpen
                ? 'border-primary-500/30 bg-elevated text-primary-500'
                : 'border-border bg-canvas text-textMuted hover:text-textPrimary hover:border-primary-500/30 hover:bg-elevated'
            }`}
            title={t('chat:addEmoji')}
          >
            <Smile size={18} />
          </button>
          {emojiOpen && (
            <MenuPanel className="absolute bottom-full left-0 mb-1 w-72 max-h-64 overflow-y-auto p-2 z-modal">
              <div className="text-3xs text-textMuted px-1 pb-1">{t('chat:emojiPacks')}</div>
              {emojiPacks.map((pack) => (
                <div key={pack.id} className="mb-2 last:mb-0">
                  <div className="text-3xs text-textMuted px-1 mb-1" title={pack.usage}>{pack.name}</div>
                  <div className="flex flex-wrap gap-0.5">
                    {pack.faces.map((face) => (
                      <button
                        key={face.id}
                        type="button"
                        title={face.name}
                        onMouseDown={(e) => { e.preventDefault(); insertEmoji(faceMarkup(face)) }}
                        className="w-8 h-8 flex items-center justify-center rounded-control text-base hover:bg-elevated transition-colors"
                      >
                        {face.emoji || (face.file
                          ? <img src={faceAssetUrl(pack, face)} alt={face.name} className="w-5 h-5" loading="lazy" />
                          : face.name.slice(0, 1))}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </MenuPanel>
          )}
        </div>
      )}

      <textarea
        ref={textareaRef}
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={conversationType === 'dm' ? t('chat:dmInputPlaceholder') : t('chat:groupInputPlaceholder')}
        rows={1}
        className="flex-1 min-w-0 resize-none rounded-card border border-border bg-canvas px-4 py-2.5 text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50 focus:border-primary-500/30 transition-shadow min-h-[40px]"
      />
      <button
        onClick={doSend}
        disabled={(!value.trim() && !hasAttachments) || !connected}
        className="icon-btn-lg icon-btn-primary shrink-0"
        title={t('chat:send')}
      >
        <Send size={16} />
      </button>
    </div>
  )
}

const ChatInput = forwardRef(ChatInputFunc)
export default ChatInput
