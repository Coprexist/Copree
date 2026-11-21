/**
 * 聊天面板顶部的「头像 + 名字 + 状态 + 操作」条 —— 群聊与私信共用这一份。
 *
 * 它**浮在消息上**：消息从这条底下滚过去，条的外观与全站顶部条完全一致（.top-bar：底色 + 一层软阴影），
 * 不额外加渐变或模糊——加了会和页面顶部条看起来深浅不一（2026-11-14 用户指出三个顶部条不一样深）。
 *
 * 高度固定（3.5rem 条 + 0.5rem 呼吸间距 + 顶部安全区），让位量在 shared/chatHeaderInset.ts（单独一个文件，
 * 免得组件文件既导出组件又导出常量，把 Fast Refresh 弄失效）：
 * 容器把 CHAT_HEADER_INSET 写进 `--header-inset`，`.chat-scroll` 的 padding-top 读它
 * （见 ui_system.md「浮层让位」）。滚动条不读变量（每帧读要强制样式重算），改成量这条的
 * rect —— 认的是 `data-float="top"`，见 MobileScrollbar。
 */
import type { ReactNode } from 'react'
import { ArrowLeft } from 'lucide-react'

interface Props {
  /** 手机端的返回（回会话列表）；不传就没有这颗 */
  onBack?: () => void
  backTitle?: string
  /** 头像（群/私信各自组装，32px） */
  avatar: ReactNode
  title: ReactNode
  /** 名字后面的小标：联邦、系统、AI… */
  chips?: ReactNode
  /** 第二行：在线人数、状态、免打扰、用量 —— 一行截断 */
  status?: ReactNode
  /** 右上操作区：一律用 .icon-btn，静音/免打扰这类"已开启"用 .is-alert */
  actions?: ReactNode
}

export default function ChatHeader({ onBack, backTitle, avatar, title, chips, status, actions }: Props) {
  return (
    <div data-float="top" className="absolute inset-x-0 top-0 z-20" style={{ paddingTop: 'env(safe-area-inset-top, 0px)' }}>
      <div className="top-bar h-14 px-3 md:px-4 flex items-center gap-2">
        {onBack && (
          <button onClick={onBack} className="icon-btn -ml-1.5 md:hidden" title={backTitle}>
            <ArrowLeft size={18} />
          </button>
        )}
        <div className="shrink-0 flex items-center">{avatar}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="font-semibold text-textPrimary text-sm truncate">{title}</span>
            {chips}
          </div>
          {status && <div className="text-3xs text-textMuted truncate">{status}</div>}
        </div>
        {actions && <div className="shrink-0 flex items-center gap-0.5">{actions}</div>}
      </div>
    </div>
  )
}
