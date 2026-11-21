/**
 * 聊天头部占掉的高度（顶部让位量的唯一定义）。
 *
 * 单独一个文件、而不是从 ChatHeader 里导出来：组件文件只导出组件，编辑时才不会因为
 * 「同一个文件既导出组件又导出常量」让 React Fast Refresh 失效（Vite 会整页 reload，
 * 改样式时白屏一下）。ChatArea / DMChatView 把它写进容器的 `--header-inset`。
 *
 * 高度固定（3.5rem 条 + 0.5rem 呼吸间距 + 刘海安全区），所以不用 ResizeObserver 量。
 */
export const CHAT_HEADER_INSET = 'calc(3.5rem + 0.5rem + env(safe-area-inset-top, 0px))'

/**
 * 浮层（顶部提示条等）要贴的那条线：头部条的**下沿**，比 CHAT_HEADER_INSET 少一段呼吸间距。
 *
 * 两条线分开的原因：消息从 INSET 开始（离头部留 0.5rem 呼吸），而提示条是浮层，
 * 贴 INSET 会在它和头部之间露出一条 0.5rem 的消息——看着就是"条和顶部之间有空隙"。
 */
export const CHAT_HEADER_BAR = 'calc(3.5rem + env(safe-area-inset-top, 0px))'

/** 头部下沿到消息起点之间那段呼吸间距（浮层贴线时要从 INSET 里减掉它） */
export const CHAT_HEADER_GAP = '0.5rem'
