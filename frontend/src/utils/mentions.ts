/**
 * @ 提及的显示转换 —— 正文里存的是 <@!id>（唯一来源见 backend/app/utils/text.py），
 * 界面上要显示成名字。与 QQ 的 <@!openid> 同形：两边只是换了壳里的 id。
 *
 * 为什么认不出的 id 原样留着：这比编一个名字诚实，也省掉一条新文案（不必再走 i18n）。
 */
export type MentionNames = Record<number, string>

export function renderMentions(content: string, names: MentionNames): string {
  if (!content) return content
  return content.replace(/<@!(\d+)>/g, (whole, id) => {
    const name = names[Number(id)]
    return name ? '@' + name : whole
  })
}
