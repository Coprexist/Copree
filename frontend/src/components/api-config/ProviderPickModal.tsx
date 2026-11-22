/**
 * 「填厂商默认 Key」的厂商选择弹窗。
 *
 * 为什么单独一个弹窗而不是塞在底部那排的下拉里：这里要同时列两类来源——
 * 自己接入过的厂商，和平台内置但还没接入的。后者点进去是"接入 + 填 Key"，
 * 属于一次性动作，用弹窗把范围说清楚比下拉合适。
 *
 * 点其中一家只是"选中"，真正的写入仍在厂商编辑器的「保存」里。
 */
import { useT } from '../../i18n/I18nContext'
import { Button, Modal } from '../ui'
import KeyMark from './KeyMark'
import type { ApiProvider, BuiltinPreset } from './types'

export default function ProviderPickModal({
  open,
  providers,
  presets,
  onClose,
  onPick,
}: {
  open: boolean
  providers: ApiProvider[]
  presets: BuiltinPreset[]
  onClose: () => void
  onPick: (target: { id?: number; presetKey?: string }) => void
}) {
  const t = useT()
  const taken = new Set(providers.map((p) => p.preset_key).filter(Boolean) as string[])
  const available = presets.filter((p) => !taken.has(p.key))

  return (
    <Modal open={open} onClose={onClose} size="md" title={t('apiConfig:fillProviderKeyTitle')}>
      <div className="space-y-4">
        <p className="text-3xs text-textMuted">{t('apiConfig:keyPickerHint')}</p>

        <div className="space-y-1.5">
          <span className="text-xs font-medium text-textSecondary">{t('apiConfig:myProviders')}</span>
          <div className="flex flex-wrap gap-1.5">
            {providers.map((p) => (
              <Button key={p.id} variant="outline" size="sm"
                onClick={() => onPick({ id: p.id })}>
                {p.name}
                {p.has_key && <KeyMark last4={p.key_last4} />}
              </Button>
            ))}
            {providers.length === 0 && (
              <span className="text-3xs text-textMuted">{t('apiConfig:emptyTitle')}</span>
            )}
          </div>
        </div>

        {available.length > 0 && (
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-textSecondary">{t('apiConfig:builtinProviders')}</span>
            <div className="flex flex-wrap gap-1.5">
              {available.map((p) => (
                <Button key={p.key} variant="outline" size="sm"
                  onClick={() => onPick({ presetKey: p.key })}>
                  {p.label}
                </Button>
              ))}
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}
