/**
 * @ 提及的显示转换 —— 正文里存的是 <@!id>（唯一来源见 backend/app/utils/text.py），
 * 界面上要显示成名字。与 QQ 的 <@!openid> 同形：两边只是换了壳里的 id。
 *
 * 为什么认不出的 id 原样留着：这比编一个名字诚实，也省掉一条新文案（不必再走 i18n）。
 */
export type MentionNames = Record<string, string>

/** @ 令牌：**只认带尖括号的**。裸的 @!90 不是语法（线上那条是模型抄漏了尖括号，谁都不认它） */
export const MENTION_TOKEN_RE = /<@!(\d+)>/g

export function renderMentions(content: string, names: MentionNames): string {
  if (!content) return content
  return content.replace(MENTION_TOKEN_RE, (whole, id) => {
    const name = names[id]
    return name ? '@' + name : whole
  })
}

/** 给人看的标签：查不到就按 unknown 模板退成 @用户41（宁可看得见，也别把令牌露在人眼前） */
export function mentionLabel(id: string, names: MentionNames, unknown: string): string {
  return '@' + (names[id] || unknown.replace('{id}', id))
}

export const escapeHtml = (text: string) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/**
 * 正文 → 带样式的 @ 标签（Markdown 里塞一个受 .mention-chip 控制的 span）。
 *
 * 只在"人话"上用：机器文本与 JSON 要逐字原样，那是给人核对用的。
 */
export function renderMentionChips(content: string, names: MentionNames, unknown: string): string {
  if (!content || !content.includes('<@!')) return content
  return content.replace(MENTION_TOKEN_RE, (_whole, id: string) =>
    `<span class="mention-chip">${escapeHtml(mentionLabel(id, names, unknown))}</span>`)
}
