import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, Plus, Smile, Trash2, Upload, X } from 'lucide-react'
import { api } from '../api/client'
import Toggle from './Toggle'
import { useT } from '../i18n/I18nContext'
import { Button, EmptyState, IconButton, Input, Modal, confirmAsync } from './ui'
import { faceAssetUrl, invalidateEmojiPacks, type EmojiFace, type EmojiPack } from '../utils/emojiPacks'

/**
 * 表情包面板（AI 详情页的一个页签）
 *
 * 两件事同屏，因为它们是一件事的两端：
 *   1. 平台装好的表情包 —— 逐个勾给这个 AI 用（默认白名单：不勾就不用）；
 *   2. 我的表情包 —— 创建者自己上传的，勾上之后这只 AI 才拿得到。
 * 勾选只决定"这个 AI 能不能用"，不决定"人能不能用"：人类发言不受 AI 偏好限制。
 *
 * 写的入口都在后端 emoji_packs 服务里（归属校验、文件白名单、大小上限同源），
 * 这里只负责收集输入与刷新：上传后要 invalidateEmojiPacks()，否则输入框的表情面板
 * 还拿着旧清单。
 */

interface AgentPack {
  id: string
  name: string
  usage: string
  faces: number
  enabled: boolean
}

interface MinePack extends EmojiPack {
  enabled: boolean
}

interface Limits {
  packs_max: number
  faces_max: number
  image_max_mb: number
}

interface Draft {
  pack: MinePack
  file: File
  preview: string
}

const EMPTY_FORM = { name: '', caption: '', keywords: '', mood: '', emoji: '' }

function errorOf(e: unknown, fallback: string): string {
  const any = e as { detail?: string; message?: string }
  return any?.detail || any?.message || fallback
}

