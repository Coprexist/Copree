import { useState, useEffect, useRef, type ReactNode, type JSX } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ArrowLeft, Loader2, BookOpen } from 'lucide-react'
import MermaidBlock from '../components/MermaidBlock'
import OutlineNav from '../components/shared/OutlineNav'

/** 手册配置：路径 → { 文档路径, 标题 } */
const MANUAL_CONFIG: Record<string, { docPath: string; title: string }> = {
  '/manual': { docPath: '/docs/guides/用户手册.md', title: '用户手册 / User Manual' },
  '/manual/admin': { docPath: '/docs/guides/管理与开发者手册.md', title: '管理与开发者手册 / Admin & Developer Manual' },
}

/** 文档路径 → 路由映射（从 MANUAL_CONFIG 自动派生） */
const DOC_TO_ROUTE = Object.fromEntries(
  Object.entries(MANUAL_CONFIG).map(([route, cfg]) => [cfg.docPath, route])
)

/** 递归提取 React 节点中的纯文本 */
function extractText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(extractText).join('')
  if (node && typeof node === 'object' && 'props' in node) {
    return extractText((node as any).props.children)
  }
  return ''
}

/** 生成 GitHub 风格的标题锚点 ID */
function headingId(text: string): string {
  return text.toLowerCase()
    .replace(/<[^>]+>/g, '')
    .replace(/[^\w一-鿿]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

/** 动态生成带 id 的标题组件 */
function HeadingRenderer({ level, children, ...props }: any) {
  const text = extractText(children)
  const Tag = `h${level}` as keyof JSX.IntrinsicElements
  return <Tag id={headingId(text)} {...props}>{children}</Tag>
}

/** 自定义 a 标签：内部手册链接用 SPA 导航，外部链接新窗口打开 */
function DocLink({ href, children, ...props }: any) {
  const navigate = useNavigate()
  const handleClick = (e: React.MouseEvent) => {
    if (!href) return
    // 外部链接或锚点 → 默认行为
    if (href.startsWith('http') || href.startsWith('mailto') || href.startsWith('#')) return
    // 内部手册链接 → SPA 跳转
    e.preventDefault()
    // 取文件名部分：`guides/xxx.md` → `xxx.md`，再拼到 /docs/guides/
    const filename = href.split('/').pop() || href
    const route = DOC_TO_ROUTE[`/docs/guides/${filename}`]
    if (route) navigate(route)
  }
  return <a href={href} onClick={handleClick} {...props}>{children}</a>
}

/** 自定义 code 渲染：mermaid 代码块用 MermaidBlock，其余默认 */
function CodeRenderer({ className, children, inline, ...props }: any) {
  const match = /language-(\w+)/.exec(className || '')
  const code = String(children).replace(/\n$/, '')

  if (!inline && match && match[1] === 'mermaid') {
    return <MermaidBlock code={code} compact={false} />
  }

  // 默认 code 渲染
  if (inline) {
    return <code className={className}>{children}</code>
  }
  // 用 block <code> 替代 <pre>，避免 react-markdown 误嵌套在 <p> 中产生 HTML 规范错误
  return (
    <code className={`block overflow-x-auto whitespace-pre-wrap rounded-card bg-elevated border border-border p-4 text-xs ${className || ''}`}>
      {children}
    </code>
  )
}

export default function ManualPage() {
  const [content, setContent] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()
  const location = useLocation()
  // 正文容器：右侧「本页目录」从这个容器里读 h1–h4（标题的 id 由 HeadingRenderer 给）
  const docRef = useRef<HTMLDivElement>(null)

  const config = MANUAL_CONFIG[location.pathname] || MANUAL_CONFIG['/manual']

  useEffect(() => {
    fetch(config.docPath)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.text()
      })
      .then(text => {
        setContent(text)
        document.title = `${config.title} - Copree`
      })
      .catch(err => setError(err.message))
  }, [config.docPath, config.title])

  // Hash 导航滚动
  useEffect(() => {
    const hash = location.hash?.replace('#', '')
    if (hash && content) {
      queueMicrotask(() => document.getElementById(hash)?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    }
  }, [location.hash, content])

  return (
    <div className="mx-auto w-full max-w-[78rem] p-4 md:p-6 pb-24 md:pb-6 xl:flex xl:items-start xl:gap-10">
      <div ref={docRef} className="mx-auto w-full min-w-0 max-w-4xl">
        {/* 头部导航 */}
        <div className="flex items-center gap-3 mb-6">
          <button
            onClick={() => navigate(-1)}
            className="p-1.5 rounded-control hover:bg-elevated text-textSecondary hover:text-textPrimary transition-colors"
            title="返回"
          >
            <ArrowLeft size={20} />
          </button>
          <BookOpen size={20} className="text-primary-400" />
          <h1 className="text-lg font-bold text-textPrimary">{config.title}</h1>
        </div>

        {/* 内容区 */}
        {error ? (
          <div className="bg-rose-400/10 border border-rose-400/20 rounded-card p-6 text-center">
            <p className="text-rose-400 font-medium">加载失败</p>
            <p className="text-textMuted text-sm mt-1">{error}</p>
            <button
              onClick={() => window.open(config.docPath, '_blank')}
              className="mt-4 text-sm text-primary-400 hover:text-primary-500 transition-colors"
            >
              尝试直接打开文件 →
            </button>
          </div>
        ) : content === null ? (
          <div className="flex justify-center py-12">
            <Loader2 size={24} className="animate-spin text-textMuted" />
          </div>
        ) : (
          <div className="doc-content prose prose-sm md:prose-base max-w-none
            prose-headings:text-textPrimary prose-headings:font-semibold
            prose-h1:text-2xl prose-h1:mt-8 prose-h1:mb-4 prose-h1:pb-2 prose-h1:border-b prose-h1:border-border
            prose-h2:text-xl prose-h2:mt-6 prose-h2:mb-3
            prose-h3:text-lg prose-h3:mt-5 prose-h3:mb-2
            prose-p:text-textSecondary prose-p:leading-relaxed prose-p:my-3
            prose-a:text-primary-400 prose-a:no-underline hover:prose-a:text-primary-300
            prose-code:bg-elevated prose-code:text-accent-400 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-xs
            prose-pre:bg-elevated prose-pre:border prose-pre:border-border prose-pre:rounded-card
            prose-table:border prose-table:border-border
            prose-th:bg-elevated prose-th:text-textPrimary prose-th:px-3 prose-th:py-2 prose-th:text-xs prose-th:font-medium
            prose-td:px-3 prose-td:py-2 prose-td:text-sm prose-td:text-textSecondary
            prose-tr:border-b prose-tr:border-border
            prose-blockquote:border-l-2 prose-blockquote:border-primary-400 prose-blockquote:text-textMuted prose-blockquote:pl-4 prose-blockquote:italic
            prose-strong:text-textPrimary
            prose-li:text-textSecondary
            prose-hr:border-border
            [&_table]:w-full [&_table]:overflow-x-auto
            [&_table_th]:text-left
            [&_table_td]:text-left
            [&_img]:rounded-card [&_img]:max-w-full
          ">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                code: CodeRenderer,
                a: DocLink,
                h1: (p: any) => <HeadingRenderer level={1} {...p} />,
                h2: (p: any) => <HeadingRenderer level={2} {...p} />,
                h3: (p: any) => <HeadingRenderer level={3} {...p} />,
                h4: (p: any) => <HeadingRenderer level={4} {...p} />,
              }}
            >
              {content}
            </ReactMarkdown>
          </div>
        )}
      </div>
      <OutlineNav targetRef={docRef} />
    </div>
  )
}
