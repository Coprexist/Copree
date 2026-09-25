// Bridge smoke test: mount the built host half against a mock webServer and a
// fake sessionController, then drive /copree-bridge end to end (auth, sessions,
// prompt, SSE frames, cancel, heartbeat registration, secret-less disable).
import http from 'node:http'
import { existsSync, readFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

/** 同意状态与实例身份落在自检自己的 DSH_HOME 里：不许碰真实 DSH 的 home */
process.env.DSH_HOME = join(tmpdir(), 'dsh-copree-smoke-home')
// 每遍都从「没同意过」开始：否则上一遍点过的同意会留在文件里，
// 「默认未同意」这条安全断言第二遍就失效了（实测踩到过）
rmSync(join(process.env.DSH_HOME, 'dsh-copree-consent.json'), { force: true })
const SMOKE_HEARTBEAT_MS = 150

/** 轮询等待某个条件成立（路由注册/摘除都是心跳触发的异步动作） */
async function waitFor(cond, ms = 3000) {
  const until = Date.now() + ms
  for (;;) {
    if (cond()) return true
    if (Date.now() > until) return false
    await new Promise((r) => setTimeout(r, 20))
  }
}
const bridgeRoutes = (server) => server.routes.filter((r) => r.path === '/copree-bridge')

const BACKEND_PORT = 59328
const GATEWAY_PORT = 59329
const SECRET = 'smoke-secret'
const TOKEN = { 'x-copree-bridge-token': SECRET }

/** 附件落盘目录用工作区内的临时目录：沙箱只允许写工作区，也方便测完清掉 */
const SMOKE_CWD = join(process.cwd(), '.smoke-work')

let failures = 0
function check(name, ok, extra = '') {
  if (ok) { console.log(`ok   - ${name}`) } else { failures += 1; console.log(`FAIL - ${name} ${extra}`) }
}

// ── 1. mock Copree backend: only the register heartbeat ────────────────
const registrations = []
// 同意由 Copree 侧说了算：自检里可切换，用来验证「未同意 → 路由不存在」这条断言
let registerHits = 0
const backend = http.createServer((req, res) => {
  let body = ''
  req.on('data', (c) => { body += c })
  req.on('end', () => {
    if (req.url && req.url.startsWith('/dsh-bridge/attachment/')) {
      if (req.headers['x-copree-bridge-token'] !== SECRET) { res.writeHead(401); res.end(); return }
      res.writeHead(200, { 'content-type': 'image/png' })
      res.end(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==', 'base64'))
      return
    }
    if (req.url === '/dsh-bridge/register') {
      registrations.push({ token: req.headers['x-copree-bridge-token'], body: JSON.parse(body || '{}') })
      res.writeHead(200, { 'content-type': 'application/json' })
      registerHits += 1
      res.end('{"ok":true}')
      return
    }
    res.writeHead(404)
    res.end()
  })
})
await new Promise((r) => backend.listen(BACKEND_PORT, '127.0.0.1', r))

// ── 2. fake sessionController ─────────────────────────────────────────
const calls = { list: 0, create: [], prompt: [], cancel: [], follow: [] }
let followSignal = null
function fakeController() {
  return {
    async list() {
      calls.list += 1
      return { items: [{ sessionId: 'session-1', cwd: SMOKE_CWD, updatedAt: 1700000000000, running: false, title: '既有会话', projections: { secret: 'no' } }] }
    },
    async create(request) { calls.create.push(request); return { sessionId: 'session-new' } },
    // 与真 SessionController 一致：prompt 会对 signal 立即 throwIfAborted，缺 signal 必须炸
    async prompt(request, signal) {
      if (signal === undefined) throw new TypeError("Cannot read properties of undefined (reading 'throwIfAborted')")
      signal.throwIfAborted()
      calls.prompt.push(request)
      return { accepted: true }
    },
    async cancel(request) { calls.cancel.push(request); return { ok: true } },
    async *follow(request, signal) {
      calls.follow.push(request)
      followSignal = signal
      // session-hold：只补一份空快照就挂着，专门用来测"有人在看"时的问询过桥
      if (request?.address?.sessionId === 'session-hold') {
        yield { type: 'snapshot', records: [] }
        await new Promise((resolve) => signal.addEventListener('abort', resolve, { once: true }))
        return
      }
      yield { type: 'snapshot', records: [
        { type: 'event', event: { type: 'user/message', data: { content: [{ type: 'text', text: '帮我改代码' }], source: { kind: 'user' } } } },
        { type: 'event', event: { type: 'system/message', data: { message: { content: [{ type: 'text', text: '系统提示' }] } } } },
        { type: 'event', event: { type: 'assistant/message', data: { message: { content: [{ type: 'text', text: '好的' }] } } } },
      ] }
      yield { type: 'assistant-stream', frame: { type: 'chunk', chunk: { type: 'text-delta', text: '正在' } } }
      yield { type: 'assistant-stream', frame: { type: 'chunk', chunk: { type: 'reasoning-delta', text: '内心戏' } } }
      yield { type: 'event', event: { type: 'tool/call', data: { callId: 'c1', name: 'file_edit', arguments: '{"path":"a.py"}' } } }
      yield { type: 'event', event: { type: 'tool/result', data: { message: { source: { callId: 'c1' }, content: [{ type: 'tool-result', toolCallId: 'c1', content: [{ type: 'text', text: '已改 3 行' }] }] } } } }
      yield { type: 'event', event: { type: 'user/message', data: { content: [{ type: 'text', text: '再改一处' }], source: { kind: 'user' } } } }
      yield { type: 'event', event: { type: 'assistant/message', data: { message: { content: [{ type: 'text', text: '改完了' }] } } } }
      yield { type: 'event', event: { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } } }
      // PTC 复合调用：每个子操作是 DSH 自己的一条 tool/ptc-dispatch 记录，
      // 桥接要照它的口径把子操作铺成独立行（子行有自己的卡片与结果行）
      yield { type: 'event', event: { type: 'tool/call', data: { callId: 'c2', name: 'run_code', arguments: '{"description":"改两个文件"}' } } }
      yield { type: 'event', event: { type: 'tool/ptc-dispatch', data: { rootCallId: 'c2', parentCallId: 'c2', subCallId: 's1', name: 'file_edit', arguments: '{"path":"a.py"}', isError: false, content: [{ type: 'text', text: '已改 a.py' }] } } }
      yield { type: 'event', event: { type: 'tool/result', data: { message: { source: { callId: 'c2' }, content: [{ type: 'tool-result', toolCallId: 'c2', content: [{ type: 'text', text: '程序自己的一段长输出' }] }] } } } }
    },
  }
}

// ── 3. mount the built host half against a mock carrier ───────────────
function mockWebServer() {
  const registrations = []
  return {
    routes: registrations,
    // 与真实 webServer 一致：返回 disposer 并把路由摘掉（「撤销同意 → 路由消失」靠它验证）
    register(route) {
      registrations.push(route)
      return () => {
        const index = registrations.indexOf(route)
        if (index >= 0) registrations.splice(index, 1)
      }
    },
    registerUpgrade() { return () => {} },
  }
}
function gatewayFor(routes, port) {
  const server = http.createServer((req, res) => {
    const url = req.url ?? '/'
    const route = routes.filter((r) => url.startsWith(r.path)).sort((a, b) => b.path.length - a.path.length)[0]
    if (!route) { res.writeHead(404); res.end(); return }
    route.handler(req, res)
  })
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(server)))
}

