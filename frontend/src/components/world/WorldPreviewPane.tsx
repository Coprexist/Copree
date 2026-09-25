/**
 * 世界预览的唯一实现：iframe 画布 + 「刷新 / 沉浸窗口」操作条。
 *
 * 中栏、专注模式的覆盖层、移动端预览 tab 三处都从这里取——各写一份时，
 * 刷新语义与沉浸跳转（tryOpenWorldWindow 的 WebView 兜底）很容易只改到一处。
 */
import { ExternalLink, RefreshCw } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { tryOpenWorldWindow } from '../../utils/worldView'
import { useT } from '../../i18n/I18nContext'

/** 操作条：只出按钮，放进各处的工具条里（调用方负责外层间距） */
export function WorldPreviewActions({ wid, onRefresh }: { wid: number; onRefresh: () => void }) {
  const t = useT()
  const navigate = useNavigate()
  return (
    <>
      <button
        onClick={onRefresh}
        className="inline-flex items-center gap-1 text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors shrink-0"
        title={t('tool:world.pane.refresh')}
      >
        <RefreshCw size={12} /> {t('tool:world.pane.refresh')}
      </button>
      <button
        onClick={() => { if (!tryOpenWorldWindow(wid)) navigate(`/world-view/${wid}`) }}
        className="inline-flex items-center gap-1 text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors shrink-0"
        title={t('tool:world.pane.immersiveHint')}
      >
        <ExternalLink size={12} /> {t('tool:world.pane.immersive')}
      </button>
    </>
  )
}

/** 画布：铺满父容器（父容器负责 flex-1 / min-h-0，这里只负责白底与自适应） */
export default function WorldPreviewFrame({ wid, previewKey }: { wid: number; previewKey: number }) {
  const t = useT()
  return (
    <iframe
      key={previewKey}
      src={`/world/${wid}/preview`}
      className="w-full h-full bg-white dark:bg-gray-900"
      title={t('tool:world.pane.preview')}
    />
  )
}
