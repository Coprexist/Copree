/**
 * 页面骨架（全站唯一布局入口）
 *
 * 统一三件事，页面本身不再重复：
 *   1. 根容器 h-full flex flex-col bg-canvas
 *   2. 顶端 PageHeader（标题/副标题/返回/右侧操作）
 *   3. 内容区滚动 + 居中等宽（宽度档位见 WIDTH）
 *
 * 宽度档位（pageWidth.ts 是唯一定义处）只给默认留白：使用者拖右缘手柄可以按自己的屏临时调宽，
 * 双击手柄回档位默认。
 */
import type { ReactNode } from 'react'
import PageHeader from './PageHeader'
import ContentColumn from './ContentColumn'
import { PAGE_WIDTH, type Width } from './pageWidth'

// 档位表在 pageWidth.ts（内容列也要用，放这里会绕成循环依赖）；这里转出去供既有调用方取
export { PAGE_WIDTH, type Width }

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
          <ContentColumn width={width} className={`px-4 py-4 md:px-6 md:py-6 pb-24 md:pb-6 ${contentClassName}`}>
            {children}
          </ContentColumn>
        )}
      </div>
    </div>
  )
}
