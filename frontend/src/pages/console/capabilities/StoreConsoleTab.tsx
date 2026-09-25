import { useState } from 'react'
import { Boxes, Package, Settings } from 'lucide-react'
import { useT } from '../../../i18n/I18nContext'
import PluginMarketSection from './PluginMarketSection'
import PluginManager from './PluginManager'
import MarketGithubTab from '../../../components/MarketGithubTab'

/**
 * 控制台 · 商城 — 插件商城 / 已安装 / 商城源
 *
 * 为什么整块搬进控制台：世界商城是给普通用户找世界的地方（仍在 /market 对外开放），
 * 而插件的安装、卸载、同步源配置都是管理动作，不该出现在普通用户的页面上。
 * 浏览用卡片、管理用表格的分工不变，只是换了门。
 */
type SubTab = 'plugins' | 'installed' | 'source'

export default function StoreConsoleTab() {
  const t = useT()
  const [subTab, setSubTab] = useState<SubTab>('plugins')

  const subTabs: { key: SubTab; label: string; icon: React.ElementType }[] = [
    { key: 'plugins', label: t('tool:store.sectionPlugins'), icon: Boxes },
    { key: 'installed', label: t('tool:store.sectionInstalled'), icon: Package },
    { key: 'source', label: t('tool:store.sectionSource'), icon: Settings },
  ]

  return (
    <div className="space-y-4">
      <div className="flex gap-1 bg-elevated rounded-control p-1 w-fit">
        {subTabs.map(st => (
          <button
            key={st.key}
            onClick={() => setSubTab(st.key)}
            className={st.key === subTab
              ? 'flex items-center gap-1.5 px-3 py-1.5 rounded-control text-sm bg-surface text-textPrimary shadow-sm'
              : 'flex items-center gap-1.5 px-3 py-1.5 rounded-control text-sm text-textSecondary hover:text-textPrimary'}
          >
            <st.icon size={15} />
            {st.label}
          </button>
        ))}
      </div>

      {subTab === 'plugins' ? <PluginMarketSection />
        : subTab === 'installed' ? <PluginManager />
        : <MarketGithubTab />}
    </div>
  )
}
