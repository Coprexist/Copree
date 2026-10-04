/**
 * Copree 翻译字典注册表
 *
 * 文案按命名空间分文件放在 ./ns/ 下，一个命名空间一个文件；本文件只做 import 汇总。
 * 调用点写法：t('nav:chat') → ns=nav, key=chat；无冒号时 ns=common。
 * 新增命名空间：在 ./ns/ 下加文件并用 t('<ns>:<key>') 调用；check-i18n.mjs 自动发现，无需改脚本。
 */

import type { Lang } from './languages'
import type { NamespacedDict } from './types'
import { navZh, navEn, navJa } from './ns/nav'
import { sidebarZh, sidebarEn, sidebarJa } from './ns/sidebar'
import { notFoundZh, notFoundEn, notFoundJa } from './ns/notFound'
import { chatlistZh, chatlistEn, chatlistJa } from './ns/chatlist'
import { chatZh, chatEn, chatJa } from './ns/chat'
import { invitationZh, invitationEn, invitationJa } from './ns/invitation'
import { dmZh, dmEn, dmJa } from './ns/dm'
import { listZh, listEn, listJa } from './ns/list'
import { searchZh, searchEn, searchJa } from './ns/search'
import { notifyZh, notifyEn, notifyJa } from './ns/notify'
import { commonZh, commonEn, commonJa } from './ns/common'
import { skillBackpackZh, skillBackpackEn, skillBackpackJa } from './ns/skillBackpack'
import { forwardZh, forwardEn, forwardJa } from './ns/forward'
import { filePreviewZh, filePreviewEn, filePreviewJa } from './ns/filePreview'
import { externalLinkZh, externalLinkEn, externalLinkJa } from './ns/externalLink'
import { authZh, authEn, authJa } from './ns/auth'
import { setupZh, setupEn, setupJa } from './ns/setup'
import { meZh, meEn, meJa } from './ns/me'
import { settingsZh, settingsEn, settingsJa } from './ns/settings'
import { worldChatZh, worldChatEn, worldChatJa } from './ns/worldChat'
import { agentsZh, agentsEn, agentsJa } from './ns/agents'
import { balanceZh, balanceEn, balanceJa } from './ns/balance'
import { agentDetailZh, agentDetailEn, agentDetailJa } from './ns/agentDetail'
import { usageZh, usageEn, usageJa } from './ns/usage'
import { adminZh, adminEn, adminJa } from './ns/admin'
import { groupSettingsZh, groupSettingsEn, groupSettingsJa } from './ns/groupSettings'
import { dmSettingsZh, dmSettingsEn, dmSettingsJa } from './ns/dmSettings'
import { profileCardZh, profileCardEn, profileCardJa } from './ns/profileCard'
import { avatarCropZh, avatarCropEn, avatarCropJa } from './ns/avatarCrop'
import { modalZh, modalEn, modalJa } from './ns/modal'
import { presetZh, presetEn, presetJa } from './ns/preset'
import { aiTypeZh, aiTypeEn, aiTypeJa } from './ns/aiType'
import { errorZh, errorEn, errorJa } from './ns/error'
import { timeZh, timeEn, timeJa } from './ns/time'
import { opencliZh, opencliEn, opencliJa } from './ns/opencli'
import { backpackZh, backpackEn, backpackJa } from './ns/backpack'
import { desktopZh, desktopEn, desktopJa } from './ns/desktop'
import { toolZh, toolEn, toolJa } from './ns/tool'
import { adminConfigZh, adminConfigEn, adminConfigJa } from './ns/adminConfig'
import { logsZh, logsEn, logsJa } from './ns/logs'

export type { TranslationDict, NamespacedDict } from './types'

