import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { X, MessageSquare, UserPlus, Star, Pencil } from 'lucide-react'
import { api } from '../api/client'
import { avatarGradient } from '../utils/avatar'
import { useT, useLang } from '../i18n/I18nContext'
import { getStatusTextStyle, BG_ELEVATED_LIGHT, BG_ELEVATED_DARK } from '../utils/statusColor.tsx'
import { fmtDate, formatMessageTime } from '../utils/time'
import { useTheme } from '../context/ThemeContext'
import { Dialog } from './ui'
import RemarkField from './RemarkField'
import RemarkName from './RemarkName'
import { avatarInitial } from '../utils/remark'
import { useRemark } from '../hooks/useRemarks'

interface ChannelGroupInfo {
  kind: string
  label: string
  name: string
  member_num?: number
  memo?: string
  class_text?: string
  tags?: string[]
}

interface ProfileCardProps {
  entityType: 'human' | 'ai' | 'group'
  entityId: number
  entityName: string
  state?: string
  avatar_url?: string | null
  onClose: () => void
}

interface ProfileData {
  entity_type: string
  entity_id: number
  name: string
  avatar_url: string | null
  bio: string | null
  status_text: string | null
  status_color: string | null
  state?: string
  created_at: string | null
  owner_name: string | null
  is_friend: boolean
  friendship_id: number | null
  is_priority: boolean
  last_active_at: string | null
}