// 回答者瀑布：把插件注册的监听捕获下来，测试里手动触发
const listeners = new Map()
const eventBus = {
  on(name, fn) { listeners.set(name, fn) },
  off(name) { listeners.delete(name) },
}

/**
 * 假 tools 服务：只为验证「标签与行数都来自声明」这条路。
 * presentResult 故意返回两块内容（编辑 / 读取），对应 PTC 下 run_code 的子操作。
 */
const fakeTools = {
  register: () => {},
  get: (name) => ({
    presentCall: (args) => ({ card: 'generic', kind: 'execute', title: `${name}：${args?.path ?? ''}`, rawInput: args }),
    presentResult: () => ({ card: 'generic', content: [{ type: 'text', text: '编辑 a.ts' }, { type: 'text', text: '读取 b.ts' }] }),
  }),
}

const mod = await import('../lib/index.js')
const webServer = mockWebServer()
const controller = fakeController()
const noop = () => {}
mod.apply(
  {
    webServer,
    ...eventBus,
    tools: fakeTools,
    systemPrompt: { section: noop },
    sessionController: controller,
    logger: { info: noop },
    effect: () => () => {},
  },
  {
    backendUrl: `http://127.0.0.1:${BACKEND_PORT}`,
    pluginSourceDir: '',
    bridgeEnabled: true,
    bridgeSecret: SECRET,
    bridgeAdvertiseUrl: 'http://10.0.0.1:3082',
    bridgeHeartbeatMs: SMOKE_HEARTBEAT_MS,
  },
)
const gateway = await gatewayFor(webServer.routes, GATEWAY_PORT)
const base = `http://127.0.0.1:${GATEWAY_PORT}/copree-bridge`