export const translations: Record<Lang, NamespacedDict> = {
  zh: {
    common: commonZh, nav: navZh, sidebar: sidebarZh, notFound: notFoundZh,
    chatlist: chatlistZh, chat: chatZh, invitation: invitationZh, dm: dmZh,
    list: listZh, search: searchZh, notify: notifyZh, skillBackpack: skillBackpackZh,
    forward: forwardZh, filePreview: filePreviewZh, externalLink: externalLinkZh, auth: authZh,
    setup: setupZh, me: meZh, settings: settingsZh, worldChat: worldChatZh,
    agents: agentsZh, balance: balanceZh, agentDetail: agentDetailZh, usage: usageZh,
    admin: adminZh, groupSettings: groupSettingsZh, dmSettings: dmSettingsZh, profileCard: profileCardZh,
    avatarCrop: avatarCropZh, modal: modalZh, preset: presetZh, aiType: aiTypeZh,
    error: errorZh, time: timeZh, opencli: opencliZh, backpack: backpackZh,
    desktop: desktopZh, tool: toolZh, adminConfig: adminConfigZh, logs: logsZh,
  },
  en: {
    common: commonEn, nav: navEn, sidebar: sidebarEn, notFound: notFoundEn,
    chatlist: chatlistEn, chat: chatEn, invitation: invitationEn, dm: dmEn,
    list: listEn, search: searchEn, notify: notifyEn, skillBackpack: skillBackpackEn,
    forward: forwardEn, filePreview: filePreviewEn, externalLink: externalLinkEn, auth: authEn,
    setup: setupEn, me: meEn, settings: settingsEn, worldChat: worldChatEn,
    agents: agentsEn, balance: balanceEn, agentDetail: agentDetailEn, usage: usageEn,
    admin: adminEn, groupSettings: groupSettingsEn, dmSettings: dmSettingsEn, profileCard: profileCardEn,
    avatarCrop: avatarCropEn, modal: modalEn, preset: presetEn, aiType: aiTypeEn,
    error: errorEn, time: timeEn, opencli: opencliEn, backpack: backpackEn,
    desktop: desktopEn, tool: toolEn, adminConfig: adminConfigEn, logs: logsEn,
  },
  ja: {
    common: commonJa, nav: navJa, sidebar: sidebarJa, notFound: notFoundJa,
    chatlist: chatlistJa, chat: chatJa, invitation: invitationJa, dm: dmJa,
    list: listJa, search: searchJa, notify: notifyJa, skillBackpack: skillBackpackJa,
    forward: forwardJa, filePreview: filePreviewJa, externalLink: externalLinkJa, auth: authJa,
    setup: setupJa, me: meJa, settings: settingsJa, worldChat: worldChatJa,
    agents: agentsJa, balance: balanceJa, agentDetail: agentDetailJa, usage: usageJa,
    admin: adminJa, groupSettings: groupSettingsJa, dmSettings: dmSettingsJa, profileCard: profileCardJa,
    avatarCrop: avatarCropJa, modal: modalJa, preset: presetJa, aiType: aiTypeJa,
    error: errorJa, time: timeJa, opencli: opencliJa, backpack: backpackJa,
    desktop: desktopJa, tool: toolJa, adminConfig: adminConfigJa, logs: logsJa,
  },
}

/** 插值：'正在{name}…' + { name: '读取文件' } → '正在读取文件…' */
function interpolate(tpl: string, vars?: Record<string, string | number>): string {
  if (!vars) return tpl
  return tpl.replace(/\{([a-zA-Z_]+)\}/g, (_, k) => (vars[k] != null ? String(vars[k]) : `{${k}}`))
}

/**
 * 命名空间查找：key 形如 'nav.chat'（common 分区）或 'tool:toolName.file_read'（tool 分区）。
 * 查找链：指定 ns → 当前语言 → zh 同 ns → 返回 path。vars 用于 {var} 插值。
 */
export function getTranslation(
  lang: Lang,
  path: string,
  vars?: Record<string, string | number>
): string {
  // 拆命名空间：'tool:key' → ns=tool, key=key；无前缀 → common
  const sep = path.indexOf(':')
  const ns = sep > 0 ? path.slice(0, sep) : 'common'
  const key = sep > 0 ? path.slice(sep + 1) : path

  const bundle = translations[lang] || translations.zh
  const dict = bundle[ns] || bundle.common
  const val = dict[key]
  if (typeof val === 'string') return interpolate(val, vars)
  // fallback：zh 同 ns → common → 原样
  const fbDict = (translations.zh[ns] || translations.zh.common)
  const fb = fbDict[key]
  if (typeof fb === 'string') return interpolate(fb, vars)
  return path
}
