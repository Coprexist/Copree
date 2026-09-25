import React from 'react'

interface ToggleProps {
  /** 当前是否开启 */
  checked: boolean
  /** 切换回调 */
  onChange: (checked: boolean) => void
  /** 禁用状态 */
  disabled?: boolean
  /** 标签文本（可选，显示在开关左侧） */
  label?: string
  /** 尺寸：md=设置页等常规位置（48×24）；sm=挤满小控件的工具条（32×16） */
  size?: 'md' | 'sm'
  /** 无文字标签时的可访问名称（列表行里的开关靠它说明"开的是什么"） */
  ariaLabel?: string
}

/**
 * 统一开关/Toggle 组件
 *
 * 统一规格（与 OpenCLI 开关一致）：
 * - 轨道 48×24px（w-12 h-6），sm 档 32×16（w-8 h-4）
 * - 滑块与轨道内高同高（20×20 / 12×12）——这样不用 flex 居中也不会偏
 * - 开启 bg-mint-400，关闭 bg-border
 * - 白色滑块 + shadow
 *
 * 加 size 而不是让调用方各写一个开关：滑块的轨道/滑块/位移必须成套改，
 * 复制出去迟早出现"有个地方的开关看起来不一样"。
 */
const Toggle: React.FC<ToggleProps> = ({ checked, onChange, disabled = false, label, size = 'md', ariaLabel }) => {
  const small = size === 'sm'
  const track = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel || label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`${small ? 'w-8 h-4' : 'w-12 h-6'} rounded-full transition-colors shrink-0 border-2 border-border ${
        disabled ? 'opacity-40 cursor-not-allowed' : ''
      } ${checked ? 'bg-mint-400 border-mint-400' : 'bg-canvas'}`}
    >
      <div
        className={`${small ? 'w-3 h-3 translate-x-0' : 'w-5 h-5'} bg-white rounded-full shadow transition-transform ${
          checked ? (small ? 'translate-x-4' : 'translate-x-6') : (small ? 'translate-x-0' : 'translate-x-0.5')
        }`}
      />
    </button>
  )

  if (label) {
    return (
      <div className="flex items-center gap-3">
        <span className="text-sm font-medium text-textPrimary">{label}</span>
        {track}
      </div>
    )
  }

  return track
}

export default Toggle
