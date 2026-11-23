/** 用户 API 配置的两种数据：厂商（在哪请求）与配置档（这套叫什么、用哪把 Key） */

export type ApiKind = 'llm' | 'voice'

export interface ModelOption {
  value: string
  label: string
}

export interface ApiProfile {
  id: number
  name: string
  has_key: boolean
  key_last4: string
  primary_model: string | null
  secondary_model: string | null
  is_default: boolean
  sort_order: number
}

export interface ApiProvider {
  id: number
  kind: ApiKind
  name: string
  preset_key: string | null
  base_url: string
  has_key: boolean
  key_last4: string
  options: ModelOption[]
  default_primary: string | null
  default_secondary: string | null
  thinking_supported: boolean
  system_layout: string
  /** 这一档是怎么来的：user|detected|preset|host；空 = 从未判定（界面显示「自动识别」） */
  system_layout_source?: string
  profiles: ApiProfile[]
}

/** 内置预设（接入时用的清单，不含任何 Key） */
export interface BuiltinPreset {
  key: string
  label: string
  base_url: string
  api_key_url: string
  kind: ApiKind
  chat_model?: string
  work_model?: string
  voice_id?: string
  models?: ModelOption[]
  voices?: ModelOption[]
  thinking_supported: boolean
  system_layout: string
}

export const EMPTY_PROVIDER: Omit<ApiProvider, 'id'> = {
  kind: 'llm',
  name: '',
  preset_key: null,
  base_url: '',
  has_key: false,
  key_last4: '',
  options: [],
  default_primary: null,
  default_secondary: null,
  thinking_supported: false,
  system_layout: 'anywhere',
  profiles: [],
}
