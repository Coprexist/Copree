#!/usr/bin/env node
/**
 * 一键生成界面截图（README / 文档用）。
 *
 *   node scripts/screenshot/run.mjs
 *   node scripts/screenshot/run.mjs --url http://127.0.0.1:5227 --only design
 *
 * 前提：后端 + 前端在跑（默认 http://127.0.0.1:5227）。
 * 数据：所有接口响应在浏览器侧被换成 scripts/screenshot/demo-data.mjs 里的演示数据，
 *       AI 头像发 frontend/public/official-avatars/ 下对应的那张（名字→文件见 demo-data.mjs 的
 *       AI_AVATARS），人类用户发现场生成的字母头像；不写数据库、不改业务代码。
 */
import { execFileSync } from 'node:child_process'
import crypto from 'node:crypto'
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { launchChrome, Session, sleep } from './cdp.mjs'
import { AI_AVATARS, DEFAULT_AVATAR_NAME } from './demo-data.mjs'
import { SHOTS, UI_SHOTS, VIEWPORT } from './shots.mjs'
// rewrite.mjs 在模块初始化时按 SHOT_LANG 建翻译表，所以解析完参数后再动态导入

const ROOT = path.resolve(import.meta.dirname, '../..')
const DEFAULT_URL = 'http://127.0.0.1:5227'
const DEFAULT_OUT = path.join(ROOT, 'docs/assets/screenshots')
const AVATAR_DIR = path.join(ROOT, 'frontend/public/official-avatars')
const AVATAR_MIME = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp' }

/** 演示数据给的文件名 → 图片字节。认不出的头像一律兜底到「我」那张，跟以前一张脸时代的行为一致 */
const avatarFiles = new Set(Object.values(AI_AVATARS))
const avatarCache = new Map()
function avatarBody(fileName) {
  const name = avatarFiles.has(fileName) ? fileName : AI_AVATARS[DEFAULT_AVATAR_NAME]
  if (!avatarCache.has(name)) {
    const file = path.join(AVATAR_DIR, name)
    avatarCache.set(name, {
      mime: AVATAR_MIME[path.extname(name).slice(1).toLowerCase()] || 'application/octet-stream',
      b64: readFileSync(file).toString('base64'),
    })
  }
  return avatarCache.get(name)
}

function parseArgs(argv) {
  const opts = { url: process.env.AISCHAT_URL || DEFAULT_URL, out: DEFAULT_OUT, only: null, lang: process.env.SHOT_LANG || 'zh' }
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--url') opts.url = argv[++i]
    else if (argv[i] === '--out') opts.out = path.resolve(argv[++i])
    else if (argv[i] === '--only') opts.only = argv[++i]
    else if (argv[i] === '--lang') opts.lang = argv[++i]
  }
  if (!['zh', 'en', 'ja'].includes(opts.lang)) throw new Error('--lang 只认 zh / en / ja：' + opts.lang)
  process.env.SHOT_LANG = opts.lang
  return opts
}

function jwtSecret() {
  if (process.env.JWT_SECRET) return process.env.JWT_SECRET.trim()
  const backend = process.env.AISCHAT_BACKEND_CONTAINER || 'ai_group_backend'
  return execFileSync('docker', ['exec', backend, 'printenv', 'JWT_SECRET_KEY'], { encoding: 'utf8' }).trim()
}

function mintToken(secret) {
  const b64 = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64url')
  const head = b64({ alg: 'HS256', typ: 'JWT' })
  const payload = b64({
    user_id: Number(process.env.AISCHAT_USER_ID || 1),
    username: 'screenshot',
    role: 'admin',
    exp: Math.floor(Date.now() / 1000) + 3600,
  })
  const body = head + '.' + payload
  return body + '.' + crypto.createHmac('sha256', secret).update(body).digest('base64url')
}