export default function EmojiPacksPanel({ agentId }: { agentId: number }) {
  const t = useT()
  const [agentPacks, setAgentPacks] = useState<AgentPack[]>([])
  const [mode, setMode] = useState<'whitelist' | 'blacklist'>('whitelist')
  const [isOwner, setIsOwner] = useState(false)
  // 能不能改这个 AI 的表情包开关：主人，或获主人授权的 can_edit 合作者
  const [canManage, setCanManage] = useState(false)
  const [mine, setMine] = useState<MinePack[]>([])
  const [limits, setLimits] = useState<Limits>({ packs_max: 20, faces_max: 256, image_max_mb: 4 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [savingPack, setSavingPack] = useState('')
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [uploading, setUploading] = useState(false)
  /** 这次选图是往哪个包加的（隐藏 input 只一个，包在这里记） */
  const uploadPack = useRef<MinePack | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [agentRes, mineRes] = await Promise.all([
        api.get<{ mode: 'whitelist' | 'blacklist'; is_owner: boolean; can_manage: boolean; packs: AgentPack[] }>(
          `/agents/${agentId}/emoji-packs`,
        ),
        api.get<{ packs: MinePack[]; limits: Limits }>('/emoji-packs/mine'),
      ])
      setAgentPacks(agentRes.packs || [])
      setMode(agentRes.mode)
      setIsOwner(agentRes.is_owner)
      setCanManage(!!agentRes.can_manage)
      setMine(mineRes.packs || [])
      if (mineRes.limits) setLimits(mineRes.limits)
    } catch (e) {
      setError(errorOf(e, t('agentDetail:emojiPacksLoadFailed')))
    } finally {
      setLoading(false)
    }
  }, [agentId, t])

  useEffect(() => { void load() }, [load])

  // 预览 URL 是浏览器资源，离开时要还回去
  useEffect(() => () => { if (draft) URL.revokeObjectURL(draft.preview) }, [draft])

  const togglePack = useCallback(async (pack: AgentPack) => {
    setSavingPack(pack.id)
    try {
      const res = await api.put<{ enabled: boolean }>(
        `/agents/${agentId}/emoji-packs/${pack.id}`, { enabled: !pack.enabled },
      )
      setAgentPacks((list) => list.map((p) => (p.id === pack.id ? { ...p, enabled: res.enabled } : p)))
    } catch (e) {
      setError(errorOf(e, t('agentDetail:emojiPacksLoadFailed')))
    } finally {
      setSavingPack('')
    }
  }, [agentId, t])

  const createPack = useCallback(async () => {
    const name = newName.trim()
    if (!name || creating) return
    setCreating(true)
    setError('')
    try {
      await api.post('/emoji-packs', { name })
      setNewName('')
      await load()
      invalidateEmojiPacks()
    } catch (e) {
      setError(errorOf(e, t('agentDetail:emojiPacksLoadFailed')))
    } finally {
      setCreating(false)
    }
  }, [newName, creating, load, t])

  const pickFile = useCallback((pack: MinePack) => {
    uploadPack.current = pack
    fileInput.current?.click()
  }, [])

  const onFileSelected = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    const pack = uploadPack.current
    e.target.value = ''                      // 同一个文件再选一次也要能触发 change
    if (!file || !pack) return
    setForm(EMPTY_FORM)
    setDraft({ pack, file, preview: URL.createObjectURL(file) })
  }, [])

  const closeDraft = useCallback(() => {
    setDraft((d) => {
      if (d) URL.revokeObjectURL(d.preview)
      return null
    })
  }, [])

  const saveFace = useCallback(async () => {
    if (!draft || uploading) return
    if (!form.name.trim() || !form.caption.trim()) {
      setError(t('agentDetail:faceRequired'))
      return
    }
    setUploading(true)
    setError('')
    try {
      await api.upload(`/emoji-packs/${draft.pack.id}/faces`, draft.file, {
        name: form.name.trim(),
        caption: form.caption.trim(),
        keywords: form.keywords.trim(),
        mood: form.mood.trim(),
        emoji: form.emoji.trim(),
      })
      closeDraft()
      await load()
      invalidateEmojiPacks()
    } catch (e) {
      setError(errorOf(e, t('agentDetail:emojiPacksLoadFailed')))
    } finally {
      setUploading(false)
    }
  }, [draft, uploading, form, closeDraft, load, t])

  const removeFace = useCallback(async (pack: MinePack, face: EmojiFace) => {
    const ok = await confirmAsync({
      title: t('common:delete'),
      message: t('agentDetail:faceDeleteConfirm', { name: face.name }),
      danger: true,
    })
    if (!ok) return
    try {
      await api.delete(`/emoji-packs/${pack.id}/faces/${face.id}`)
      await load()
      invalidateEmojiPacks()
    } catch (e) {
      setError(errorOf(e, t('agentDetail:emojiPacksLoadFailed')))
    }
  }, [load, t])

  const removePack = useCallback(async (pack: MinePack) => {
    const ok = await confirmAsync({
      title: t('common:delete'),
      message: t('agentDetail:minePackDeleteConfirm', { name: pack.name }),
      danger: true,
    })
    if (!ok) return
    try {
      await api.delete(`/emoji-packs/${pack.id}`)
      await load()
      invalidateEmojiPacks()
    } catch (e) {
      setError(errorOf(e, t('agentDetail:emojiPacksLoadFailed')))
    }
  }, [load, t])

  return (
    <div className="space-y-4">
      {/* ── 平台表情包：这只 AI 能用哪些 ── */}
      <section className="card p-4">
        <div className="flex items-center gap-2 mb-1">
          <Smile size={16} className="text-primary-400" />
          <h3 className="text-sm font-medium text-textPrimary">{t('agentDetail:emojiPacksTitle')}</h3>
          {mode === 'whitelist' && (
            <span className="chip text-textMuted">{t('agentDetail:emojiPacksWhitelist')}</span>
          )}
        </div>
        <p className="text-xs text-textMuted mb-3">{t('agentDetail:emojiPacksHint')}</p>
        {!isOwner && <p className="text-xs text-accent-400 mb-3">{t('agentDetail:emojiPacksOwnerOnly')}</p>}

        {loading ? (
          <p className="text-sm text-textMuted">{t('common:loading')}</p>
        ) : agentPacks.length === 0 ? (
          <EmptyState icon={Smile} title={t('agentDetail:emojiPacksEmpty')} />
        ) : (
          <div>
            {agentPacks.map((pack) => (
              <div key={pack.id} className="flex items-center gap-3 py-2.5 border-t border-border/60 first:border-t-0">
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-textPrimary truncate" title={pack.usage || pack.name}>{pack.name}</div>
                  <div className="text-3xs text-textMuted truncate mt-0.5">
                    {t('agentDetail:emojiPackFaces', { count: pack.faces })}
                    {pack.usage ? ` · ${pack.usage}` : ''}
                  </div>
                </div>
                {savingPack === pack.id && <Loader2 size={14} className="animate-spin text-textMuted" />}
                <Toggle
                  size="sm"
                  checked={pack.enabled}
                  disabled={!(isOwner || canManage) || savingPack === pack.id}
                  ariaLabel={pack.name}
                  onChange={() => void togglePack(pack)}
                />
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── 我的表情包：创建者自己上传/自制 ── */}
      <section className="card p-4">
        <div className="flex items-center gap-2 mb-1">
          <Upload size={16} className="text-primary-400" />
          <h3 className="text-sm font-medium text-textPrimary">{t('agentDetail:minePacksTitle')}</h3>
          <span className="text-3xs text-textMuted">
            {t('agentDetail:minePackLimit', {
              count: Math.max(limits.packs_max - mine.length, 0),
              max: limits.packs_max,
            })}
          </span>
        </div>
        <p className="text-xs text-textMuted mb-3">{t('agentDetail:minePacksHint')}</p>

        <div className="flex items-center gap-2 mb-3">
          <div className="flex-1 min-w-0">
            <Input
              fieldSize="sm"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void createPack() }}
              placeholder={t('agentDetail:minePackNamePlaceholder')}
              maxLength={40}
            />
          </div>
          <Button
            size="sm"
            icon={<Plus size={14} />}
            loading={creating}
            disabled={!newName.trim() || mine.length >= limits.packs_max}
            onClick={() => void createPack()}
          >
            {t('agentDetail:minePackCreate')}
          </Button>
        </div>

        {loading ? (
          <p className="text-sm text-textMuted">{t('common:loading')}</p>
        ) : mine.length === 0 ? (
          <EmptyState icon={Smile} title={t('agentDetail:minePacksEmpty')} />
        ) : (
          <div className="space-y-3">
            {mine.map((pack) => (
              <div key={pack.id} className="rounded-control border border-border bg-canvas">
                <div className="flex items-center gap-2 px-3 py-2">
                  <span className="text-sm font-medium text-textPrimary truncate" title={pack.usage || pack.name}>
                    {pack.name}
                  </span>
                  <span className="text-3xs text-textMuted shrink-0">
                    {t('agentDetail:emojiPackFaces', { count: pack.faces.length })}
                  </span>
                  {!pack.enabled && (
                    <span className="chip bg-rose-500/10 text-rose-400 shrink-0">{t('agentDetail:minePackDisabled')}</span>
                  )}
                  <div className="ml-auto flex items-center gap-1.5 shrink-0">
                    <Button size="xs" variant="outline" icon={<Upload size={12} />} onClick={() => pickFile(pack)}>
                      {t('agentDetail:minePackAddFace')}
                    </Button>
                    <IconButton
                      size="sm"
                      tone="danger"
                      icon={<Trash2 size={14} />}
                      label={t('agentDetail:minePackDelete')}
                      onClick={() => void removePack(pack)}
                    />
                  </div>
                </div>
                {pack.faces.length === 0 ? (
                  <p className="text-xs text-textMuted px-3 pb-3">{t('agentDetail:faceGridEmpty')}</p>
                ) : (
                  <div className="grid grid-cols-4 sm:grid-cols-6 gap-2 px-3 pb-3">
                    {pack.faces.map((face) => (
                      <div key={face.id} className="group relative rounded-control border border-border bg-surface p-1.5">
                        <img
                          src={faceAssetUrl(pack, face)}
                          alt={face.name}
                          title={face.caption || face.name}
                          className="w-full aspect-square object-contain"
                        />
                        <div className="text-3xs text-textMuted truncate mt-1">{face.name}</div>
                        <IconButton
                          size="sm"
                          tone="danger"
                          icon={<X size={12} />}
                          label={t('common:delete')}
                          onClick={() => void removeFace(pack, face)}
                          className="absolute top-0.5 right-0.5 bg-surface/90 opacity-0 group-hover:opacity-100 transition-opacity"
                        />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {error && <p className="text-xs text-rose-400 mt-3 break-all">{error}</p>}
        <input
          ref={fileInput}
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          className="hidden"
          onChange={onFileSelected}
        />
      </section>

      {/* ── 上传一个表情：图 + 给 AI 看的说明 ── */}
      <Modal
        open={!!draft}
        onClose={closeDraft}
        title={t('agentDetail:faceModalTitle')}
        size="md"
        footer={(
          <>
            <Button variant="outline" size="sm" onClick={closeDraft}>{t('common:cancel')}</Button>
            <Button size="sm" loading={uploading} onClick={() => void saveFace()}>{t('agentDetail:faceSave')}</Button>
          </>
        )}
      >
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            {draft && (
              <img
                src={draft.preview}
                alt=""
                className="w-16 h-16 rounded-control border border-border object-contain bg-canvas shrink-0"
              />
            )}
            <p className="text-xs text-textMuted">
              {t('agentDetail:faceImageHint', { size: limits.image_max_mb })}
            </p>
          </div>
          <Input
            label={t('agentDetail:faceName')}
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            placeholder={t('agentDetail:faceNamePlaceholder')}
            maxLength={24}
          />
          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-textSecondary">{t('agentDetail:faceCaption')}</label>
            <textarea
              className="field"
              rows={3}
              value={form.caption}
              onChange={(e) => setForm((f) => ({ ...f, caption: e.target.value }))}
              placeholder={t('agentDetail:faceCaptionPlaceholder')}
              maxLength={160}
            />
            <p className="text-3xs text-textMuted">{t('agentDetail:faceCaptionHint')}</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Input
              label={t('agentDetail:faceKeywords')}
              value={form.keywords}
              onChange={(e) => setForm((f) => ({ ...f, keywords: e.target.value }))}
              maxLength={120}
            />
            <Input
              label={t('agentDetail:faceMood')}
              value={form.mood}
              onChange={(e) => setForm((f) => ({ ...f, mood: e.target.value }))}
              maxLength={24}
            />
          </div>
          <Input
            label={t('agentDetail:faceEmoji')}
            hint={t('agentDetail:faceEmojiHint')}
            value={form.emoji}
            onChange={(e) => setForm((f) => ({ ...f, emoji: e.target.value }))}
            maxLength={32}
          />
        </div>
      </Modal>
    </div>
  )
}
