/**
 * 通道富消息的可视化：平台的卡片与合并转发的聊天记录。
 *
 * 正文仍是平台拍平的那段文本（渲染即落库，不改事实），这里只负责"怎么给人看"。
 * 解析在 utils/channelText.ts；认不出就返回 null，气泡原样显示纯文本。
 */
import { useState } from 'react'
import { Link2 } from 'lucide-react'
import { Modal } from './ui'
import { useT } from '../i18n/I18nContext'
import type { ChannelCard, ChannelContent as ChannelContentData, ChannelForward } from '../utils/channelText'

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

/** 原文开关：解析可能丢细节（正文里那些键值的原始排列），核对时能展开看。
 *  卡片视图（CardView）把这段做进了自己的页脚，这里留给弹窗等整块没有链接的场景 */
function RawToggle({ content, isMine }: { content: string; isMine: boolean }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className={`text-3xs transition-colors ${isMine ? 'text-white/60 hover:text-white/90' : 'text-textMuted hover:text-textSecondary'}`}
      >
        {t('chat:channelRaw')}
      </button>
      {open && (
        <pre className={`mt-1 whitespace-pre-wrap break-all text-3xs leading-relaxed rounded-control p-2 ${isMine ? 'bg-white/10 text-white/80' : 'bg-canvas text-textSecondary'}`}>
          {content}
        </pre>
      )}
    </div>
  )
}

function CardView({ card, content, isMine }: { card: ChannelCard; content: string; isMine: boolean }) {
  const t = useT()
  const [rawOpen, setRawOpen] = useState(false)
  const host = hostOf(card.url)
  // 和转发卡片一样：自己不画壳——外面那层气泡就是壳，再套一层小卡片就成了框里套框。
  // 层级也就两段：正文（标题 + 描述），页脚一行（来源 + 原文）；"图文卡片"这种类型名不占一行。
  const body = (
    <>
      {card.title && (
        <div className={`text-sm font-medium break-words ${isMine ? 'text-white' : 'text-textPrimary'}`}>{card.title}</div>
      )}
      {card.desc && (
        <div className={`mt-1 text-xs line-clamp-3 break-words ${isMine ? 'text-white/70' : 'text-textSecondary'}`}>{card.desc}</div>
      )}
    </>
  )
  const source = card.tag || host || card.label || t('chat:channelLink')
  // 整张卡片是一个链接（和转发卡片一样"点哪都进"），页脚里的「原文」自己吃掉点击。
  // 原文展开的 <pre> 不放在这个链接里、也不放在右对齐的页脚里：那样它会被挤成一条
  // 只由内容决定宽度、长 URL 直接冲出卡片——放到卡片下面，宽度就跟着卡片走。
  const swallow = (e: React.MouseEvent) => { e.preventDefault(); e.stopPropagation() }
  const toggleRaw = (e: React.MouseEvent) => { swallow(e); setRawOpen(v => !v) }
  const face = (
    <div className="text-left">
      {body}
      <div className={`mt-2 pt-2 flex items-center gap-1 text-3xs border-t ${isMine ? 'border-white/25 text-white/60' : 'border-border text-textMuted'}`}>
        <Link2 size={10} className="shrink-0" />
        <span className="truncate min-w-0">{source}</span>
        <span
          role="button"
          tabIndex={0}
          onClick={toggleRaw}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setRawOpen(v => !v) } }}
          className={`ml-auto shrink-0 cursor-pointer transition-colors ${isMine ? 'hover:text-white/90' : 'hover:text-textSecondary'}`}
        >
          {t('chat:channelRaw')}
        </span>
      </div>
    </div>
  )
  return (
    <div className="min-w-[220px] max-w-[340px]">
      {card.linkable ? (
        <a
          href={card.url}
          target="_blank"
          rel="noopener noreferrer"
          title={card.url}
          className="channel-card block"
        >
          {face}
        </a>
      ) : face}
      {rawOpen && (
        <pre className={`mt-2 whitespace-pre-wrap break-all text-3xs leading-relaxed rounded-control p-2 ${isMine ? 'bg-white/10 text-white/80' : 'bg-canvas text-textSecondary'}`}>
          {content}
        </pre>
      )}
    </div>
  )
}

