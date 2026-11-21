/**
 * 页面标题栏（全站唯一顶端规范）
 *
 * 单一来源：所有页面的顶端一律用它，不再手写 h-14 border-b 的 div。
 * 规格：高 56px · bg-surface · 底部 1px 描边 · 标题 14px 半粗 · 副标题 12px 次要色
 * 用法：配 PageShell 使用（见 ui/PageShell.tsx），右侧操作区作为 children。
 */
import type { ReactNode } from 'react'
import { ChevronLeft } from 'lucide-react'
import IconButton from './IconButton'

interface PageHeaderProps {
  title: ReactNode
  subtitle?: ReactNode
  onBack?: () => void
  backLabel?: string
  /** 左侧前置节点（移动端菜单键等） */
  leading?: ReactNode
  children?: ReactNode
}

export default function PageHeader({ title, subtitle, onBack, backLabel = '返回', leading, children }: PageHeaderProps) {
  return (
    <header className="top-bar px-4 h-14 flex items-center gap-2 shrink-0">
      {leading}
      {onBack && (
        <IconButton
          icon={<ChevronLeft size={16} />}
          label={backLabel}
          size="sm"
          onClick={onBack}
          className="-ml-1.5"
        />
      )}
      <h1 className="font-semibold text-textPrimary text-sm truncate">{title}</h1>
      {subtitle && <span className="text-xs text-textMuted hidden sm:inline truncate">{subtitle}</span>}
      <div className="ml-auto flex items-center gap-2 shrink-0">{children}</div>
    </header>
  )
}
