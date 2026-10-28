/**
 * 斜杠命令端口：Copro 镜像页的「命令」按钮靠它。
 *
 * 三层现实（都实测过，别再猜）：
 *   1. DSH 的 commands 服务是 cordis Service（`CommandRuntime extends TypertRemoteService
 *      extends Service`，服务名 `commands`）——**进程内可取**，但必须走 cordis 的依赖声明：
 *      直接写 `ctx.commands` 时属性还不存在（这是 cordis Service 的惰性可见性），
 *      旧插件因此一直拿到 undefined，页面只好说"更新插件"。
 *   2. 它的 `list(agent)` / `execute(agent, line, attachments, signal)` 标了 @Remote：
 *      Remote 只是**额外**给浏览器页的通道，不是"只能远程调"。`execute` 收显式 signal，
 *      所以进程内调用不会出现以前那种 "reading 'aborted'"（那是没传 signal 的签名错误）。
 *   3. `agent` 取 `ctx.agents.get(sessionId)`——与工具 scope 用的是同一个入口。
 */
import type { BridgeCommand, BridgeCommandRun, CommandsPort } from './bridge'

/** cordis 上下文里我们用到的那几个面（不引 harness 类型，避免与宿主版本绑死）。 */
interface CordisLike {
  inject?: (services: string[], callback: (ctx: CordisLike) => void) => unknown
  get?: (name: string) => any
  commands?: any
  agents?: { get?: (sessionId: string) => any }
}

interface CommandService {
  list: (agent: unknown) => unknown
  execute: (agent: unknown, line: string, attachments: unknown[], signal: AbortSignal) => Promise<unknown>
  find?: (agent: unknown, name: string) => unknown
}

export interface CommandsPortOptions {
  log: (message: string) => void
}

/** 一条命令描述符 → 页面要的展示项（DSH 的形状是 {name, description, input?}）。 */
function toBridgeCommand(item: any): BridgeCommand {
  return {
    name: String(item?.name ?? ''),
    description: String(item?.description ?? ''),
    hint: String(item?.input?.hint ?? ''),
  }
}

function toRun(settled: any): BridgeCommandRun {
  const result = settled?.result
  return {
    kind: 'ok',
    commandId: String(settled?.commandId ?? ''),
    result: result?.kind === 'error' ? 'error' : 'success',
    text: String(result?.text ?? ''),
  }
}

/**
 * 建立命令端口。
 *
 * 这里只有"取服务 + 收形状"两件事，不认识任何具体命令——DSH 里注册了什么就有什么。
 */
export function createCommandsPort(ctx: CordisLike, options: CommandsPortOptions): CommandsPort {
  let service: CommandService | undefined
  let state: 'ok' | 'missing' | null = null

  const adopt = (candidate: any): void => {
    if (typeof candidate?.list === 'function' && typeof candidate?.execute === 'function') {
      service = candidate as CommandService
      if (state !== 'ok') {
        state = 'ok'
        options.log('dsh-copree: 已拿到 DSH 的 commands 服务（命令面板可用）')
      }
    }
  }

  // 主路径：cordis 依赖声明。`inject` 会在服务就绪后回调，早于它就访问只会拿到 undefined。
  try {
    ctx.inject?.(['commands'], (injected) => adopt(injected.commands ?? injected.get?.('commands')))
  } catch (error) {
    options.log(`dsh-copree: 声明 commands 依赖失败：${String((error as Error)?.message ?? error)}`)
  }
  // 兜底：服务已在同一上下文装配好时直接取（老版本 harness / 早于 inject 回调的时机）
  adopt(ctx.get?.('commands') ?? ctx.commands)

  const agentOf = (sessionId: string): any => ctx.agents?.get?.(sessionId)
  const note = (): void => {
    if (state === 'missing') return
    state = 'missing'
    options.log('dsh-copree: 这个 DSH 版本没有向插件暴露 commands 服务（命令面板会显示"暂不可用"）')
  }

  return {
    get available() {
      if (service === undefined) {
        // 服务可能晚一步就绪：再探一次，避免"页面先问、服务后到"时永久不可用
        adopt(ctx.get?.('commands') ?? ctx.commands)
      }
      if (service === undefined) {
        note()
        return false
      }
      return true
    },

    list(sessionId) {
      if (!service) return null
      const agent = agentOf(sessionId)
      // 会话不在活动列表：命令挂在 agent 上，没有 agent 就无从谈起（与"没有命令"分开）
      if (!agent) return null
      try {
        return (service.list(agent) as any[]).map(toBridgeCommand)
      } catch (error) {
        options.log(`dsh-copree: 列命令失败：${String((error as Error)?.message ?? error)}`)
        return null
      }
    },

    async execute(sessionId, line) {
      if (!service) return { kind: 'unavailable' }
      const agent = agentOf(sessionId)
      if (!agent) return { kind: 'no-session' }
      // 信号由我们自己持有：execute 的签名要求它，缺了会在留档那一刻炸
      const controller = new AbortController()
      try {
        // undefined = 语法不成立或名字没解析出来（DSH 的语义：准入未命中不留任何事件）
        const settled = await service.execute(agent, line, [], controller.signal)
        if (!settled) return { kind: 'unknown' }
        return toRun(settled)
      } catch (error) {
        // 命令自己抛错：如实回话，页面显示原文，不要伪装成"没这条命令"
        return {
          kind: 'ok',
          commandId: '',
          result: 'error',
          text: String((error as Error)?.message ?? error),
        }
      }
    },
  }
}
