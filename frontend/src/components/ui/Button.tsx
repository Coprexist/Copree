import { ButtonHTMLAttributes, ReactNode } from 'react'
import { Loader2 } from 'lucide-react'

/**
 * 统一 Button 组件
 *
 * 样式来自 CSS 语义类（.btn .btn-md .btn-primary 等，见 index.css）——
 * 与手写按钮共享同一规范源，一处定义全站统一。
 *
 * 变体：primary / accent / secondary / outline / ghost / danger / success / link
 * 尺寸：xs(24px) / sm(32px) / md(40px) / lg(48px) —— 固定高度，任何内容严格等高
 *       （link 是例外：文字按钮没有底色与固定高度）
 */
type Variant =
  | 'primary' | 'accent' | 'secondary' | 'outline' | 'ghost' | 'danger' | 'success' | 'link'
  | 'outline-danger' | 'tinted' | 'tinted-accent' | 'tinted-danger' | 'tinted-success'
type Size = 'xs' | 'sm' | 'md' | 'lg'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  icon?: ReactNode
  children?: ReactNode
}

const VARIANT_CLASS: Record<Variant, string> = {
  primary: 'btn-primary',
  accent: 'btn-accent',
  secondary: 'btn-secondary',
  outline: 'btn-outline',
  'outline-danger': 'btn-outline-danger',
  ghost: 'btn-ghost',
  danger: 'btn-danger',
  success: 'btn-success',
  link: 'btn-link',
  tinted: 'btn-tinted',
  'tinted-accent': 'btn-tinted-accent',
  'tinted-danger': 'btn-tinted-danger',
  'tinted-success': 'btn-tinted-success',
}

const SIZE_CLASS: Record<Size, string> = {
  xs: 'btn-xs',
  sm: 'btn-sm',
  md: 'btn-md',
  lg: 'btn-lg',
}

const ICON_SIZE: Record<Size, number> = { xs: 12, sm: 14, md: 16, lg: 18 }

export default function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  icon,
  children,
  className = '',
  disabled,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={`btn ${VARIANT_CLASS[variant]} ${SIZE_CLASS[size]} ${className}`}
      {...rest}
    >
      {loading ? <Loader2 size={ICON_SIZE[size]} className="animate-spin" /> : icon}
      {children}
    </button>
  )
}