// 闸门在 DSH 侧：没在设置里点同意之前，不该有桥接路由、也不该发心跳（连"被检测到"都不该有）
const consentOf = async () => (await (await fetch(`http://127.0.0.1:${GATEWAY_PORT}/copree-consent`)).json()).allowed
check('默认未同意：不注册桥接路由', bridgeRoutes(webServer).length === 0, JSON.stringify(webServer.routes.map((r) => r.path)))
check('默认未同意：一个心跳都不发', registerHits === 0, `hits=${registerHits}`)
check('同意端点此时报告未同意', (await consentOf()) === false)
const opened = await fetch(`http://127.0.0.1:${GATEWAY_PORT}/copree-consent`, {
  method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ allowed: true }),
})
check('在 DSH 里点同意后立即可用', opened.ok && (await waitFor(() => bridgeRoutes(webServer).length > 0)) && (await waitFor(() => registerHits > 0)))

const anonymous = await fetch(`${base}/status`)
check('未带密钥的请求被拒', anonymous.status === 401)

const status = await (await fetch(`${base}/status`, { headers: TOKEN })).json()
check('status 报告插件身份', status.ok === true && status.plugin === 'dsh-copree' && typeof status.version === 'string', JSON.stringify(status))
// 诊断字段：提问/审批只发给「正在看这条会话」的页面，没人看时回落 DSH 默认链
check('status 报出谁在看（订阅诊断）', Array.isArray(status.streams), JSON.stringify(status))

const sessions = await (await fetch(`${base}/sessions`, { headers: TOKEN })).json()
check('sessions 只透出展示字段', sessions.items.length === 1 && sessions.items[0].sessionId === 'session-1' && sessions.items[0].cwd === SMOKE_CWD && sessions.items[0].projections === undefined, JSON.stringify(sessions))

const created = await (await fetch(`${base}/prompt`, {
  method: 'POST', headers: { ...TOKEN, 'content-type': 'application/json' },
  body: JSON.stringify({ text: '  帮我改代码  ' }),
})).json()
check('无 sessionId 时先建会话再投递', created.sessionId === 'session-new' && calls.create.length === 1 && calls.prompt.length === 1 && calls.prompt[0].content[0].text === '  帮我改代码  ', JSON.stringify(created))
check('投递带 requestId（幂等锚点）', typeof calls.prompt[0].requestId === 'string' && calls.prompt[0].requestId.length > 8)
const reused = await (await fetch(`${base}/prompt`, {
  method: 'POST', headers: { ...TOKEN, 'content-type': 'application/json' },
  body: JSON.stringify({ text: '继续', sessionId: 'session-1' }),
})).json()
check('带 sessionId 时复用既有会话', reused.sessionId === 'session-1' && calls.create.length === 1 && calls.prompt.length === 2)

