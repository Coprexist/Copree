/**
 * 头像渐变类名（纯函数）。
 *
 * - 自己（右侧）：紫色渐变 (primary)
 * - 他人（左侧）：同一套紫、方向镜像（to-bl），但末端用 primary-300 淡紫。
 *   原来他人是另一套青绿；换成 500→700 又因为明度落差太大，在圆上像白到黑的阴影。
 * - 系统：红色渐变 (rose)
 * - 有头像时 to- 色带 /30 透明度，无头像时完全不透明
 *
 * ⚠️ Tailwind JIT 必须看到完整静态类名才能生成 CSS！
 * 不能用 `${dir} from-xxx` 这种拼接——dir 是变量，JIT 分析不到。
 * 因此每个返回分支都必须写死完整字符串。
 */
export function avatarGradient(
  senderType: string | undefined,
  isMine: boolean,
  hasAvatar: boolean,
): string {
  if (senderType === 'system') {
    if (hasAvatar) return 'bg-gradient-to-bl from-rose-400 to-rose-600/30'
    return 'bg-gradient-to-bl from-rose-400 to-rose-600'
  }
  if (isMine) {
    if (hasAvatar) return 'bg-gradient-to-br from-primary-500 to-primary-700/30'
    return 'bg-gradient-to-br from-primary-500 to-primary-700'
  }
  if (hasAvatar) return 'bg-gradient-to-br from-primary-500 to-primary-300/30'
  return 'bg-gradient-to-br from-primary-500 to-primary-300'
}

/** 内层渐变缩进尺寸——比外层小 1px，防抗锯齿溢出 */
export const AVATAR_GRADIENT_INSET = 'inset-px'

/** 头像阴影色：自己与他人同色（白色底上紫色投影不会脏），系统仍走红 */
export function avatarShadowColor(senderType: string | undefined): string {
  if (senderType === 'system') return 'shadow-rose-400/15'
  return 'shadow-primary-500/15'
}