function ImagePart({ url, name, isMine }: { url: string; name: string; isMine: boolean }) {
  const t = useT()
  const [broken, setBroken] = useState(false)
  if (broken) {
    return (
      <div className={`text-3xs rounded-control px-2 py-1 ${isMine ? 'bg-white/10 text-white/60' : 'bg-canvas text-textMuted'}`}>
        {t('chat:channelImageExpired')}{name ? ` · ${name}` : ''}
      </div>
    )
  }
  return (
    <img
      src={url}
      alt={name}
      title={name}
      loading="lazy"
      onError={() => setBroken(true)}
      className="max-w-[180px] max-h-[180px] rounded-control border border-black/10 object-contain"
    />
  )
}

/**
 * 合并转发的聊天记录：卡片只露标题 + 条数，点开是弹窗里的完整内容。
 *
 * 以前是就地展开——聊天记录一长就把消息流撑开，翻回去还要再收起一次；
 * 弹窗把"看细节"和"翻消息"拆开，卡片本身高度恒定。
 */
function ForwardView({ forward, content, isMine }: { forward: ChannelForward; content: string; isMine: boolean }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  return (
    <>
      {/* 样子照 QQ 的转发卡片来（标题 + 前几条预览 + 分隔线 + 查看条数），但**自己不画壳**：
          外面那层气泡就是它的壳，再套一层小卡片就成了框里套框 */}
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="w-full text-left"
      >
        <div className="min-w-[220px]">
          <div className={`text-sm font-medium truncate ${isMine ? 'text-white' : 'text-textPrimary'}`}>{forward.title}</div>
          {forward.messages.slice(0, 3).map((msg, i) => (
            <div
              key={i}
              className={`mt-1 text-xs truncate ${isMine ? 'text-white/70' : 'text-textSecondary'}`}
            >
              {msg.sender ? `${msg.sender}: ` : ''}
              {msg.text || (msg.attachments.length > 0 ? '[图片]' : '')}
            </div>
          ))}
          <div className={`mt-2 pt-2 text-3xs border-t ${isMine ? 'border-white/25 text-white/70' : 'border-border text-textMuted'}`}>
            {t('chat:channelForwardCount').replace('{n}', String(forward.messages.length))}
          </div>
        </div>
      </button>
      {/* 聊天记录常常很长：手机端铺满整屏，桌面端还是居中卡片 */}
      <Modal open={open} onClose={() => setOpen(false)} title={forward.title} size="md" mobileFullscreen>
        <div className="space-y-3">
          {forward.messages.map((msg, i) => (
            <div key={i}>
              {msg.sender && <div className="text-3xs text-textMuted">{msg.sender}</div>}
              {msg.text && (
                <div className="mt-0.5 text-sm whitespace-pre-wrap break-words text-textPrimary">{msg.text}</div>
              )}
              <div className="mt-1 flex flex-wrap gap-1.5">
                {msg.attachments.map((att, j) => (
                  att.isImage
                    ? <ImagePart key={j} url={att.url} name={att.name} isMine={false} />
                    : (
                      <div key={j} className="text-3xs rounded-control px-2 py-1 bg-elevated text-textSecondary">
                        [{att.label}] {att.name}{att.size ? ` · ${att.size}` : ''}
                      </div>
                    )
                ))}
              </div>
            </div>
          ))}
        </div>
        <div className="mt-3 border-t border-border pt-2">
          <RawToggle content={content} isMine={false} />
        </div>
      </Modal>
    </>
  )
}

export default function ChannelContent({ data, content, isMine }: {
  data: ChannelContentData
  content: string
  isMine: boolean
}) {
  if (data.kind === 'card') return <CardView card={data} content={content} isMine={isMine} />
  return <ForwardView forward={data} content={content} isMine={isMine} />
}
