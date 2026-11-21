// 全页截图：用 CDP 的 captureBeyondViewport，避免只截视口
import { spawn } from 'node:child_process'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const args = process.argv.slice(2)
const width = Number(args[0] || 1440)
const outPrefix = args[1] || 'shot'
const urls = args.slice(2)

const port = 9333
const profile = mkdtempSync(join(tmpdir(), 'chrome-'))
const chrome = spawn('google-chrome-stable', [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  `--window-size=${width},1000`, 'about:blank',
], { stdio: 'ignore' })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitJson(url, tries = 60) {
  for (let i = 0; i < tries; i++) {
    try {
      const res = await fetch(url)
      if (res.ok) return await res.json()
    } catch {}
    await sleep(250)
  }
  throw new Error(`CDP 未就绪: ${url}`)
}

class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map() }
  static async open(wsUrl) {
    const ws = new WebSocket(wsUrl)
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
    const cdp = new Cdp(ws)
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.id && cdp.pending.has(msg.id)) {
        const { resolve, reject } = cdp.pending.get(msg.id)
        cdp.pending.delete(msg.id)
        msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result)
      }
    }
    return cdp
  }
  send(method, params = {}) {
    const id = ++this.id
    this.ws.send(JSON.stringify({ id, method, params }))
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }))
  }
}

try {
  await waitJson(`http://127.0.0.1:${port}/json/version`)
  for (const [i, url] of urls.entries()) {
    const target = await waitJson(`http://127.0.0.1:${port}/json/list`).then((list) => {
      const page = list.find((t) => t.type === 'page')
      if (!page) throw new Error('没有可用的 page target')
      return page
    })
    const cdp = await Cdp.open(target.webSocketDebuggerUrl)
    await cdp.send('Page.enable')
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width, height: 900, deviceScaleFactor: 1, mobile: width < 600,
    })
    await cdp.send('Page.navigate', { url })
    await sleep(1800)
    // 先滚一遍触发 loading="lazy"，否则长图截出来是空白
    await cdp.send('Runtime.evaluate', {
      expression: `(async () => {
        document.documentElement.style.scrollBehavior = 'auto'
        const step = 800
        for (let y = 0; y < document.body.scrollHeight; y += step) {
          window.scrollTo(0, y)
          await new Promise((r) => setTimeout(r, 60))
        }
        window.scrollTo(0, 0)
        await new Promise((r) => setTimeout(r, 400))
      })()`,
      awaitPromise: true,
    })
    // 等图片全部解码完（公网经 frp 时大图可能慢于截图时机）
    await cdp.send('Runtime.evaluate', {
      expression: `Promise.race([
        Promise.all([...document.images].map((img) => img.complete ? 0 : new Promise((r) => {
          img.addEventListener('load', r, { once: true })
          img.addEventListener('error', r, { once: true })
        }))),
        new Promise((r) => setTimeout(r, 15000)),
      ])`,
      awaitPromise: true,
    })
    const { cssContentSize } = await cdp.send('Page.getLayoutMetrics')
    const height = Math.ceil(cssContentSize.height)
    const TILE = 1200
    for (let y = 0, k = 0; y < height; y += TILE, k++) {
      const h = Math.min(TILE, height - y)
      const { data } = await cdp.send('Page.captureScreenshot', {
        format: 'png', captureBeyondViewport: true,
        clip: { x: 0, y, width, height: h, scale: 1 },
      })
      const file = `${outPrefix}-${i}-${k}.png`
      writeFileSync(file, Buffer.from(data, 'base64'))
      console.log(`${file}  ${width}x${h}@${y}  ${url}`)
    }
    cdp.ws.close()

  }
} finally {
  chrome.kill()
}
