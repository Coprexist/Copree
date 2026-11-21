import { useEffect, useState } from 'react'
import { Loader2, Check, X, RotateCcw, Settings2, Brain, Plug } from 'lucide-react'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { Button, Card, Input, Select, SecretInput} from './ui'

/**
 * 通用配置组卡片（管理员图形化修改任意配置组）
 *
 * 从后端 schema（GET /admin/configs）自动渲染表单：
 * - 每个字段按 type 渲染控件（select/number/float/text/secret）
 * - label/hint 用 i18n key（后端下发 label_key/hint_key，前端 t() 翻译，三语友好）
 * - 保存 PUT /admin/configs/{group}（热更新，无需重启）
 * - 恢复 DELETE /admin/configs/{group}（回到环境变量配置）
 *
 * 特别支持：embedding 组的「测试连接」按钮（POST /admin/embedding-config/test）
 */
export default function ConfigGroupCard({ groupKey }: { groupKey: string }) {
  const t = useT()
  const [group, setGroup] = useState<any>(null)
  const [values, setValues] = useState<Record<string, any>>({})
  const [source, setSource] = useState<Record<string, string>>({})
  const [secretSet, setSecretSet] = useState<Record<string, boolean>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [msg, setMsg] = useState('')
  const [msgOk, setMsgOk] = useState(false)
  // 测试连接结果：null=未测，'testing'=测试中，'ok'/'fail'=结果（文案纯文本，图标由前端渲染）
  const [testState, setTestState] = useState<null | 'testing' | 'ok' | 'fail'>(null)
  const [testMsg, setTestMsg] = useState('')

  // i18n 取文案：key 不存在时 fallback 回原值
  const tr = (key?: string, vars?: Record<string, string | number>) =>
    key ? t(`adminConfig:${key}`, vars) : ''

  const load = async () => {
    try {
      // schema + 当前生效值
      const [schemaRes, cfg] = await Promise.all([
        api.get<any>('/admin/configs'),
        api.get<any>(`/admin/configs/${groupKey}`),
      ])
      const g = (schemaRes.groups || []).find((x: any) => x.key === groupKey)
      if (!g) return
      setGroup(g)

      const v: Record<string, any> = {}
      const s: Record<string, string> = {}
      const ss: Record<string, boolean> = {}
      for (const [key, field] of Object.entries(g.fields as Record<string, any>)) {
        if (field.type === 'secret') {
          ss[key] = !!cfg[`${key}_set`]
          v[key] = ''
        } else {
          v[key] = cfg[key] ?? ''
        }
        s[key] = cfg.source?.[key] || 'env'
      }
      setValues(v)
      setSource(s)
      setSecretSet(ss)
    } catch { /* */ }
  }

  useEffect(() => { load() }, [groupKey])

  const handleSave = async () => {
    setSaving(true)
    setMsg('')
    try {
      const payload: Record<string, any> = {}
      for (const key of Object.keys(group.fields)) {
        const field = group.fields[key]
        if (field.type === 'secret') {
          if (values[key]?.trim()) payload[key] = values[key].trim()  // 留空 = 不改
        } else if (field.type === 'number') {
          const n = Number(values[key])
          if (values[key] !== '' && !isNaN(n)) payload[key] = n
        } else if (field.type === 'float') {
          const n = Number(values[key])
          if (values[key] !== '' && !isNaN(n)) payload[key] = n
        } else {
          if (values[key] !== '') payload[key] = values[key]
        }
      }
      await api.put(`/admin/configs/${groupKey}`, payload)
      setSaved(true)
      await load()
      setTimeout(() => setSaved(false), 3000)
    } catch (err: any) {
      setMsg(err?.detail || tr('saveFailed'))
      setMsgOk(false)
    }
    setSaving(false)
  }

  const handleReset = async () => {
    if (!confirm(tr('resetConfirm', { label: tr(group?.label_key) }))) return
    setSaving(true)
    try {
      await api.delete(`/admin/configs/${groupKey}`)
      await load()
      setMsg(tr('resetDone'))
      setMsgOk(true)
    } catch {
      setMsg(tr('resetFailed'))
      setMsgOk(false)
    }
    setSaving(false)
  }

  const handleTest = async () => {
    setTestState('testing')
    setTestMsg('')
    try {
      const res = await api.post<any>('/admin/embedding-config/test', {})
      setTestState('ok')
      setTestMsg(tr('testOk', { dim: res.dimension }))
    } catch (err: any) {
      setTestState('fail')
      setTestMsg(tr('testFail', { msg: err?.detail || 'error' }))
    }
  }

  if (!group) return null

  const Icon = groupKey === 'embedding' ? Brain : Settings2
  const isSecret = (key: string) => group.fields[key]?.type === 'secret'
  const secretPlaceholder = tr('secretPlaceholder')

  return (
    <Card
      title={tr(group.label_key)}
      icon={<Icon size={16} className="text-accent-400" />}
      hint={group.hint_key ? tr(group.hint_key) : undefined}
    >
      {/* 字段表单（按 schema 自动渲染）：标签 / 说明 / 来源标记都交给 Input / Select 自己排 */}
      {Object.entries(group.fields as Record<string, any>).map(([key, field]) => {
        const label = (
          <span className="flex flex-wrap items-center gap-x-2">
            <span>{tr(field.label_key)}</span>
            {isSecret(key) && (
              <span className="text-xs font-normal text-textMuted">
                {secretSet[key] ? tr('secretSet') : tr('secretNotSet')}
              </span>
            )}
            <span className="text-xs font-normal text-textMuted">
              {source[key] === 'db' ? tr('sourceDb') : tr('sourceEnv')}
            </span>
          </span>
        )
        const hint = field.hint_key ? tr(field.hint_key) : undefined

        if (field.type === 'select') {
          return (
            <Select
              key={key}
              fieldSize="sm"
              label={label}
              hint={hint}
              value={values[key] ?? ''}
              onChange={(e) => setValues({ ...values, [key]: e.target.value })}
              options={(field.options || []).map((opt: any) => ({
                value: opt.value,
                label: tr(opt.label_key),
              }))}
            />
          )
        }

        const numeric = field.type === 'number' || field.type === 'float'
        // 密钥字段一律走 SecretInput：不用 type=password，免得浏览器当成账号密码自动填充
        if (isSecret(key)) {
          return (
            <SecretInput
              key={key}
              fieldSize="sm"
              label={label}
              hint={hint}
              value={values[key] ?? ''}
              onChange={(e) => setValues({ ...values, [key]: e.target.value })}
              placeholder={secretSet[key] ? secretPlaceholder : ''}
              wrapperClassName="w-full"
            />
          )
        }
        return (
          <Input
            key={key}
            fieldSize="sm"
            label={label}
            hint={hint}
            type={numeric ? 'number' : 'text'}
            step={numeric ? field.step : undefined}
            min={numeric ? field.min : undefined}
            max={numeric ? field.max : undefined}
            value={values[key] ?? ''}
            onChange={(e) => setValues({ ...values, [key]: e.target.value })}
            placeholder={isSecret(key) && secretSet[key] ? secretPlaceholder : ''}
            className="w-full"
          />
        )
      })}

      {msg && (
        <p className={`text-sm flex items-center gap-1.5 ${msgOk ? 'text-mint-400' : 'text-rose-400'}`}>
          {msgOk ? <Check size={14} /> : <X size={14} />}
          {msg}
        </p>
      )}
      {testState && (
        <p className={`text-sm flex items-center gap-1.5 ${testState === 'ok' ? 'text-mint-400' : testState === 'fail' ? 'text-rose-400' : 'text-textSecondary'}`}>
          {testState === 'ok' && <Check size={14} />}
          {testState === 'fail' && <X size={14} />}
          {testState === 'testing' && <Loader2 size={14} className="animate-spin" />}
          {testState === 'testing' ? tr('testing') : testMsg}
        </p>
      )}

      {/* 操作按钮（统一 Button 组件） */}
      <div className="flex items-center gap-2 pt-1">
        {groupKey === 'embedding' && (
          <Button variant="secondary" size="sm" onClick={handleTest} disabled={saving} icon={<Plug size={14} />}>
            {tr('test')}
          </Button>
        )}
        <Button
          variant="accent"
          size="sm"
          onClick={handleSave}
          disabled={saving}
          loading={saving}
          className="flex-1"
        >
          {saved ? tr('saved') : tr('save')}
        </Button>
        <Button variant="secondary" size="sm" onClick={handleReset} disabled={saving} title={tr('reset')}>
          <RotateCcw size={16} />
        </Button>
      </div>
    </Card>
  )
}
