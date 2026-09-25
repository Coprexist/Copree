// 用户管理：列表 + 新建 / 重置密码 / CSV 批量导入

import { useState, useEffect, useRef } from 'react'
import { Modal } from '../../../components/ui'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'

function ResetPasswordDialog({ target, onDone, onClose }: {
  target: { id: number; username: string }
  onDone: () => void
  onClose: () => void
}) {
  const t = useT()
  const inputRef = useRef<HTMLInputElement>(null)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { inputRef.current?.focus() }, [])

  const reset = async () => {
    if (password.length < 6 || busy) return
    setBusy(true)
    try {
      await api.put(`/admin/users/${target.id}/reset-password`, { new_password: password })
      onDone()
    } catch {}
    setBusy(false)
    onClose()
  }

  return (
    <Modal open title={t('admin.resetPasswordTitle').replace('{username}', target.username)} onClose={onClose}>
      <input
        ref={inputRef}
        type="password"
        value={password}
        onChange={e => setPassword(e.target.value)}
        placeholder={t('admin.resetPasswordPlaceholder')}
        className="w-full px-3 py-2 text-sm border border-border bg-canvas rounded-control focus:outline-none focus:ring-1 focus:ring-primary-500 mb-3"
        onKeyDown={e => e.key === 'Enter' && reset()}
      />
      <div className="flex justify-end gap-2">
        <button onClick={onClose} className="btn btn-xs btn-outline">
          {t('common.cancel')}
        </button>
        <button
          disabled={password.length < 6 || busy}
          onClick={reset}
          className="btn btn-xs btn-primary"
        >
          {busy ? t('common.loading') : t('admin.resetPassword')}
        </button>
      </div>
    </Modal>
  )
}

function CreateUserDialog({ onDone, onClose }: { onDone: () => void; onClose: () => void }) {
  const t = useT()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const usernameOk = username.length >= 2
  const passwordOk = password.length >= 6

  const submit = async () => {
    if (!usernameOk || !passwordOk || busy) return
    setBusy(true)
    setError('')
    try {
      await api.post('/admin/users', { username, password, email: email || undefined })
      onDone()
      onClose()
    } catch (e: any) {
      setError(e?.detail || e?.message || t('common.error'))
    }
    setBusy(false)
  }

  return (
    <Modal open title={t('admin.createUserTitle')} onClose={onClose}>
      {error && <p className="text-xs text-rose-400 mb-2">{error}</p>}
      <input value={username} onChange={e => setUsername(e.target.value)} placeholder={t('admin.createUserUsername')}
        className="w-full px-3 py-2 text-sm border border-border bg-canvas rounded-control mb-2 focus:outline-none focus:ring-1 focus:ring-primary-500" />
      <p className="text-2xs text-textMuted -mt-1.5 mb-2">{username.length > 0 && !usernameOk ? t('admin.createUserUsernameHint') : '\u00a0'}</p>
      <input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder={t('admin.createUserPassword')}
        className="w-full px-3 py-2 text-sm border border-border bg-canvas rounded-control mb-2 focus:outline-none focus:ring-1 focus:ring-primary-500" />
      <p className="text-2xs text-textMuted -mt-1.5 mb-2">{password.length > 0 && !passwordOk ? t('admin.createUserPasswordHint') : '\u00a0'}</p>
      <input value={email} onChange={e => setEmail(e.target.value)} placeholder={t('admin.createUserEmail')}
        className="w-full px-3 py-2 text-sm border border-border bg-canvas rounded-control mb-3 focus:outline-none focus:ring-1 focus:ring-primary-500" />
      <div className="flex justify-end gap-2">
        <button onClick={onClose} className="btn btn-xs btn-outline">{t('common.cancel')}</button>
        <button
          disabled={!usernameOk || !passwordOk || busy}
          onClick={submit}
          className="btn btn-xs btn-primary"
        >{busy ? t('common.loading') : t('admin.createUser')}</button>
      </div>
    </Modal>
  )
}

function ImportCsvDialog({ onDone, onClose }: { onDone: (count: number) => void; onClose: () => void }) {
  const t = useT()
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState<any>(null)

  const submit = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file || busy) return
    setBusy(true)
    setResults(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const data = await api.post('/admin/users/import-csv', form)
      setResults(data)
      if (data.created > 0) onDone(data.created)
    } catch (e: any) {
      setResults({ error: e?.detail || e?.message || t('common.error') })
    }
    setBusy(false)
  }

  return (
    <Modal open title={results ? undefined : t('admin.importCsvTitle')} onClose={onClose}>
      {results ? (
        <div className="text-sm space-y-1">
          {results.error ? (
            <p className="text-rose-400">{results.error}</p>
          ) : (
            <>
              <p className="text-mint-400">{results.message}</p>
              <div className="max-h-40 overflow-y-auto text-xs text-textMuted space-y-0.5 mt-2">
                {results.details?.filter((r: any) => r.status !== 'ok').map((r: any, i: number) => (
                  <p key={i} className={r.status === 'error' ? 'text-rose-400' : ''}>
                    {t('admin.importCsvRowResult').replace('{row}', String(r.row))}: {r.status === 'error' ? r.reason : r.status}
                  </p>
                ))}
              </div>
            </>
          )}
          <button onClick={onClose} className="btn btn-xs btn-primary mt-2">{t('common.close')}</button>
        </div>
      ) : (
        <>
          <p className="text-xs text-textMuted mb-3">{t('admin.importCsvHint')}</p>
          <input ref={fileRef} type="file" accept=".csv"
            className="w-full text-sm file:mr-3 file:py-1.5 file:px-3 file:rounded-control file:border-0 file:text-xs file:font-medium file:bg-primary-500 file:text-white hover:file:bg-primary-400 mb-3" />
          <div className="flex justify-end gap-2">
            <button onClick={onClose} className="btn btn-xs btn-outline">{t('common.cancel')}</button>
            <button disabled={busy} onClick={submit}
              className="btn btn-xs btn-primary"
            >{busy ? t('common.loading') : t('admin.importCsv')}</button>
          </div>
        </>
      )}
    </Modal>
  )
}

