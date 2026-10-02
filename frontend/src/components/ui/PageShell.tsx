/**
 * 页面骨架（全站唯一布局入口）
 *
 * 统一三件事，页面本身不再重复：
 *   1. 根容器 h-full flex flex-col bg-canvas
 *   2. 顶端 PageHeader（标题/副标题/返回/右侧操作）
 *   3. 内容区滚动 + 居中等宽（宽度档位见 WIDTH）
 *
 * 宽度档位是唯一的页面留白来源：
 *   narrow  max-w-xl   表单类（新建、发布）
 *   content max-w-3xl  单列内容（我的、用量、群视界列表）
 *   wide    max-w-4xl  卡片网格 / 表格（AI、商城、后台）
 *   full    不设上限    双栏页面（好友、设置、聊天）
 */
import type { ReactNode } from 'react'
import PageHeader from './PageHeader'

export type Width = 'narrow' | 'content' | 'wide' | 'full'

/** 宽度档位只此一份：页面（PageShell）与控制台页签（ConsolePage）都从这里取 */
export const PAGE_WIDTH: Record<Width, string> = {
  narrow: 'max-w-xl',
  content: 'max-w-3xl',
  wide: 'max-w-4xl',
  full: '',
}

interface PageShellProps {
  title: ReactNode
  subtitle?: ReactNode
  onBack?: () => void
  leading?: ReactNode
  actions?: ReactNode
  width?: Width
  /** 内容区附加类名（如 space-y-5） */
  contentClassName?: string
  /** 内容区自带内边距与滚动（双栏/整幅页面用） */
  flush?: boolean
  children: ReactNode
}

export default function PageShell({
  title,
  subtitle,
  onBack,
  leading,
  actions,
  width = 'content',
  contentClassName = 'space-y-5',
  flush = false,
  children,
}: PageShellProps) {
  return (
    <div className="h-full flex flex-col bg-canvas">
      <PageHeader title={title} subtitle={subtitle} onBack={onBack} leading={leading}>
        {actions}
      </PageHeader>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {flush ? (
          children
        ) : (
          <div className={`${PAGE_WIDTH[width]} mx-auto px-4 py-4 md:px-6 md:py-6 pb-24 md:pb-6 ${contentClassName}`}>
            {children}
          </div>
        )}
      </div>
    </div>
  )
}
