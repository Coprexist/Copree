/**
 * 共享 Markdown 渲染器 — 消息气泡 / 群视界设计页对话 共用（单一实现，勿各自手写）。
 * 从 MessageBubble 抽出：GFM + 数学公式 + 换行 + 代码高亮 + 彩色文字标签 + XSS 过滤。
 */
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import remarkBreaks from 'remark-breaks'
import rehypeKatex from 'rehype-katex'
import rehypeRaw from 'rehype-raw'
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize'
import { visit } from 'unist-util-visit'
import CodeRenderer from './CodeRenderer'
import { useEmojiPacks } from '../../hooks/useEmojiPacks'
import { renderEmojiMarkers } from '../../utils/emojiPacks'
import { useBubbleDark } from '../../hooks/useBubbleDark'

// 将行内代码重定向到独立组件，避免和代码块共用同一个 code 组件
function remarkInlineCode() {
  return (tree: any) => { visit(tree, 'inlineCode', (node: any) => {
    node.data = { hName: 'inlinecode' }
  }) }
}

const COLOR_VARS: Record<string, [string, string]> = {
  red: ['255 100 100', '220 50 50'],
  orange: ['255 180 50', '220 130 0'],
  gold: ['255 215 0', '180 140 0'],
  green: ['80 220 120', '20 150 60'],
  blue: ['100 150 255', '50 100 220'],
  purple: ['180 130 255', '130 80 200'],
  pink: ['255 130 200', '220 80 150'],
  gray: ['180 180 180', '100 100 100'],
}

/** 彩色文字标签（<span class="text-red"> / [red]...[/red]）→ 行内颜色；深/浅色底两套，判定见 useBubbleDark */
function colorize(content: string, darkBubble: boolean) {
  const rgb = (k: string) => `rgb(${COLOR_VARS[k][darkBubble ? 0 : 1]})`
  let out = content
  for (const k of Object.keys(COLOR_VARS)) {
    out = out
      .replace(new RegExp(`<span\\s+class=['"]text-${k}['"][^>]*>([\\s\\S]*?)<\\/span>`, 'gi'), `<span style="color:${rgb(k)}">$1</span>`)
      .replace(new RegExp(`\\[${k}\\]([\\s\\S]*?)\\[\\/${k}\\]`, 'g'), `<span style="color:${rgb(k)}">$1</span>`)
  }
  return out
    .replace(/\\\[([\s\S]*?)\\\]/g, '$$$$$1$$$$')
    .replace(/\\\(([\s\S]*?)\\\)/g, '$$$1$$')
}

export default function MarkdownContent({ content, isMine = false }: { content: string; isMine?: boolean }) {
  // 表情渲染收口于此：正文是字符或 :id: 短码，装了插件才把短码画成图
  const packs = useEmojiPacks()
  const darkBubble = useBubbleDark(isMine)
  return (
    <Markdown
      children={colorize(renderEmojiMarkers(content, packs), darkBubble)}
      remarkPlugins={[[remarkGfm, { singleTilde: false }], remarkMath, remarkBreaks, remarkInlineCode]}
      rehypePlugins={[rehypeRaw, [rehypeSanitize, {
        ...defaultSchema,
        tagNames: [...(defaultSchema.tagNames || []), 'inlinecode', 'math', 'math-inline'],
        attributes: {
          ...defaultSchema.attributes,
          a: [...(defaultSchema.attributes?.a || ['href']), 'class', 'target', 'rel'],
          code: [...(defaultSchema.attributes?.code || []), 'class'],
          // 属性名要用 hast 的写法（className 不是 class）：写 class 等于没放行，实测过
          span: [...(defaultSchema.attributes?.span || []), 'style', 'className'],
          img: [...(defaultSchema.attributes?.img || ['src', 'alt']), 'class'],
          div: [...(defaultSchema.attributes?.div || []), 'class'],
        },
      }], rehypeKatex]}
      components={{ 
        code: CodeRenderer,
        inlinecode: ({ children }: any) => (
          <code className="bg-black/5 dark:bg-white/10 rounded px-1 py-0.5 text-[0.85em] inline-block max-w-full break-words">{children}</code>
        ),
        table: ({ node, ...props }: any) => <div className="markdown-table-wrapper"><table {...props} /></div>,
        th: ({ node, ...props }: any) => <th {...props} />,
        td: ({ node, ...props }: any) => <td {...props} />,
      } as any}
    />
  )
}
