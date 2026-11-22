import { InputHTMLAttributes, ReactNode, useId, useMemo, useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { useT } from '../../i18n/I18nContext'

/**
 * 密钥 / API Key / Token 输入框（全站唯一实现）
 *
 * 为什么不写 type="password"：
 * 浏览器会把 type=password 当成「账号密码」——自动填入你保存的用户名密码、
 * 提交后弹「是否保存密码」、密钥框旁边冒出密码管理器的钥匙图标。对一串 API Key
 * 来说这三件事都是误导，而且用户会以为自己存的账号被填进去了。
 *
 * 做法：始终 type="text"，遮蔽靠 -webkit-text-security: disc（纯视觉，浏览器不认为是密码），
 * 再关掉 autocomplete / 1Password / LastPass / Bitwarden 的自动填充挂件。
 * 只有极少数不支持 -webkit-text-security 的浏览器才退回 type=password（并关闭自动填充）。
 *
 * 真·账号密码（登录、改密、创建用户）仍用原生 type="password"。
 * 新代码一律用 SecretInput，不要在密钥框上写 type="password"。
 */
interface SecretInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  /** 尺寸档位：默认 32px 胶囊（与 .btn-sm 同行等高） */
  fieldSize?: 'sm' | 'md' | 'lg'
  /** 外层的布局类（flex-1 / w-full 这类要挂在包裹层上，不是 input 上） */
  wrapperClassName?: string
  label?: ReactNode
  hint?: ReactNode
}

export default function SecretInput({
  fieldSize = 'md',
  wrapperClassName = '',
  className = '',
  label,
  hint,
  id,
  ...rest
}: SecretInputProps) {
  const t = useT()
  const autoId = useId()
  const inputId = id || (label ? 'secret-' + autoId : undefined)
  const [visible, setVisible] = useState(false)
  // 只算一次：能不能用 CSS 遮蔽（不支持时退回原生 password）
  const canMask = useMemo(
    () => typeof CSS !== 'undefined' && CSS.supports('(-webkit-text-security: disc)'),
    [],
  )
  const masked = !visible
  const size = fieldSize === 'sm' ? 'field-sm' : fieldSize === 'lg' ? 'field-lg' : ''

  const field = (
    <div className={'relative ' + wrapperClassName}>
      <input
        {...rest}
        id={inputId}
        type={masked && !canMask ? 'password' : 'text'}
        name={rest.name || 'secret'}
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        data-1p-ignore="true"
        data-lpignore="true"
        data-bwignore="true"
        className={'field ' + size + ' field-with-icon-right ' + className}
        style={masked && canMask ? ({ WebkitTextSecurity: 'disc' } as React.CSSProperties) : undefined}
      />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        aria-label={visible ? t('common:hide') : t('common:show')}
        title={visible ? t('common:hide') : t('common:show')}
        aria-pressed={visible}
        className={'icon-btn icon-btn-quiet icon-btn-xs icon-btn-pill absolute right-1 top-1/2 -translate-y-1/2'
          + (visible ? ' text-textPrimary' : '')}
      >
        {visible ? <EyeOff size={14} /> : <Eye size={14} />}
      </button>
    </div>
  )

  if (!label && !hint) return field
  return (
    <div className="space-y-1.5">
      {label && (
        <label htmlFor={inputId} className="block text-sm font-medium text-textSecondary">
          {label}
        </label>
      )}
      {field}
      {hint && <p className="text-xs text-textMuted">{hint}</p>}
    </div>
  )
}
