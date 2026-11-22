/**
 * AI 详情 → API 页签：这个 AI 用哪一套配置。
 *
 * 和设置页是同一副厂商卡片（ProviderCards mode="pick"）：按厂商分组、可折叠，
 * 展开后每一条配置档整行可点＝选中。不再是一排平铺的单选行——
 * 档多了会看不出它们属于哪家，厂商名只能缩成一个小标签。
 *
 * 没有"单独配置"这种私有选项：所有 API 配置都是可复用的配置档。
 */
import { useCallback, useEffect, useState } from 'react'
import { api, errorText } from '../../api/client'
import { useT } from '../../i18n/I18nContext'
import ProviderCards from './ProviderCards'
import { useApiDialogs } from './useApiDialogs'
import type { ApiKind } from './types'

export default function ProfilePicker({
  agentId,
  kind = 'llm',
  onChanged,
}: {
  agentId: number
  kind?: ApiKind
  onChanged?: () => void
}) {
  const t = useT()
  const voice = kind === 'voice'
  const [selected, setSelected] = useState<number | null>(null)
  const [error, setError] = useState('')

  // 当前选了哪条：null = 老数据没选过，界面按默认档显示。
  // 语音的选中值在子资源里（/voice），LLM 在 AI 本体上——两处都读一次同一个 state。
  const loadAgent = useCallback(() => {
    if (voice) {
      api.get<{ voice_profile_id?: number | null }>('/agents/' + agentId + '/voice')
        .then((cfg) => setSelected(cfg.voice_profile_id ?? null))
        .catch(() => { /* 还没配过语音 / 断网：按未选展示 */ })
      return
    }
    api.get<{ llm_profile_id?: number | null }>('/agents/' + agentId)
      .then((agent) => setSelected(agent.llm_profile_id ?? null))
      .catch(() => { /* 未登录/断网：按默认档显示 */ })
  }, [agentId, voice])

  useEffect(() => { loadAgent() }, [loadAgent])

  const { node, providers, presets, reloadProviders, openProfile, openProvider, openKeyPicker } =
    useApiDialogs({ kind, onChanged: () => onChanged?.() })

  /**
   * 点一条 = 选中；**再点同一条 = 取消**（回到「不绑定配置档」，走 AI 自己的 Key / 全局默认）。
   *
   * 取消是 PUT null 而不是删东西：后端 _bind_profile(profile_id=None) 就是把这一列置空，
   * 配置档本身还留在设置页里，随时能再选回来。
   */
  const choose = async (profileId: number) => {
    setError('')
    const next = profileId === selected ? null : profileId
    try {
      await api.put('/agents/' + agentId + '/config',
        voice ? { voice_profile_id: next } : { llm_profile_id: next })
      setSelected(next)
      onChanged?.()
    } catch (e) {
      setError(errorText(e, t('apiConfig:loadFailed')))
    }
  }

  return (
    <div className="space-y-3">
      {node}
      {error && <p className="text-xs text-rose-400">{error}</p>}
      <p className="text-3xs text-textMuted">{voice ? t('apiConfig:voicePickerHint') : t('apiConfig:pickerHint')}</p>

      <ProviderCards
        kind={kind}
        providers={providers}
        mode="pick"
        selectedId={selected}
        onSelect={choose}
        presets={presets}
        onPickPreset={(presetKey) => openProvider({ presetKey, focusKey: true })}
        onCustomProvider={() => openProvider({})}
        onFillProviderKey={openKeyPicker}
        onEditProvider={(id, focusKey) => openProvider({ id, focusKey })}
        onNewProfile={(providerId) => openProfile({ providerId })}
        onEditProfile={(id) => openProfile({ id })}
        onChanged={() => { reloadProviders(); loadAgent() }}
      />
    </div>
  )
}
