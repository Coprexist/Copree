/** 创建 AI 弹窗的共享类型：预设数据、子档、以及表单的单一形状。 */

export interface ModelOption {
  value: string
  label: string
  provider_name?: string
  provider_key?: string
}

export interface ProviderInfo {
  name: string
  provider: string
  base_url: string
  api_key_url?: string
  thinking_supported: boolean
  is_default: boolean
  models: ModelOption[]
}

export type AiTypeValue = 'general' | 'semi_general' | 'resonance'

/** 档位预设：文案只存 key，渲染时查字典——中文不再写进数据层 */
export interface PresetData {
  key: string
  nameKey: string
  descKey: string
  temperature: number
  thinking_enabled: boolean
  max_tool_rounds: number
  alarm_max_tool_rounds: number
  force_alarm_on_end: boolean
  max_alarms: number
  delay_reply_enabled: boolean
  is_ai_editable: boolean
  hide_ai_identity: boolean
  reminder_grace: string
  memory_load_mode: string
  memory_recent_count: number
}

export interface SubOption {
  id: string
  nameKey: string
  descKey: string
  icon: string
  params: Partial<PresetData>
  ai_type?: AiTypeValue
}

/** 创建表单的全部字段。新增字段只改这里和 INITIAL_FORM，不必再逐层传参 */
export interface AgentForm {
  name: string
  systemPrompt: string
  temperature: number
  topP: number
  presencePenalty: number
  frequencyPenalty: number
  thinkingEnabled: boolean
  hideAiIdentity: boolean
  reminderGrace: string
  delayReplyEnabled: boolean | null
  configProfile: string
  maxToolRounds: number
  alarmMaxToolRounds: number
  forceAlarmOnEnd: boolean
  maxAlarms: number
  isAiEditable: boolean
  allowFriendRequests: boolean
  autoRespondFriendRequest: boolean
  discoverable: boolean
  allowOthersChat: boolean
  othersChatMode: string
  othersChatQuota: number
  othersChatUsed: number
  disallowMode: string
  chatModel: string
  workModel: string
  apiCreditCost: number
  aiType: AiTypeValue
  apiBaseUrl: string
  apiKey: string
  memoryLoadMode: string
  memoryRecentCount: number
  memorySharedScope: string
  bio: string
  statusText: string
  autoDndThreshold: number
  autoDndDuration: number
  autoResetQuota: boolean
  groupOwnerPays: boolean
  conversationLogsLimit: number | null
  userCanViewLogs: boolean | null
}

/** 值 + setter 一体的表单 API：setter 名从字段名推导，子组件只收这一个对象 */
export type AgentFormApi = AgentForm & {
  [K in keyof AgentForm as `set${Capitalize<K & string>}`]: (v: AgentForm[K]) => void
}
