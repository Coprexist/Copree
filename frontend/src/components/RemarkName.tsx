import { displayName, hasRemark } from '../utils/remark'

interface Props {
  remark?: string | null
  name?: string | null
  className?: string
}

/**
 * 带备注的名字：备注（原名）
 *
 * 备注用主色、原名用弱化色——一眼分出「哪个是我起的称呼、哪个是真实名字」。
 * 纯文本场景（title、通知）用 utils/remark.ts 的 displayName()，不要各拼一遍。
 */
export default function RemarkName({ remark, name, className }: Props) {
  if (!hasRemark(remark)) return <span className={className}>{(name || '').trim()}</span>
  const base = (name || '').trim()
  return (
    <span className={className} title={displayName(remark, name)}>
      <span className="text-primary-500">{(remark || '').trim()}</span>
      {base && <span className="text-textMuted">（{base}）</span>}
    </span>
  )
}
