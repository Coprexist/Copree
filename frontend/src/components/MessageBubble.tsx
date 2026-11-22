import { memo, useState, useMemo, useEffect, useRef } from 'react'
import { useAuth } from '../context/AuthContext'
import { useBubbleDark } from '../hooks/useBubbleDark'
import { FileIcon, Download, Globe, ShieldAlert, MessageSquare, MoreHorizontal } from 'lucide-react'
import { formatMessageTime } from '../utils/time'
import { formatFileSize } from '../utils/format'
import { avatarGradient, avatarShadowColor } from '../utils/avatar'
import { getChatStyle, chatStyleClasses } from '../utils/providers.tsx'
import { scrollToInContainer } from '../utils/scroll'
import { copyText } from '../utils/clipboard'
import { useLang, useT } from '../i18n/I18nContext'
import { api, fileDownloadUrl } from '../api/client'
import { attachmentImageSizing } from '../hooks/useAttachmentUpload'
import FilePreviewModal from './FilePreviewModal'
import InvitationCard from './InvitationCard'
import ChannelContent from './ChannelContent'
import VoiceMessage from './VoiceMessage'
import { parseChannelContent } from '../utils/channelText'
import { MenuItem, MenuPanel } from './ui'
import { useTimeTick } from '../hooks/useTimeTick'
// CSS 变量：isMine 直接决定配色（不用 getComputedStyle，性能快）
const DARK_VARS = [
  '--b-link-r:255', '--b-link-g:255', '--b-link-b:255', '--b-link-a:0.85',
  '--b-code-r:255', '--b-code-g:255', '--b-code-b:255', '--b-code-a:0.15',
  '--b-pre-r:0',    '--b-pre-g:0',    '--b-pre-b:0',    '--b-pre-a:0.2',
  '--b-hr-r:255',   '--b-hr-g:255',   '--b-hr-b:255',   '--b-hr-a:0.2',
  '--b-thead-r:55', '--b-thead-g:45', '--b-thead-b:70', '--b-thead-a:1',
  '--b-zebra-r:48', '--b-zebra-g:40', '--b-zebra-b:60', '--b-zebra-a:0.5',
  '--b-hover-r:55', '--b-hover-g:45', '--b-hover-b:70', '--b-hover-a:0.8',
  '--b-scrollbar-r:255_255_255', '--b-scrollbar-a:0.3',
  /* 气泡彩色文字（深色底用亮色） */
  '--b-text-red:255 100 100', '--b-text-orange:255 180 50', '--b-text-gold:255 215 0',
  '--b-text-green:80 220 120', '--b-text-blue:100 150 255', '--b-text-purple:180 130 255',
  '--b-text-pink:255 130 200', '--b-text-gray:180 180 180',
]

const LIGHT_VARS = [
  '--b-link-r:initial',
  '--b-thead-r:237', '--b-thead-g:233', '--b-thead-b:254', '--b-thead-a:1',
  '--b-zebra-r:243', '--b-zebra-g:240', '--b-zebra-b:255', '--b-zebra-a:1',
  /* 气泡彩色文字（浅色底用标准色） */
  '--b-text-red:220 50 50', '--b-text-orange:220 130 0', '--b-text-gold:180 140 0',
  '--b-text-green:20 150 60', '--b-text-blue:50 100 220', '--b-text-purple:130 80 200',
  '--b-text-pink:220 80 150', '--b-text-gray:100 100 100',
]

/** 弹跳三点 */
const BouncingDots = ({ className = '' }: { className?: string }) => (
  <span className={`inline-flex gap-0.5 ${className}`}>
    <span className="w-1 h-1 bg-current rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
    <span className="w-1 h-1 bg-current rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
    <span className="w-1 h-1 bg-current rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
  </span>
);

