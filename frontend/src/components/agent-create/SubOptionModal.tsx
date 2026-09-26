import { ArrowLeft, X } from 'lucide-react'
import { useT } from '../../i18n/I18nContext'
import { CARD_ICONS, SUB_OPTIONS } from './presets'
import { PresetIcon, SubIcon } from './fields'
import type { PresetData } from './types'

/** 子档弹窗：选中后关闭并由主弹窗接管动画与表单填充 */
export default function SubOptionModal({
  preset, selectedSub, onSelect, onClose,
}: {
  preset: PresetData
  selectedSub: string | null
  onSelect: (subId: string) => void
  onClose: () => void
}) {
  const t = useT()
  const icon = CARD_ICONS[preset.key]
  const subOptions = SUB_OPTIONS[preset.key] || []
  return (
    <div className="fixed inset-0 md:bg-black/60 flex items-center justify-center z-toast bg-surface" onClick={onClose}>
      <div
        className="bg-elevated border border-border rounded-none md:rounded-dialog p-6 w-full max-w-full md:max-w-md mx-0 md:mx-4 shadow-2xl shadow-black/30 md:animate-pop-in h-full md:h-auto flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 移动端头部 */}
        <div className="flex items-center justify-between mb-3 md:hidden shrink-0">
          <button onClick={onClose} className="icon-btn-sm -ml-1 text-textSecondary">
            <ArrowLeft size={20} />
          </button>
          <div className="flex items-center gap-2">
            <span className="text-xl"><PresetIcon name={icon.icon} /></span>
            <h2 className="text-sm font-semibold text-textPrimary">{t(preset.nameKey)}</h2>
          </div>
          <div className="w-6" />
        </div>

        {/* 桌面端头部 */}
        <div className="hidden md:flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <span className="text-2xl"><PresetIcon name={icon.icon} /></span>
            <h2 className="text-base font-semibold text-textPrimary">{t(preset.nameKey)}</h2>
          </div>
          <button onClick={onClose} className="text-textMuted hover:text-textSecondary transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* 可滚动内容 */}
        <div className="flex-1 overflow-y-auto md:overflow-visible pb-[var(--safe-bottom)] md:pb-0">
        <p className="text-xs text-textMuted mb-4">{t(preset.descKey)}</p>
        <p className="text-xs text-textMuted mb-4 italic text-center bg-canvas/50 rounded-control py-2">
          {t('modal.createAgentPresetHint')}
        </p>
        <div className="space-y-3">
          {subOptions.map(sub => (
            <button
              key={sub.id}
              onClick={() => onSelect(sub.id)}
              className={`w-full text-left p-4 rounded-card border transition-all duration-150
                ${selectedSub === sub.id
                  ? 'border-primary-400/60 bg-primary-500/10 shadow-md shadow-primary-500/5'
                  : 'border-border/50 bg-elevated hover:border-primary-500/30 hover:bg-canvas'
                }`}
            >
              <div className="flex items-start gap-3">
                <span className="text-2xl flex-shrink-0"><SubIcon name={sub.icon} /></span>
                <div>
                  <span className="text-sm font-semibold text-textPrimary">{t(sub.nameKey)}</span>
                  <p className="text-xs text-textSecondary mt-1 leading-relaxed">{t(sub.descKey)}</p>
                </div>
              </div>
            </button>
          ))}
        </div>
        </div>
      </div>
    </div>
  )
}