async function main() {
  const opts = parseArgs(process.argv.slice(2))
  const { rewriteApi, shouldPassThrough } = await import('./rewrite.mjs')
  // --only 可以给多个（逗号分隔），在 README 那 8 张与组件规范配图里一起找；
  // 不给 --only 时只拍 README 那 8 张，组件配图得点名（它们另有输出目录）
  const all = [...SHOTS, ...UI_SHOTS]
  const wanted = opts.only ? opts.only.split(',').map((s) => s.trim()).filter(Boolean) : null
  const shots = wanted ? all.filter((s) => wanted.includes(s.name)) : SHOTS
  if (!shots.length) throw new Error('没有匹配的截图：' + opts.only)
  if (wanted) {
    const missing = wanted.filter((n) => !all.some((s) => s.name === n))
    if (missing.length) console.warn('  ! 清单里没有这些名字：' + missing.join('、'))
  }

  const token = mintToken(jwtSecret())
  const profileDir = mkdtempSync(path.join(tmpdir(), 'copree-shot-'))
  const port = 9300 + Math.floor(Math.random() * 200)
  const { proc: chrome, wsUrl, bin } = await launchChrome({ port, profileDir })
  const session = await Session.connect(wsUrl)
  mkdirSync(opts.out, { recursive: true })

  console.log('目标：' + opts.url + ' ｜ 输出：' + opts.out + ' ｜ 界面语言：' + opts.lang + ' ｜ 浏览器：' + bin)

  await session.send('Page.enable')
  await session.send('Runtime.enable')
  await session.send('Network.enable')
  await session.send('Fetch.enable', {
    patterns: [
      { urlPattern: '*://*/api/fs/download-avatar/*', requestStage: 'Request' },
      { urlPattern: '*://*/api/*', requestStage: 'Response' },
    ],
  })

  // rawApi 的页签按原样看真实接口（控制台这类后台页：演示用户 role=user 会被路由挡回 /chat）
  let activeShot = null

  session.on('Fetch.requestPaused', async (p) => {
    const url = p.request.url
    try {
      // 头像：demo-avatar-<名字>、demo-group-<名字> 现场生成（人 / 群自定义图）；
      // 其余是演示数据里 AI 各自的文件名，发团队自绘那张
      if (url.includes('/fs/download-avatar/')) {
        const generated = url.match(/demo-(?:avatar|group)-([^/?#]+)\.png/)
        const avatar = generated ? null : avatarBody(decodeURIComponent((url.split('/fs/download-avatar/')[1] || '').split(/[?#]/)[0]))
        await session.send('Fetch.fulfillRequest', {
          requestId: p.requestId,
          responseCode: 200,
          responseHeaders: [
            { name: 'Content-Type', value: generated ? 'image/svg+xml' : avatar.mime },
            { name: 'Cache-Control', value: 'no-store' },
          ],
          body: generated ? letterAvatarSvg(decodeURIComponent(generated[1])) : avatar.b64,
        })
        return
      }
      const headers = p.responseHeaders || []
      const contentType = (headers.find((h) => h.name.toLowerCase() === 'content-type') || {}).value || ''
      // rawApi：这一张不换演示数据（只有真页面上才有的后台界面需要，用前先确认图里没有个人数据）
      if (!contentType.includes('json') || shouldPassThrough(url) || activeShot?.rawApi) {
        await continueResponse(session, p.requestId)
        return
      }
      const raw = await session.send('Fetch.getResponseBody', { requestId: p.requestId })
      const text = raw.base64Encoded ? Buffer.from(raw.body, 'base64').toString('utf8') : raw.body
      const rewritten = JSON.stringify(rewriteApi(new URL(url).pathname, JSON.parse(text)))
      const outHeaders = headers
        .filter((h) => h.name.toLowerCase() !== 'content-length')
        .concat([{ name: 'Content-Length', value: String(Buffer.byteLength(rewritten)) }])
      await session.send('Fetch.fulfillRequest', {
        requestId: p.requestId,
        responseCode: p.responseStatusCode || 200,
        responseHeaders: outHeaders,
        body: Buffer.from(rewritten).toString('base64'),
      })
    } catch (err) {
      console.warn('  改写失败，放行原响应：' + url + ' — ' + err.message)
      await continueResponse(session, p.requestId).catch(() => {})
    }
  })

  await session.send('Emulation.setDeviceMetricsOverride', { ...VIEWPORT, deviceScaleFactor: 2, mobile: false })
  await session.send('Page.navigate', { url: opts.url + '/login' })
  await sleep(2000)
  await session.eval('localStorage.setItem("access_token", ' + JSON.stringify(token) + ')')
  // App 取语言的顺序是：设置向导覆盖 → 用户设置 → 缓存。截图统一用第一档顶掉，跟演示数据的语言一致
  await session.eval('localStorage.setItem("i18n_override_lang", ' + JSON.stringify(opts.lang) + ')')

  for (const shot of shots) {
    activeShot = shot
    await session.send('Page.navigate', { url: opts.url + shot.path })
    await sleep(shot.settle || 4000)
    const prepare = typeof shot.prepare === 'function' ? shot.prepare() : shot.prepare
    for (const js of [].concat(prepare || [])) await session.eval(js).catch(() => {})
    await sleep(600)
    // 页面崩了（ErrorBoundary / Vite 报错浮层）照样能截图，但图是废的 —— 必须报出来
    const broken = await session.eval('document.querySelectorAll("vite-error-overlay, [data-error-boundary], .error-boundary").length').catch(() => 0)
    if (broken) console.warn('  ! ' + shot.name + '：页面渲染出错，这张图不可用')
    // clip：只截某个元素（组件规范配图用）。先滚进视口，再按整页坐标算矩形
    let clip
    if (shot.clip) {
      await session.eval('(() => { const el = document.querySelector(' + JSON.stringify(shot.clip) + '); if (el) el.scrollIntoView({ block: "center", inline: "nearest" }); return !!el })()').catch(() => false)
      await sleep(400)
      clip = await session.eval('(() => {' +
        'const el = document.querySelector(' + JSON.stringify(shot.clip) + ');' +
        'if (!el) return null;' +
        'const r = el.getBoundingClientRect();' +
        'return { x: r.x + window.scrollX, y: r.y + window.scrollY, width: r.width, height: r.height, scale: 1 } })()').catch(() => null)
      if (!clip) console.warn('  ! ' + shot.name + '：clip 选择器没找到，退回整页')
    }
    // 满屏插画类页面用 JPEG（PNG 会有 1.5 MB）；文字密集的界面用 PNG（更锐利）
    const format = shot.format || 'png'
    const { data } = await session.send('Page.captureScreenshot', {
      format,
      quality: format === 'jpeg' ? (shot.quality || 88) : undefined,
      fromSurface: true,
      clip: clip || undefined,
    })
    const ext = format === 'jpeg' ? '.jpg' : '.png'
    // out：这一张自己的子目录（组件规范配图住 docs/assets/screenshots/ui/）
    const file = path.join(opts.out, shot.out || '', shot.name + ext)
    mkdirSync(path.dirname(file), { recursive: true })
    writeFileSync(file, Buffer.from(data, 'base64'))
    console.log('  ✓ ' + shot.name + ext + '  ' + (clip
      ? Math.round(clip.width * 2) + 'x' + Math.round(clip.height * 2) + '（元素裁切）'
      : VIEWPORT.width * 2 + 'x' + VIEWPORT.height * 2))
  }

  session.close()
  chrome.kill()
  console.log('完成：' + shots.length + ' 张')
}

/** Chrome 是 detached 起的，脚本结束时必须自己收尾，否则事件循环不退出 */
function shutdown(code) {
  process.exit(code)
}

/**
 * 现场生成的字母头像 / 群头像方块（SVG）：颜色由名字稳定派生，不依赖任何第三方
 * 图片素材，因此截图里的人类用户头像与群「自定义头像」都不是别人的作品。
 */
function letterAvatarSvg(name) {
  const hue = [...String(name)].reduce((h, ch) => (h * 31 + ch.codePointAt(0)) >>> 0, 0) % 360
  const initial = escapeXml([...String(name)][0] || '?')
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">' +
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">' +
    '<stop offset="0" stop-color="hsl(' + hue + ' 62% 58%)"/>' +
    '<stop offset="1" stop-color="hsl(' + ((hue + 38) % 360) + ' 64% 42%)"/>' +
    '</linearGradient></defs>' +
    '<rect width="256" height="256" fill="url(#g)"/>' +
    '<text x="128" y="136" text-anchor="middle" dominant-baseline="central" ' +
    'font-family="system-ui, -apple-system, Segoe UI, Noto Sans SC, sans-serif" ' +
    'font-size="118" font-weight="600" fill="rgba(255,255,255,.94)">' + initial + '</text></svg>'
  return Buffer.from(svg).toString('base64')
}

function escapeXml(text) {
  return text.replace(/[<>&"']/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' })[c])
}

async function continueResponse(session, requestId) {
  try {
    await session.send('Fetch.continueResponse', { requestId })
  } catch {
    await session.send('Fetch.continueRequest', { requestId })
  }
}

main().then(() => shutdown(0)).catch((err) => {
  console.error(err.message)
  shutdown(1)
})
