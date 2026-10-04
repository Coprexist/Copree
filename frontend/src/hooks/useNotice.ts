/**
 * 一条带"语气"的临时提示（成功 / 失败）。
 *
 * 为什么把语气和文案一起存：配色以前靠 `msg.includes('失败')` 反推，界面一换语言就失灵
 * （英文 "failed"、日文「失敗」都匹配不上中文关键词，红色提示会变绿）。语气在**产生提示
 * 的那一刻**就是确定的——try/catch 走哪一支、接口有没有报错——所以存下来，别再猜文案。
 *
 * 配色也收在这里：页面各拼一份，迟早出现三种红。
 */
import { useCallback, useState } from 'react'

export type NoticeTone = 'ok' | 'error'

export interface Notice {
  text: string
  tone: NoticeTone
}

/** 纯文字提示 */
export const NOTICE_TEXT_CLASS: Record<NoticeTone, string> = {
  ok: 'text-mint-400',
  error: 'text-rose-400',
}

/** 带底色的横幅提示 */
export const NOTICE_BOX_CLASS: Record<NoticeTone, string> = {
  ok: 'bg-mint-400/10 border border-mint-400/20 text-mint-400',
  error: 'bg-rose-500/10 border border-rose-500/20 text-rose-400',
}

export function useNotice() {
  const [notice, setNotice] = useState<Notice | null>(null)
  // 空文案 = 清空：调用点不必再写一个 if（后端偶尔回空 message）
  const ok = useCallback((text: string | null | undefined) => setNotice(text ? { text, tone: 'ok' } : null), [])
  const fail = useCallback((text: string | null | undefined) => setNotice(text ? { text, tone: 'error' } : null), [])
  const clear = useCallback(() => setNotice(null), [])
  return { notice, ok, fail, clear }
}