export default function ProfileCard({ entityType, entityId, entityName, state, avatar_url, onClose }: ProfileCardProps) {
  const t = useT()
  const lang = useLang()
  const { theme } = useTheme()
  const navigate = useNavigate()
  const [profile, setProfile] = useState<ProfileData | null>(null)
  const [groupDetail, setGroupDetail] = useState<{
    channels?: ChannelGroupInfo[]
    announcement?: string | null
    bio?: string | null
  } | null>(null)
  const [fullImg, setFullImg] = useState<string | null>(null)
  const isActive = profile?.state === 'active' || state === 'active'
  const [loading, setLoading] = useState(entityType === 'group' ? false : true)
  const [sending, setSending] = useState(false)
  const [showAddFriend, setShowAddFriend] = useState(false)
  const [friendMessage, setFriendMessage] = useState('')
  const [addingFriend, setAddingFriend] = useState(false)
  const [requestSent, setRequestSent] = useState(false)
  const [isPriority, setIsPriority] = useState(false)
  // 备注：资料卡自己的输入框与"头像首字"读同一份 store（不再单独拉接口）
  const remarkTarget = entityType === 'group' ? 'group' : 'user'
  const { value: remarkValue } = useRemark(remarkTarget, entityId)
  const [togglingPriority, setTogglingPriority] = useState(false)

  // 群聊不需要加载 profile（数据直接从 props 来）
  useEffect(() => {
    if (entityType === 'group') return
    setLoading(true)
    api.get<ProfileData>(`/user/profile/${entityType}/${entityId}`)
      .then((data) => { setProfile(data); setIsPriority(data.is_priority || false) })
      .catch(() => setProfile(null))
      .finally(() => setLoading(false))
  }, [entityType, entityId])

  // 群资料卡：列表里没有通道侧的东西，打开时才拉一次（通道那边这个群叫什么、多少人）
  useEffect(() => {
    if (entityType !== 'group') return
    api.get<{ channels?: ChannelGroupInfo[]; announcement?: string | null; bio?: string | null }>(`/groups/${entityId}`)
      .then(setGroupDetail)
      .catch(() => setGroupDetail(null))
  }, [entityType, entityId])

  const handleTogglePriority = async () => {
    const fid = profile?.friendship_id
    if (!fid) return
    setTogglingPriority(true)
    try {
      const r = await api.post<{ is_priority: boolean }>(`/friends/${fid}/toggle-priority`)
      setIsPriority(r.is_priority)
    } catch { /* ignore */ }
    finally { setTogglingPriority(false) }
  }

  const handleSendDM = async () => {
    setSending(true)
    try {
      const dm = await api.post<{ session_id: string }>(`/dm/${entityId}`)
      if (dm.session_id) {
        onClose()
        navigate(`/chat/dm/${dm.session_id}`)
      }
    } catch (err: any) {
      alert(err.message || t('error:startDmFailed'))
    } finally {
      setSending(false)
    }
  }

  const handleAddFriend = async () => {
    setAddingFriend(true)
    try {
      await api.post('/friends/requests', {
        target_type: entityType,
        target_id: entityId,
        message: friendMessage.trim() || undefined,
      })
      // 申请只是送出去了，对方还没通过——不能标成好友，否则卡片会显示成
      // 「已是好友」而刷新后又变回去
      setRequestSent(true)
      setShowAddFriend(false)
      setFriendMessage('')
      alert(t('search:addFriendSuccess'))
    } catch (err: any) {
      alert(err.message || t('search:addFriendFailed'))
    } finally {
      setAddingFriend(false)
    }
  }


  if (loading) {
    return (
      <Dialog onClose={onClose} className="flex items-center justify-center">
        <div
          className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-md mx-4 shadow-2xl shadow-black/30"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-center py-8">
            <div className="animate-spin w-6 h-6 border-2 border-primary-500 border-t-transparent rounded-full" />
          </div>
        </div>
      </Dialog>
    )
  }

  const name = profile?.name || entityName
  const avatarUrl = entityType === 'group' ? (avatar_url || profile?.avatar_url) : profile?.avatar_url
  const bio = profile?.bio
  const statusText = profile?.status_text
  const statusColor = profile?.status_color
  const ownerName = profile?.owner_name
  const createdAt = profile?.created_at
  const isFriend = profile?.is_friend ?? false
  const isGroup = entityType === 'group'
  // 群的"简介"只有一个来源：通道侧那边的群简介（站内没有这个概念）
  const groupChannelMemo = (groupDetail?.channels || [])
    .map((c) => c.memo || '')
    .filter(Boolean)
    .join(' / ')
  const avatarShape = isGroup ? 'rounded-card' : 'rounded-full'

  return (
    <Dialog onClose={onClose} className="flex items-center justify-center">
      <div
        className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-md mx-4 shadow-2xl shadow-black/30 pb-[var(--safe-bottom)] md:pb-6"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-3 flex-1 min-w-0">
            {/* 头像 */}
            <button
              onClick={() => avatarUrl && setFullImg(avatarUrl)}
              className={`relative w-14 h-14 ${avatarShape} flex items-center justify-center shrink-0 overflow-hidden ${
                isGroup ? 'bg-elevated' : avatarGradient(entityType, false, true)
              }`}>
              {avatarUrl ? (
                <img src={avatarUrl} alt={name} className={`w-full h-full ${avatarShape} object-cover hover:opacity-80 transition-opacity`} />
              ) : (
                <div className={`w-full h-full ${avatarShape} flex items-center justify-center ${
                  isGroup ? 'bg-primary-500/10' : avatarGradient(entityType, false, false)
                }`}>
                  <span className="text-xl font-bold text-white">{avatarInitial(remarkValue, name)}</span>
                </div>
              )}
            </button>
            <div className="min-w-0">
              <h3 className="font-semibold text-textPrimary text-base truncate">
                <RemarkName remark={remarkValue} name={name} />
              </h3>
              <div className="flex items-center gap-1.5 text-sm text-textSecondary">
                {entityType === 'ai' && <span>{t('profileCard:aiPrefix')}</span>}
                {statusText && (
                  <span className="font-medium truncate" style={statusColor
                    ? getStatusTextStyle(statusColor, theme === 'dark' ? BG_ELEVATED_DARK : BG_ELEVATED_LIGHT)
                    : undefined}>
                    · {statusText}
                  </span>
                )}
              </div>
            </div>
          </div>
          <button onClick={onClose} className="icon-btn-sm text-textMuted shrink-0">
            <X size={20} />
          </button>
        </div>

        {/* 备注：我给它的称呼，列表优先显示它（只对我可见） */}
        <div className="mb-3 flex items-center gap-2">
          <Pencil size={12} className="text-textMuted shrink-0" />
          <div className="flex-1 min-w-0">
            <RemarkField
              targetType={remarkTarget}
              targetId={entityId}
              placeholder={t('profileCard:remarkPlaceholder')}
              inputClassName="text-xs"
            />
          </div>
        </div>

        {/* 简介：群自己那份没有就用通道侧的群简介（QQ 群的"群简介"） */}
        <div className="mb-3">
          <p className="text-sm text-textMuted leading-relaxed italic">
            {isGroup
              ? (groupDetail?.bio || groupChannelMemo || t('profileCard:groupBioEmpty'))
              : (bio || t('profileCard:bioEmpty'))}
          </p>
        </div>

        {/* 群公告：设了才显示（列表里不带，详情里给） */}
        {isGroup && groupDetail?.announcement && (
          <div className="mb-3 rounded-card border border-border bg-canvas px-3 py-2">
            <div className="text-3xs text-textSecondary">{t('groupSettings:announcement')}</div>
            <div className="text-xs text-textPrimary whitespace-pre-wrap leading-relaxed">{groupDetail.announcement}</div>
          </div>
        )}

        {/* 通道那边它是什么样：群名/人数/简介——同一个 AI 接好几个群时，界面靠这个分辨"哪个是哪个" */}
        {(groupDetail?.channels || []).map((c, i) => (
          <div key={i} className="mb-3 rounded-card border border-border bg-canvas px-3 py-2">
            <div className="flex items-center gap-1.5 text-3xs text-textSecondary">
              <MessageSquare size={12} />
              {t('profileCard:channelGroup', { label: c.label })}
            </div>
            <div className="text-sm text-textPrimary truncate">{c.name}</div>
            <div className="text-3xs text-textMuted truncate">
              {c.member_num ? t('profileCard:channelMembers', { n: String(c.member_num) }) : ''}
              {c.memo ? (c.member_num ? ' · ' : '') + c.memo : ''}
              {c.class_text ? ' · ' + c.class_text : ''}
              {c.tags && c.tags.length ? ' · ' + c.tags.join(' / ') : ''}
            </div>
          </div>
        ))}

        {/* 详细信息 */}
        <div className="mb-4 space-y-1 text-xs text-textMuted">
          {entityType === 'ai' && ownerName && (
            <div>{t('profileCard:creator')}: {ownerName}</div>
          )}
          <div className="flex flex-wrap gap-x-2">
            {createdAt && (
              <span>{t('profileCard:registeredOn')}: {fmtDate(createdAt, lang)}</span>
            )}
            {isActive ? (
              <span className="text-mint-500">{t('dm:online')}</span>
            ) : profile?.last_active_at ? (
              <span>{t('dm:lastActive')} {formatMessageTime(profile.last_active_at, lang)}</span>
            ) : null}
            {/* 关系状态跟注册时间/在线一样是"这个人的信息"，放这一行；不放操作区（那不是能点的东西） */}
            {!isGroup && isFriend && <span>{t('profileCard:alreadyFriend')}</span>}
            {!isGroup && !isFriend && requestSent && <span>{t('profileCard:requestSent')}</span>}
          </div>
        </div>

        {/* 操作按钮 */}
        <div className="space-y-2">
          {isGroup ? null : (
            <>
          {isFriend && profile?.friendship_id && (
            <button
              onClick={handleTogglePriority}
              disabled={togglingPriority}
              className={`btn btn-md w-full gap-2 ${isPriority ? 'btn-tinted' : 'btn-outline'}`}
            >
              <Star size={16} fill={isPriority ? 'currentColor' : 'none'} />
              {isPriority ? t('profileCard:priorityOn') : t('profileCard:priorityOff')}
            </button>
          )}

          {/* 加好友区域：已是好友 / 已发申请都不给按钮（状态在上一行的信息里，后端也会拒） */}
          {!isFriend && !requestSent && (
            showAddFriend ? (
              <div className="space-y-2">
                <textarea
                  value={friendMessage}
                  onChange={(e) => setFriendMessage(e.target.value)}
                  placeholder={t('profileCard:friendMessagePlaceholder')}
                  rows={2}
                  maxLength={200}
                  className="field field-noresize w-full"
                  autoFocus
                />
                <div className="flex gap-2">
                  <button
                    onClick={() => { setShowAddFriend(false); setFriendMessage('') }}
                    className="btn btn-sm btn-outline flex-1"
                  >
                    {t('common:cancel')}
                  </button>
                  <button
                    onClick={handleAddFriend}
                    disabled={addingFriend}
                    className="btn btn-sm btn-success flex-1"
                  >
                    <UserPlus size={12} />
                    {addingFriend ? '...' : t('profileCard:sendRequest')}
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => setShowAddFriend(true)}
                className="btn btn-sm btn-tinted-success w-full gap-2"
              >
                <UserPlus size={16} />
                {t('profileCard:addFriend')}
              </button>
            )
          )}
          </>
          )}
          {/* 发消息 */}
          <button
            onClick={handleSendDM}
            disabled={sending}
            className="btn btn-md btn-primary w-full gap-2"
          >
            <MessageSquare size={16} />
            {sending ? t('profileCard:sending') : isFriend ? t('profileCard:sendDM') : t('profileCard:sendDM')}
          </button>

          {/* 群聊信息 */}
          {isGroup && (
            <div className="text-2xs text-textMuted text-center pt-2">
              {createdAt && <span>{t('profileCard:createdOn')} {fmtDate(createdAt, lang)}</span>}
            </div>
          )}
          </div>
        </div>

      {/* 查看大图 */}
      {fullImg && (
        <div className="fixed inset-0 bg-black/90 z-toast flex items-center justify-center" onClick={() => setFullImg(null)}>
          <img src={fullImg} alt="" className="max-w-[90vw] max-h-[90vh] object-contain" />
          <button onClick={() => setFullImg(null)} className="icon-btn icon-btn-lg icon-btn-overlay icon-btn-pill absolute top-4 right-4" aria-label={t('common:close')} title={t('common:close')}>
            <X size={24} />
          </button>
        </div>
      )}
    </Dialog>
  )
}
