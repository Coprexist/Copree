import { KeyRound } from 'lucide-react'
import { useT } from '../../i18n/I18nContext'
import { HelpTip } from '../ui'

/**
 * 「这家已经存了 Key」的小标记：一把 keyround 图标，悬停/点击都出说明。
 *
 * 为什么是一个组件而不是各处各写：这同一个语义现在出现在两个地方，形态只差定位——
 *   corner  厂商卡片标题的右上角（绝对定位、不参与布局，名字再长也挤不走它）
 *   inline  胶囊按钮里紧跟名字（正常流里的一个 flex 项）
 * 图标尺寸、说明文案、打开方式（悬停 150ms + 点击）都该是同一份，改文案只改一处。
 *
 * 说明走平台的 HelpTip（portal + fixed）：卡片里有 overflow 裁剪，弹窗里又有滚动容器，
 * 挂原地会被裁掉。
 */
export default function KeyMark({
  last4,
  placement = 'inline',
}: {
  /** 已存 Key 的末四位（说明里要露出来，别让人猜是哪把） */
  last4?: string
  placement?: 'corner' | 'inline'
}) {
  const t = useT()
  const hint = t('apiConfig:hasKeyHint', { last4: last4 ?? '' })
  const corner = placement === 'corner'
  return (
    <HelpTip
      openOn="both"
      align={corner ? 'right' : 'left'}
      label={hint}
      content={hint}
      className={corner ? 'absolute -top-1 right-0 text-textMuted' : 'shrink-0 text-textMuted'}>
      <KeyRound size={10} />
    </HelpTip>
  )
}
