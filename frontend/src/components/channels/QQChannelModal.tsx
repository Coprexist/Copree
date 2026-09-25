import { MessageSquare, X } from 'lucide-react'
import { useT } from '../../i18n/I18nContext'
import { Dialog } from '../ui'
import QQChannelCard from './QQChannelCard'

/**
 * QQ 通道的独立弹窗 —— 通道配置里有引导、凭据、落点、策略、配对四块，
 * 塞进「详细设置」那张长表单里会挤成一团。这里给它一间自己的屋子（和群视界那套弹窗同规格）。
 */
export default function QQChannelModal({ agentId, onClose }: { agentId: number; onClose: () => void }) {
  const t = useT()
  // layer="toast"：详细设置本身就是 z-toast（60），默认的 z-modal（50）会被它盖住；
  // 两个同层时后渲染的那个在上，所以这里的弹窗能稳稳压在设置弹窗之上。
  return (
    <Dialog onClose={onClose} layer="toast" className="flex items-center justify-center p-4">
      <div
        className="w-full max-w-lg sm:max-w-2xl lg:max-w-4xl bg-surface border border-border rounded-dialog max-h-[85vh] flex flex-col shadow-xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-surface border-b border-border px-4 py-3 flex items-center gap-2 shrink-0">
          <MessageSquare size={15} className="text-primary-400 shrink-0" />
          <span className="text-sm font-semibold text-textPrimary truncate">{t('tool:channel.title')}</span>
          <span className="text-3xs text-textMuted hidden sm:inline truncate">{t('tool:channel.desc')}</span>
          <button onClick={onClose} className="ml-auto p-1 text-textMuted hover:text-textPrimary transition-colors shrink-0">
            <X size={16} />
          </button>
        </div>
        <div className="px-4 py-4 overflow-y-auto">
          {/* 标题栏已经写了"QQ 通道"，卡片自己就不用再亮一次牌子 */}
          <QQChannelCard agentId={agentId} showTitle={false} />
        </div>
      </div>
    </Dialog>
  )
}
