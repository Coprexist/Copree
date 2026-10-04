/**
 * profileCard 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('profileCard:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const profileCardZh: TranslationDict = {

  // ======================== 个人资料卡 / ProfileCard ========================
  'blocked': '已屏蔽',
  'aiPrefix': 'AI ·',
  'human': '人类',
  'sending': '发起中...',
  'sendDM': '发私信',
  'viewProfile': '查看资料',
  'addFriend': '加好友',
  'sendRequest': '发送申请',
  'alreadyFriend': '已是好友',
  'requestSent': '好友申请已发送',
  'friendMessagePlaceholder': '附言（选填，最多200字）',
  'creator': '制作者',
  'registeredOn': '注册时间',
  'createdOn': '创建于',
  'priorityOn': '已特别关心',
  'priorityOff': '设为特别关心',
  'bioEmpty': '空空如也便是此人的简介',
  'groupBioEmpty': '空空如也便是此群的简介',
  'channelGroup': '来自{label}的群',
  'channelMembers': '{n} 人',
}

export const profileCardEn: TranslationDict = {
  'blocked': 'Blocked',
  'aiPrefix': 'AI ·',
  'human': 'Human',
  'sending': 'Starting...',
  'sendDM': 'Send DM',
  'viewProfile': 'View Profile',
  'addFriend': 'Add Friend',
  'sendRequest': 'Send Request',
  'alreadyFriend': 'Already friends',
  'requestSent': 'Friend request sent',
  'friendMessagePlaceholder': 'Message (optional, max 200 chars)',
  'creator': 'Creator',
  'registeredOn': 'Registered',
  'createdOn': 'Created',
  'priorityOn': 'Priority',
  'priorityOff': 'Set Priority',
  'bioEmpty': 'This person has no bio.',
  'groupBioEmpty': 'This group has no description',
  'channelGroup': '{label} group',
  'channelMembers': '{n} members',
}

export const profileCardJa: TranslationDict = {
  'blocked': 'ブロック中',
  'aiPrefix': 'AI ·',
  'human': '人間',
  'sending': '開始中...',
  'sendDM': 'DMを送信',
  'viewProfile': 'プロフィールを見る',
  'addFriend': 'フレンド追加',
  'sendRequest': '申請を送信',
  'alreadyFriend': 'すでにフレンド',
  'requestSent': 'フレンド申請を送信済み',
  'friendMessagePlaceholder': 'メッセージ（任意、200文字以内）',
  'creator': '制作者',
  'registeredOn': '登録日時',
  'createdOn': '作成日',
  'priorityOn': '特別関心済み',
  'priorityOff': '特別関心に設定',
  'bioEmpty': 'プロフィールはまだありません。',
  'groupBioEmpty': 'このグループに説明はありません',
  'channelGroup': '{label} のグループ',
  'channelMembers': '{n} 人',
}
