import { InputHTMLAttributes, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { MenuItem, MenuPanel } from './MenuPanel'

/**
 * 可输入的候选框（combobox）：候选一个不少地列出来，前缀命中的排前面
 *
 * 为什么不用 `<input list>` + `<datalist>`：浏览器会对候选做前缀过滤，把不匹配的整个藏起来——
 * 框里写着 deepseek-flash 时，下拉里就只剩它自己，看着像"只有这一个模型可选"。
 * 这里自己画浮层（复用站内 MenuPanel 配方）：候选全列，前缀命中的在前，其余在后；
 * 输入框仍可手填任意值——这两个框本来就允许写清单以外的模型名。
 */
export function sortByPrefix(options: string[], query: string): string[] {
  const unique = Array.from(new Set(options.filter(Boolean)))
  const q = query.trim().toLowerCase()
  if (!q) return unique
  const hit: string[] = []
  const rest: string[] = []
  for (const opt of unique) (opt.toLowerCase().startsWith(q) ? hit : rest).push(opt)
  return [...hit, ...rest]
}

interface ComboBoxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'> {
  value: string
  onValueChange: (value: string) => void
  options: string[]
  fieldSize?: 'sm' | 'md'
}

export default function ComboBox({
  value,
  onValueChange,
  options,
  fieldSize = 'md',
  className = '',
  ...rest
}: ComboBoxProps) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)
  const ordered = useMemo(() => sortByPrefix(options, value), [options, value])

  // 浮层是绝对定位、不算输入框的一部分，点外面只能听文档
  useEffect(() => {
    if (!open) return
    const onDocDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocDown)
    return () => document.removeEventListener('mousedown', onDocDown)
  }, [open])

  const commit = (next: string) => {
    onValueChange(next)
    setOpen(false)
  }

  return (
    <div ref={boxRef} className="relative">
      <input
        {...rest}
        type="text"
        value={value}
        onChange={(e) => { onValueChange(e.target.value); setOpen(true); setActive(0) }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setOpen(true)
            setActive(i => Math.min(i + 1, ordered.length - 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setActive(i => Math.max(i - 1, 0))
          } else if (e.key === 'Enter' && open && ordered[active]) {
            e.preventDefault()
            commit(ordered[active])
          } else if (e.key === 'Escape') {
            setOpen(false)
          }
        }}
        className={`field pr-8 ${fieldSize === 'sm' ? 'field-sm' : ''} ${className}`}
      />
      {/* 箭头只是个明确入口：候选本来 focus 就展开，不是唯一开关 */}
      <button
        type="button"
        tabIndex={-1}
        aria-label="展开候选"
        onClick={() => setOpen(o => !o)}
        className="icon-btn-sm absolute right-1 top-1/2 -translate-y-1/2"
      >
        <ChevronDown size={14} className="text-textMuted" />
      </button>
      {open && ordered.length > 0 && (
        <MenuPanel role="listbox" className="absolute inset-x-0 top-full z-overlay mt-1 max-h-56 overflow-auto py-1">
          {ordered.map((opt, i) => (
            <MenuItem
              key={opt}
              role="option"
              active={i === active}
              aria-selected={opt === value}
              onMouseEnter={() => setActive(i)}
              // 用 mousedown 抢先于 input 的失焦：blur 先关浮层的话这一下就点空了
              onMouseDown={(e) => { e.preventDefault(); commit(opt) }}
              className="truncate font-mono"
            >
              {opt}
            </MenuItem>
          ))}
        </MenuPanel>
      )}
    </div>
  )
}
