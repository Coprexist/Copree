/**
 * 页面内容宽度档位（全站唯一来源）
 *
 * 档位是默认留白：narrow 表单、content 单列、wide 表格、wider 长文/两列详情、full 铺满。
 * 拖动调宽是使用者的临时覆盖（见 ContentColumn），不改变这里的默认值。
 */
export type Width = 'narrow' | 'content' | 'wide' | 'wider' | 'full'

export const PAGE_WIDTH: Record<Width, string> = {
  narrow: 'max-w-xl',
  content: 'max-w-3xl',
  wide: 'max-w-4xl',
  wider: 'max-w-5xl',
  full: '',
}
