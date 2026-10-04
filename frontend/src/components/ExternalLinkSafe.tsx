import { useState, type ReactNode, type MouseEvent } from 'react'
import { ExternalLink, ShieldAlert } from 'lucide-react'
import { useT } from '../i18n/I18nContext'
import { Dialog } from './ui'

interface ExternalLinkSafeProps {
  href: string
  children: ReactNode
  className?: string
  /** 如果为 true，跳过确认弹窗（内部路由用） */
  skipConfirm?: boolean
}

/**
 * 安全外部链接：打开前弹出确认弹窗，防止用户无意中离开网站。
 * 用于所有 target="_blank" 的外部链接。
 */
export default function ExternalLinkSafe({ href, children, className, skipConfirm }: ExternalLinkSafeProps) {
  const t = useT()
  const [showConfirm, setShowConfirm] = useState(false)

  const handleClick = (e: MouseEvent) => {
    if (skipConfirm) return
    e.preventDefault()
    setShowConfirm(true)
  }

  const handleConfirm = () => {
    setShowConfirm(false)
    window.open(href, '_blank', 'noopener,noreferrer')
  }

  if (skipConfirm) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className={className}>
        {children}
      </a>
    )
  }

  return (
    <>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className={className}
        onClick={handleClick}
      >
        {children}
        <ExternalLink size={10} className="inline ml-0.5 opacity-50" />
      </a>

      {/* 确认弹窗 */}
      {showConfirm && (
        <Dialog onClose={() =>  setShowConfirm(false)} className="flex items-center justify-center">
          <div
            className="bg-surface rounded-dialog border border-border w-full max-w-sm mx-4 shadow-2xl"
            onClick={e => e.stopPropagation()}
          >
            <div className="p-5">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-full bg-accent-400/10 flex items-center justify-center shrink-0">
                  <ShieldAlert size={20} className="text-accent-400" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-textPrimary">{t('externalLink:title')}</h3>
                  <p className="text-xs text-textMuted mt-0.5">{t('externalLink:hint')}</p>
                </div>
              </div>

              <div className="bg-canvas border border-border rounded-card px-3 py-2.5 mb-4">
                <p className="text-xs text-textPrimary font-mono break-all">{href}</p>
              </div>

              <p className="text-xs text-textMuted mb-4">
                {t('externalLink:warning')}
              </p>
            </div>

            <div className="flex border-t border-border">
              <button
                onClick={() => setShowConfirm(false)}
                className="flex-1 py-3 text-sm text-textSecondary hover:text-textPrimary font-medium transition-colors rounded-bl-2xl"
              >
                {t('common:cancel')}
              </button>
              <button
                onClick={handleConfirm}
                className="flex-1 py-3 text-sm text-primary-400 hover:text-primary-500 font-semibold border-l border-border transition-colors rounded-br-2xl"
              >
                {t('externalLink:confirm')}
              </button>
            </div>
          </div>
        </Dialog>
      )}
    </>
  )
}
