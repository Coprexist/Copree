import { useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useT } from '../../i18n/I18nContext'
import IconButton from './IconButton'

/**
 * 分页条：列表条数不由调用方截断，而是"一次给一页"。
 *
 * 原先通道卡里的配对列表是一整列铺开——待批准的人一多就把整张卡顶得很长，
 * 后面的设置项被挤到屏幕外。这里统一画法，任何"会变长的小列表"都用它收住：
 * 左边说清一共多少条，右边翻页，只有一页时整条不出现。
 */
interface PagerProps {
  total: number
  pageSize: number
  page: number
  onChange: (page: number) => void
  className?: string
}

/** 页码按条数夹回有效范围：批准/拉黑之后后面几页可能就没了，别停在空白页 */
const pageCount = (total: number, pageSize: number) =>
  Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, pageSize)))

const clampPage = (page: number, total: number, pageSize: number) =>
  Math.min(Math.max(1, page), pageCount(total, pageSize))

export default function Pager({ total, pageSize, page, onChange, className = '' }: PagerProps) {
  const t = useT()
  const pages = pageCount(total, pageSize)
  const current = clampPage(page, total, pageSize)
  if (pages <= 1) return null
  return (
    <div className={'flex items-center justify-between gap-2 text-3xs text-textMuted ' + className}>
      <span className="tabular-nums">{t('common:pagerTotal', { n: total })}</span>
      <div className="flex items-center gap-0.5">
        <IconButton
          size="sm"
          icon={<ChevronLeft size={13} />}
          label={t('common:prevPage')}
          disabled={current <= 1}
          onClick={() => onChange(current - 1)}
        />
        <span className="tabular-nums min-w-[3.25rem] text-center">{current} / {pages}</span>
        <IconButton
          size="sm"
          icon={<ChevronRight size={13} />}
          label={t('common:nextPage')}
          disabled={current >= pages}
          onClick={() => onChange(current + 1)}
        />
      </div>
    </div>
  )
}

/**
 * 分页状态 + 当前页的行 + 分页条本体，一次给全。
 *
 * 调用点原本要自己写三样东西：页码 state、slice 当前页、再拼一个 <Pager>；
 * 三个列表就是三份重复，还容易漏掉"条数变少后页码越界"这一步（这里由 clamp 兜住）。
 */
export function usePaged<T>(rows: readonly T[], pageSize: number) {
  const [page, setPage] = useState(1)
  const current = clampPage(page, rows.length, pageSize)
  return {
    rows: rows.slice((current - 1) * pageSize, current * pageSize),
    pager: <Pager total={rows.length} pageSize={pageSize} page={current} onChange={setPage} />,
  }
}
