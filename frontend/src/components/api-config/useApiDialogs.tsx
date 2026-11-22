/**
 * 两个编辑器的唯一入口 —— 页面里只调 openProvider() / openProfile()。
 *
 * 为什么不各页面自己写一遍 Modal 开关：设置页卡片、AI 详情页签都要开这两个编辑器，
 * 各写一遍就是四次手抄（还会各自漂出不一样的默认值）。这里一收敛，行为只有一份。
 *
 * 补全链路也在这里完成：配置档里点「去接入厂商」→ 关掉配置档 → 开厂商编辑器 →
 * 存完带着草稿回到配置档编辑器，已经填的名字和模型不会丢。
 *
 * 内置厂商清单（presets）也在这里取：卡片底部要直接把它铺出来。
 * 它以前藏在一个"接入厂商"弹窗里，用户根本找不到——数据一直在，只是入口太深。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import ProfileEditor, { EMPTY_DRAFT, type ProfileDraft } from './ProfileEditor'
import ProviderPickModal from './ProviderPickModal'
import ProviderEditor from './ProviderEditor'
import type { ApiKind, ApiProvider, BuiltinPreset } from './types'

export function useApiDialogs({
  kind,
  onChanged,
}: {
  kind: ApiKind
  onChanged?: () => void
}) {
  const [providers, setProviders] = useState<ApiProvider[]>([])
  const [presets, setPresets] = useState<BuiltinPreset[]>([])
  const [providerState, setProviderState] = useState<{ id?: number; presetKey?: string; focusKey?: boolean } | null>(null)
  const [profileState, setProfileState] = useState<{ id?: number; providerId?: number; draft?: ProfileDraft } | null>(null)
  const [keyPickerOpen, setKeyPickerOpen] = useState(false)
  const [pendingDraft, setPendingDraft] = useState<ProfileDraft | null>(null)
  const draftRef = useRef<ProfileDraft>(EMPTY_DRAFT)

  const reload = useCallback(async () => {
    try {
      const res = await api.get<{ providers: ApiProvider[] }>('/user/api-config/providers?kind=' + kind)
      setProviders(res.providers || [])
    } catch {
      /* 未登录/断网：保持上一次的结果，页面自己显示空态 */
    }
  }, [kind])

  // 内置厂商（平台预设）：列表底部直接铺成胶囊，点一下就去接入
  const reloadPresets = useCallback(async () => {
    try {
      const res = await api.get<{ presets: BuiltinPreset[] }>('/user/provider-presets?kind=' + kind)
      setPresets(res.presets || [])
    } catch {
      setPresets([])
    }
  }, [kind])

  useEffect(() => { reload() }, [reload])
  useEffect(() => { reloadPresets() }, [reloadPresets])

  const changed = useCallback(() => {
    reload()
    onChanged?.()
  }, [reload, onChanged])

  const openProvider = useCallback((opts: { id?: number; presetKey?: string; focusKey?: boolean } = {}) => {
    setProviderState(opts)
  }, [])

  const openProfile = useCallback((opts: { id?: number; providerId?: number } = {}) => {
    setProfileState(opts)
  }, [])

  // 配置档里缺厂商：先记住当前草稿，关掉配置档去补厂商
  const needProvider = useCallback(() => {
    setPendingDraft(draftRef.current)
    setProfileState(null)
    setProviderState({})
  }, [])

  const node = (
    <>
      <ProviderPickModal
        open={keyPickerOpen}
        providers={providers}
        presets={presets}
        onClose={() => setKeyPickerOpen(false)}
        onPick={(target) => {
          setKeyPickerOpen(false)
          // 选中之后打开那家的编辑器，光标直接落在「默认 Key」上
          setProviderState({ ...target, focusKey: true })
        }}
      />
      {/* key 挂上"这次要编辑谁"：从 xiaomi 直接点蚂蚁时，Modal 一直是 open 的，
          不加 key 会先渲染上一次的表单再换成新的（肉眼看到的就是闪一下旧数据）。
          换 key = 整个组件重挂载，state 从零开始，不存在残留。 */}
      <ProviderEditor
        key={'provider-' + (providerState?.id ?? providerState?.presetKey ?? 'new')}
        open={!!providerState}
        kind={kind}
        providerId={providerState?.id}
        presetKey={providerState?.presetKey}
        autoFocusKey={providerState?.focusKey}
        onClose={() => {
          setProviderState(null)
          // 从「去接入厂商」过来的：补完就回到配置档，草稿还在
          if (pendingDraft) {
            const back = pendingDraft
            setPendingDraft(null)
            setProfileState({ providerId: back.providerId, draft: back })
          }
        }}
        onSaved={(saved) => {
          changed()
          if (pendingDraft) {
            const back = { ...pendingDraft, providerId: saved.id }
            setPendingDraft(null)
            setProfileState({ providerId: saved.id, draft: back })
          }
        }}
      />

      <ProfileEditor
        key={'profile-' + (profileState?.id ?? profileState?.providerId ?? 'new')}
        open={!!profileState}
        kind={kind}
        providers={providers}
        profileId={profileState?.id}
        defaultProviderId={profileState?.providerId}
        draft={profileState?.draft}
        onDraft={(d) => { draftRef.current = d }}
        onNeedProvider={needProvider}
        onClose={() => setProfileState(null)}
        onSaved={changed}
      />
    </>
  )

  return {
    node,
    providers,
    presets,
    reloadProviders: reload,
    openProvider,
    openProfile,
    // 底部「填厂商默认 Key」：先选厂商，再进它的编辑器
    openKeyPicker: () => setKeyPickerOpen(true),
  }
}
