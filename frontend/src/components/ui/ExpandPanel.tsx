import type { ReactNode } from 'react'
import { X } from 'lucide-react'

/**
 * 展开详情面板 —— 列表里"就地展开"的那块详情（技能背包、插件页共用）
 *
 * 为什么是就地展开而不是弹窗/抽屉：站内既有参照（技能背包）就是列表 + 就地展开，
 * 全站统一成一种交互，用户学一次就够；表单住在这里，列表行高就永远是固定的。
 */
export default function ExpandPanel({ title, meta, collapseLabel, onCollapse, children }: {
  title: ReactNode
  /** 标题右侧的补充信息（分类、版本等），由调用方组装 */
  meta?: ReactNode
  collapseLabel: string
  onCollapse: () => void
  children: ReactNode
}) {
  return (
    <div className="bg-surface rounded-card border border-primary-500/20 p-4 space-y-3 shadow-lg">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap min-w-0">
          <p className="text-xs font-semibold text-textPrimary truncate">{title}</p>
          {meta}
        </div>
        <button
          onClick={onCollapse}
          className="flex items-center gap-1 text-2xs text-textMuted hover:text-textSecondary transition-colors shrink-0"
        >
          {collapseLabel} <X size={12} />
        </button>
      </div>
      {children}
    </div>
  )
}