interface MessageBubbleProps {
  senderName: string
  senderAvatarUrl?: string | null
  content: string
  isMine: boolean
  createdAt: string
  state?: string
  senderType?: string
  senderId?: number
  thinking?: boolean
  isTyping?: boolean
  sourcePublicId?: string | null
  /** 消息入口通道：'qq' = 从 QQ 进来的（界面画来源标识用） */
  via?: string | null
  attachments?: Array<{file_id?: number, name?: string, size?: number, mime_type?: string, transcript?: string, type?: string, invitation_id?: number, group_name?: string, inviter_name?: string, status?: string}> | null
  messageType?: string
  messageId?: number
  /** 回复引用：image 是被引用那条里的第一张图，显示成小图（纯文字的消息没有） */
  replyTo?: { id: number; sender: string; content: string; image?: { file_id: number; name?: string } } | null
  onAvatarClick?: (type: string, id: number, name: string, state?: string) => void
  onReply?: (messageId: number, senderName: string, content: string) => void
  /** 这条被撤回了：正文不再显示，只留一句占位 */
  revoked?: boolean
  onRevoke?: (messageId: number) => void
  /** 语音文稿：file_id → 说了什么（家在 file_metadata，按 id 取，不复存在消息里） */
  transcripts?: Record<number, string>
}

function fileIconColor(mimeType: string): string {
  if (mimeType.startsWith('image/')) return 'text-mint-400'
  if (mimeType.startsWith('video/')) return 'text-rose-400'
  if (mimeType.includes('pdf')) return 'text-rose-400'
  if (mimeType.includes('zip') || mimeType.includes('tar') || mimeType.includes('gz')) return 'text-accent-400'
  return 'text-primary-400'
}

// 共享 Markdown 渲染（GFM/公式/代码高亮/彩色文字），见 shared/MarkdownContent
import MarkdownContent from './shared/MarkdownContent'
import QuotedMessage from './shared/QuotedMessage'

