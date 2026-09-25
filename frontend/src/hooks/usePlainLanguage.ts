import { useCallback, useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../context/AuthContext'

/**
 * 表达方式（专业模式 / 通俗模式）的读写入口（切换后立即生效）
 *
 * 存的是布尔 ui_prefs.plain_language：true = 通俗模式，false/缺省 = 专业模式。
 * 用布尔而不是字符串枚举，是因为后端注入段只认"要不要加通俗约束"这一件事，
 * 加一层枚举只会多一种能写坏的状态；两个模式的名字活在 i18n 里。
 *
 * 状态只有一个来源：AuthContext 的 user.ui_prefs —— 组件不存副本，
 * 否则同一个选择在多个输入区会各说各话。后端按 key 合并 ui_prefs，所以写入时带上
 * 现有 ui_prefs，避免顺手清掉主题色等别的键。
 *
 * 设置页走的是整页「保存」批量提交，不共用这里，保持那页的交互一致。
 */
export function usePlainLanguage() {
  const { user, refreshUser } = useAuth()
  const [saving, setSaving] = useState(false)
  const plain = user?.ui_prefs?.plain_language === true

  const setPlain = useCallback(async (next: boolean) => {
    if (saving || next === plain) return
    setSaving(true)
    try {
      await api.put('/user/settings', {
        ui_prefs: { ...(user?.ui_prefs || {}), plain_language: next },
      })
      await refreshUser()
    } catch (err) {
      console.error('切换表达方式失败', err)
    } finally {
      setSaving(false)
    }
  }, [user?.ui_prefs, plain, saving, refreshUser])

  return { plain, setPlain, saving }
}
