/**
 * 一条 SSE 流的读帧器：fetch + reader + 分帧 + 断线重连。
 *
 * 为什么重连必须在这里而不是各页面自己写循环：页面手写 read 循环时，「流断了」和
 * 「对端没有新消息」在界面上长得一模一样。DSH 的提问与审批是阻塞式的——人不在页面
 * 上回答，会话就停在原地；而断线后页面静默地什么都收不到，人就一直卡着。
 * 所以断线是必须自愈的：重连后 DSH 侧补发未答请求与快照，状态自己就回来了。
 */
export type SseStreamOptions = {
  url: string
  /** 额外请求头（鉴权等）；取值时机由调用方决定 */
  headers?: Record<string, string>
  /** 中止信号：abort 之后不再重连，函数 resolve */
  signal: AbortSignal
  /** 每收到一帧调用一次；解析失败的帧直接跳过（半截帧不该打断整条流） */
  onFrame: (frame: any) => void
  /** 断线后到下一次重连的等待，默认 1.5s */
  retryDelayMs?: number
  /** 每次重连前调用（attempt 从 1 起）；用于在界面上给出「正在重连」的可见状态 */
  onRetry?: (attempt: number) => void
  /** 唯一一次显式报错：响应本身就不通（如 401/503）时给出原因 */
  onError?: (error: unknown) => void
}

/** 一直读到 abort 为止；正常结束（对端关闭）也会按 retryDelayMs 重连。 */
export async function streamSse(options: SseStreamOptions): Promise<void> {
  const { url, headers, signal, onFrame, retryDelayMs = 1500, onRetry, onError } = options
  for (let attempt = 0; !signal.aborted; attempt += 1) {
    // 这一次连上过没有：连上过再断是常态（代理掐断/对端重启），交给重连自愈；
    // 压根没连上（未注册/未同意/鉴权失败）才值得把原因说给人听。
    let connected = false
    try {
      const response = await fetch(url, { headers, signal })
      if (!response.ok || !response.body) throw new Error('HTTP ' + response.status)
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      for (; ;) {
        const chunk = await reader.read()
        if (chunk.done) break
        buffer += decoder.decode(chunk.value, { stream: true })
        // SSE 以空行分帧；最后一段可能被切断，留在 buffer 里等下一块
        const blocks = buffer.split('\n\n')
        buffer = blocks.pop() || ''
        for (const block of blocks) {
          const dataLine = block.split('\n').find((line) => line.startsWith('data: '))
          if (!dataLine) continue
          try { onFrame(JSON.parse(dataLine.slice(6))); connected = true } catch { /* 坏帧跳过 */ }
        }
      }
    } catch (error) {
      if (signal.aborted) return
      if (!connected) onError?.(error)
    }
    if (signal.aborted) return
    onRetry?.(attempt + 1)
    await new Promise((resolve) => setTimeout(resolve, retryDelayMs))
  }
}
