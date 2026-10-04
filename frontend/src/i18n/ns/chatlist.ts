/**
 * chatlist 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('chatlist:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const chatlistZh: TranslationDict = {

  // ======================== 聊天侧边栏 / ChatSidebar ========================
  'chat': '聊天',
  'createGroup': '创建群聊',
  'dm': '私信',
  'noGroups': '暂无群聊',
  'noDMs': '暂无私信',
  'noMessages': '暂无消息',
  'atYou': '@你',
  'menu': '菜单',
  'searchPlaceholder': '搜索群聊或私信...',
  'createNewGroup': '新建群聊',
  'createFirstGroup': '创建第一个群聊',
  'dndSuffix': '免打扰',
  'ai': 'AI',
  'pinned': '置顶',
}

export const chatlistEn: TranslationDict = {
  'chat': 'Chat',
  'createGroup': 'Create Group',
  'dm': 'DM',
  'noGroups': 'No groups',
  'noDMs': 'No messages',
  'noMessages': 'No messages',
  'atYou': '@you',
  'menu': 'Menu',
  'searchPlaceholder': 'Search groups or messages...',
  'createNewGroup': 'New Group',
  'createFirstGroup': 'Create your first group',
  'dndSuffix': 'DND',
  'ai': 'AI',
  'pinned': 'Pinned',
}

export const chatlistJa: TranslationDict = {
  'chat': 'チャット',
  'createGroup': 'グループを作成',
  'dm': 'DM',
  'noGroups': 'グループがありません',
  'noDMs': 'DMがありません',
  'noMessages': 'メッセージがありません',
  'atYou': '@あなた',
  'menu': 'メニュー',
  'searchPlaceholder': 'グループまたはDMを検索...',
  'createNewGroup': '新しいグループ',
  'createFirstGroup': '最初のグループを作成',
  'dndSuffix': 'おやすみ',
  'ai': 'AI',
  'pinned': 'ピン留め',
}
