// 模型与嵌入：Embedding 提供方与向量维度——以前埋在「系统设置」页底部

import ConfigGroupCard from '../../../components/ConfigGroupCard'
import { useT } from '../../../i18n/I18nContext'

export default function EmbeddingSettingsTab() {
  const t = useT()
  return (
    <div className="space-y-4">
      <p className="text-sm text-textMuted">{t('admin:embeddingSettingsHint')}</p>
      <ConfigGroupCard groupKey="embedding" />
    </div>
  )
}
