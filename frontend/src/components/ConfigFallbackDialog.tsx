/**
 * 「这里需要一份可用的模型配置」—— 全站统一的兜底对话框。
 *
 * 为什么抽出来：缺配置 / 额度用光的地方不止一处（智能整理、创建助手、世界 AI 调度…），
 * 每处各写一个弹窗，文案会分叉、入口会漂移，最后用户看到的是「有的地方能去配、有的地方只能干瞪眼」。
 * 这里只做一件事：把原因说清，给一个去设置默认配置的按钮。判定仍在后端 resolver（单一来源），
 * 组件只负责展示它给的原因码。
 *
 * 用法：拿到后端返回的 needs_config.reason 就挂上它，不用自己判断是哪种缺法。
 */
import { useNavigate } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import { useT } from '../i18n/I18nContext'
import { Button, Dialog } from './ui'

export interface NeedsConfig {
  reason: string
  chain?: Array<Record<string, unknown>>
}

/** 原因码 → 文案 key。resolver 的词表是唯一来源，这里只做翻译。 */
const REASON_KEY: Record<string, string> = {
  no_quota: 'configFallback:reasonNoQuota',
  no_member: 'configFallback:reasonNoQuota',
  no_default: 'configFallback:reasonNoDefault',
  no_key: 'configFallback:reasonNoKey',
  bad_key: 'configFallback:reasonBadKey',
  bad_bind: 'configFallback:reasonBadBind',
  not_configured: 'configFallback:reasonNotConfigured',
}

export default function ConfigFallbackDialog({ needs, what, onClose }: {
  needs: NeedsConfig | null
  /** 正在做的事（「智能整理」），拼进一句话里 */
  what?: string
  onClose: () => void
}) {
  const t = useT()
  const navigate = useNavigate()
  if (!needs) return null

  const reasonKey = REASON_KEY[needs.reason] || 'configFallback:reasonNotConfigured'

  return (
    <Dialog onClose={onClose} className="flex items-center justify-center">
      <div
        className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-sm mx-4 shadow-2xl shadow-black/30"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-2">
          <AlertTriangle size={18} className="mt-0.5 shrink-0 text-accent-400" />
          <div>
            <h2 className="text-base font-semibold text-textPrimary">{t('configFallback:title')}</h2>
            <p className="mt-1 text-sm text-textSecondary">
              {t(reasonKey)}
            </p>
            <p className="mt-1.5 text-xs text-textMuted">
              {t('configFallback:hint')}{what ? '（' + what + '）' : ''}
            </p>
          </div>
        </div>

        <div className="mt-5 flex gap-2">
          <Button size="sm" variant="outline" className="flex-1" onClick={onClose}>
            {t('configFallback:later')}
          </Button>
          <Button
            size="sm"
            className="flex-1"
            onClick={() => { onClose(); navigate('/settings?tab=api') }}
          >
            {t('configFallback:go')}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
