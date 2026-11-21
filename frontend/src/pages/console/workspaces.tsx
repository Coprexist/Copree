import type { ElementType } from 'react'
import type { Width } from '../../components/ui/PageShell'
import {
  Activity, BarChart3, BellRing, Bot, Database, Eraser, FileText, Gauge, Globe, Key, Layers,
  LayoutDashboard, LayoutGrid, MessageCircle, Plug, ScrollText, Settings, Shield, Store, Terminal, Ticket, Users,
} from 'lucide-react'
import OverviewTab from './general/OverviewTab'
import SystemSettingsTab from './general/SystemSettingsTab'
import AuthSettingsTab from './general/AuthSettingsTab'
import UsersTab from './people/UsersTab'
import AgentsTab from './people/AgentsTab'
import GroupsTab from './people/GroupsTab'
import CodesTab from './people/CodesTab'
import CapabilitiesTab from './capabilities/CapabilitiesTab'
import SystemPromptTab from './capabilities/SystemPromptTab'
import ApiKeyPoolTab from './capabilities/ApiKeyPoolTab'
import OpenCliTab from './capabilities/OpenCliTab'
import FederationTab from './capabilities/FederationTab'
import StoreConsoleTab from './capabilities/StoreConsoleTab'
import OpsOverviewTab from './monitoring/OpsOverviewTab'
import SystemMetricsTab from './monitoring/SystemMetricsTab'
import UsageDashboardTab from './monitoring/UsageDashboardTab'
import ConversationLogTab from './monitoring/ConversationLogTab'
import LogsTab from './monitoring/LogsTab'
import FileAlertsTab from './monitoring/FileAlertsTab'
import SettingsOverviewTab from './settings/SettingsOverviewTab'
import RuntimeSettingsTab from './settings/RuntimeSettingsTab'
import EmbeddingSettingsTab from './settings/EmbeddingSettingsTab'
import BackupTab from './settings/BackupTab'
import CleanupTab from './settings/CleanupTab'

/**
 * 控制台的单一事实来源：工作区 → 页签 → 文案/图标/组件，全在这里。
 *
 * 两级导航是刻意的：上面一排工作区（大方向），左边一列是当前工作区里的具体页面。
 * 好处是左边那列通常只有 2~6 项（一眼扫完），加新功能时只往对应工作区里塞一行，
 * （唯一例外是「设置」：配置域天生有十来个，一域一页比塞进长页面更好找）
 * 不用再去挤一条 20 项的导航栏；代码也按工作区分目录（pages/console/<工作区>/），
 * 一个工作区改动不会牵动另一个。
 *
 * 手机端不参与这套：仍然是"分组列表 → 详情"两步，保持原来那套交互。
 */
export type ConsoleWorkspaceKey = 'general' | 'people' | 'capabilities' | 'monitoring' | 'settings'

export interface ConsoleItem {
  key: string
  /** 侧栏/列表标签；只存 i18n key，避免组件里散落硬编码文案 */
  labelKey: string
  /** 手机端列表里的一行说明 */
  descKey: string
  icon: ElementType
  Component: ElementType
  /**
   * 内容宽度档位（与 PageShell 同一套）。
   * 表格/网格/日志这类靠宽度吃饭的用 full；表单与单列内容用窄档并居中——
   * 不声明时按 wide 兜底，免得新页签在大屏上一条线贴到最左边。
   *
   * 这里只给**默认**宽度：使用者拖内容列两侧的宽度条就能改（双击回默认）。
   * 所以页签内部别再写 max-w——自己收一道窄，拖条看着就像失灵了。
   */
  width?: Width
}

export interface ConsoleWorkspace {
  key: ConsoleWorkspaceKey
  labelKey: string
  icon: ElementType
  items: ConsoleItem[]
}