export default function UsersTab() {
  const t = useT()
  const [data, setData] = useState<any>(null)
  const [page, setPage] = useState(1)
  const [resetTarget, setResetTarget] = useState<any>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [showImport, setShowImport] = useState(false)

  useEffect(() => {
    api.get(`/admin/users?page=${page}`).then(setData).catch(console.error)
  }, [page])

  if (!data) return <p className="text-textMuted">{t('common.loading')}</p>

  return (
    <div>
      {resetTarget && (
        <ResetPasswordDialog
          target={resetTarget}
          onClose={() => setResetTarget(null)}
          onDone={() => setPage(page)}
        />
      )}
      {showCreate && (
        <CreateUserDialog
          onClose={() => setShowCreate(false)}
          onDone={() => setPage(page)}
        />
      )}
      {showImport && (
        <ImportCsvDialog
          onClose={() => setShowImport(false)}
          onDone={() => setPage(page)}
        />
      )}
      <div className="flex items-center gap-2 mb-3">
        <button onClick={() => setShowCreate(true)}
          className="text-xs px-3 py-1.5 bg-primary-500 text-white rounded-control hover:bg-primary-600 transition-colors">
          + {t('admin.createUser')}
        </button>
        <button onClick={() => setShowImport(true)}
          className="text-xs px-3 py-1.5 border border-border rounded-control text-textSecondary hover:bg-elevated transition-colors">
          {t('admin.importCsv')}
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-textPrimary">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.usersColId')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.usersColUsername')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.usersColRole')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.usersColQuota')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.usersColStatus')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.usersColAction')}</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((u: any) => (
              <tr key={u.id} className="border-b border-border/50">
                <td className="py-2 px-3">{u.id}</td>
                <td className="py-2 px-3 font-medium">{u.username}</td>
                <td className="py-2 px-3">
                  <span className={`text-xs px-2 py-0.5 rounded-full ${
                    u.role === 'admin' ? 'bg-primary-500/10 text-primary-600 dark:text-primary-300' : ''
                  }`}>
                    {u.role}
                  </span>
                </td>
                <td className="py-2 px-3">{u.ai_quota}</td>
                <td className="py-2 px-3">
                  <span className={`text-xs ${u.is_active ? 'text-mint-400' : 'text-rose-400'}`}>
                    {u.is_active ? t('admin.active') : t('admin.banned')}
                  </span>
                </td>
                <td className="py-2 px-3">
                  <div className="flex items-center gap-2">
                    <button
                      onClick={async () => {
                        await api.post(`/admin/users/${u.id}/ban`, {})
                        setPage(page) // 触发刷新
                      }}
                      className="text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300"
                    >
                      {u.is_active ? t('admin.ban') : t('admin.unban')}
                    </button>
                    {u.role !== 'admin' ? (
                      <button
                        onClick={async () => {
                          if (!confirm(t('admin.confirmPromote').replace('{username}', u.username))) return
                          await api.put(`/admin/users/${u.id}/role`, { role: 'admin' })
                          setPage(page)
                        }}
                        className="text-xs text-mint-400 hover:text-mint-500 dark:hover:text-mint-300"
                      >
                        {t('admin.promote')}
                      </button>
                    ) : (
                      <button
                        onClick={async () => {
                          if (!confirm(t('admin.confirmDemote').replace('{username}', u.username))) return
                          await api.put(`/admin/users/${u.id}/role`, { role: 'user' })
                          setPage(page)
                        }}
                        className="text-xs text-rose-400 hover:text-rose-500 dark:hover:text-rose-300"
                      >
                        {t('admin.demote')}
                      </button>
                    )}
                    <button
                      onClick={() => setResetTarget(u)}
                      className="text-xs text-accent-400 hover:text-accent-500 dark:hover:text-accent-300"
                    >
                      {t('admin.resetPassword')}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex gap-2 mt-4">
        <button
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          disabled={page <= 1}
          className="text-sm px-3 py-1 border border-border bg-canvas rounded hover:bg-elevated disabled:opacity-40 text-textSecondary"
        >
          {t('common.prevPage')}
        </button>
        <button
          onClick={() => setPage((p) => p + 1)}
          disabled={data.items.length < data.page_size}
          className="text-sm px-3 py-1 border border-border bg-canvas rounded hover:bg-elevated disabled:opacity-40 text-textSecondary"
        >
          {t('common.nextPage')}
        </button>
      </div>
    </div>
  )
}
