/**
 * 桌面端「有新版本」提示条。
 *
 * 只在安装包发行版（distribution=desktop）里出现，且只对管理员显示——网页版
 * 的用户改不了服务器，给他们看升级提示只是噪音；网页版的管理员则在「管理面板 →
 * 通用」里看到完整卡片（这就是用户要的分工）。
 *
 * 交互取向：**不打断**。它是一条贴在内容区顶部的细条，不遮挡、不自动跳转、
 * 可按版本号「忽略」，忽略后同版本不再出现（localStorage，键里带版本号，
 * 下一个版本会重新提示）。
 */
import { useState } from 'react'
import { ArrowUpCircle, X } from 'lucide-react'
import { useT } from '../i18n/I18nContext'
import { useDistribution } from '../hooks/useAppVersion'
import { useUpdateStatus } from '../hooks/useUpdateStatus'

const DISMISS_KEY = 'copree.update.dismissed'

export default function UpdateBanner({ enabled }: { enabled: boolean }) {
  const t = useT()
  const distribution = useDistribution()
  const show = enabled && distribution === 'desktop'
  const { status } = useUpdateStatus(show)
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(DISMISS_KEY) || '')

  if (!show || !status?.has_update) return null
  if (dismissed === status.latest) return null

  const target = status.download_url || status.release_url
  const open = () => {
    if (target) window.open(target, '_blank', 'noopener,noreferrer')
  }
  const dismiss = () => {
    localStorage.setItem(DISMISS_KEY, status.latest)
    setDismissed(status.latest)
  }

  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-border bg-elevated px-4 py-2 text-xs">
      <ArrowUpCircle size={14} className="shrink-0 text-accent-400" />
      <span className="min-w-0 truncate text-textSecondary">
        {t('update:bannerText', { version: status.latest ? 'v' + status.latest : '' })}
      </span>
      <button
        type="button"
        onClick={open}
        className="shrink-0 font-medium text-primary-400 transition-colors hover:text-primary-300"
      >
        {t('update:bannerAction')}
      </button>
      <button
        type="button"
        onClick={dismiss}
        aria-label={t('update:dismiss')}
        title={t('update:dismiss')}
        className="ml-auto shrink-0 p-0.5 text-textMuted transition-colors hover:text-textPrimary"
      >
        <X size={14} />
      </button>
    </div>
  )
}
