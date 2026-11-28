import { useEffect, useRef, useState } from 'react'
import { useT } from '../i18n/I18nContext'
import { Button } from './ui'
import { useRemark, type RemarkTarget } from '../hooks/useRemarks'

interface Props {
  targetType: RemarkTarget
  targetId: number
  placeholder: string
  /** 输入框下方那行说明（各处的口径不一样，所以由调用方给） */
  hint?: string
  /** 保存失败时的统一出口：面板把它塞进自己的错误条 */
  onError?: (message: string) => void
  inputClassName?: string
  /** 按钮档位：默认 sm 与 .field（32px）同高；所在容器整排是别的档位时才覆盖 */
  size?: 'sm' | 'md'
}

/** 保存成功提示的停留时间：够看清，又不至于一直占着按钮 */
const SAVED_FEEDBACK_MS = 1800

/**
 * 备注输入：群设置、私信设置、资料卡三处共用
 *
 * 以前三处各写一遍「input + 保存按钮 + PUT」，改一处就得记着改另两处；
 * 基线与保存状态都来自 useRemark，保存成功后按钮自己变灰并短暂提示「已保存」。
 */
export default function RemarkField({
  targetType, targetId, placeholder, hint, onError, inputClassName, size = 'sm',
}: Props) {
  const t = useT()
  const { value, saving, save } = useRemark(targetType, targetId)
  const [draft, setDraft] = useState(value)
  const [justSaved, setJustSaved] = useState(false)
  const timer = useRef<number | null>(null)

  // 别处（另一个面板）改过这份备注时也跟上
  useEffect(() => { setDraft(value) }, [value])

  // 组件卸载时把提示的定时器收掉，避免在已卸载的组件上 setState
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
  }, [])

  const commit = async () => {
    try {
      await save(draft)
      setJustSaved(true)
      if (timer.current !== null) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setJustSaved(false), SAVED_FEEDBACK_MS)
    } catch (e: any) {
      onError?.(e?.detail || t('error:saveFailed'))
    }
  }

  return (
    <div>
      <div className="flex items-center gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={placeholder}
          maxLength={32}
          onKeyDown={(e) => { if (e.key === 'Enter') void commit() }}
          className={inputClassName ? 'field flex-1 ' + inputClassName : 'field flex-1'}
        />
        <Button
          size={size}
          className="shrink-0"
          disabled={saving || (!justSaved && draft.trim() === value)}
          onClick={() => void commit()}
        >
          {justSaved ? t('common:saved') : t('common:save')}
        </Button>
      </div>
      {hint && <div className="text-3xs text-textMuted mt-1">{hint}</div>}
    </div>
  )
}
