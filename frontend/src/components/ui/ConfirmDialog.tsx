import { ReactNode, useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import Modal from './Modal'
import Button from './Button'
import { useT } from '../../i18n/I18nContext'

/**
 * 统一确认弹窗（替换原生 confirm()）
 *
 * 原生 confirm() 是浏览器默认样式，破坏整体观感。
 * 用法：把「是否确认」变成受控状态，返回 Promise<boolean>。
 *
 * 示例：
 *   const confirmed = await confirmAsync({ title: '删除？', message: '不可恢复' })
 */
export interface ConfirmOptions {
  title?: string
  /** 正文：字符串按纯文本渲染；需要富内容（引用卡片、提示行）时传 ReactNode */
  message: ReactNode
  confirmText?: string
  cancelText?: string
  danger?: boolean
  /** 去重键：展示中/排队中的同键请求只保留一个（防重复触发弹出第二个弹窗） */
  key?: string
}

// 全局确认队列：一次只弹一个；同键请求复用同一个 Promise。
// 2026-09-18 用户报「点了确定又出现一个，第二个还要等一会儿才自动消失」：
// 旧实现是**单槽位覆盖式**——后一个请求直接顶掉前一个的 pendingResolver，
// 前一个 Promise 永远不 resolve，而界面上会依次冒出两个弹窗。
interface PendingConfirm {
  key: string
  options: ConfirmOptions
  resolve: (v: boolean) => void
  promise: Promise<boolean>
}

const queue: PendingConfirm[] = []
let current: PendingConfirm | null = null
const listeners = new Set<() => void>()

function notify() {
  listeners.forEach((fn) => fn())
}

function next() {
  current = queue.shift() ?? null
  notify()
}

/** 在任意处调用：返回 Promise，用户确认后 resolve(true/false) */
export function confirmAsync(options: ConfirmOptions): Promise<boolean> {
  const key = options.key ?? `${options.title ?? ''}|${typeof options.message === 'string' ? options.message : ''}`
  const dup = current?.key === key ? current : queue.find((p) => p.key === key)
  if (dup) return dup.promise
  let resolve!: (v: boolean) => void
  const promise = new Promise<boolean>((r) => { resolve = r })
  queue.push({ key, options, resolve, promise })
  if (!current) next()
  return promise
}

function resolveAndClose(value: boolean) {
  const done = current
  current = null
  done?.resolve(value)
  next()
}

/** 全局确认弹窗组件（在 App 根部挂一次） */
export function ConfirmDialogHost() {
  const t = useT()
  const [, force] = useState(0)
  // 订阅必须放 effect：写在渲染体里会每次渲染都往 Set 塞一个永不摘除的监听（泄漏，StrictMode 下翻倍）
  useEffect(() => {
    const listener = () => force((n) => n + 1)
    listeners.add(listener)
    return () => { listeners.delete(listener) }
  }, [])

  const options = current?.options
  if (!options) return null

  return (
    <Modal
      open
      onClose={() => resolveAndClose(false)}
      width="max-w-sm"
      title={
        <span className="flex items-center gap-2">
          <AlertTriangle size={18} className={options.danger ? 'text-rose-400' : 'text-accent-400'} />
          {options.title || t('common:confirm')}
        </span>
      }
      footer={
        <>
          <Button variant="secondary" onClick={() => resolveAndClose(false)}>
            {options.cancelText || t('common:cancel')}
          </Button>
          <Button variant={options.danger ? 'danger' : 'primary'} onClick={() => resolveAndClose(true)}>
            {options.confirmText || t('common:confirm')}
          </Button>
        </>
      }
    >
      <div className="text-sm text-textSecondary">{options.message}</div>
    </Modal>
  )
}
