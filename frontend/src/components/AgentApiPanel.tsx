/**
 * AI 详情 → API 页签：这个 AI 用哪家 LLM、用哪家语音。
 *
 * 一块设置一页（LLM / 语音互斥切换，切的是视图所以用下划线页签）。
 * 两半是同一副组件：都选一条配置档，选中即整包换（地址、Key、模型/音色）。
 * kind 只决定取哪类厂商与配置档，交互一模一样。
 */
import { useState } from 'react'
import { useT } from '../i18n/I18nContext'
import { UnderlineTabs } from './ui'
import ProfilePicker from './api-config/ProfilePicker'

export default function AgentApiPanel({ agentId }: { agentId: number }) {
  const t = useT()
  const [sub, setSub] = useState<'llm' | 'voice'>('llm')

  return (
    <div className="space-y-4">
      <UnderlineTabs
        size="sm"
        items={[
          { key: 'llm' as const, label: t('agentDetail:agentApiLlmTitle') },
          { key: 'voice' as const, label: t('agentDetail:voiceSection') },
        ]}
        value={sub}
        onChange={setSub}
      />

      {sub === 'llm' && (
        <section className="card p-4 space-y-3">
          <h3 className="text-sm font-semibold text-textPrimary">{t('apiConfig:pickerTitle')}</h3>
          <ProfilePicker agentId={agentId} />
        </section>
      )}

      {sub === 'voice' && (
        <section className="card p-4 space-y-3">
          <h3 className="text-sm font-semibold text-textPrimary">{t('apiConfig:voicePickerTitle')}</h3>
          <ProfilePicker agentId={agentId} kind="voice" />
        </section>
      )}
    </div>
  )
}
