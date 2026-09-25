import { ReactNode, SelectHTMLAttributes, useId } from 'react'
import { ChevronDown } from 'lucide-react'

/**
 * 统一 Select 下拉组件
 *
 * 视觉来自 .field 语义类（index.css），与 Input 完全一致。
 * 支持 label 与 hint 说明（小白友好）。
 */
interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: ReactNode
  hint?: ReactNode
  options: { value: string; label: ReactNode }[]
  placeholder?: string
  fieldSize?: 'sm' | 'md'
}

export default function Select({
  label,
  hint,
  options,
  placeholder,
  fieldSize = 'md',
  className = '',
  id,
  ...rest
}: SelectProps) {
  const autoId = useId()
  const selectId = id || (label ? `select-${autoId}` : undefined)

  return (
    <div className="space-y-1.5">
      {label && (
        <label htmlFor={selectId} className="block text-sm font-medium text-textSecondary">
          {label}
        </label>
      )}
      {/* 原生下拉的箭头各浏览器长得都不一样：appearance-none 掉，换自己的 SVG，全站一个样 */}
      <div className="relative">
        <select
          id={selectId}
          className={`field appearance-none pr-8 ${fieldSize === 'sm' ? 'field-sm' : ''} ${className}`}
          {...rest}
        >
          {placeholder && <option value="">{placeholder}</option>}
          {options.map((opt) => (
            <option key={String(opt.value)} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <ChevronDown
          size={fieldSize === 'sm' ? 14 : 16}
          className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-textMuted"
        />
      </div>
      {hint && <p className="text-xs text-textMuted">{hint}</p>}
    </div>
  )
}
