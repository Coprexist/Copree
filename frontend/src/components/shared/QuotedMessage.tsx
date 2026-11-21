import { fileDownloadUrl } from '../../api/client'
import EmojiText from './EmojiText'

/**
 * 一条"引用"长什么样 —— 气泡里的回复引用与输入框上方的引用条共用这一份。
 *
 * 为什么抽出来：同一套「竖线 + 名字 + 缩略图 + 文字」原来在 MessageBubble 与 ChatView
 * 各写了一遍，一改就漂——先是引用里带图时竖线没跟着长高（父级 items-start 时 h-full
 * 撑不开），后是图片该在名字前还是名字后两边不一致。顺序、配色、竖线只在实现这一处。
 *
 * 顺序固定：竖线 → 名字（可带「回复 」前缀）→ 缩略图 → 文字。
 * 名字后面才是内容，与正文同一条规矩；名字不带 @（这里是「谁说的」，不是称呼）。
 */
interface QuotedMessageProps {
  /** 被引用那条的发言人 */
  sender: string
  /** 被引用的正文（可为空：纯图片消息） */
  content?: string
  /** 被引用消息里的第一张图，缩略显示 */
  image?: { file_id: number; name?: string }
  /** 深色气泡里（自己发的消息）：配色转白系 */
  inverse?: boolean
  /** 名字前缀：输入框上方用「回复 」，气泡里不写 */
  prefix?: string
  /** 文字行数上限：气泡 2 行、输入框上方 1 行 */
  lines?: 1 | 2
  /** 缩略图尺寸（默认 32px；输入框上方给 28px） */
  thumbClass?: string
}

export default function QuotedMessage({
  sender,
  content,
  image,
  inverse = false,
  prefix = '',
  lines = 2,
  thumbClass = 'w-8 h-8',
}: QuotedMessageProps) {
  const bar = inverse ? 'bg-white/40' : 'bg-primary-400'
  const name = inverse ? 'text-white/80' : 'text-primary-400'
  const text = inverse ? 'text-white/50' : 'text-textMuted'

  return (
    <>
      {/* 竖线：self-stretch 才会被拉到与内容等高（父级是 items-start，h-full 撑不开） */}
      <div className={`w-0.5 self-stretch min-h-[1.5em] rounded-full shrink-0 ${bar}`} />
      <div className={`text-2xs leading-relaxed min-w-0 ${lines === 1 ? 'truncate' : 'line-clamp-2'}`}>
        <span className={`font-medium ${name}`}>{prefix}{sender}</span>
        {image && (
          <img
            src={fileDownloadUrl(image.file_id)}
            alt={image.name || ''}
            className={`inline-block align-middle rounded-control object-cover ml-1.5 ${thumbClass}`}
            loading="lazy"
          />
        )}
        {content ? (
          <span className={text}> <EmojiText content={content} /></span>
        ) : null}
      </div>
    </>
  )
}
