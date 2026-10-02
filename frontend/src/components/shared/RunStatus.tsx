/**
 * 一次对话日志的结局标记 —— AI 详情页与管理台日志列表共用（单一实现，勿各自手写）。
 *
 * 后端按 AI 侧留下的字判结局（status）：报错 / 被收尾 / 无输出 / 正常。
 * 列表按它分组，被截断和空转的轮次才不会被正常回复淹没。
 */
import { useT } from '../../i18n/I18nContext'

export type RunStatus = 'ok' | 'no_output' | 'wrapup' | 'error'

/** 展示顺序：要修的排前面，正常垫底 */
export const RUN_STATUS_ORDER: RunStatus[] = ['error', 'wrapup', 'no_output', 'ok']

const STYLES: Record<RunStatus, { chip: string; dot: string; key: string }> = {
  error:     { chip: 'bg-rose-500/10 text-rose-500',     dot: 'bg-rose-500',     key: 'logs:statusError' },
  wrapup:    { chip: 'bg-accent-500/10 text-accent-500', dot: 'bg-accent-500',   key: 'logs:statusWrapup' },
  no_output: { chip: 'bg-primary-500/10 text-primary-400', dot: 'bg-primary-400', key: 'logs:statusNoOutput' },
  ok:        { chip: 'bg-mint-500/10 text-mint-500',     dot: 'bg-mint-500',     key: 'logs:statusOk' },
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

export function RunStatusDot({ status, className = '' }: { status: unknown; className?: string }) {
  return <span className={`inline-block w-1.5 h-1.5 rounded-full ${STYLES[statusOf(status)].dot} ${className}`} />
}

/** 按结局分组（空组不返回），组内保持调用方给的顺序 */
export function groupByStatus<T>(items: T[], status: (item: T) => unknown): { status: RunStatus; items: T[] }[] {
  return RUN_STATUS_ORDER
    .map(value => ({ status: value, items: items.filter(item => statusOf(status(item)) === value) }))
    .filter(group => group.items.length > 0)
}
