/**
 * 一次对话日志的结局标记 —— AI 详情页与管理台日志列表共用（单一实现，勿各自手写）。
 *
 * 后端按 AI 侧留下的字判结局（status）：报错 / 被收尾 / 无输出 / 正常。
 * 它贴在每一条上：列表的分组轴是状态帧（见 LogState），结局是条目自己的属性，
 * 两者混作一个维度只会把「待修的轮次」藏进正常的堆里。
 */
import { useT } from '../../i18n/I18nContext'

export type RunStatus = 'ok' | 'no_output' | 'wrapup' | 'error'

/** 展示顺序：要修的排前面，正常垫底 */
export const RUN_STATUS_ORDER: RunStatus[] = ['error', 'wrapup', 'no_output', 'ok']

const STYLES: Record<RunStatus, { chip: string; key: string }> = {
  error:     { chip: 'bg-rose-500/10 text-rose-500',       key: 'logs:statusError' },
  wrapup:    { chip: 'bg-accent-500/10 text-accent-500',   key: 'logs:statusWrapup' },
  no_output: { chip: 'bg-primary-500/10 text-primary-400', key: 'logs:statusNoOutput' },
  ok:        { chip: 'bg-mint-500/10 text-mint-500',       key: 'logs:statusOk' },
}

export function statusOf(value: unknown): RunStatus {
  const raw = String(value ?? '')
  return (RUN_STATUS_ORDER as string[]).includes(raw) ? (raw as RunStatus) : 'ok'
}

export function RunStatusChip({ status, className = '' }: { status: unknown; className?: string }) {
  const t = useT()
  const style = STYLES[statusOf(status)]
  return (
    <span className={`text-3xs px-1.5 py-0.5 rounded ${style.chip} ${className}`}>{t(style.key)}</span>
  )
}

