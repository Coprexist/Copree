/**
 * configFallback 命名空间：全站统一的「需要一份可用配置」对话框（见 components/ConfigFallbackDialog）。
 *
 * 原因码来自后端 resolver 的词表，这里只做翻译——加一种缺法时，改 resolver 与这张表。
 */

import type { TranslationDict } from '../types'

export const configFallbackZh: TranslationDict = {
  'title': '还差一份可用的模型配置',
  'reasonNoQuota': '池额度已经用光，你也没有设置自己的默认配置。',
  'reasonNoDefault': '池额度不可用，你还没有设置默认的 API 配置。',
  'reasonNoKey': '配置里没有可用的 Key。',
  'reasonBadKey': '配置里的 Key 解不开（可能加密密钥变过）。',
  'reasonBadBind': '绑定的配置已经被删除或不匹配。',
  'reasonNotConfigured': '还没有可用的模型配置。',
  'hint': '设置一次默认配置，之后所有用到默认配置的地方都会用它',
  'go': '去设置默认配置',
  'later': '稍后再说',
}

export const configFallbackEn: TranslationDict = {
  'title': 'A usable model config is missing',
  'reasonNoQuota': 'The shared pool is out of credit and you have no default config of your own.',
  'reasonNoDefault': 'The pool is unavailable and you have not set a default API config yet.',
  'reasonNoKey': 'The config has no usable key.',
  'reasonBadKey': 'The key in the config cannot be decrypted (the encryption secret may have changed).',
  'reasonBadBind': 'The bound config was deleted or does not match.',
  'reasonNotConfigured': 'No usable model config yet.',
  'hint': 'Set a default once — everywhere that falls back to a default will use it',
  'go': 'Set default config',
  'later': 'Not now',
}

export const configFallbackJa: TranslationDict = {
  'title': '利用できるモデル設定がありません',
  'reasonNoQuota': 'プールの残量が尽きており、ご自身の既定設定も未設定です。',
  'reasonNoDefault': 'プールが使えず、既定の API 設定もまだありません。',
  'reasonNoKey': '設定に利用できるキーがありません。',
  'reasonBadKey': '設定のキーを復号できません（暗号化キーが変わった可能性）。',
  'reasonBadBind': '紐付けられた設定が削除されたか一致しません。',
  'reasonNotConfigured': '利用できるモデル設定がまだありません。',
  'hint': '一度既定設定をしておくと、既定を使うすべての箇所でそれが使われます',
  'go': '既定設定を開く',
  'later': 'あとで',
}
