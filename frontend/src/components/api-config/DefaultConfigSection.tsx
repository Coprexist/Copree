/**
 * 设置页「API 配置」→ 默认配置（独立页签）
 *
 * 一个场景一张卡：总开关、有序配置序列、每格子开关、失败红点。
 * 全局默认档已于 2026-11-28 取消——「某个场景用哪些配置、按什么顺序」就是这个场景的
 * 默认配置（docs/dev/api_config_chain.md §3.4）。
 *
 * 场景清单来自后端（GET /user/api-config/scenarios），前端只按 key 查文案：
 * 后端加一个场景，这里自动多一张卡，不需要改这个文件。
 */
import { useCallback, useEffect, useState } from 'react'
import { api, errorText } from '../../api/client'
import { useT } from '../../i18n/I18nContext'
import type { ApiKind, ApiProvider } from './types'
import ScenarioCard from './ScenarioCard'
import type { ProfileOption, Scenario } from './ScenarioCard'

/** 场景 key → 文案 key（清单在后端，文案在前端 i18n，各管一件事） */
const SCENARIO_TEXT: Record<string, { name: string; desc: string }> = {
  agent_llm: { name: 'apiConfig:scenarioAgentLlm', desc: 'apiConfig:scenarioAgentLlmDesc' },
  world_ai: { name: 'apiConfig:scenarioWorldAi', desc: 'apiConfig:scenarioWorldAiDesc' },
  smart_organize: { name: 'apiConfig:scenarioSmartOrganize', desc: 'apiConfig:scenarioSmartOrganizeDesc' },
  agent_create: { name: 'apiConfig:scenarioAgentCreate', desc: 'apiConfig:scenarioAgentCreateDesc' },
  voice_default: { name: 'apiConfig:scenarioVoiceDefault', desc: 'apiConfig:scenarioVoiceDefaultDesc' },
}

const EMPTY_PROFILES: Record<ApiKind, ProfileOption[]> = { llm: [], voice: [] }

/** 场景按种类分组显示；顺序即页面顺序（LLM 在前，语音在后） */
const GROUPS: { kind: ApiKind; labelKey: string }[] = [
  { kind: 'llm', labelKey: 'apiConfig:groupLlm' },
  { kind: 'voice', labelKey: 'apiConfig:groupVoice' },
]

export default function DefaultConfigSection() {
  const t = useT()
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [profiles, setProfiles] = useState(EMPTY_PROFILES)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const [data, llm, voice] = await Promise.all([
        api.get<{ scenarios: Scenario[] }>('/user/api-config/scenarios'),
        api.get<{ providers: ApiProvider[] }>('/user/api-config/providers?kind=llm'),
        api.get<{ providers: ApiProvider[] }>('/user/api-config/providers?kind=voice'),
      ])
      setScenarios(data.scenarios || [])
      const flatten = (providers: ApiProvider[]): ProfileOption[] =>
        providers.flatMap((p) => p.profiles.map((pr) => ({ id: pr.id, label: p.name + ' · ' + pr.name })))
      setProfiles({ llm: flatten(llm.providers || []), voice: flatten(voice.providers || []) })
      setError('')
    } catch (e) {
      setError(errorText(e, t('apiConfig:loadFailed')))
    }
  }, [t])

  useEffect(() => { void load() }, [load])

  // 厂商 / 配置档在别的页签里改过（新建、删除），这里要跟着变
  useEffect(() => {
    const onChanged = () => { void load() }
    window.addEventListener('api-config-changed', onChanged)
    return () => window.removeEventListener('api-config-changed', onChanged)
  }, [load])

  const text = (key: string) => SCENARIO_TEXT[key] || { name: key, desc: '' }

  return (
    <div className="space-y-3">
      <p className="text-xs text-textMuted">{t('apiConfig:scenariosDesc')}</p>
      {error && <p className="text-xs text-rose-400">{error}</p>}
      {scenarios.length === 0 && !error && (
        <p className="text-xs text-textMuted">{t('apiConfig:scenariosEmpty')}</p>
      )}
      {/* 按种类分组：LLM 与语音是两套配置档，混在一列里读起来会以为它们能互相顶替 */}
      {GROUPS.map(({ kind, labelKey }) => {
        const items = scenarios.filter((s) => s.kind === kind)
        if (items.length === 0) return null
        return (
          <section key={kind} className="space-y-2">
            <h3 className="text-xs font-medium text-textSecondary">{t(labelKey)}</h3>
            {items.map((scenario) => {
              const label = text(scenario.key)
              return (
                <ScenarioCard
                  key={scenario.key}
                  scenario={scenario}
                  name={t(label.name)}
                  desc={label.desc ? t(label.desc) : ''}
                  profiles={profiles[scenario.kind] ?? []}
                  onChanged={load}
                />
              )
            })}
          </section>
        )
      })}
    </div>
  )
}