const empty = await fetch(`${base}/prompt`, { method: 'POST', headers: { ...TOKEN, 'content-type': 'application/json' }, body: JSON.stringify({ text: '   ', sessionId: 'session-1' }) })
check('空文本被拒', empty.status === 400)

const streamResponse = await fetch(`${base}/stream?sessionId=session-1`, { headers: TOKEN })
const streamText = await streamResponse.text()
const frames = streamText.split('\n\n').filter((block) => block.startsWith('data: ')).map((block) => JSON.parse(block.slice(6)))
const kinds = frames.map((f) => f.k)
check('SSE 帧序完整且跳过不桥接的事件', JSON.stringify(kinds) === JSON.stringify(['snapshot', 'delta', 'think', 'tool', 'toolDone', 'user', 'say', 'turnEnd', 'tool', 'tool', 'toolDone', 'toolDone']), JSON.stringify(kinds))
check('思考增量也过桥（页面折叠成一行）', frames[2].k === 'think' && frames[2].text === '内心戏', JSON.stringify(frames[2]))
// live 标记：有它页面才知道「增量往后接」与「落库原文覆盖」，否则同一段话会显示两遍
check('直播帧带 live 标记（增量 vs 落库原文）',
  frames[2].live === true && frames[1].live === true && frames[1].k === 'delta' && frames[6].live === undefined,
  JSON.stringify([frames[1], frames[2], frames[6]]))
check('快照里的记录同样翻译', frames[0].records.length === 2 && frames[0].records[0].k === 'user' && frames[0].records[1].k === 'say')
check('正文增量来自 text-delta', frames[1].text === '正在')
check('工具行带声明式卡片（card/kind/title 来自工具自己）',
  frames[3].card === 'generic' && frames[3].kind === 'execute' && frames[3].title === 'file_edit：a.py',
  JSON.stringify(frames[3]))
check('完成态卡片铺成多行（PTC 子操作不再折成一个）',
  frames[4].callId === 'c1' && frames[4].ok === true && JSON.stringify(frames[4].lines) === JSON.stringify(['编辑 a.ts', '读取 b.ts']),
  JSON.stringify(frames[4]))
check('直播段同样翻译持久事件', frames[5].text === '再改一处' && frames[6].text === '改完了')
// PTC：父调用 c2 后面依次是「子操作 tool」「子操作 toolDone」「父调用 toolDone」
const ptcSub = frames[frames.length - 3]
const ptcSubDone = frames[frames.length - 2]
const ptcParentDone = frames[frames.length - 1]
check('PTC 子操作按它自己的声明发一对帧（页面多出一行）',
  ptcSub.k === 'tool' && ptcSub.callId === 's1' && ptcSub.name === 'file_edit' && ptcSub.kind === 'execute' && ptcSub.title === 'file_edit：a.py',
  JSON.stringify(ptcSub))
check('子操作的结果行也来自它自己的声明',
  ptcSubDone.k === 'toolDone' && ptcSubDone.callId === 's1' && JSON.stringify(ptcSubDone.lines) === JSON.stringify(['编辑 a.ts', '读取 b.ts']),
  JSON.stringify(ptcSubDone))
check('父行有子操作时不再重复铺那份聚合输出',
  ptcParentDone.k === 'toolDone' && ptcParentDone.callId === 'c2' && ptcParentDone.ok === true && (ptcParentDone.lines ?? []).length === 0,
  JSON.stringify(ptcParentDone))
check('follow 用会话地址并按需开流', calls.follow[0].address.kind === 'session' && calls.follow[0].address.sessionId === 'session-1' && calls.follow[0].assistantStream === true, JSON.stringify(calls.follow[0]))

