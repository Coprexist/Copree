import { useState, useEffect, Fragment } from 'react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'
import { Globe, Link, Plus, Trash2, RefreshCw, Power, Shield, X } from 'lucide-react'
import ExternalLinkSafe from '../../../components/ExternalLinkSafe'
import { Dialog } from '../../../components/ui'

interface InstanceInfo {
  instance_id: string
  public_id: string | null
  display_name: string
  public_url: string
  github_token_configured: boolean
  created_at: string | null
  updated_at: string | null
}

interface Peer {
  id: number
  peer_public_id: string
  display_name: string
  remote_url: string
  is_enabled: boolean
  connection_state: string
  last_connected_at: string | null
  url_rotated_at: string | null
  url_rotation_count: number
  remote_url_backup: string | null
  created_at: string | null
  updated_at: string | null
}

interface FederatedEntity {
  id: number
  federated_id: string
  peer_id: number
  peer_display_name: string
  entity_type: string
  local_ref_id: string
  display_name: string
  is_enabled: boolean
  direction: string
  created_at: string | null
  updated_at: string | null
}

export default function FederationTab() {
  const t = useT()
  const [instance, setInstance] = useState<InstanceInfo | null>(null)
  const [peers, setPeers] = useState<Peer[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  // 注册状态机: idle → confirm(风险告知) → loading → success | error
  type RegisterState = 'idle' | 'confirm' | 'loading' | 'success' | 'error'
  const [registerState, setRegisterState] = useState<RegisterState>('idle')
  const [registerResult, setRegisterResult] = useState('')
  const [registerErrorCode, setRegisterErrorCode] = useState('')
  const [githubToken, setGithubToken] = useState('')
  const [tokenSaving, setTokenSaving] = useState(false)
  const [dialogToken, setDialogToken] = useState('')  // 弹窗内的临时 Token 输入
  const [quickToken, setQuickToken] = useState('')    // TOKEN_MISSING / TOKEN_INVALID 时行内快速替换
  const [showTokenInput, setShowTokenInput] = useState(false)  // 非编辑视图中的 Token 更换
  const [manageToken, setManageToken] = useState('')

  // 表单状态
  const [showAddPeer, setShowAddPeer] = useState(false)
  const [newPeer, setNewPeer] = useState({ display_name: '', peer_public_id: '', remote_url: '', shared_secret: '' })
  const [editingPeerId, setEditingPeerId] = useState<number | null>(null)
  const [editPeerForm, setEditPeerForm] = useState({ display_name: '', remote_url: '', shared_secret: '' })
  const [editInstance, setEditInstance] = useState(false)
  const [instanceForm, setInstanceForm] = useState({ display_name: '', public_url: '', public_id: '' })

  // URL 轮换
  const [rotatingPeerId, setRotatingPeerId] = useState<number | null>(null)
  const [rotateUrl, setRotateUrl] = useState('')
  const [rotateError, setRotateError] = useState('')
  const [rotateLoading, setRotateLoading] = useState(false)

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setLoading(true)
    setError('')
    try {
      const [inst, peerList] = await Promise.all([
        api.get<InstanceInfo>('/admin/federation/instance'),
        api.get<Peer[]>('/admin/federation/peers'),
      ])
      setInstance(inst)
      setPeers(peerList)
      setInstanceForm({ display_name: inst.display_name, public_url: inst.public_url, public_id: inst.public_id || '' })
    } catch (e: any) {
      setError(e?.message || t('admin.loadFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleSaveInstance = async () => {
    await api.put('/admin/federation/instance', instanceForm)
    setEditInstance(false)
    await loadData()
  }

  const handleAddPeer = async () => {
    if (!newPeer.peer_public_id || !newPeer.shared_secret) return
    try {
      await api.post('/admin/federation/peers', newPeer)
      setNewPeer({ display_name: '', peer_public_id: '', remote_url: '', shared_secret: '' })
      setShowAddPeer(false)
      await loadData()
    } catch (e: any) {
      setError(e?.message || t('admin.addPeerFailed'))
    }
  }

  const handleDeletePeer = async (id: number) => {
    if (!confirm(t('admin.confirmRemovePeer'))) return
    try {
      await api.delete(`/admin/federation/peers/${id}`)
      await loadData()
    } catch (e: any) {
      setError(e?.message || t('admin.deletePeerFailed'))
    }
  }

  const handleConnect = async (id: number) => {
    setError('')
    try {
      await api.post(`/admin/federation/peers/${id}/connect`)
      await loadData()
    } catch (e: any) {
      setError(e?.message || t('admin.connectFailed'))
      await loadData()  // 仍然刷新，后端可能更新了状态
    }
  }

  const handleDisconnect = async (id: number) => {
    try {
      await api.post(`/admin/federation/peers/${id}/disconnect`)
      await loadData()
    } catch (e: any) {
      setError(e?.message || t('admin.disconnectFailed'))
    }
  }

  const handleEditPeer = (peer: Peer) => {
    setEditingPeerId(peer.id)
    setEditPeerForm({ display_name: peer.display_name, remote_url: peer.remote_url, shared_secret: '' })
  }

  const handleSavePeer = async () => {
    if (!editingPeerId) return
    const payload: any = {
      display_name: editPeerForm.display_name,
      remote_url: editPeerForm.remote_url,
    }
    // 只有填了密钥才发送（留空 = 不修改）
    if (editPeerForm.shared_secret.trim()) {
      payload.shared_secret = editPeerForm.shared_secret.trim()
    }
    await api.put(`/admin/federation/peers/${editingPeerId}`, payload)
    setEditingPeerId(null)
    await loadData()
  }

  const handleRotateUrl = async (peerId: number) => {
    const peer = peers.find(p => p.id === peerId)
    if (!peer) return

    // 打开轮换表单
    if (rotatingPeerId !== peerId) {
      setRotatingPeerId(peerId)
      setRotateUrl(peer.remote_url.replace(/\/federation\/ws$/, '') + '/federation/ws')
      setRotateError('')
      return
    }

    // 执行轮换
    if (!rotateUrl.trim()) {
      setRotateError(t('admin.urlRequired'))
      return
    }

    setRotateLoading(true)
    setRotateError('')
    try {
      await api.post(`/admin/federation/peers/${peerId}/rotate-url`, { new_url: rotateUrl.trim() })
      setRotatingPeerId(null)
      await loadData()
    } catch (e: any) {
      setRotateError(e?.message || t('admin.rotateFailed'))
    } finally {
      setRotateLoading(false)
    }
  }

  const handleRegister = async () => {
    // 首次点击 → 显示风险告知弹窗
    if (registerState !== 'confirm') {
      setRegisterState('confirm')
      return
    }

    // 再次点击（已确认风险）→ 执行注册
    setRegisterState('loading')

    // 如果弹窗中填了 Token（无论是首次配置还是更换），先保存
    const needSaveToken = !!dialogToken.trim()
    if (needSaveToken) {
      setRegisterResult(t('admin.savingToken'))
      try {
        await api.put('/admin/federation/instance/github-token', { token: dialogToken.trim() })
        setDialogToken('')
        await loadData() // 刷新 github_token_configured
      } catch (e: any) {
        setRegisterState('error')
        setRegisterResult(e?.response?.data?.detail || t('admin.tokenSaveFailed'))
        return
      }
    }

    setRegisterResult(t('admin.verifyingRegister'))
    setRegisterErrorCode('')

    try {
      const result = await api.post<{
        success: boolean
        message: string
        error_code?: string
        existing_entry?: { display_name: string; registered_at: string }
      }>('/admin/federation/instance/register')

      if (result.success) {
        setRegisterState('success')
        setRegisterResult(result.message)
        await loadData()
      } else {
        setRegisterState('error')
        setRegisterErrorCode(result.error_code || '')
        setRegisterResult(result.message || t('admin.registerFailed'))
      }
    } catch (e: any) {
      setRegisterState('error')
      const detail = e?.response?.data?.detail
      if (typeof detail === 'object') {
        setRegisterErrorCode(detail.error_code || '')
        setRegisterResult(detail.message || JSON.stringify(detail))
      } else {
        setRegisterResult(typeof detail === 'string' ? detail : (e?.message || t('admin.registerFailed')))
      }
    }
  }

  const handleQuickSaveToken = async () => {
    // 快速保存 Token 并重试注册（TOKEN_MISSING / TOKEN_INVALID 错误恢复）
    if (!quickToken.trim()) return
    setRegisterState('loading')
    setRegisterResult(t('admin.savingToken'))
    try {
      await api.put('/admin/federation/instance/github-token', { token: quickToken.trim() })
      setQuickToken('')
      await loadData()
      // 保存成功后自动重试注册
      setRegisterResult(t('admin.verifyingRegister'))
      const result = await api.post<{
        success: boolean; message: string; error_code?: string
      }>('/admin/federation/instance/register')
      if (result.success) {
        setRegisterState('success')
        setRegisterResult(result.message)
        await loadData()
      } else {
        setRegisterState('error')
        setRegisterErrorCode(result.error_code || '')
        setRegisterResult(result.message || t('admin.registerFailed'))
      }
    } catch (e: any) {
      setRegisterState('error')
      const detail = e?.response?.data?.detail
      if (typeof detail === 'object') {
        setRegisterErrorCode(detail.error_code || '')
        setRegisterResult(detail.message || JSON.stringify(detail))
      } else {
        setRegisterResult(typeof detail === 'string' ? detail : (e?.message || t('admin.saveOrRegisterFailed')))
      }
    }
  }

  const handleManageToken = async () => {
    if (!manageToken.trim()) return
    setTokenSaving(true)
    try {
      await api.put('/admin/federation/instance/github-token', { token: manageToken.trim() })
      setManageToken('')
      setShowTokenInput(false)
      setRegisterResult(t('admin.tokenUpdated'))
      await loadData()
    } catch (e: any) {
      setRegisterResult(e?.response?.data?.detail || t('admin.saveFailed'))
    } finally {
      setTokenSaving(false)
    }
  }

  const handleRegenerateId = async () => {
    if (!confirm(t('admin.confirmRegenerateId'))) return
    try {
      const result = await api.post<{ success: boolean; public_id: string }>('/admin/federation/instance/regenerate-id')
      if (result.public_id) {
        setInstanceForm({ ...instanceForm, public_id: result.public_id })
        setRegisterResult('')
        await loadData()
      }
    } catch (e: any) {
      alert(e?.response?.data?.detail || t('admin.saveFailed'))
    }
  }

  const handleSaveToken = async () => {
    if (!githubToken.trim()) {
      setRegisterResult(t('admin.pleaseEnterToken'))
      return
    }
    setTokenSaving(true)
    try {
      await api.put('/admin/federation/instance/github-token', { token: githubToken.trim() })
      setGithubToken('')
      setRegisterResult(t('admin.tokenSavedEncrypted'))
      await loadData()
    } catch (e: any) {
      setRegisterResult(e?.response?.data?.detail || t('admin.saveFailed'))
    } finally {
      setTokenSaving(false)
    }
  }

  const stateBadge = (state: string) => {
    const map: Record<string, { bg: string; text: string; label: string }> = {
      connected: { bg: 'bg-mint-500/10', text: 'text-mint-400', label: t('admin.connected') },
      connecting: { bg: 'bg-accent-500/10', text: 'text-accent-400', label: t('admin.connecting') },
      disconnected: { bg: 'bg-slate-500/10', text: 'text-slate-400', label: t('common.disconnected') },
      failed: { bg: 'bg-rose-500/10', text: 'text-rose-400', label: t('admin.failed') },
      rotating: { bg: 'bg-primary-500/10', text: 'text-primary-400', label: t('admin.rotating') },
    }
    const s = map[state] || map.disconnected
    return <span className={`text-xs px-2 py-0.5 rounded-full ${s.bg} ${s.text}`}>{s.label}</span>
  }

  if (loading) return <div className="text-textMuted text-sm p-4">{t('common.loading')}</div>

  const isRegistering = registerState === 'loading'

  return (
    <div className="space-y-6">
      {/* 操作错误横幅（可关闭，不破坏整个页面） */}
      {error && (
        <div className="flex items-start gap-2 bg-rose-400/5 border border-rose-400/20 rounded-control px-4 py-2.5">
          <span className="text-rose-400 text-sm flex-1 whitespace-pre-wrap">{error}</span>
          <button onClick={() => setError('')} className="text-rose-400/60 hover:text-rose-400 shrink-0 text-sm"><X size={14} /></button>
        </div>
      )}
      {/* 风险告知弹窗 */}
      {registerState === 'confirm' && (
        <Dialog onClose={() =>  { setRegisterState('idle'); setDialogToken('') } } className="flex items-center justify-center">
          <div className="bg-surface border border-border rounded-card p-6 max-w-md mx-4 shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="text-base font-semibold text-textPrimary mb-3">{t('admin.registerNotice')}</h3>
            <div className="text-sm text-textSecondary space-y-2 mb-5">
              <p>{t('admin.registerNoticeText')}</p>
              <ul className="list-disc pl-5 space-y-1 text-xs">
                <li>{t('admin.registerNoticeItem1')}</li>
                <li>{t('admin.registerNoticeItem2')}</li>
                <li>{t('admin.registerNoticeItem3')}</li>
                <li>{t('admin.registerNoticeItem4')}</li>
                <li className="text-accent-400">{t('admin.registerNoticeItem5')}</li>
              </ul>
              <p className="text-xs text-textMuted mt-2">{t('admin.registerNoticeVerify')}</p>
            </div>
            {/* Token 输入区：未配置时突出显示，已配置时可折叠更换 */}
            <div className={`mb-4 p-3 rounded-control ${!instance?.github_token_configured ? 'bg-accent-500/5 border border-accent-500/20' : 'bg-canvas border border-border'}`}>
              {instance?.github_token_configured ? (
                <details className="text-xs">
                  <summary className="text-textMuted cursor-pointer hover:text-textPrimary transition-colors">
                    {t('admin.tokenConfiguredClickChange')}
                  </summary>
                  <div className="mt-2">
                    <input
                      type="password"
                      value={dialogToken}
                      onChange={e => setDialogToken(e.target.value)}
                      className="w-full px-3 py-1.5 text-sm bg-canvas border border-border rounded-control text-textPrimary font-mono"
                      placeholder={t('admin.pasteNewToken')}
                    />
                    <p className="text-3xs text-textMuted mt-1">
                      {t('admin.tokenHintOrGet')} <ExternalLinkSafe href="https://github.com/settings/tokens/new" className="text-primary-400 hover:text-primary-500 dark:hover:text-primary-300">github.com/settings/tokens</ExternalLinkSafe>
                    </p>
                  </div>
                </details>
              ) : (
                <>
                  <label className="text-xs text-textMuted">
                    {t('admin.githubToken')}{' '}
                    <ExternalLinkSafe href="https://github.com/settings/tokens/new" className="text-primary-400 hover:text-primary-500 dark:hover:text-primary-300">
                      {t('common.back')} →
                    </ExternalLinkSafe>
                  </label>
                  <p className="text-3xs text-textMuted mb-1.5" dangerouslySetInnerHTML={{ __html: t('admin.tokenHelpText') }} />
                  <input
                    type="password"
                    value={dialogToken}
                    onChange={e => setDialogToken(e.target.value)}
                    className="w-full px-3 py-1.5 text-sm bg-canvas border border-border rounded-control text-textPrimary font-mono"
                    placeholder="ghp_xxxxxxxxxxxxxxxx"
                  />
                </>
              )}
            </div>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => { setRegisterState('idle'); setDialogToken('') }}
                className="px-4 py-1.5 text-xs bg-canvas border border-border text-textSecondary rounded-control hover:bg-border/20 transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={handleRegister}
                disabled={!instance?.github_token_configured && !dialogToken.trim()}
                className={`px-4 py-1.5 text-xs rounded-control transition-colors ${
                  !instance?.github_token_configured && !dialogToken.trim()
                    ? 'bg-canvas text-textMuted cursor-not-allowed'
                    : 'bg-mint-600 hover:bg-mint-500 text-white'
                }`}
              >
                {!instance?.github_token_configured ? t('admin.saveTokenAndRegister') : t('admin.knownAndContinue')}
              </button>
            </div>
          </div>
        </Dialog>
      )}
      {/* 实例身份 */}
      <section className="bg-surface border border-border rounded-card p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-textPrimary flex items-center gap-2">
            <Shield size={16} /> {t('admin.instanceIdentity')}
          </h2>
          <button
            onClick={() => setEditInstance(!editInstance)}
            className="text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors"
          >
            {editInstance ? t('common.cancel') : t('common.edit')}
          </button>
        </div>

        {editInstance ? (
          <div className="space-y-3">
            <div>
              <label className="text-xs text-textMuted">{t('admin.displayName')}</label>
              <input
                value={instanceForm.display_name}
                onChange={e => setInstanceForm({ ...instanceForm, display_name: e.target.value })}
                className="w-full mt-1 px-3 py-1.5 text-sm bg-canvas border border-border rounded-control text-textPrimary"
                placeholder={t('admin.instanceNamePlaceholder')}
              />
            </div>
            <div>
              <label className="text-xs text-textMuted">
                {t('admin.publicUrlLabel')}
              </label>
              <div className="flex items-stretch mt-1">
                <select
                  value={(instanceForm.public_url.match(/^(wss?):\/\//)?.[1]) || 'wss'}
                  onChange={e => {
                    const host = instanceForm.public_url.replace(/^wss?:\/\//, '').replace(/\/federation\/ws$/, '')
                    setInstanceForm({ ...instanceForm, public_url: `${e.target.value}://${host}/federation/ws` })
                  }}
                  className="w-20 px-2 text-sm bg-canvas border border-border rounded-l-lg text-textPrimary shrink-0"
                >
                  <option value="wss">wss://</option>
                  <option value="ws">ws://</option>
                </select>
                <input
                  value={(() => {
                    const m = instanceForm.public_url.match(/^(wss?):\/\/(.+?)\/federation\/ws$/)
                    return m ? m[2] : instanceForm.public_url.replace(/^wss?:\/\//, '')
                  })()}
                  onChange={e => {
                    const proto = (instanceForm.public_url.match(/^(wss?):\/\//)?.[1]) || 'wss'
                    const host = e.target.value.replace(/\/$/, '')
                    setInstanceForm({ ...instanceForm, public_url: `${proto}://${host}/federation/ws` })
                  }}
                  className="flex-1 px-3 py-1.5 text-sm bg-canvas border border-border border-x-0 text-textPrimary font-mono"
                  placeholder="ip-or-domain:5228"
                />
                <span className="inline-flex items-center px-2 text-xs text-textMuted bg-canvas border border-border rounded-r-lg shrink-0 font-mono">
                  /federation/ws
                </span>
              </div>
            </div>
            <div>
              <label className="text-xs text-textMuted">{t('admin.publicIdLabel')}</label>
              <div className="flex items-center gap-2 mt-1">
                <input
                  value={instanceForm.public_id}
                  readOnly
                  className="flex-1 px-3 py-1.5 text-sm bg-canvas border border-border rounded-control text-textPrimary font-mono cursor-default"
                />
                <button
                  onClick={handleRegenerateId}
                  className="btn btn-xs btn-outline shrink-0 text-3xs text-textMuted hover:text-accent-400 hover:bg-accent-500/10"
                  title={t('admin.confirmRegenerateId')}
                >
                  {t('admin.regenerate')}
                </button>
              </div>
            </div>
            <div>
              <label className="text-xs text-textMuted">
                {t('admin.githubTokenLabel')}{' '}
                <ExternalLinkSafe href="https://github.com/settings/tokens/new" className="text-primary-400 hover:text-primary-500 dark:hover:text-primary-300">
                  {t('common.back')} →
                </ExternalLinkSafe>
              </label>
              <p className="text-3xs text-textMuted mb-1" dangerouslySetInnerHTML={{ __html: t('admin.tokenHelpText') }} />
              <div className="flex gap-2 mt-1">
                <input
                  type="password"
                  value={githubToken}
                  onChange={e => setGithubToken(e.target.value)}
                  className="flex-1 px-3 py-1.5 text-sm bg-canvas border border-border rounded-control text-textPrimary font-mono"
                  placeholder={instance?.github_token_configured ? t('admin.tokenConfiguredPlaceholder') : t('admin.tokenPlaceholder')}
                />
                <button
                  onClick={handleSaveToken}
                  disabled={tokenSaving || !githubToken.trim()}
                  className={`shrink-0 px-3 py-1.5 text-xs rounded-control transition-colors ${
                    tokenSaving || !githubToken.trim()
                      ? 'bg-canvas text-textMuted cursor-not-allowed'
                      : 'bg-accent-600 hover:bg-accent-500 text-white'
                  }`}
                >
                  {tokenSaving ? t('common.saving') : t('admin.saveToken')}
                </button>
              </div>
              {instance?.github_token_configured && !githubToken && (
                <p className="text-3xs text-mint-400 mt-1">{t('admin.tokenConfigured')}</p>
              )}
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleSaveInstance}
                className="btn btn-xs btn-primary"
              >
                {t('admin.saveInstanceInfo')}
              </button>
              <button
                onClick={handleRegister}
                disabled={registerState === 'loading'}
                className={`px-4 py-1.5 text-xs rounded-control transition-colors ${
                  registerState === 'loading'
                    ? 'bg-canvas text-textMuted cursor-not-allowed'
                    : registerState === 'success'
                    ? 'bg-mint-700 text-mint-300'
                    : 'bg-mint-600 hover:bg-mint-500 text-white'
                }`}
              >
                {registerState === 'loading' ? t('admin.registering') : registerState === 'success' ? t('admin.registered') : t('admin.registerToGithub')}
              </button>
            </div>
            {registerResult && (
              <p className={`text-xs whitespace-pre-line ${
                registerState === 'success' ? 'text-mint-400' :
                registerState === 'loading' ? 'text-accent-400' :
                'text-rose-400'
              }`}>
                {registerErrorCode && (
                  <span className="inline-block px-1.5 py-0.5 rounded text-3xs bg-rose-500/10 text-rose-400 mr-1 font-mono">
                    {registerErrorCode}
                  </span>
                )}
                {registerResult}
              </p>
            )}
            {/* TOKEN_MISSING 时行内快速输入，无需跳转到编辑模式 */}
            {(registerErrorCode === 'TOKEN_MISSING' || registerErrorCode === 'TOKEN_INVALID') && (
              <div className="flex gap-2 mt-2">
                <input
                  type="password"
                  value={quickToken}
                  onChange={e => setQuickToken(e.target.value)}
                  className="flex-1 px-3 py-1.5 text-sm bg-canvas border border-border rounded-control text-textPrimary font-mono"
                  placeholder="ghp_xxxxxxxxxxxxxxxx"
                />
                <button
                  onClick={handleQuickSaveToken}
                  disabled={registerState === 'loading' || !quickToken.trim()}
                  className="shrink-0 px-3 py-1.5 text-xs bg-accent-600 hover:bg-accent-500 text-white rounded-control transition-colors disabled:opacity-50"
                >
                  {registerState === 'loading' ? t('common.saving') : t('admin.saveAndRetry')}
                </button>
              </div>
            )}
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
              <div>
                <span className="text-textMuted text-xs">{t('admin.instanceId')}</span>
                <p className="text-textPrimary font-mono text-xs mt-0.5 truncate">{instance?.instance_id}</p>
              </div>
              <div>
                <span className="text-textMuted text-xs">{t('admin.publicId')}</span>
                <p className="text-textPrimary font-mono text-xs mt-0.5">
                  {instance?.public_id || <span className="text-textMuted italic">{t('admin.notGenerated')}</span>}
                </p>
              </div>
              <div>
                <span className="text-textMuted text-xs">{t('admin.displayName')}</span>
                <p className="text-textPrimary text-xs mt-0.5">{instance?.display_name || '-'}</p>
              </div>
              <div>
                <span className="text-textMuted text-xs">{t('admin.publicUrl')}</span>
                <p className="text-textPrimary text-xs mt-0.5">{instance?.public_url || '-'}</p>
              </div>
              <div>
                <span className="text-textMuted text-xs">{t('admin.githubToken')}</span>
                <div className="flex items-center gap-2 mt-0.5">
                  <p className="text-xs">
                    {instance?.github_token_configured ? (
                      <span className="text-mint-400">{t('admin.tokenConfiguredEncrypted')}</span>
                    ) : (
                      <span className="text-textMuted italic">{t('admin.notConfigured')}</span>
                    )}
                  </p>
                  <button
                    onClick={() => setShowTokenInput(!showTokenInput)}
                    className="text-3xs text-textMuted hover:text-textPrimary border border-border rounded px-1.5 py-0.5 transition-colors"
                  >
                    {showTokenInput ? t('common.cancel') : instance?.github_token_configured ? t('admin.change') : t('admin.configure')}
                  </button>
                </div>
                {showTokenInput && (
                  <div className="flex gap-2 mt-1.5">
                    <input
                      type="password"
                      value={manageToken}
                      onChange={e => setManageToken(e.target.value)}
                      className="flex-1 px-2 py-1 text-xs bg-canvas border border-border rounded-control text-textPrimary font-mono"
                      placeholder={instance?.github_token_configured ? t('admin.pasteNewToken') : t('admin.tokenPlaceholder')}
                    />
                    <button
                      onClick={handleManageToken}
                      disabled={tokenSaving || !manageToken.trim()}
                      className="shrink-0 px-2 py-1 text-xs bg-accent-600 hover:bg-accent-500 text-white rounded transition-colors disabled:opacity-50"
                    >
                      {tokenSaving ? t('common.saving') : t('common.save')}
                    </button>
                  </div>
                )}
              </div>
            </div>
            {instance?.public_id && (
              <div className="flex gap-2 mt-3">
                <button
                  onClick={handleRegister}
                  disabled={isRegistering}
                  className={`px-3 py-1 text-xs rounded-control transition-colors ${
                    isRegistering
                      ? 'bg-canvas text-textMuted cursor-not-allowed'
                      : 'bg-mint-600 hover:bg-mint-500 text-white'
                  }`}
                >
                  {isRegistering ? t('admin.registering') : registerState === 'success' ? t('admin.registered') : t('admin.registerToGithub')}
                </button>
                {registerResult && (
                  <span className={`text-xs self-center ${
                    registerState === 'success' ? 'text-mint-400' :
                    registerState === 'loading' ? 'text-accent-400' :
                    'text-rose-400'
                  }`}>
                    {registerErrorCode && <span className="font-mono mr-1">[{registerErrorCode}]</span>}
                    {registerResult}
                  </span>
                )}
                {/* TOKEN_MISSING 时行内快速输入 */}
                {(registerErrorCode === 'TOKEN_MISSING' || registerErrorCode === 'TOKEN_INVALID') && (
                  <div className="flex gap-2 mt-2 w-full">
                    <input
                      type="password"
                      value={quickToken}
                      onChange={e => setQuickToken(e.target.value)}
                      className="flex-1 px-3 py-1.5 text-sm bg-canvas border border-border rounded-control text-textPrimary font-mono"
                      placeholder={t('admin.tokenPlaceholder')}
                    />
                    <button
                      onClick={handleQuickSaveToken}
                      disabled={registerState === 'loading' || !quickToken.trim()}
                      className="shrink-0 px-3 py-1.5 text-xs bg-accent-600 hover:bg-accent-500 text-white rounded-control transition-colors disabled:opacity-50"
                    >
                      {registerState === 'loading' ? t('common.saving') : t('admin.saveAndRetry')}
                    </button>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </section>

      {/* 对等端列表 */}
      <section className="bg-surface border border-border rounded-card p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-textPrimary flex items-center gap-2">
            <Link size={16} /> {t('admin.peers').replace('{count}', String(peers.length))}
          </h2>
          <button
            onClick={() => setShowAddPeer(!showAddPeer)}
            className="flex items-center gap-1 text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors"
          >
            <Plus size={14} /> {t('admin.addPeer')}
          </button>
        </div>

        {/* 添加对等端表单 */}
        {showAddPeer && (
          <div className="mb-4 p-4 bg-canvas rounded-control border border-border space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-textMuted">{t('admin.peerPublicId')}</label>
                <input
                  value={newPeer.peer_public_id}
                  onChange={e => setNewPeer({ ...newPeer, peer_public_id: e.target.value })}
                  className="w-full mt-1 px-3 py-1.5 text-sm bg-surface border border-border rounded-control text-textPrimary font-mono"
                  placeholder={t('admin.peerIdPlaceholder')}
                />
              </div>
              <div>
                <label className="text-xs text-textMuted">{t('admin.peerDisplayName')}</label>
                <input
                  value={newPeer.display_name}
                  onChange={e => setNewPeer({ ...newPeer, display_name: e.target.value })}
                  className="w-full mt-1 px-3 py-1.5 text-sm bg-surface border border-border rounded-control text-textPrimary"
                  placeholder={t('admin.peerNamePlaceholder')}
                />
              </div>
              <div className="md:col-span-2">
                <label className="text-xs text-textMuted">
                  {t('admin.peerWsUrl')} <span className="text-textMuted/60">— {t('admin.optional')}</span>
                </label>
                <div className="flex items-stretch mt-1">
                  <select
                    value={(newPeer.remote_url.match(/^(wss?):\/\//)?.[1]) || 'wss'}
                    onChange={e => {
                      const host = newPeer.remote_url.replace(/^wss?:\/\//, '').replace(/\/federation\/ws$/, '')
                      setNewPeer({ ...newPeer, remote_url: host ? `${e.target.value}://${host}/federation/ws` : '' })
                    }}
                    className="w-20 px-2 text-sm bg-surface border border-border rounded-l-lg text-textPrimary shrink-0"
                  >
                    <option value="wss">wss://</option>
                    <option value="ws">ws://</option>
                  </select>
                  <input
                    value={(() => {
                      const m = newPeer.remote_url.match(/^(wss?):\/\/(.+?)\/federation\/ws$/)
                      return m ? m[2] : newPeer.remote_url.replace(/^wss?:\/\//, '')
                    })()}
                    onChange={e => {
                      const proto = (newPeer.remote_url.match(/^(wss?):\/\//)?.[1]) || 'wss'
                      const host = e.target.value.trim().replace(/\/$/, '')
                      setNewPeer({ ...newPeer, remote_url: host ? `${proto}://${host}/federation/ws` : '' })
                    }}
                    className="flex-1 px-3 py-1.5 text-sm bg-surface border border-border border-x-0 text-textPrimary font-mono"
                    placeholder="ip-or-domain:port"
                  />
                  <span className="inline-flex items-center px-2 text-xs text-textMuted bg-surface border border-border rounded-r-lg shrink-0 font-mono">
                    /federation/ws
                  </span>
                </div>
                <p className="text-3xs text-textMuted mt-1">{t('admin.peerUrlOptionalHint')}</p>
              </div>
              <div className="md:col-span-2">
                <label className="text-xs text-textMuted">{t('admin.sharedSecret')}</label>
                <input
                  type="password"
                  value={newPeer.shared_secret}
                  onChange={e => setNewPeer({ ...newPeer, shared_secret: e.target.value })}
                  className="w-full mt-1 px-3 py-1.5 text-sm bg-surface border border-border rounded-control text-textPrimary"
                  placeholder={t('admin.secretPlaceholder')}
                />
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleAddPeer}
                className="btn btn-xs btn-primary"
              >
                {t('admin.addAndConnect')}
              </button>
              <button
                onClick={() => setShowAddPeer(false)}
                className="px-4 py-1.5 text-xs bg-canvas border border-border text-textSecondary rounded-control hover:bg-border/20 transition-colors"
              >
                {t('common.cancel')}
              </button>
            </div>
          </div>
        )}

        {/* 对等端表格 */}
        {peers.length === 0 ? (
          <p className="text-sm text-textMuted py-4 text-center">{t('admin.noPeers')}</p>
        ) : (
          <div className="space-y-2">
            {peers.map(peer => (
              <Fragment key={peer.id}>
                <div className="flex items-center justify-between p-3 bg-canvas rounded-control border border-border hover:border-primary-500/20 transition-colors">
                <div className="flex items-center gap-3 min-w-0">
                  <div className={`w-2 h-2 rounded-full shrink-0 ${
                    peer.connection_state === 'connected' ? 'bg-mint-400' :
                    peer.connection_state === 'connecting' ? 'bg-accent-400 animate-pulse' :
                    'bg-slate-500'
                  }`} />
                  <div className="min-w-0">
                    <p className="text-sm text-textPrimary font-medium truncate">
                      {peer.display_name || peer.peer_public_id}
                    </p>
                    <p className="text-xs text-textMuted font-mono truncate">{peer.peer_public_id}</p>
                    <p className="text-3xs text-textMuted truncate">{peer.remote_url}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {stateBadge(rotatingPeerId === peer.id ? 'rotating' : peer.connection_state)}
                  {peer.connection_state === 'connected' ? (
                    <>
                      <button
                        onClick={() => handleRotateUrl(peer.id)}
                        className="p-1.5 text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 hover:bg-primary-500/10 rounded-control transition-colors"
                        title={t('admin.rotateUrl')}
                      >
                        <RefreshCw size={14} />
                      </button>
                      <button
                        onClick={() => handleDisconnect(peer.id)}
                        className="p-1.5 text-rose-400 hover:text-rose-500 dark:hover:text-rose-300 dark:hover:text-rose-300 hover:bg-rose-500/10 rounded-control transition-colors"
                        title={t('admin.disconnect')}
                      >
                        <Power size={14} />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={() => handleConnect(peer.id)}
                        className="p-1.5 text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 hover:bg-primary-500/10 rounded-control transition-colors"
                        title={t('admin.connect')}
                      >
                        <RefreshCw size={14} />
                      </button>
                      <button
                        onClick={() => handleEditPeer(peer)}
                        className="p-1.5 text-textMuted hover:text-accent-400 hover:bg-accent-500/10 rounded-control transition-colors"
                        title={t('common.edit')}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                      </button>
                    </>
                  )}
                  <button
                    onClick={() => handleDeletePeer(peer.id)}
                    className="p-1.5 text-textMuted hover:text-rose-400 hover:bg-rose-500/10 rounded-control transition-colors"
                    title={t('common.delete')}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              {/* 编辑对等端表单 */}
              {editingPeerId === peer.id && (
                <div className="p-3 bg-canvas rounded-control border border-accent-500/20 space-y-2">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                    <div>
                      <label className="text-3xs text-textMuted">{t('admin.displayName')}</label>
                      <input
                        value={editPeerForm.display_name}
                        onChange={e => setEditPeerForm({ ...editPeerForm, display_name: e.target.value })}
                        className="w-full mt-0.5 px-2 py-1 text-xs bg-surface border border-border rounded text-textPrimary"
                      />
                    </div>
                    <div>
                      <label className="text-3xs text-textMuted">{t('admin.peerUrl')}</label>
                      <div className="flex items-stretch mt-0.5">
                        <select
                          value={(editPeerForm.remote_url.match(/^(wss?):\/\//)?.[1]) || 'wss'}
                          onChange={e => {
                            const host = editPeerForm.remote_url.replace(/^wss?:\/\//, '').replace(/\/federation\/ws$/, '')
                            setEditPeerForm({ ...editPeerForm, remote_url: host ? `${e.target.value}://${host}/federation/ws` : '' })
                          }}
                          className="w-16 px-1.5 text-xs bg-surface border border-border rounded-l text-textPrimary shrink-0"
                        >
                          <option value="wss">wss://</option>
                          <option value="ws">ws://</option>
                        </select>
                        <input
                          value={(() => {
                            const m = editPeerForm.remote_url.match(/^(wss?):\/\/(.+?)\/federation\/ws$/)
                            return m ? m[2] : editPeerForm.remote_url.replace(/^wss?:\/\//, '')
                          })()}
                          onChange={e => {
                            const proto = (editPeerForm.remote_url.match(/^(wss?):\/\//)?.[1]) || 'wss'
                            const host = e.target.value.trim().replace(/\/$/, '')
                            setEditPeerForm({ ...editPeerForm, remote_url: host ? `${proto}://${host}/federation/ws` : '' })
                          }}
                          className="flex-1 px-2 py-1 text-xs bg-surface border border-border border-x-0 text-textPrimary font-mono"
                          placeholder="copree.example.com:5228"
                        />
                        <span className="inline-flex items-center px-1.5 text-3xs text-textMuted bg-surface border border-border rounded-r shrink-0 font-mono">
                          /federation/ws
                        </span>
                      </div>
                    </div>
                    <div className="md:col-span-2">
                      <label className="text-3xs text-textMuted">{t('admin.secretNoChange')}</label>
                      <input
                        type="password"
                        value={editPeerForm.shared_secret}
                        onChange={e => setEditPeerForm({ ...editPeerForm, shared_secret: e.target.value })}
                        className="w-full mt-0.5 px-2 py-1 text-xs bg-surface border border-border rounded text-textPrimary"
                        placeholder={t('admin.newSecretPlaceholder')}
                      />
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={handleSavePeer}
                      className="btn btn-xs btn-primary"
                    >
                      {t('common.save')}
                    </button>
                    <button
                      onClick={() => setEditingPeerId(null)}
                      className="px-3 py-1 text-xs bg-canvas border border-border text-textSecondary rounded hover:bg-border/20 transition-colors"
                    >
                      {t('common.cancel')}
                    </button>
                  </div>
                </div>
              )}
              {/* 轮换 URL 表单 */}
              {rotatingPeerId === peer.id && (
                <div className="p-3 bg-canvas rounded-control border border-primary-500/20 space-y-2">
                  <div className="flex items-center gap-2 text-xs text-primary-400">
                    <RefreshCw size={12} className="animate-spin" />
                    <span>{t('admin.rotateHint')}</span>
                  </div>
                  <div className="flex gap-2">
                    <select
                      value={rotateUrl.startsWith('wss://') ? 'wss://' : 'ws://'}
                      onChange={e => setRotateUrl(e.target.value + rotateUrl.replace(/^(wss?):\/\//, ''))}
                      className="px-2 py-1 text-xs bg-surface border border-border rounded text-textPrimary shrink-0"
                    >
                      <option value="wss://">wss://</option>
                      <option value="ws://">ws://</option>
                    </select>
                    <input
                      value={rotateUrl.replace(/^(wss?):\/\//, '').replace(/\/federation\/ws$/, '')}
                      onChange={e => setRotateUrl((rotateUrl.startsWith('wss://') ? 'wss://' : 'ws://') + e.target.value + '/federation/ws')}
                      className="flex-1 px-2 py-1 text-xs bg-surface border border-border rounded text-textPrimary font-mono"
                      placeholder="host:port"
                    />
                    <span className="flex items-center text-xs text-textMuted shrink-0">/federation/ws</span>
                  </div>
                  {rotateError && (
                    <p className="text-xs text-rose-400">{rotateError}</p>
                  )}
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleRotateUrl(peer.id)}
                      disabled={rotateLoading}
                      className="px-3 py-1 text-xs bg-primary-600 hover:bg-primary-500 disabled:opacity-50 text-white rounded transition-colors"
                    >
                      {rotateLoading ? t('admin.rotating') : t('admin.initiateRotate')}
                    </button>
                    <button
                      onClick={() => { setRotatingPeerId(null); setRotateError('') }}
                      className="px-3 py-1 text-xs bg-canvas border border-border text-textSecondary rounded hover:bg-border/20 transition-colors"
                    >
                      {t('common.cancel')}
                    </button>
                  </div>
                </div>
              )}
              {/* 轮换次数 */}
              {peer.url_rotation_count > 0 && (
                <p className="text-3xs text-textMuted ml-11">
                  {t('admin.urlRotated')} <span className="text-primary-400">{peer.url_rotation_count}</span>
                  {peer.url_rotated_at && (
                    <span> · {t('admin.lastRotated')} {new Date(peer.url_rotated_at).toLocaleString()}</span>
                  )}
                </p>
              )}
              </Fragment>
            ))}
          </div>
        )}
      </section>

      {/* 联邦实体 */}
      <FederatedEntitiesSection />
      {/* GitHub 注册说明（可选） */}
      <section className="bg-surface border border-border rounded-card p-5">
        <h2 className="text-sm font-semibold text-textPrimary flex items-center gap-2 mb-4">
          <Globe size={16} /> {t('admin.federationDiscovery')}
        </h2>
        <p className="text-sm text-textMuted">{t('admin.federationDiscoveryDesc')}</p>
        <p className="text-xs text-textMuted mt-2">
          {t('admin.federationDiscoveryHint')}
        </p>
      </section>
    </div>
  )
}

// ── 联邦实体列表（v0.2.0 新增） ──

function FederatedEntitiesSection() {
  const t = useT()
  const [entities, setEntities] = useState<FederatedEntity[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    loadEntities()
  }, [])

  const loadEntities = async () => {
    setLoading(true)
    try {
      const data = await api.get<FederatedEntity[]>('/admin/federation/entities')
      setEntities(data)
    } catch (_) {
      // silent
    } finally {
      setLoading(false)
    }
  }

  const toggleEntity = async (entity: FederatedEntity) => {
    try {
      await api.put(`/admin/federation/entities/${entity.id}`, {
        is_enabled: !entity.is_enabled,
      })
      await loadEntities()
    } catch (_) {}
  }

  const deleteEntity = async (entity: FederatedEntity) => {
    if (!confirm(t('admin.confirmRemoveEntity'))) return
    try {
      await api.delete(`/admin/federation/entities/${entity.id}`)
      await loadEntities()
    } catch (_) {}
  }

  const typeIcon = (entityType: string) => {
    switch (entityType) {
      case 'group': return <Globe size={14} />
      case 'dm': return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
      case 'user': return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
      case 'agent': return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="9" y1="9" x2="9" y2="9.01"/><line x1="15" y1="9" x2="15" y2="9.01"/></svg>
      default: return <Globe size={14} />
    }
  }

  const typeLabel = (entityType: string) => {
    const map: Record<string, string> = { group: t('common.group'), dm: 'DM', user: t('common.user'), agent: 'AI' }
    return map[entityType] || entityType
  }

  if (loading) return <div className="bg-surface border border-border rounded-card p-5 text-sm text-textMuted">{t('common.loading')}</div>

  return (
    <section className="bg-surface border border-border rounded-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-textPrimary flex items-center gap-2">
          <Globe size={16} /> {t('admin.federationEntities').replace('{count}', String(entities.length))}
        </h2>
        <p className="text-xs text-textMuted">{t('admin.federationEntitiesHint')}</p>
      </div>

      {entities.length === 0 ? (
        <p className="text-sm text-textMuted py-4 text-center">{t('admin.noFederatedEntities')}</p>
      ) : (
        <div className="space-y-2">
          {entities.map(entity => (
            <div key={entity.id} className="flex items-center justify-between p-3 bg-canvas rounded-control border border-border">
              <div className="flex items-center gap-3 min-w-0">
                <span className="text-textMuted shrink-0">{typeIcon(entity.entity_type)}</span>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="text-sm text-textPrimary font-mono truncate">{entity.federated_id}</p>
                    <span className="text-3xs px-1.5 py-0.5 rounded bg-primary-500/10 text-primary-400">
                      {typeLabel(entity.entity_type)}
                    </span>
                  </div>
                  <p className="text-xs text-textMuted">
                    {entity.peer_display_name} · {entity.display_name || entity.local_ref_id}
                    {entity.direction === 'bidirectional' && <span className="ml-1 text-mint-400">⇄</span>}
                    {entity.direction === 'incoming' && <span className="ml-1 text-accent-400">←</span>}
                    {entity.direction === 'outgoing' && <span className="ml-1 text-blue-400">→</span>}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => toggleEntity(entity)}
                  className={`text-xs px-2 py-1 rounded transition-colors ${
                    entity.is_enabled
                      ? 'bg-mint-500/10 text-mint-400 hover:bg-rose-500/10 hover:text-rose-400'
                      : 'bg-rose-500/10 text-rose-400 hover:bg-mint-500/10 hover:text-mint-400'
                  }`}
                  title={entity.is_enabled ? t('admin.disable') : t('admin.enable')}
                >
                  {entity.is_enabled ? t('admin.enabled') : t('admin.disabled')}
                </button>
                <button
                  onClick={() => deleteEntity(entity)}
                  className="p-1.5 text-textMuted hover:text-rose-400 hover:bg-rose-500/10 rounded-control transition-colors"
                  title={t('common.delete')}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
