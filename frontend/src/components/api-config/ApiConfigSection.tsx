/**
 * 设置页 →「我的厂商」整块：标题 + 接入按钮 + 折叠卡片。
 *
 * 这块只负责"管理"，不负责"这个 AI 用哪套"——后者在 AI 详情的 API 页签（ProfilePicker）。
 */
import { useT } from '../../i18n/I18nContext'
import ProviderCards from './ProviderCards'
import { useApiDialogs } from './useApiDialogs'
import type { ApiKind } from './types'

export default function ApiConfigSection({
  kind = 'llm',
  title,
  desc,
}: {
  kind?: ApiKind
  title?: string
  desc?: string
}) {
  const t = useT()
  const voice = kind === 'voice'
  const { node, providers, presets, reloadProviders, openProvider, openProfile, openKeyPicker } =
    useApiDialogs({ kind })

  return (
    <section className="space-y-2">
      {node}
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-textPrimary">
          {title || (voice ? t('apiConfig:sectionVoice') : t('apiConfig:sectionProviders'))}
        </h3>
        <span className="text-3xs text-textMuted truncate">
          {desc || (voice ? t('apiConfig:sectionVoiceDesc') : t('apiConfig:sectionProvidersDesc'))}
        </span>
      </div>
      <ProviderCards
        kind={kind}
        providers={providers}
        presets={presets}
        onPickPreset={(presetKey) => openProvider({ presetKey, focusKey: true })}
        onCustomProvider={() => openProvider({})}
        onFillProviderKey={openKeyPicker}
        onEditProvider={(id, focusKey) => openProvider({ id, focusKey })}
        onNewProfile={(providerId) => openProfile({ providerId })}
        onEditProfile={(id) => openProfile({ id })}
        onChanged={() => { reloadProviders(); window.dispatchEvent(new Event('api-config-changed')) }}
      />
    </section>
  )
}
