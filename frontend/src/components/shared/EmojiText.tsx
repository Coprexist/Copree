import { useEmojiPacks } from '../../hooks/useEmojiPacks'
import { splitEmojiParts } from '../../utils/emojiPacks'

/**
 * 消息文本的行内渲染：字符照出，仅图片的表情画成小图。
 *
 * 与 MarkdownContent 的分工只在场景：这里是单行预览（通知浮窗、引用条、会话列表），
 * 没有 Markdown 管线，所以按片段直接渲染。切片口径与后端归一一致。
 */
export default function EmojiText({ content }: { content: string }) {
  const packs = useEmojiPacks()
  return (
    <>
      {splitEmojiParts(content, packs).map((part, i) => part.file
        ? <img key={i} src={part.file} alt={part.name} title={part.name}
               className="inline-block w-[1.15em] h-[1.15em] align-[-0.2em]" loading="lazy" />
        : <span key={i}>{part.text}</span>)}
    </>
  )
}