const aborted = followSignal?.aborted === true
check('流结束后释放 follow 信号', followSignal !== null, `aborted=${aborted}`)

const cancelled = await (await fetch(`${base}/cancel`, { method: 'POST', headers: { ...TOKEN, 'content-type': 'application/json' }, body: JSON.stringify({ sessionId: 'session-1' }) })).json()
check('cancel 透传会话 id', cancelled.ok === true && calls.cancel[0].sessionId === 'session-1')

// ── 插队：mode='steer' 必须原样到 DSH（忙时插到最近一个步骤边界）────────────
const steer = await (await fetch(`${base}/prompt`, {
  method: 'POST', headers: { ...TOKEN, 'content-type': 'application/json' },
  body: JSON.stringify({ text: '插一句：先别动那个文件', sessionId: 'session-1', mode: 'steer' }),
})).json()
check('steer 透传给 DSH（插到最近一个步骤边界）',
  steer.sessionId === 'session-1' && calls.prompt[calls.prompt.length - 1].mode === 'steer',
  JSON.stringify(calls.prompt[calls.prompt.length - 1]))
const normal = await (await fetch(`${base}/prompt`, {
  method: 'POST', headers: { ...TOKEN, 'content-type': 'application/json' },
  body: JSON.stringify({ text: '这一条正常排队', sessionId: 'session-1' }),
})).json()
check('不带 mode 的投递就是 followup（下一轮再跑）', normal.sessionId === 'session-1' && calls.prompt[calls.prompt.length - 1].mode === undefined)

// ── 图片附件：围栏解析 → 落盘 → 正文里只留可读路径 ────────────────────
const PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=='
const fence = (items) => `<copree-attachments>${JSON.stringify(items)}</copree-attachments>`
const imageText = `看这张图\n\n[图片 #7: dot.png]\n${fence([{ name: 'dot.png', mime: 'image/png', data: PNG_B64 }])}`
await fetch(`${base}/prompt`, {
  method: 'POST', headers: { ...TOKEN, 'content-type': 'application/json' },
  body: JSON.stringify({ text: imageText, sessionId: 'session-1' }),
})
const imgPrompt = calls.prompt[calls.prompt.length - 1].content[0].text
const savedPath = imgPrompt.trim().split('\n').pop().slice(2)
check('图片围栏落盘，路径写进会话正文', /\[图片附件\]\n- .*dot\.png$/.test(imgPrompt.trim()), imgPrompt)
check('base64 与围栏都不进会话正文', !imgPrompt.includes(PNG_B64) && !imgPrompt.includes('copree-attachments'))
check('落盘文件就是原图字节', existsSync(savedPath) && readFileSync(savedPath).length === Buffer.from(PNG_B64, 'base64').length, savedPath)
check('人读的图片标记留在正文（气泡据此渲染缩略图）', imgPrompt.includes('[图片 #7: dot.png]'))

const onlyImage = await fetch(`${base}/prompt`, {
  method: 'POST', headers: { ...TOKEN, 'content-type': 'application/json' },
  body: JSON.stringify({ sessionId: 'session-1', text: fence([{ name: 'a.png', mime: 'image/png', data: PNG_B64 }]) }),
})
check('只带图片、没有文字也不算空消息', onlyImage.status === 200)

const many = Array.from({ length: 9 }, (_, i) => ({ name: `m${i}.png`, mime: 'image/png', data: PNG_B64 }))
await fetch(`${base}/prompt`, {
  method: 'POST', headers: { ...TOKEN, 'content-type': 'application/json' },
  body: JSON.stringify({ sessionId: 'session-1', text: fence(many) }),
})
const skipText = calls.prompt[calls.prompt.length - 1].content[0].text
check('超量图片按张跳过并如实告知', skipText.includes('1 张未能保存'), skipText)

