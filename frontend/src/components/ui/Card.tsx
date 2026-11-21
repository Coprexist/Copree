import { ReactNode } from 'react'

/**
 * 统一 Card 区块组件
 *
 * 样式来自 .card / .card-pad-lg 语义类（index.css），与手写卡片同一规范源。
 * action 是标题行右侧的动作槽（开关、状态徽标、右上角按钮），
 * 免得每个设置卡片各写一遍 flex 头。
 */
interface CardProps {
  title?: ReactNode
  icon?: ReactNode
  hint?: ReactNode
  action?: ReactNode
  children?: ReactNode
  className?: string
}

export default function Card({ title, icon, hint, action, children, className = '' }: CardProps) {
  return (
    <section className={`card card-pad-lg space-y-4 ${className}`}>
      {(title || hint || action) && (
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            {title && (
              <h3 className="text-sm font-semibold text-textPrimary flex items-center gap-2">
                {icon}
                {title}
              </h3>
            )}
            {hint && <p className={`text-xs text-textMuted ${title ? 'mt-1' : ''}`}>{hint}</p>}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </div>
      )}
      {children}
    </section>
  )
}
