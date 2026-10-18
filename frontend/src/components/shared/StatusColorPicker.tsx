/**
 * 状态颜色选择器 —— 预设色圆点 + 自定义取色，全站共用这一份（AI 设置弹窗、AI 详情页、
 * 我的、设置向导原来各抄了一遍，连自定义那格方角的毛病都抄了四份）。
 *
 * 自定义那格不能拿 <input type="color"> 直接当圆点：原生控件里那块颜色是它自己的 swatch，
 * 在外层套 rounded-full 只圆了控件本身，方块照样露在圆里。这里改成自己画一个圆（底色 = 当前颜色），
 * 中间一个笔图标表示可以点，原生取色器铺满整格、透明，只负责弹出系统面板。
 */
import { Pen, X } from 'lucide-react'
import { STATUS_COLORS } from '../../utils/statusColor'
import { useT } from '../../i18n/I18nContext'

interface Props {
  value: string
  onChange: (value: string) => void
  /** 自定义取色器「调完」时再叫一次（详情页要在这里落库；只改本地状态的页面不用传） */
  onCommit?: (value: string) => void
  /** 行容器类，默认与原来各处一致 */
  className?: string
}

const SWATCH = 'w-6 h-6 rounded-full border-2 transition-all'

export default function StatusColorPicker({ value, onChange, onCommit, className }: Props) {
  const t = useT()
  // 选的颜色不在预设里：自定义那格自己亮起来（原来它永远是灰圈，看不出是当前选中）
  const custom = value !== '' && !STATUS_COLORS.some(color => color.value === value)

  return (
    <div className={className ?? 'flex items-center gap-2 flex-wrap'}>
      {STATUS_COLORS.map(color => (
        <button
          key={color.value || 'none'}
          type="button"
          onClick={() => onChange(color.value)}
          className={`${SWATCH} ${
            value === color.value
              ? 'border-primary-400 scale-110 shadow-md'
              : color.value === ''
                ? 'border-border bg-canvas hover:border-textMuted'
                : 'border-transparent hover:scale-105'
          }`}
          style={color.value ? { backgroundColor: color.value } : undefined}
          title={color.label}
        >
          {color.value === '' && <X size={10} className="text-textMuted m-auto" />}
        </button>
      ))}
      <label
        className={`${SWATCH} relative overflow-hidden cursor-pointer ${
          custom ? 'border-primary-400 scale-110 shadow-md' : 'border-border hover:border-primary-400'
        }`}
        style={{ backgroundColor: value || '#6366f1' }}
        title={t('me:statusColorCustom')}
      >
        <span className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <Pen size={10} className="text-white/70 drop-shadow" />
        </span>
        <input
          type="color"
          value={value || '#6366f1'}
          onChange={e => onChange(e.target.value)}
          onBlur={e => onCommit?.(e.target.value)}
          aria-label={t('me:statusColorCustom')}
          className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
        />
      </label>
    </div>
  )
}
