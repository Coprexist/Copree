/**
 * 对话面板的「等你回答」零件 —— 群视界世界对话与 DSH 对话页共用同一份实现。
 *
 * 为什么抽出来：审批弹窗在两边是同一件事（AI 要动手前问一句，人选同意/不同意），
 * 一边一份必然各自漂移；DSH 那条链还多一个「提问」（ask_user_question），
 * 也按同一套视觉语言放在这里，别处不再造第二个弹窗底座。
 */
import { useEffect, useRef, useState } from 'react'
import { ShieldAlert, CheckCircle2, X } from 'lucide-react'
import { Button, Dialog, MenuPanel, MENU_CAPTION } from '../ui'
import MarkdownContent from './MarkdownContent'
import CodeRenderer from './CodeRenderer'
import { useT } from '../../i18n/I18nContext'
import type { ReadyAttachment } from '../../hooks/useAttachmentUpload'

/** 审批请求：与 useWorldChat 的同名结构字段一致（世界侧直接可用，不用适配层） */
export interface ChatApproval {
  approval_id: string
  /** 事件类型关键词：download | delete | modify | plan | other */
  kind: string
  title: string
  /** 一行摘要（动哪个文件/哪个地址） */
  detail?: string
  /** 用户真正要看的内容：文件内容、计划正文、AI 的补充说明 */
  body?: string
  /** body 怎么渲染：markdown（散文/计划）| code（代码块）| text（纯文本） */
  body_format?: 'markdown' | 'code' | 'text'
  body_lang?: string
  /** 距超时还剩多少秒（服务端唯一口径；输入框里打字的心跳会把它续回窗口大小） */
  expires_in?: number
}

/** 一个选项：DSH 的提问可选带说明 */
export interface AskQuestionOption { label: string; description?: string }

/** 一条等待回答的问题（DSH 的 ask_user_question 原样过来） */
export interface AskQuestion {
  id: string
  question: string
  detail?: string
  header?: string
  options?: AskQuestionOption[]
  multiSelect?: boolean
}

/** 待答的请求：审批（要动手前问一句）或提问（要信息） */
export interface AskPrompt {
  id: string
  kind: 'approval' | 'question'
  toolName?: string
  reason?: string
  questions?: AskQuestion[]
}

/**
 * 排队消息面板：AI 还在处理时先排着，一条条按顺序发出去。
 * 形状与世界对话原来那段完全一致（含命令消息的等宽字体与种类徽章），
 * 所以搬到共享模块不改变任何一边的观感。
 */
export function PendingQueuePanel({ items, onRemove, style, hint }: {
  items: { text: string; attachments?: ReadyAttachment[]; kind?: 'msg' | 'cmd' }[]
  onRemove: (index: number) => void
  style?: React.CSSProperties
  /** 一句话说明这批消息什么时候发出去（DSH 侧会写明"当前轮结束后"，世界侧不需要） */
  hint?: string
}) {
  const t = useT()
  if (items.length === 0) return null
  return (
    <MenuPanel
      className="absolute bottom-full left-3 right-3 mx-auto mb-1 max-h-32 overflow-y-auto z-modal"
      style={style}
    >
      <div className={MENU_CAPTION}>
        {t('tool:world.queue.title', { n: items.length })}
        {hint && <span className="ml-1 font-normal normal-case">{hint}</span>}
      </div>
      {items.map((it, i) => (
        <div key={i} className="flex items-center gap-2 px-3 py-1.5 text-xs border-b border-border/40 last:border-b-0">
          <span className={`truncate flex-1 ${it.kind === 'cmd' ? 'font-mono text-primary-400' : 'text-textPrimary'}`}>
            {it.text || (it.attachments?.length ? t('tool:world.queue.image') : '')}
            {!!it.attachments?.length && (
              <span className="ml-1 text-3xs text-textMuted">{t('tool:world.queue.imageCount', { n: it.attachments.length })}</span>
            )}
          </span>
          {/* 种类徽章：圆角胶囊 + text-3xs（与审批弹窗的种类徽章同款口径） */}
          <span className={`shrink-0 px-1.5 py-0.5 rounded-full text-3xs ${it.kind === 'cmd' ? 'bg-primary-500/10 text-primary-400' : 'bg-elevated text-textMuted'}`}>
            {it.kind === 'cmd' ? t('tool:world.queue.kind.cmd') : t('tool:world.queue.kind.msg')}
          </span>
          <button
            onClick={() => onRemove(i)}
            className="shrink-0 p-0.5 rounded text-textMuted hover:text-rose-400 hover:bg-rose-500/10 transition-colors"
            title={t('tool:world.queue.remove')}
          ><X size={12} /></button>
        </div>
      ))}
    </MenuPanel>
  )
}

