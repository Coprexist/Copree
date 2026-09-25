/**
 * 表达方式开关（专业模式 / 通俗模式）—— 只出现在群视界的世界对话工具条上。
 *
 * 后端 build_expression_segment 的唯一调用点是 world_chat_service：这两个模式只改变
 * 世界里的 AI 怎么说话。普通群聊/私信不认它，所以那边不再摆这个开关（摆了也是死的，
 * 只会让人以为它管普通聊天）。
 *
 * 形态定稿为「通俗模式 + 滑块」：关＝专业模式。中途试过两段文字直选，放进世界对话
 * 那条全是小图标的工具条里太抢眼（用户原话「一点都不搭」）；滑块 48×24 与旁边的图标
 * 按钮同高，开关状态本身也一眼看得出来，不用先解释再点。
 */
import Toggle from './Toggle'
import { usePlainLanguage } from '../hooks/usePlainLanguage'
import { useT } from '../i18n/I18nContext'

/** 两个模式：存储上 plain=true 是通俗，false 是专业（缺省即专业）。 */
export const MODE_PRO = {
  plain: false,
  labelKey: 'worldChat.expressionModePro',
  descKey: 'worldChat.expressionModeProDesc',
}
export const MODE_PLAIN = {
  plain: true,
  labelKey: 'worldChat.expressionModePlain',
  descKey: 'worldChat.expressionModePlainDesc',
}

export default function ExpressionModeSwitch() {
  const t = useT()
  const { plain, setPlain, saving } = usePlainLanguage()

  return (
    <label
      className="inline-flex items-center shrink-0 cursor-pointer select-none gap-1.5"
      title={`${t('worldChat.expressionMode')}：${t(plain ? MODE_PLAIN.descKey : MODE_PRO.descKey)}`}
    >
      <span className="text-2xs text-textMuted">{t(MODE_PLAIN.labelKey)}</span>
      <Toggle size="sm" checked={plain} onChange={setPlain} disabled={saving} />
    </label>
  )
}
