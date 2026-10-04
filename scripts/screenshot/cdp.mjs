/**
 * 极简 CDP 客户端：起一个 headless Chrome，通过 DevTools 协议驱动页面。
 * 只实现截图脚本需要的最小集合（导航 / 求值 / 事件 / 截图）。
 */
import { spawn } from 'node:child_process'

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  '/usr/bin/google-chrome-stable',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].filter(Boolean)

export async function launchChrome({ port, profileDir }) {
  let lastErr
  for (const bin of CHROME_CANDIDATES) {
    try {
      const proc = spawn(bin, [
        '--headless=new',
        '--remote-debugging-port=' + port,
        '--user-data-dir=' + profileDir,
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-gpu',
        '--hide-scrollbars',
        '--force-color-profile=srgb',
        // 容器里 /dev/shm 通常只有 64MB，不关掉它渲染进程会直接崩：进程活着但调试端口始终没有页面
        '--disable-dev-shm-usage',
        '--no-sandbox',
        'about:blank',
      ], { stdio: ['ignore', 'ignore', 'pipe'], detached: true })
      // 候选路径不存在时 spawn 抛 ENOENT：不接住会变成未处理异常打死整个进程，后面的候选一个也试不到
      proc.on('error', () => {})
      proc.stderr.resume()
      proc.unref()
      const wsUrl = await waitForTarget(port)
      if (wsUrl) return { proc, wsUrl, bin }
      proc.kill()
    } catch (e) {
      lastErr = e
    }
  }
  throw new Error('未找到可用的 Chrome/Chromium（可用 CHROME_PATH 指定）：' + (lastErr?.message || ''))
}

async function waitForTarget(port) {
  for (let i = 0; i < 80; i++) {
    try {
      const targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json()
      const page = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl)
      if (page) return page.webSocketDebuggerUrl
    } catch {}
    await sleep(250)
  }
  return null
}

export class Session {
  constructor(ws) {
    this.ws = ws
    this.seq = 0
    this.pending = new Map()
    this.handlers = new Map()
    ws.onmessage = (ev) => this._dispatch(JSON.parse(ev.data))
  }

  static async connect(wsUrl) {
    const ws = new WebSocket(wsUrl)
    await new Promise((resolve, reject) => {
      ws.onopen = resolve
      ws.onerror = () => reject(new Error('无法连接 CDP：' + wsUrl))
    })
    const session = new Session(ws)
    session.errorCount = 0
    return session
  }

  _dispatch(msg) {
    if (msg.id && this.pending.has(msg.id)) {
      const { resolve, reject } = this.pending.get(msg.id)
      this.pending.delete(msg.id)
      msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result)
      return
    }
    for (const handler of this.handlers.get(msg.method) || []) {
      // 处理函数各自 await，不阻塞其它 CDP 消息的分发
      Promise.resolve(handler(msg.params)).catch(() => {})
    }
  }

  send(method, params = {}, timeoutMs = 30000) {
    const id = ++this.seq
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.ws.send(JSON.stringify({ id, method, params }))
      setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error('CDP 超时：' + method))
      }, timeoutMs)
    })
  }

  /** 事件处理失败不致命：调试期只做提示 */
  on(method, handler) {
    if (!this.handlers.has(method)) this.handlers.set(method, [])
    this.handlers.get(method).push(handler)
  }

  async eval(expression) {
    const r = await this.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (r.exceptionDetails) throw new Error('页面脚本出错：' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text))
    return r.result?.value
  }

  close() {
    try { this.ws.close() } catch {}
  }
}
