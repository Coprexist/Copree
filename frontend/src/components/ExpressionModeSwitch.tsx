/**
 * 表达方式开关（专业模式 / 通俗模式）的唯一实现。
 *
 * 两处入口共用它：普通聊天输入工具条、群视界世界对话工具条，差异只有 size。
 * 形态定稿为「通俗模式 + 滑块」：关＝专业模式。中途试过两段文字直选，放进世界对话
 * 那条全是小图标的工具条里太抢眼（用户原话「一点都不搭」）；滑块 48×24 与旁边的图标
 * 按钮同高，开关状态本身也一眼看得出来，不用先解释再点。
 */
import Toggle from './Toggle'
import { usePlainLanguage } from '../hooks/usePlainLanguage'
import { useT } from '../i18n/I18nContext'

/**
 * 两个模式：存储上 plain=true 是通俗，false 是专业（缺省即专业）。
 * 文案 key 也只在这里一份，设置页复用它渲染两张选项卡，避免同一套名字写两遍。
 */
export const MODE_PRO = {
  plain: false,
  labelKey: 'settings.expressionModePro',
  descKey: 'settings.expressionModeProDesc',
}
export const MODE_PLAIN = {
  plain: true,
  labelKey: 'settings.expressionModePlain',
  descKey: 'settings.expressionModePlainDesc',
}
export const EXPRESSION_MODES = [MODE_PRO, MODE_PLAIN]

export default function ExpressionModeSwitch({ size = 'md' }: { size?: 'sm' | 'md' }) {
  const t = useT()
  const { plain, setPlain, saving } = usePlainLanguage()
  const big = size === 'md'

  return (
    // 用滑块而不是两段文字按钮：工具条上一排都是小控件，两块带底色的文字太抢眼；
    // 滑块只有 48×24，和旁边的图标按钮同高，状态（开/关）本身也一眼看得出来。
    // 标签固定写「通俗模式」——它说的是这个滑块管什么，关闭即专业模式（说明在 title 里）。
    <label
      className={`inline-flex items-center shrink-0 cursor-pointer select-none ${big ? 'gap-2' : 'gap-1.5'}`}
      title={`${t('settings.expressionMode')}：${t(plain ? MODE_PLAIN.descKey : MODE_PRO.descKey)}`}
    >
      <span className={`${big ? 'text-xs' : 'text-2xs'} text-textMuted`}>{t(MODE_PLAIN.labelKey)}</span>
      <Toggle size={big ? 'md' : 'sm'} checked={plain} onChange={setPlain} disabled={saving} />
    </label>
  )
}
