import { useTheme } from '../context/ThemeContext'

/**
 * 气泡底色是不是深色：自己发的气泡本身就是深色底（与页面主题无关），别人的跟随页面主题。
 *
 * 表格的 CSS 变量与正文彩色文字都要用它——两处各判一次的时候，深色主题下别人的消息
 * 会出现"表格按深色底、彩色文字按浅色底"的错配（实测：亮红 255 100 100 vs 深红 220 50 50）。
 */
export function useBubbleDark(isMine: boolean): boolean {
  const { theme } = useTheme()
  return isMine || theme === 'dark'
}
