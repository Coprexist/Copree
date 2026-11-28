/**
 * 备注的显示规则：只有一个地方定义，列表、聊天头部、资料卡都调它
 *
 * 为什么不是「有备注就只显示备注」：备注是用户起的称呼，原名是事实。
 * 两者一起显示（备注在前、原名在括号里），别人一看就知道这行对应的是谁。
 */

/** 备注（原名）；没备注就是原名 */
export function displayName(remark: string | null | undefined, name: string | null | undefined): string {
  const base = (name || '').trim()
  const mark = (remark || '').trim()
  if (!mark) return base
  return base ? mark + '（' + base + '）' : mark
}

/**
 * 排序用的名字：备注优先，没备注用原名。
 * 微信 / QQ 通讯录都按「我看到的那个名字」的拼音首字母排——改了备注，位置就跟着变。
 */
export function sortName(remark: string | null | undefined, name: string | null | undefined): string {
  return (remark || '').trim() || (name || '').trim()
}

/** 头像上的字：有备注用备注首字（一眼认出这是自己起的名），否则原名首字 */
export function avatarInitial(remark: string | null | undefined, name: string | null | undefined): string {
  const source = (remark || '').trim() || (name || '').trim()
  return source.charAt(0).toUpperCase() || '?'
}

/** 有没有备注（决定名字配色、头像字用哪个） */
export function hasRemark(remark: string | null | undefined): boolean {
  return Boolean((remark || '').trim())
}