/**
 * 提问弹窗（DSH 的 ask_user_question）：选项 + 可选的自由填写。
 * 与审批弹窗同一套底座与尺寸口径，人在同一个位置完成"回答"。
 */
export function QuestionDialog({ ask, onSubmit, busy }: {
  ask: AskPrompt
  onSubmit: (answers: { id: string; selected: string[]; custom?: string }[]) => void
  busy?: boolean
}) {
  const t = useT()
  const questions = ask.questions || []
  const [selected, setSelected] = useState<Record<string, string[]>>({})
  const [custom, setCustom] = useState<Record<string, string>>({})

  const toggle = (q: AskQuestion, label: string) => {
    setSelected((prev) => {
      const cur = prev[q.id] || []
      if (q.multiSelect) {
        return { ...prev, [q.id]: cur.includes(label) ? cur.filter((x) => x !== label) : [...cur, label] }
      }
      return { ...prev, [q.id]: [label] }
    })
  }

  const ready = questions.every((q) => (selected[q.id]?.length || 0) > 0 || (custom[q.id] || '').trim().length > 0)
  const submit = () => {
    if (!ready || busy) return
    onSubmit(questions.map((q) => ({
      id: q.id,
      selected: selected[q.id] || [],
      ...((custom[q.id] || '').trim() ? { custom: custom[q.id].trim() } : {}),
    })))
  }

  return (
    <Dialog className="flex items-center justify-center p-4">
      <div className="w-full max-w-lg sm:max-w-xl bg-surface border border-border rounded-dialog shadow-xl overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border">
          <CheckCircle2 size={14} className="text-primary-400" />
          <span className="text-sm font-medium">{t('tool:dsh.ask.title')}</span>
        </div>
        <div className="px-4 py-3 max-h-[55vh] overflow-y-auto space-y-3">
          {questions.map((q) => (
            <div key={q.id} className="space-y-1.5">
              {q.header && <div className="text-3xs text-textMuted">{q.header}</div>}
              <div className="text-sm font-medium text-textPrimary">{q.question}</div>
              {q.detail && <div className="text-xs text-textSecondary whitespace-pre-wrap">{q.detail}</div>}
              {!!q.options?.length && (
                <div className="space-y-1">
                  {q.options.map((opt) => {
                    const on = (selected[q.id] || []).includes(opt.label)
                    return (
                      <button
                        key={opt.label}
                        onClick={() => toggle(q, opt.label)}
                        className={`w-full text-left px-3 py-2 rounded-control border text-xs transition-colors ${
                          on ? 'bg-primary-500/15 border-primary-400/40 text-textPrimary' : 'bg-elevated/50 border-border text-textSecondary hover:bg-elevated'
                        }`}
                      >
                        <span className="font-medium">{opt.label}</span>
                        {opt.description && <span className="block text-3xs text-textMuted mt-0.5">{opt.description}</span>}
                      </button>
                    )
                  })}
                </div>
              )}
              <input
                value={custom[q.id] || ''}
                onChange={(e) => setCustom((prev) => ({ ...prev, [q.id]: e.target.value }))}
                placeholder={t('tool:dsh.ask.otherPlaceholder')}
                className="w-full bg-elevated border border-border rounded-control px-3 py-2 text-xs text-textPrimary focus:outline-none focus:border-primary-400"
              />
              {q.multiSelect && <div className="text-3xs text-textMuted">{t('tool:dsh.ask.multi')}</div>}
            </div>
          ))}
        </div>
        <div className="px-4 py-3 border-t border-border flex items-center gap-2">
          <span className="flex-1 text-3xs text-textMuted">{ready ? '' : t('tool:dsh.ask.emptyAnswer')}</span>
          <Button size="sm" onClick={submit} disabled={!ready || busy}>{t('tool:dsh.ask.submit')}</Button>
        </div>
      </div>
    </Dialog>
  )
}

/** 打字心跳的最短间隔：一次输入最多每 20s 报一次（空闲窗口 10 分钟，够用又不刷后端） */
const TOUCH_INTERVAL_MS = 20_000