const MessageBubble = memo(function MessageBubble({
  senderName, senderAvatarUrl, content, isMine, createdAt, state,
  senderType, senderId, thinking, isTyping, sourcePublicId, via, attachments, messageType, onAvatarClick, messageId, replyTo, onReply, revoked, onRevoke, transcripts,
}: MessageBubbleProps) {
  const { user } = useAuth()
  const lang = useLang()
  const t = useT()
  useTimeTick()  // 全局 tick，驱动相对时间更新（不需要返回值）

  const [previewFile, setPreviewFile] = useState<{ file_id: number; name: string; size: number; mime_type: string } | null>(null)
  const [invStatus, setInvStatus] = useState<string | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  // 文稿默认展开；收了之后这条消息记住（只在这一条上生效，不落库）
  const [showTranscript, setShowTranscript] = useState(true)
  const [copied, setCopied] = useState(false)
  const [avatarBroken, setAvatarBroken] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)

  /** 复制这条消息的正文（就是界面上看到的那份：@ 已经渲染成名字） */
  const handleCopy = async () => {
    if (!(await copyText(content))) return
    setCopied(true)
    // 让「已复制」在菜单里停留一下再收起，不然点完什么都没有只剩下安静
    setTimeout(() => {
      setCopied(false)
      setMenuOpen(false)
    }, 900)
  }

  // 操作菜单：点外面或按 Esc 就收（挂在消息上的浮层不该逼人再点一次）
  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [menuOpen])

  // 气泡里表格/代码/链接的昼夜配色，转成 inline style 挂在气泡上。
  // 深浅判定只有 useBubbleDark 一处：它读主题上下文而非 <html> 的 dark class（那个 class 由
  // ThemeProvider 在 effect 里同步，渲染阶段读到的还是上一个主题），正文彩色文字用的是同一个函数
  const darkBubble = useBubbleDark(isMine)
  const tableVars = useMemo(() => {
    const vars = darkBubble ? DARK_VARS : LIGHT_VARS
    const obj: Record<string, string> = {}
    for (const v of vars) {
      const idx = v.indexOf(':')
      if (idx !== -1) obj[v.slice(0, idx).trim()] = v.slice(idx + 1).trim()
    }
    return obj
  }, [darkBubble])


  const invAtt = attachments?.find(a => a.type === 'group_invitation')
  const isInvitation = messageType === 'group_invitation' && invAtt
  const currentStatus = invStatus || invAtt?.status || 'pending'
  const fileAtts = attachments?.filter(a => a.type !== 'group_invitation') || []
  // 这条消息里有没有可展开的语音文稿（有才在菜单里出"显示/隐藏文稿"）
  const hasTranscript = fileAtts.some(a => {
    if (!a.file_id || !String(a.mime_type || '').startsWith('audio/')) return false
    return !!(transcripts?.[a.file_id] ?? a.transcript)
  })
  // 可下载的附件（邀请卡片不是文件）。单附件下它自己，多附件一次下全部：
  // 菜单文案跟着数量走，不能还写"下载"却只给第一个
  const downloadable = fileAtts.filter(a => a.file_id)
  const downloadAttachments = () => {
    // 一次手势里连续点多个 <a download>：浏览器会排队，Chrome 首次多问一句"允许下载多个文件"，
    // 比让我们自己 setTimeout 更稳（延时后就不再算作同一次用户手势了）
    for (const att of downloadable) {
      const a = document.createElement('a')
      a.href = fileDownloadUrl(att.file_id!)
      a.download = att.name || 'file'
      a.click()
    }
  }
  // 通道把卡片/聊天记录拍平成了正文：能还原就还原，认不出就照原样显示（解析器是纯函数，见 utils/channelText）
  const channelBlock = useMemo(
    () => (fileAtts.length ? null : parseChannelContent(content)),
    [content, fileAtts.length],
  )

  // 正文里的占位（[图片]、[表情:没钱]…）和附件是同一件事的两种说法。通道入站时把
  // "[图片]" 写进了正文（AI 需要知道"这里有一张图"），但人不需要同时看到图和这行字：
  // 有附件时把纯占位剥掉，剩下的话照常显示。
  // 附件名（用来判断正文里的"[文件] 名字"是不是在重复下面的附件卡片）
  const attachmentNames = useMemo(
    () => fileAtts.map((a) => String(a.name || '').trim()).filter(Boolean),
    [fileAtts],
  )
  const displayContent = useMemo(() => {
    if (!fileAtts.length) return content
    let text = content
      // 表情名本身可能自带方括号（QQ 里真有叫「[没钱]」的：正文是 [表情:[没钱]]），
      // 所以不能"遇到 ] 就收"，要多吃掉一个结尾括号，否则会剩一个孤零零的 ]
      .replace(/\[(?:图片|语音|视频|文件)\]|\[表情(?::[^\n]*?)?\]\]?/g, '')
    // "[文件] 名字"：名字已经写在下面的附件卡片上了，"清掉占位符"只做了一半。
    // 只在名字和某个附件完全一致时剥——否则正文里可能真有一句「[文件] 你自己看」。
    for (const name of attachmentNames) {
      text = text.split('[文件] ' + name).join('').split('[文件]' + name).join('')
    }
    return text
      .replace(/[ \t]+/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim()
  }, [content, fileAtts.length, attachmentNames])

  // 只发媒体/邀请、没有文字的消息不套气泡：图片自己就是内容，外面再包一层圆角底只会显得脏。
  // 卡片/转发**不在这里**：它们的排版自己不带壳（见 ChannelContent），外面照样用气泡那层外观——
  // 看着像气泡，但不会再套出一层小卡片。
  // 撤回已在下面早返回，这里不再判 revoked
  const bareShell = !displayContent && !isTyping
    && (fileAtts.length > 0 || !!(isInvitation && invAtt))

  const handleAcceptInvitation = async (invitationId: number) => {
    try {
      await api.post(`/group-invitations/${invitationId}/accept`)
      setInvStatus('accepted')
      window.dispatchEvent(new CustomEvent('groupListRefresh'))
    } catch (_) { /* ignore */ }
  }
  const handleRejectInvitation = async (invitationId: number) => {
    try {
      await api.post(`/group-invitations/${invitationId}/reject`)
      setInvStatus('rejected')
    } catch (_) { /* ignore */ }
  }

  // 气泡样式拆两层：背景层（上色+圆角边框+阴影，参与魔视界旋转）与内容层（文字颜色+布局）
  // 背景层 absolute inset-0 铺满外层，尺寸由内容层撑起，圆角边框天然对齐。
  // 外观本身（渐变/边框/投影，含各自的夜间档）都在 index.css 的 .bubble-face / .bubble-self 里，
  // 这里只挑语义名 + 圆角方向——改气泡长相不用进这个文件。
  const bubbleBg = bareShell
    ? ''
    : isMine
      ? 'bubble-self rounded-dialog rounded-tr-md'
      : senderType === 'system'
        ? 'bg-rose-50 dark:bg-rose-900/20 rounded-dialog rounded-tl-md border border-rose-200 dark:border-rose-800'
        : 'bubble-face rounded-dialog rounded-tl-md'
  // 操作球（···）挂在哪：有气泡时是右上/左上的角标，纯媒体（语音、图）没有气泡可依，
  // 就退成"媒体行末尾的操作"，垂直居中落在那 20px 留白里（content 的 pr-5）
  const menuAnchor = bareShell
    ? 'top-1/2 -translate-y-1/2 right-0'
    : `-top-2 ${isMine ? '-left-2' : '-right-3'}`
  const menuAlign = bareShell || !isMine ? 'right-0' : 'left-0'
  // 有气泡时这颗球是"探出角标"，带一点投影才立得住；无气泡时它只是行尾操作，扁的才对
  const menuShadow = bareShell ? '' : 'shadow-[0_2px_8px_-3px_rgb(0_0_0/0.3)]'

  const bubbleText = isMine
    ? 'text-bubbleInk'
    : senderType === 'system'
      ? 'text-rose-900 dark:text-rose-300'
      : 'text-textPrimary'

  // 纯语音消息：菜单挂到语音行时长后面（VoiceMessage 的 menuSlot），不再浮在内容右上角
  const inlineVoiceMenu = bareShell && fileAtts.some(a => String(a.mime_type || '').startsWith('audio/'))
  const showMenu = messageId != null
    && (onReply || onRevoke || displayContent || fileAtts.length > 0)

  // 菜单主体只写一份：浮在气泡角上、还是插进语音行尾，差别只在外面那层壳
  const menuBody = (
    <>
      <button
        onClick={() => setMenuOpen(v => !v)}
        className={bareShell
          ? `w-6 h-6 rounded-control grid place-items-center shrink-0 transition-colors text-textMuted hover:text-textPrimary hover:bg-elevated ${menuShadow}`
          : `w-[22px] h-[22px] rounded-full bg-surface/90 text-primary-500 border border-primary-500/20 dark:border-primary-400/25 flex items-center justify-center hover:bg-primary-500/10 dark:hover:bg-elevated transition-colors ${menuShadow}`}
        title={t('chat:actions')}
      >
        <MoreHorizontal size={bareShell ? 14 : 13} />
      </button>
      {menuOpen && (
        <MenuPanel className={`absolute top-full mt-1 w-24 z-modal ${menuAlign}`}>
          {onReply && (
            <MenuItem onClick={() => { setMenuOpen(false); onReply(messageId!, senderName, content) }}>
              {t('chat:reply')}
            </MenuItem>
          )}
          {!!content && (
            <MenuItem onClick={handleCopy}>{copied ? t('chat:copied') : t('chat:copy')}</MenuItem>
          )}
          {downloadable.length > 0 && (
            <MenuItem onClick={() => { setMenuOpen(false); downloadAttachments() }}>
              {downloadable.length > 1 ? t('chat:downloadAll') : t('chat:download')}
            </MenuItem>
          )}
          {hasTranscript && (
            <MenuItem onClick={() => { setMenuOpen(false); setShowTranscript(v => !v) }}>
              {showTranscript ? t('chat:hideTranscript') : t('chat:showTranscript')}
            </MenuItem>
          )}
          {onRevoke && (
            <MenuItem onClick={() => { setMenuOpen(false); onRevoke(messageId!) }}>
              {t('chat:revoke')}
            </MenuItem>
          )}
        </MenuPanel>
      )}
    </>
  )

  // 头像地址坏掉（文件被删、格式不对）时退回首字母渐变，别把浏览器的裂图图标露给用户
  const hasAvatar = !!senderAvatarUrl && !avatarBroken
  useEffect(() => setAvatarBroken(false), [senderAvatarUrl])
  const avatarGradientShadow = avatarShadowColor(senderType)
  const { gap, mb, avatar: avatarSize, textSize: avatarTextSize } = chatStyleClasses(getChatStyle())

  // 撤回不是"谁说的话"，是"这条没了"的系统提示：居中灰胶囊，不带头像、名字与气泡。
  // 以前它在气泡里按发信人左右对齐，看起来像对方还在说这句话。
  // 胶囊外观不在这里另拼——ui_system.md 的规矩是只用 .chip 这一处来源。
  // 这里是撤回的**唯一出口**：下面的气泡代码不必再判 revoked（判了也是死条件）。
  if (revoked) {
    return (
      <div id={messageId ? `msg-${messageId}` : undefined} className={`flex justify-center ${mb} msg-enter`}>
        {/* 名字与后半句分开排：宽度不够时先缩名字（truncate），后半句不缩 */}
        <span className="chip chip-muted select-none max-w-full">
          <span className="min-w-0 truncate">{senderName}</span>
          <span className="shrink-0">{t('chat:revokedSuffix')}</span>
        </span>
      </div>
    )
  }

  // Katex/表格/pre 溢出等不适合用 CSS 变量的样式继续用 Tailwind
  const layoutCls = [
    '[&_.katex-display]:overflow-x-auto',
    '[&_.katex-display]:-mx-1',
    '[&_.katex-display]:px-1',
    '[&_.katex]:text-inherit',
    '[&_.katex]:max-w-full',
    '[&_.katex]:overflow-x-auto',
    '[&_.katex]:overflow-y-hidden',
    '[&_.katex]:align-middle',
    '[&_pre]:overflow-x-auto',
    '[&_pre]:-mx-1',
    '[&_pre]:px-1',
    '[&_img]:max-w-full',
    '[&_img]:rounded-control',
    // 表格圆角（overflow 由 index.css overflow-x-auto 控制）
    '[&_.markdown-table-wrapper]:rounded-control',
    '[&_.markdown-table-wrapper]:border',
    '[&_.markdown-table-wrapper]:border-border',
    // 移除 wrapper 额外间距
    '[&_.markdown-table-wrapper]:my-0',
  ].join(' ')

  return (<>
    <div id={messageId ? `msg-${messageId}` : undefined} className={`flex ${gap} ${mb} msg-enter group ${isMine ? 'flex-row-reverse' : ''}`}>
      <div className="relative shrink-0">
        {!isMine && (thinking || isTyping) && (
          <div className="absolute -inset-0.5 rounded-full ai-pulse-active" />
        )}
        {hasAvatar ? (
          <div
            onClick={() => { if (onAvatarClick && senderType && senderId && senderType !== 'system' && senderType !== 'assistant') onAvatarClick(senderType, senderId, senderName, state) }}
            className={`relative ${avatarSize} rounded-full overflow-hidden ${senderType !== 'system' && senderType !== 'assistant' ? 'cursor-pointer hover:scale-105 transition-transform' : ''} shadow ${avatarGradientShadow}`}
            title={t('chat:viewProfile').replace('{name}', senderName)}
          >
            <div className={`absolute inset-px rounded-full ${avatarGradient(senderType, isMine, true)}`} />
            <img src={senderAvatarUrl ?? undefined} alt={senderName} className="relative w-full h-full rounded-full object-cover" loading="lazy" decoding="async" onError={() => setAvatarBroken(true)} />
          </div>
        ) : (
          <div
            onClick={() => { if (onAvatarClick && senderType && senderId && senderType !== 'system' && senderType !== 'assistant') onAvatarClick(senderType, senderId, senderName, state) }}
            className={`relative ${avatarSize} rounded-full flex items-center justify-center ${avatarTextSize} font-bold ${avatarGradient(senderType, isMine, false)} ${senderType !== 'system' && senderType !== 'assistant' ? 'cursor-pointer hover:scale-105 transition-transform' : ''} shadow ${avatarGradientShadow} overflow-hidden`}
            title={senderType === 'system' ? '系统通知' : thinking ? t('chat:thinking') : isTyping ? t('chat:typing') : t('chat:viewProfile').replace('{name}', senderName)}
          >
            {senderType === 'system' ? <ShieldAlert size={16} className="text-white" />
              : (thinking || isTyping) ? <BouncingDots className="text-white/80" />
              : senderName.charAt(0).toUpperCase()}
          </div>
        )}
      </div>

      <div className={`max-w-[72%] ${isMine ? 'items-end' : 'items-start'}`}>
        <div className={`flex items-center gap-2 mb-1 flex-wrap ${isMine ? 'flex-row-reverse' : ''}`}>
          <span className={`text-xs font-medium ${senderType === 'system' ? 'text-rose-500' : 'text-textSecondary'}`}>{senderName}</span>
          {via === 'qq' && (
            <span className="chip chip-primary shrink-0" title={t('chat:fromQq')}>
              <MessageSquare size={10} className="inline" /> {t('chat:fromQq')}
            </span>
          )}
          {sourcePublicId && (
            <span className="chip chip-primary shrink-0" title={t('chat:fromInstance').replace('{publicId}', sourcePublicId)}>
              <Globe size={10} className="inline" /> {sourcePublicId.length > 15 ? sourcePublicId.slice(0, 15) + '...' : sourcePublicId}
            </span>
          )}
          <span className="text-3xs text-textMuted">{formatMessageTime(createdAt, lang)}</span>
          {thinking && <span className="text-3xs text-primary-400 animate-pulse font-medium">{t('chat:thinking')}</span>}
          {isTyping && <span className="text-3xs text-mint-400 animate-pulse font-medium">{t('chat:typing')}</span>}
        </div>
        <div className={`relative ${thinking || isTyping ? 'opacity-70' : ''}`}>
          {/* 背景层：只上色/圆角/边框/阴影，不含图片 → 天然被魔视界选中旋转；尺寸由外层决定 */}
          <div data-mv-force className={`absolute inset-0 ${bubbleBg}`} aria-hidden="true" />
          {/* 内容层：文字/图片/布局，不参与旋转（图片保持清晰） */}
          {/* 媒体消息右侧留出一点空：操作菜单（···）浮在内容右上角，不留空会压住时长 */}
          <div className={`bubble-content relative ${bareShell ? (inlineVoiceMenu ? '' : 'pr-5') : 'px-2.5 py-2 md:px-4 md:py-2.5'} text-sm leading-relaxed break-words ${bubbleText} ${layoutCls}`} style={tableVars}>
          {replyTo != null && (
            <div className={`flex items-start gap-1.5 mb-1.5 pb-1.5 border-b ${isMine ? 'border-white/20' : 'border-border'} cursor-pointer hover:opacity-80 transition-opacity`}
              onClick={() => {
                // 只滚动消息列表容器本身（scrollIntoView 会连带滚动外层 main/Layout，把标题栏滚出视口）
                const target = document.getElementById(`msg-${replyTo.id}`)
                const list = target?.closest('.overflow-y-auto') as HTMLElement | null
                if (target && list) {
                  scrollToInContainer(list, target, { smooth: true, offset: -list.clientHeight / 2 })
                }
              }}>
              {/* 引用的样子只有一处实现（shared/QuotedMessage）：竖线、顺序、配色都跟着那边走 */}
              <QuotedMessage
                sender={replyTo.sender}
                content={replyTo.content}
                image={replyTo.image}
                inverse={isMine}
              />
            </div>
          )}
          {isInvitation && invAtt ? (
            <InvitationCard invitationId={invAtt.invitation_id!} groupName={invAtt.group_name || ''} inviterName={invAtt.inviter_name || ''} message={undefined} status={currentStatus as 'pending' | 'accepted' | 'rejected'} onAccept={handleAcceptInvitation} onReject={handleRejectInvitation} isMine={isMine} />
          ) : isTyping ? (
            <BouncingDots className="text-primary-400 align-middle" />
          ) : channelBlock ? (
            <ChannelContent data={channelBlock} content={content} isMine={isMine} />
          ) : (
            displayContent ? <MarkdownContent content={displayContent} isMine={isMine} /> : null
          )}
          {fileAtts.length > 0 && (
            <div className={`${displayContent ? 'mt-2 pt-2 border-t' : ''} flex flex-wrap gap-1.5 ${isMine ? 'border-white/20' : 'border-border'}`}>
              {fileAtts.map(att => {
                const dlUrl = fileDownloadUrl(att.file_id!)
                const fid = att.file_id!
                const fname = att.name!
                const fsize = att.size!
                const fmime = att.mime_type!
                if (fmime.startsWith('image/')) return (
                  <button key={fid} type="button" onClick={() => setPreviewFile({ file_id: fid, name: fname, size: fsize, mime_type: fmime })} className="block max-w-full">
                    <img src={dlUrl} alt={fname} className={`max-w-[280px] max-h-[200px] rounded-control cursor-pointer hover:opacity-90 transition-opacity border border-white/10 ${attachmentImageSizing(att)}`} title={fname} loading="lazy" />
                  </button>
                )
                if (fmime.startsWith('audio/')) return (
                  // 没有气泡当底时（只发语音）按浅色底配色，否则白字白底看不见
                  <VoiceMessage
                      key={fid}
                      fileId={fid}
                      name={fname}
                      isMine={bareShell ? false : isMine}
                      transcript={transcripts?.[fid] ?? att.transcript}
                      showTranscript={showTranscript}
                      menuSlot={inlineVoiceMenu ? (
                        <span ref={menuRef} className="relative shrink-0">{menuBody}</span>
                      ) : undefined}
                    />
                )
                return (
                  // ui-standard-ignore: 气泡内文件胶囊（配色随气泡明暗 isMine 切换），不是按钮形态
                  <button key={fid} onClick={() => setPreviewFile({ file_id: fid, name: fname, size: fsize, mime_type: fmime })}
                    className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-control text-xs transition-colors ${isMine ? 'bg-white/10 hover:bg-white/20 text-white/90' : 'bg-canvas hover:bg-elevated text-textSecondary hover:text-textPrimary border border-border'}`}
                    title={`${fname} (${formatFileSize(fsize)})`}>
                    <FileIcon size={12} className={isMine ? 'text-white/80' : fileIconColor(fmime)} />
                    <span className="max-w-[100px] truncate">{fname}</span>
                    <span className="text-3xs opacity-60">{formatFileSize(fsize)}</span>
                    <Download size={11} className="opacity-60" />
                  </button>
                )
              })}
            </div>
          )}

          {/* 操作菜单：纯语音消息插进语音行尾（menuSlot），其余浮在气泡角上 */}
          {showMenu && !inlineVoiceMenu && (
            <div
              ref={menuRef}
              className={`absolute ${menuAnchor} transition-opacity md:opacity-0 group-hover:opacity-100 ${menuOpen ? 'md:opacity-100' : ''}`}
            >
              {menuBody}
            </div>
          )}
        </div>
        </div>
      </div>
    </div>
    {previewFile && <FilePreviewModal fileId={previewFile.file_id} fileName={previewFile.name} fileSize={previewFile.size} mimeType={previewFile.mime_type} onClose={() => setPreviewFile(null)} />}
  </>)
})
export default MessageBubble