export const CONSOLE_WORKSPACES: ConsoleWorkspace[] = [
  {
    key: 'general',
    labelKey: 'admin:groupGeneral',
    icon: Gauge,
    items: [
      { key: 'overview', labelKey: 'admin:overview', descKey: 'admin:overviewDesc', icon: LayoutDashboard, Component: OverviewTab },
      // 运维总览是给不看日志的人看的门面，放通用里第一眼就能看到
      { key: 'ops', labelKey: 'admin:opsTab', descKey: 'admin:opsTabDesc', icon: Activity, Component: OpsOverviewTab },
    ],
  },
  {
    key: 'people',
    labelKey: 'admin:groupPeople',
    icon: Users,
    items: [
      { key: 'users', labelKey: 'admin:users', descKey: 'admin:usersDesc', icon: Users, Component: UsersTab },
      { key: 'agents', labelKey: 'admin:agents', descKey: 'admin:agentsDesc', icon: Bot, Component: AgentsTab },
      { key: 'groups', labelKey: 'admin:groups', descKey: 'admin:groupsDesc', icon: MessageCircle, Component: GroupsTab },
      { key: 'codes', labelKey: 'admin:codes', descKey: 'admin:codesDesc', icon: Ticket, Component: CodesTab },
    ],
  },
  {
    key: 'capabilities',
    labelKey: 'admin:groupCapabilities',
    icon: Plug,
    items: [
      { key: 'capabilities', labelKey: 'admin:capabilities', descKey: 'admin:capabilitiesDesc', icon: Plug, Component: CapabilitiesTab },
      { key: 'prompt', labelKey: 'admin:prompt', descKey: 'admin:promptDesc', icon: Layers, Component: SystemPromptTab, width: 'content' },
    ],
  },
  {
    key: 'monitoring',
    labelKey: 'admin:groupMonitoring',
    icon: Activity,
    items: [
      { key: 'metrics', labelKey: 'admin:systemMetrics', descKey: 'admin:metricsDesc', icon: Activity, Component: SystemMetricsTab },
      { key: 'usage', labelKey: 'admin:usage', descKey: 'admin:usageDesc', icon: BarChart3, Component: UsageDashboardTab },
      { key: 'convlog', labelKey: 'admin:logs', descKey: 'admin:convlogDesc', icon: ScrollText, Component: ConversationLogTab, width: 'wider' },
      { key: 'alerts', labelKey: 'admin:fileAlerts', descKey: 'admin:fileAlertsDesc', icon: BellRing, Component: FileAlertsTab, width: 'full' },
      { key: 'logs', labelKey: 'admin:audit', descKey: 'admin:auditDesc', icon: FileText, Component: LogsTab },
    ],
  },
  {
    // 所有的配置类表单收在这一个分档里：总览页是索引（看全 + 跳详情），每个域自己一页详情。
    // 以前它们散在「通用/能力/数据」三个工作区，想改一件事得先回忆"那设置在哪个区"。
    key: 'settings',
    labelKey: 'admin:groupSettings',
    icon: Settings,
    items: [
      { key: 'settings', labelKey: 'admin:settingsOverview', descKey: 'admin:settingsOverviewDesc', icon: LayoutGrid, Component: SettingsOverviewTab, width: 'full' },
      { key: 'system', labelKey: 'admin:system', descKey: 'admin:systemDesc', icon: Settings, Component: SystemSettingsTab, width: 'full' },
      { key: 'runtime', labelKey: 'admin:runtimeSettings', descKey: 'admin:runtimeSettingsDesc', icon: Gauge, Component: RuntimeSettingsTab, width: 'full' },
      { key: 'embedding', labelKey: 'admin:embeddingSettings', descKey: 'admin:embeddingSettingsDesc', icon: Bot, Component: EmbeddingSettingsTab, width: 'full' },
      { key: 'auth', labelKey: 'admin:auth', descKey: 'admin:authDesc', icon: Shield, Component: AuthSettingsTab, width: 'full' },
      { key: 'apipool', labelKey: 'admin:apiKeyPool', descKey: 'admin:apiKeyPoolDesc', icon: Key, Component: ApiKeyPoolTab },
      { key: 'opencli', labelKey: 'admin:opencli', descKey: 'admin:opencliDesc', icon: Terminal, Component: OpenCliTab },
      { key: 'federation', labelKey: 'admin:federation', descKey: 'admin:federationDesc', icon: Globe, Component: FederationTab },
      { key: 'store', labelKey: 'admin:store', descKey: 'admin:storeDesc', icon: Store, Component: StoreConsoleTab },
      { key: 'backup', labelKey: 'admin:backup', descKey: 'admin:backupDesc', icon: Database, Component: BackupTab },
      { key: 'cleanup', labelKey: 'admin:cleanup', descKey: 'admin:cleanupDesc', icon: Eraser, Component: CleanupTab },
    ],
  },
]

/** URL 里的 ?tab= 可能来自旧书签或手改，这里统一收敛：不认识的值一律回落到工作台首页 */
export function findConsoleItem(key: string | null): ConsoleItem {
  for (const workspace of CONSOLE_WORKSPACES) {
    const hit = workspace.items.find(item => item.key === key)
    if (hit) return hit
  }
  return CONSOLE_WORKSPACES[0].items[0]
}

export function workspaceOf(itemKey: string): ConsoleWorkspace {
  return CONSOLE_WORKSPACES.find(w => w.items.some(i => i.key === itemKey)) ?? CONSOLE_WORKSPACES[0]
}
