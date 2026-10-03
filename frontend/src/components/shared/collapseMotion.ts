/**
 * 折叠动画的参数 —— 站内的 useHeightTransition 与导出件的脚本共用这一份：
 * 导出件里没有 React，同一套数值只能拼进脚本里，分开写迟早会走样。
 */
export const FOLD_EASING = 'cubic-bezier(0.4, 0, 0.2, 1)'

/** 普通块的时长：位移在基准量级以内都用它 */
export const FOLD_DURATION_MS = 200
/** 长块最多给到这么久：再长就显得拖沓，中间那一段自然会快 */
export const FOLD_MAX_DURATION_MS = 420
/** 位移每多一千像素加多少毫秒 */
export const FOLD_MS_PER_1000PX = 90

/**
 * 时长随位移缩放：一块两千多像素的系统提示和一块两百像素的工具返回用同一个时长的话，
 * 前者会「唰」地一下、后者慢悠悠——长度不一样，时间也得跟着不一样。
 */
export function foldDuration(distance: number): number {
  const extra = Math.abs(distance) / 1000 * FOLD_MS_PER_1000PX
  return Math.round(Math.min(FOLD_MAX_DURATION_MS, FOLD_DURATION_MS + extra))
}

export function foldTransition(distance: number): string {
  return `height ${foldDuration(distance)}ms ${FOLD_EASING}`
}
