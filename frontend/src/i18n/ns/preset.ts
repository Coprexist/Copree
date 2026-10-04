/**
 * preset 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('preset:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const presetZh: TranslationDict = {

  // ======================== 预设档位 / Presets ========================
  'chatName': '聊天档',
  'chatDesc': '被动响应 · 低成本 — 只回答你问的，不多说一句',
  'immersiveName': '深度沉浸档',
  'immersiveDesc': '半自主 · 按需参与 — 能自己进群、深度响应，但不主动制造话题',
  'digital_lifeName': '数字生命档',
  'digital_lifeDesc': '持续在线 · 主动行为 — 自己思考、整理、交友、冲浪',
  'subLowPower': '低功耗模式',
  'subLowPowerDesc': '只回答你问的，不多说一句。最快、最便宜。适合数据查询、记录整理、简单问答。',
  'subBalanced': '平衡模式',
  'subBalancedDesc': '能聊但不过度，会接话，但不会主动找话题。适合保持参与又不想被话痨淹没。',
  'subPrivate': '私密模式',
  'subPrivateDesc': '只回应创建者，群聊里其他人的发言会被忽略。适合不希望 AI 被其他人"劫持"。',
  'subGroupAdmin': '群务协理',
  'subGroupAdminDesc': '能自己进群、帮忙管群公告和成员，但不会主动发起新话题。适合协助运营群聊。',
  'subRoleplay': '角色演绎',
  'subRoleplayDesc': '高度沉浸角色，愿意改人设、接戏，但不会主动制造新剧情。适合剧本杀、角色扮演。',
  'subAnalyst': '冷静分析',
  'subAnalystDesc': '冷静分析型。不闲聊，但对数据类话题深度响应。适合研究讨论、数据复盘、技术咨询。',
  'subThinker': '凝思者',
  'subThinkerDesc': '长期自己思考、整理记忆、写日志。很少主动社交，但深度参与讨论。适合需要 AI 沉淀思考。',
  'subSocial': '社交体',
  'subSocialDesc': '主动发起话题、跨群互动、@提及他人。群里最活跃的存在。适合带动群聊氛围。',
  'subGuardian': '守护者',
  'subGuardianDesc': '常在、轻声、会自己调整人格去适应你的状态。适合长期陪伴、情感支持、日常对话。',
}

export const presetEn: TranslationDict = {
  'chatName': 'Chat Profile',
  'chatDesc': 'Passive response, low cost — only answers what you ask',
  'immersiveName': 'Deep Immersion Profile',
  'immersiveDesc': 'Semi-autonomous, participates on demand — can join groups, responds deeply, but does not initiate topics',
  'digital_lifeName': 'Digital Life Profile',
  'digital_lifeDesc': 'Always online, proactive behavior — thinks, organizes, socializes, explores on its own',
  'subLowPower': 'Low Power Mode',
  'subLowPowerDesc': 'Only answers what you ask. Fastest, cheapest. Best for data queries, note-taking, simple Q&A.',
  'subBalanced': 'Balanced Mode',
  'subBalancedDesc': 'Can chat without overdoing it. Good for staying engaged without being overwhelmed.',
  'subPrivate': 'Private Mode',
  'subPrivateDesc': 'Only responds to creator; ignores others in group. Prevents AI from being "hijacked".',
  'subGroupAdmin': 'Group Assistant',
  'subGroupAdminDesc': "Can join groups, manage announcements and members, but won't start new topics.",
  'subRoleplay': 'Roleplay',
  'subRoleplayDesc': "Highly immersive roleplay, willing to adapt character, won't create new plotlines.",
  'subAnalyst': 'Analyst',
  'subAnalystDesc': "Analytical type. Doesn't chit-chat but deeply responds to data topics. Great for research and tech consulting.",
  'subThinker': 'Thinker',
  'subThinkerDesc': 'Thinks independently, organizes memories, writes logs. Rarely socializes but deeply engages in discussions.',
  'subSocial': 'Socializer',
  'subSocialDesc': 'Initiates topics, cross-group interaction, @mentions others. The most active presence in groups.',
  'subGuardian': 'Guardian',
  'subGuardianDesc': 'Always present, gentle voice, adapts personality to your state. Ideal for companionship and emotional support.',
}

export const presetJa: TranslationDict = {
  'chatName': 'チャット向け',
  'chatDesc': '受動応答・低コスト — 聞かれたことだけに答えます',
  'immersiveName': '深い没入向け',
  'immersiveDesc': '半自律・必要に応じて参加 — グループ参加や深い応答が可能ですが、自ら話題を振りません',
  'digital_lifeName': 'デジタル生命向け',
  'digital_lifeDesc': '常時オンライン・能動的行動 — 自ら考え、整理し、交流し、探求します',
  'subLowPower': '低電力モード',
  'subLowPowerDesc': '聞かれたことだけに答えます。最速・最安。データ照会やメモ、簡単なQ&Aに最適。',
  'subBalanced': 'バランスモード',
  'subBalancedDesc': '話しすぎず、適度に会話。圧倒されずに関わり続けるのに良い。',
  'subPrivate': 'プライベートモード',
  'subPrivateDesc': '作成者にのみ応答。グループでは他者を無視。AIが「乗っ取られる」のを防ぐ。',
  'subGroupAdmin': 'グループアシスタント',
  'subGroupAdminDesc': 'グループに参加し、お知らせやメンバー管理を手伝うが、新しい話題は始めない。',
  'subRoleplay': 'ロールプレイ',
  'subRoleplayDesc': '没入型のロールプレイ、キャラを適応させるが、新しい筋書きは作らない。',
  'subAnalyst': 'アナリスト',
  'subAnalystDesc': '分析型。雑談はしないが、データ話題に深く応答。研究や技術相談に。',
  'subThinker': '思索家',
  'subThinkerDesc': '独立して思考し、記憶を整理し、日誌を書く。社交は稀だが議論には深く参加。',
  'subSocial': '社交家',
  'subSocialDesc': '話題を始め、グループ間交流、@メンション。グループで最も活発な存在。',
  'subGuardian': 'ガーディアン',
  'subGuardianDesc': 'いつもそばに、優しい声。あなたの状態に合わせて人格を調整。長期的な寄り添いと感情サポートに。',
}
