/**
 * 折叠动画的参数 —— 站内的 useHeightTransition 与导出件的脚本共用这一份：
 * 导出件里没有 React，同一条曲线只能拼进脚本里，分开写迟早会走样。
 */
export const FOLD_DURATION_MS = 200

/** 两端都慢起慢收，末段尤其要缓：收起快到底时若还有速度，看着像被拽下去 */
export const FOLD_EASING = 'cubic-bezier(0.4, 0, 0.2, 1)'

export const FOLD_TRANSITION = `height ${FOLD_DURATION_MS}ms ${FOLD_EASING}`
