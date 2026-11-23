import MermaidBlock from '../MermaidBlock'
import hljs from 'highlight.js'
// 高亮配色由全局 index.css 自定义（日/夜两套，取主题色系）——不再引 highlight.js 内置主题 CSS

/**
 * 共享代码块渲染器——消息气泡和文件预览共用。
 *
 * - mermaid 块 → <MermaidBlock>
 * - inline code → 允许断词换行
 * - block code → 语法高亮 + 横向滚动
 */
export default function CodeRenderer({ className, children, inline }: any) {
  const match = /language-(\w+)/.exec(className || '')
  const code = String(children).replace(/\n$/, '')

  if (!inline && match && match[1] === 'mermaid') {
    return <MermaidBlock code={code} compact />
  }

  if (inline) {
    return (
      <code className={`bg-black/5 dark:bg-white/10 rounded px-1 py-0.5 text-[0.85em] break-all ${className || ''}`}>
        {children}
      </code>
    )
  }

  // 语法高亮：指定语言优先 → 自动检测 → 原样输出
  let html: string | null = null
  if (match && hljs.getLanguage(match[1])) {
    try { html = hljs.highlight(code, { language: match[1] }).value } catch {}
  }
  if (!html) {
    try { html = hljs.highlightAuto(code).value } catch {}
  }

  return (
    <code
      className={`hljs block overflow-x-auto whitespace-pre rounded-card bg-black/5 dark:bg-white/5 border border-border/50 p-5 text-xs text-gray-900 dark:text-gray-100 ${className || ''}`}
      dangerouslySetInnerHTML={{ __html: html ?? code }}
    />
  )
}
