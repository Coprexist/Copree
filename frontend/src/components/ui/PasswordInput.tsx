import { forwardRef, InputHTMLAttributes, useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { useT } from '../../i18n/I18nContext'

/**
 * 真·账号密码输入框：原生 type="password" + 「显示 / 隐藏」眼睛按钮。
 *
 * 与 SecretInput 的分工是**故意的**，不是重复实现：
 * - 这里要的就是浏览器密码管理器那套（记住密码、自动填充、提交后提示保存）——
 *   所以必须是原生 password；
 * - SecretInput 给 API Key / Token 用，它反而不想要密码管理器（密钥框旁边冒出
 *   「保存密码」的钥匙图标会让人以为自己存的账号被填进来了），所以那边是
 *   type="text" + -webkit-text-security 纯视觉遮蔽。
 *
 * 眼睛按钮与 SecretInput 同一套样式（icon-btn / icon-btn-pill），位置也一致，
 * 两处并排出现时不会一个偏左一个偏右。
 */
interface PasswordInputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** 尺寸档：sm=32 / md=32（默认，与 .field 同档）/ lg=40（表单主输入） */
  fieldSize?: 'sm' | 'md' | 'lg'
  /** 布局类（w-full 这类要挂在外层包裹 div 上） */
  wrapperClassName?: string
}

const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(function PasswordInput(
  { fieldSize = 'md', wrapperClassName = '', className = '', ...rest }, ref,
) {
  const t = useT()
  const [visible, setVisible] = useState(false)
  const large = fieldSize === 'lg'
  const size = fieldSize === 'sm' ? 'field-sm' : large ? 'field-lg' : ''
  const label = visible ? t('common:hide') : t('common:show')
  // 眼睛按框高选档：40px 的框里放 24px 的小钮会显得又小又贴边（登录页实测），
  // 换 28px 并内移 2px，弧线两侧的留白才对得上；32px 的框维持 24px 不变。
  const eyeBtn = large
    ? 'icon-btn icon-btn-quiet icon-btn-sm icon-btn-pill absolute right-1.5 top-1/2 -translate-y-1/2'
    : 'icon-btn icon-btn-quiet icon-btn-xs icon-btn-pill absolute right-1 top-1/2 -translate-y-1/2'

  // 眼睛默认不出现：空框上常挂一只眼睛既吵又暗示"这里有东西"。
  // 悬停 / 聚焦（点进来）/ 键盘聚焦到按钮本身时才淡入。
  // 例外是**明文态**——那必须常显，否则用户看不出密码正暴露在屏幕上。
  const eyeVisibility = visible
    ? 'opacity-100'
    : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100'

  return (
    <div className={'group relative ' + wrapperClassName}>
      {/* ui-standard-ignore: 真·账号密码，要交给浏览器密码管理器（见文件头） */}
      <input
        {...rest}
        ref={ref}
        type={visible ? 'text' : 'password'}
        className={'field ' + size + ' field-with-icon-right ' + className}
      />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        aria-label={label}
        title={label}
        aria-pressed={visible}
        // 明文时图标转深一档：不只靠「眼睛有没有斜线」来表状态，扫一眼就知道现在是显示还是遮蔽
        className={eyeBtn + ' transition-opacity ' + eyeVisibility + (visible ? ' text-textPrimary' : '')}
      >
        {visible ? <EyeOff size={large ? 16 : 14} /> : <Eye size={large ? 16 : 14} />}
      </button>
    </div>
  )
})

export default PasswordInput
