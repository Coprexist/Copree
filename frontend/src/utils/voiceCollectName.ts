import { parseServerDate } from './time'

/**
 * 收藏语音时的标题：谁发的 + 说了什么的前几个字。
 *
 * 收藏进「我的文件」之后，这条语音就是一个普通的目录项，**文件名就是它在存储里的标题**。
 * 不在这里写清楚，列表里就只剩 `voice_20261114033204_49.mp3` 这种没人认得的东西。
 *
 * 文稿优先（AI 的语音本身就是文字合成的，人的语音有 ASR 回填），没有文稿时退回
 * 「谁发的 + 时间」——至少能认出是哪一条、什么时候说的。扩展名不在这里拼：
 * 落盘时由后端按源文件补齐，前端只负责标题。
 */
const HEAD_CHARS = 12

/** 消息时间 → "MM-DD HH:mm"（本地时区；后端裸串是 UTC，交给 parseServerDate） */
function clockName(createdAt?: string): string {
  if (!createdAt) return ''
  const at = parseServerDate(createdAt)
  if (Number.isNaN(at.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`
}

/** 文稿压成一行取前几个字：换行与连续空白都摊平，避免名字里出现怪空格 */
function headOf(transcript?: string): string {
  return Array.from((transcript || '').replace(/\s+/g, ' ').trim())
    .slice(0, HEAD_CHARS)
    .join('')
    .trim()
}

export function voiceCollectName(opts: {
  sender: string
  transcript?: string
  createdAt?: string
}): string {
  const sender = (opts.sender || '').trim()
  const head = headOf(opts.transcript) || clockName(opts.createdAt)
  return [sender, head].filter(Boolean).join(' ')
}