// ── 引用回取：正文里没有 base64，字节由插件按 fileId 从 Copree 拉回来落盘 ──
const pullDir = '/tmp/bridge-smoke-attach'
await fetch(`${base}/prompt`, {
  method: 'POST', headers: { ...TOKEN, 'content-type': 'application/json' },
  body: JSON.stringify({
    text: '回取这张图\n\n[图片 #42: pulled.png]',
    sessionId: 'session-1',
    cwd: pullDir,
    attachments: [{ fileId: 42, name: 'pulled.png', mime: 'image/png' }],
  }),
})
const pullPrompt = calls.prompt[calls.prompt.length - 1].content[0].text
const pulledPath = pullPrompt.trim().split('\n').pop().slice(2)
check('按 fileId 回取附件并落盘', /\[图片附件\]\n- .*pulled\.png$/.test(pullPrompt.trim()), pullPrompt)
check('回取的文件字节正确', existsSync(pulledPath) && readFileSync(pulledPath).length === Buffer.from(PNG_B64, 'base64').length, pulledPath)
// 落点以会话自己的工作区为准（连图片都该落在会话读得到的目录里，调用方说的 cwd 只是兜底）
check('回取落在会话工作区的附件目录', pulledPath.startsWith(SMOKE_CWD + '/.copree/attachments/'), pulledPath)
check('引用路径下正文不含 base64', !pullPrompt.includes('iVBOR'))

// ── 人在哪：审批与提问过桥（DSH 侧等人回答的东西必须能弹到 Copree 页面）──
check('插件注册了审批与提问两个回答者', listeners.has('approval/request') && listeners.has('user-questions/request'))

// 先在另一个端口开一条流，模拟"有人正在看 session-1"
const watchPort = 59331
const watchGateway = await gatewayFor(webServer.routes, watchPort)
const watchBase = `http://127.0.0.1:${watchPort}/copree-bridge`
const watchAbort = new AbortController()
const watchResponse = await fetch(`${watchBase}/stream?sessionId=session-hold`, { headers: TOKEN, signal: watchAbort.signal })
const watchReader = watchResponse.body.getReader()
const watchDecoder = new TextDecoder()
let watchBuffer = ''
/** 流里先有快照等帧，等到目标种类为止（最多 10 帧，防呆） */
const nextFrameOfKind = async (kind) => {
  for (let i = 0; i < 10; i += 1) {
    const frame = await nextFrame()
    if (!frame) return null
    if (frame.k === kind) return frame
  }
  return null
}

const nextFrame = async () => {
  for (;;) {
    const idx = watchBuffer.indexOf('\n\n')
    if (idx >= 0) {
      const block = watchBuffer.slice(0, idx)
      watchBuffer = watchBuffer.slice(idx + 2)
      const line = block.split('\n').find((l) => l.startsWith('data: '))
      if (line) return JSON.parse(line.slice(6))
      continue
    }
    const chunk = await watchReader.read()
    if (chunk.done) return null
    watchBuffer += watchDecoder.decode(chunk.value, { stream: true })
  }
}

const approvalOutcome = listeners.get('approval/request')(
  { agent: { sessionId: 'session-hold' }, toolName: 'bash', reason: '要跑一条命令' },
  async () => 'unavailable',
)
const askFrame = await nextFrameOfKind('ask')
check('审批请求过桥成 ask 帧', askFrame.k === 'ask' && askFrame.ask.kind === 'approval' && askFrame.ask.toolName === 'bash' && askFrame.ask.reason === '要跑一条命令', JSON.stringify(askFrame))
const approved = await (await fetch(`${base}/answer`, {
  method: 'POST', headers: { ...TOKEN, 'content-type': 'application/json' },
  body: JSON.stringify({ id: askFrame.ask.id, approve: true }),
})).json()
check('页面回的决定被接受', approved.ok === true)
check('同意 → DSH 收到 allowed-once', (await approvalOutcome) === 'allowed-once')
const doneFrame = await nextFrameOfKind('askDone')
check('答完通知同一会话的所有观看者', doneFrame.k === 'askDone' && doneFrame.id === askFrame.ask.id, JSON.stringify(doneFrame))

