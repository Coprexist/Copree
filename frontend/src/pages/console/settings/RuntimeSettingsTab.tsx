// 运行时参数：检索权重、时区、上传上限、文件额度——以前埋在「系统设置」页底部

import ConfigGroupCard from '../../../components/ConfigGroupCard'
import { useT } from '../../../i18n/I18nContext'

export default function RuntimeSettingsTab() {
  const t = useT()
  return (
    <div className="space-y-4">
      <p className="text-sm text-textMuted">{t('admin:runtimeSettingsHint')}</p>
      <ConfigGroupCard groupKey="runtime" />
    </div>
  )
}
