import type { ReactNode } from 'react'

/**
 * 列表面板 —— 管理页里"紧凑列表"的统一外壳
 *
 * 单一来源：卡片边框/圆角/内边距、表头、分隔线、空态以前在插件页、命令白名单、
 * 技能背包各写一遍（连类名都是抄的）。定在这里之后改一次全站生效。
 *
 * 配套的展开详情用 ExpandPanel —— 两者合起来就是站内的统一模式：
 * **紧凑列表给状态与动作，详情就地展开**（不弹窗、不摊表单在行里）。
 */
export interface ListColumn {
  key: string
  label: ReactNode
  /** 额外单元格类（宽度、对齐等） */
  className?: string
}

interface ListPanelProps {
  /** 表头列；不给则不渲染 thead（纯行列表） */
  columns?: ListColumn[]
  /** 标题（卡片内的 h3，如「命令白名单」） */
  title?: ReactNode
  /** 表头上方的工具条：搜索、筛选、批量操作 */
  toolbar?: ReactNode
  /** tbody 内容（<tr> 列表） */
  children: ReactNode
  /** 无数据时的内容（渲染在 tbody 里，占满一行） */
  empty?: ReactNode
  className?: string
}

export default function ListPanel({ columns, title, toolbar, children, empty, className = '' }: ListPanelProps) {
  return (
    <div className={`bg-surface rounded-card border border-border p-5 ${className}`}>
      {title && <h3 className="font-semibold mb-3 text-textPrimary">{title}</h3>}
      {toolbar && <div className="mb-3 flex items-center gap-2 flex-wrap">{toolbar}</div>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-textPrimary">
          {columns && columns.length > 0 && (
            <thead>
              <tr className="border-b border-border">
                {columns.map(col => (
                  <th key={col.key} className={`text-left py-2 px-3 font-medium text-textSecondary whitespace-nowrap ${col.className || ''}`}>
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
          )}
          <tbody>
            {children}
            {empty && (
              <tr>
                <td colSpan={Math.max(columns?.length || 1, 1)} className="py-6">
                  {empty}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** 行的统一样式：分隔线 + 单元格内边距。用了它就别再手写 border-b border-border/50 */
export const LIST_ROW_CLASS = 'border-b border-border/50'
/** 单元格统一样式 */
export const LIST_CELL_CLASS = 'py-2 px-3'
