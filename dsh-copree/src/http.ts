// SPDX-License-Identifier: MIT
/**
 * dsh-copree 两个半侧共用的最小 HTTP 原语。
 *
 * 单独成文件只为「一处定义」：宿主半侧的代理路由、世界工作区端点与反向桥接
 * 都要读 JSON body、回 JSON 响应，重复实现会让三处各自漂移。
 */
import type { IncomingMessage, ServerResponse } from 'node:http'

/** 常规请求体的默认上限：设置类请求都是小 JSON，256KB 足够 */
export const DEFAULT_BODY_LIMIT = 262144

/**
 * 读取请求 JSON body。
 * limit 可覆盖：投递消息那条会把图片以 base64 放在正文里，256KB 装不下（见 /prompt）。
 */
export function readJsonBody(req: IncomingMessage, limit = DEFAULT_BODY_LIMIT): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    let size = 0
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => {
      size += c.length
      if (size > limit) { reject(new Error('body too large')); req.destroy(); return }
      chunks.push(c)
    })
    req.on('end', () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')) }
      catch { reject(new Error('invalid json')) }
    })
    req.on('error', reject)
  })
}

/** 回一个 JSON 响应（固定 content-type，避免各处自行拼 head）。 */
export function sendJson(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' })
  res.end(JSON.stringify(body))
}