/** 剩余时间 "9:32"：分:秒，一眼看出还剩多久，三语也不用各写一套复数规则 */
export function formatRemaining(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

/** 审批弹窗（审阅/计划模式）：AI 请求下载/删除/改动机制，等用户点按钮，选完服务端自动继续。
 *  事件类型关键词由后端按下发的 kind 渲染，用户一眼看清在批什么。 */
export function ApprovalDialog({ approval, onDecide, onTouch, allowNote = true }: {
  approval: ChatApproval
  onDecide: (ok: boolean, note: string) => void
  onTouch: (approvalId: string) => Promise<number>
  /** 有没有"补充一句"这条通道：DSH 的审批只有同意/不同意，没地方放附言，就别摆一个白写的输入框 */
  allowNote?: boolean
}) {
  const t = useT()
  const kindKey = `tool:world.kind.${approval.kind}`
  const localized = t(kindKey)
  const body = approval.body || ''
  // 理由/补充要求（可选）：跟那一票一起发给 AI——不同意时说清为什么，同意时顺手加要求
  const [note, setNote] = useState('')
  // 倒计时（用户 2026-09-23：等确认的时间太紧，而且"正在编辑输入框"时还在计时）：
  // 剩余秒数以服务端为准（下发值 / 心跳返回值），本地只负责每秒减一格
  const [remaining, setRemaining] = useState(approval.expires_in ?? 0)
  useEffect(() => { setRemaining(approval.expires_in ?? 0) }, [approval.approval_id, approval.expires_in])
  useEffect(() => {
    const timer = setInterval(() => setRemaining((s) => (s > 0 ? s - 1 : 0)), 1000)
    return () => clearInterval(timer)
  }, [])
  const lastTouchRef = useRef(0)
  // 打字就是"有人在场"：节流上报心跳，让服务端把空闲窗口续上（不是代替点按钮）
  const handleNote = (value: string) => {
    setNote(value)
    const now = Date.now()
    if (now - lastTouchRef.current < TOUCH_INTERVAL_MS) return
    lastTouchRef.current = now
    onTouch(approval.approval_id).then((left) => { if (left > 0) setRemaining(left) })
  }
  return (
    // 无 onClose：审批必须由用户明确点同意/不同意（ESC 与点遮罩都不放行），
    // 但 Dialog 仍负责锁背景滚动 + 统一层级与遮罩
    <Dialog className="world-msg flex items-center justify-center p-4">
      {/* 宽屏适配（用户 2026-09-19 反馈：计划弹窗在宽屏下还是窄窄一条）：
          带正文的（计划 / 代码）在 lg 上放开到 4xl 才够读；只有一句话的确认不用那么宽 */}
      <div
        className={`w-full bg-surface border border-border rounded-dialog shadow-xl overflow-hidden ${body ? 'max-w-lg sm:max-w-2xl lg:max-w-4xl' : 'max-w-lg sm:max-w-xl'}`}
      >
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border">
          {/* 琥珀是项目语义里的「需要你注意」（accent-*，站内 111 处在用）；amber-* 是 Tailwind 默认色板，不在令牌内 */}
          <ShieldAlert size={14} className="text-accent-400" />
          <span className="text-sm font-medium">{t('tool:world.approval.title')}</span>
          <span className="text-3xs px-2 py-0.5 rounded-full bg-elevated text-textMuted shrink-0">
            {localized && localized !== kindKey ? localized : approval.kind}
          </span>
        </div>
        <div className="px-4 py-3 max-h-[55vh] overflow-y-auto space-y-2">
          <div className="text-sm font-medium">{approval.title}</div>
          {approval.detail && <div className="text-xs text-textSecondary">{approval.detail}</div>}
          {/* 正文按 AI 实际写的东西渲染：散文/计划走 markdown，代码走代码块（同日聊天同款渲染器） */}
          {!!body && (
            approval.body_format === 'code' ? (
              <div className="max-h-[45vh] overflow-auto">
                <CodeRenderer className={`language-${approval.body_lang || 'plaintext'}`}>{body}</CodeRenderer>
              </div>
            ) : approval.body_format === 'markdown' ? (
              <div className="text-sm text-textPrimary">
                <MarkdownContent content={body} />
              </div>
            ) : (
              <pre className="text-2xs whitespace-pre-wrap break-words bg-elevated rounded-control p-2 text-textSecondary">{body}</pre>
            )
          )}
        </div>
        <div className="px-4 py-3 border-t border-border space-y-2">
          {/* 输入框在按钮上方且不随正文滚动：想补一句时不必先把内容翻到底 */}
          {allowNote && (
            <textarea
              className="field w-full text-xs"
              rows={2}
              maxLength={2000}
              value={note}
              onChange={(e) => handleNote(e.target.value)}
              placeholder={t('tool:world.approval.notePlaceholder')}
            />
          )}
          <div className="flex items-center gap-2">
            <span className="flex-1 text-3xs text-textMuted">
              {t('tool:world.approval.waiting')}
              {remaining > 0 && (
                <span className="ml-1 text-textSecondary">· {t('tool:world.approval.remaining', { time: formatRemaining(remaining) })}</span>
              )}
            </span>
            <Button size="sm" variant="outline" onClick={() => onDecide(false, note)}>
              {t('tool:world.approval.deny')}
            </Button>
            <Button size="sm" onClick={() => onDecide(true, note)}>
              {t('tool:world.approval.approve')}
            </Button>
          </div>
        </div>
      </div>
    </Dialog>
  )
}