const questionOutcome = listeners.get('user-questions/request')(
  { agent: { sessionId: 'session-hold' }, questions: [{ id: 'q1', question: '选哪个', options: [{ label: 'A', description: '甲' }, { label: 'B' }] }] },
  async () => ({ answers: [] }),
)
const questionFrame = await nextFrameOfKind('ask')
check('提问请求过桥成 ask 帧（带选项）', questionFrame.k === 'ask' && questionFrame.ask.kind === 'question' && questionFrame.ask.questions[0].options.length === 2, JSON.stringify(questionFrame))
await fetch(`${base}/answer`, {
  method: 'POST', headers: { ...TOKEN, 'content-type': 'application/json' },
  body: JSON.stringify({ id: questionFrame.ask.id, answers: [{ id: 'q1', selected: ['A'], custom: '就它' }] }),
})
const questionAnswer = await questionOutcome
check('回答按所选选项交回 DSH', questionAnswer.answers[0].id === 'q1' && questionAnswer.answers[0].selected[0] === 'A' && questionAnswer.answers[0].custom === '就它', JSON.stringify(questionAnswer))
await nextFrameOfKind('askDone')

// 没人在看这条会话 → 立刻交回默认链（绝不挂死会话）
const delegated = await listeners.get('approval/request')(
  { agent: { sessionId: 'session-nobody-watching' }, toolName: 'bash' },
  async () => 'unavailable',
)
check('没人在看时交回 DSH 默认链', delegated === 'unavailable')

watchAbort.abort()
watchGateway.close()

// 心跳：apply 时立刻注册一次，带密钥与可达地址
const deadline = Date.now() + 2000
while (registrations.length === 0 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 50))
check('心跳注册到 Copree 后端', registrations.length >= 1 && registrations[0].token === SECRET && registrations[0].body.advertiseUrl === 'http://10.0.0.1:3082' && registrations[0].body.plugin === 'dsh-copree', JSON.stringify(registrations[0] ?? null))
// 撤销同意 → 路由摘掉 + 心跳停（拿到密钥也调不到）；再同意 → 都回来
const beforeRevoke = registerHits
const closed = await fetch(`http://127.0.0.1:${GATEWAY_PORT}/copree-consent`, {
  method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ allowed: false }),
})
check('撤销同意后桥接路由被摘掉', closed.ok && (await waitFor(() => bridgeRoutes(webServer).length === 0)))
await new Promise((r) => setTimeout(r, SMOKE_HEARTBEAT_MS * 2))
check('撤销同意后心跳也停了', registerHits === beforeRevoke, `before=${beforeRevoke} after=${registerHits}`)
await fetch(`http://127.0.0.1:${GATEWAY_PORT}/copree-consent`, {
  method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ allowed: true }),
})
check('重新同意后桥接恢复', await waitFor(() => bridgeRoutes(webServer).length > 0) && (await waitFor(() => registerHits > beforeRevoke)))

// 未配置密钥 → 整条桥接不注册
const disabledServer = mockWebServer()
mod.apply(
  { webServer: disabledServer, tools: { register: noop }, systemPrompt: { section: noop }, sessionController: controller, logger: { info: noop }, effect: () => () => {} },
  { backendUrl: `http://127.0.0.1:${BACKEND_PORT}`, pluginSourceDir: '', bridgeEnabled: true, bridgeSecret: '', bridgeAdvertiseUrl: '' },
)
check('未配置密钥时不注册桥接路由', disabledServer.routes.every((r) => r.path !== '/copree-bridge'), JSON.stringify(disabledServer.routes.map((r) => r.path)))

rmSync(SMOKE_CWD, { recursive: true, force: true })
gateway.close()
backend.close()
console.log(`bridge smoke: ${failures === 0 ? 'PASS' : `${failures} FAILED`}`)
process.exit(failures === 0 ? 0 : 1)
