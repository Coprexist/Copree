// 设置总览：所有配置域一屏看全，每张卡右边给出「详细设置 →」
//
// 为什么总览页不放表单本体：同一个表单在两处渲染，保存逻辑、校验、脏状态都要维护两份，
// 迟早分叉。总览只负责「有哪些设置、现在是什么值、点哪儿改」，修改一律去各自的详情页。

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'
import { Button, Card } from '../../../components/ui'

type CardDef = {
  key: string
  titleKey: string
  hintKey: string
  /** 摘要行：只在能便宜取到值的域里给 */
  summary?: Array<{ labelKey: string; value: (cfg: any) => string }>
  group?: 'runtime' | 'embedding'
}

const CARDS: CardDef[] = [
  { key: 'system', titleKey: 'admin:system', hintKey: 'admin:systemDesc' },
  {
    key: 'runtime', titleKey: 'admin:runtimeSettings', hintKey: 'admin:runtimeSettingsHint', group: 'runtime',
    summary: [
      { labelKey: 'adminConfig:displayTimezone', value: c => String(c.display_timezone ?? '-') },
      { labelKey: 'adminConfig:uploadMaxSize', value: c => String(c.upload_max_size_mb ?? '-') },
      { labelKey: 'adminConfig:fileDailyMbHuman', value: c => String(c.file_daily_mb_human ?? '-') },
      { labelKey: 'adminConfig:fileDailyMbAi', value: c => String(c.file_daily_mb_ai ?? '-') },
    ],
  },
  {
    key: 'embedding', titleKey: 'admin:embeddingSettings', hintKey: 'admin:embeddingSettingsHint', group: 'embedding',
    summary: [
      { labelKey: 'adminConfig:embeddingBackend', value: c => String(c.embedding_backend ?? '-') },
      { labelKey: 'adminConfig:embeddingModel', value: c => String(c.embedding_model ?? '-') },
      { labelKey: 'adminConfig:embeddingDimension', value: c => String(c.embedding_dimension ?? '-') },
    ],
  },
  { key: 'auth', titleKey: 'admin:auth', hintKey: 'admin:authDesc' },
  { key: 'apipool', titleKey: 'admin:apiKeyPool', hintKey: 'admin:apiKeyPoolDesc' },
  { key: 'opencli', titleKey: 'admin:opencli', hintKey: 'admin:opencliDesc' },
  { key: 'federation', titleKey: 'admin:federation', hintKey: 'admin:federationDesc' },
  { key: 'store', titleKey: 'admin:store', hintKey: 'admin:storeDesc' },
  { key: 'backup', titleKey: 'admin:backup', hintKey: 'admin:backupDesc' },
  { key: 'cleanup', titleKey: 'admin:cleanup', hintKey: 'admin:cleanupDesc' },
]

export default function SettingsOverviewTab() {
  const t = useT()
  const navigate = useNavigate()
  const [runtime, setRuntime] = useState<any>(null)
  const [embedding, setEmbedding] = useState<any>(null)

  useEffect(() => {
    api.get('/admin/configs/runtime').then(setRuntime).catch(() => {})
    api.get('/admin/configs/embedding').then(setEmbedding).catch(() => {})
  }, [])

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {CARDS.map(card => {
        const cfg = card.group === 'runtime' ? runtime : card.group === 'embedding' ? embedding : null
        return (
          <Card
            key={card.key}
            title={t(card.titleKey)}
            hint={t(card.hintKey)}
            action={
              <Button variant="outline" size="xs" onClick={() => navigate({ search: 'tab=' + card.key })}>
                {t('admin:settingsOpenDetail')}
              </Button>
            }
          >
            {card.summary && cfg ? (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
                {card.summary.map(row => (
                  <div key={row.labelKey} className="flex items-baseline justify-between gap-2 min-w-0">
                    <dt className="text-textMuted truncate">{t(row.labelKey)}</dt>
                    <dd className="text-textPrimary tabular-nums truncate">{row.value(cfg)}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-xs text-textMuted">{t('admin:settingsOpenHint')}</p>
            )}
          </Card>
        )
      })}
    </div>
  )
}
