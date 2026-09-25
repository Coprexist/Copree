import type { ElementType } from 'react'
import {
  Activity, BarChart3, Bot, Database, Eraser, FileText, Gauge, Globe, Key, Layers,
  LayoutDashboard, MessageCircle, Plug, ScrollText, Settings, Shield, Store, Terminal, Ticket, Users,
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
import SystemMetricsTab from './monitoring/SystemMetricsTab'
import UsageDashboardTab from './monitoring/UsageDashboardTab'
import ConversationLogTab from './monitoring/ConversationLogTab'
import LogsTab from './monitoring/LogsTab'
import BackupTab from './data/BackupTab'
import CleanupTab from './data/CleanupTab'

/**
 * 控制台的单一事实来源：工作区 → 页签 → 文案/图标/组件，全在这里。
 *
 * 两级导航是刻意的：上面一排工作区（大方向），左边一列是当前工作区里的具体页面。
 * 好处是左边那列永远只有 2~6 项（一眼扫完），加新功能时只往对应工作区里塞一行，
 * 不用再去挤一条 20 项的导航栏；代码也按工作区分目录（pages/console/<工作区>/），
 * 一个工作区改动不会牵动另一个。
 *
 * 手机端不参与这套：仍然是"分组列表 → 详情"两步，保持原来那套交互。
 */
export type ConsoleWorkspaceKey = 'general' | 'people' | 'capabilities' | 'monitoring' | 'data'

export interface ConsoleItem {
  key: string
  /** 侧栏/列表标签；只存 i18n key，避免组件里散落硬编码文案 */
  labelKey: string
  /** 手机端列表里的一行说明 */
  descKey: string
  icon: ElementType
  Component: ElementType
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
    labelKey: 'admin.groupGeneral',
    icon: Gauge,
    items: [
      { key: 'overview', labelKey: 'admin.overview', descKey: 'admin.overviewDesc', icon: LayoutDashboard, Component: OverviewTab },
      { key: 'system', labelKey: 'admin.system', descKey: 'admin.systemDesc', icon: Settings, Component: SystemSettingsTab },
      { key: 'auth', labelKey: 'admin.auth', descKey: 'admin.authDesc', icon: Shield, Component: AuthSettingsTab },
    ],
  },
  {
    key: 'people',
    labelKey: 'admin.groupPeople',
    icon: Users,
    items: [
      { key: 'users', labelKey: 'admin.users', descKey: 'admin.usersDesc', icon: Users, Component: UsersTab },
      { key: 'agents', labelKey: 'admin.agents', descKey: 'admin.agentsDesc', icon: Bot, Component: AgentsTab },
      { key: 'groups', labelKey: 'admin.groups', descKey: 'admin.groupsDesc', icon: MessageCircle, Component: GroupsTab },
      { key: 'codes', labelKey: 'admin.codes', descKey: 'admin.codesDesc', icon: Ticket, Component: CodesTab },
    ],
  },
  {
    key: 'capabilities',
    labelKey: 'admin.groupCapabilities',
    icon: Plug,
    items: [
      { key: 'capabilities', labelKey: 'admin.capabilities', descKey: 'admin.capabilitiesDesc', icon: Plug, Component: CapabilitiesTab },
      { key: 'prompt', labelKey: 'admin.prompt', descKey: 'admin.promptDesc', icon: Layers, Component: SystemPromptTab },
      { key: 'apipool', labelKey: 'admin.apiKeyPool', descKey: 'admin.apiKeyPoolDesc', icon: Key, Component: ApiKeyPoolTab },
      { key: 'opencli', labelKey: 'admin.opencli', descKey: 'admin.opencliDesc', icon: Terminal, Component: OpenCliTab },
      { key: 'federation', labelKey: 'admin.federation', descKey: 'admin.federationDesc', icon: Globe, Component: FederationTab },
      { key: 'store', labelKey: 'admin.store', descKey: 'admin.storeDesc', icon: Store, Component: StoreConsoleTab },
    ],
  },
  {
    key: 'monitoring',
    labelKey: 'admin.groupMonitoring',
    icon: Activity,
    items: [
      { key: 'metrics', labelKey: 'admin.systemMetrics', descKey: 'admin.metricsDesc', icon: Activity, Component: SystemMetricsTab },
      { key: 'usage', labelKey: 'admin.usage', descKey: 'admin.usageDesc', icon: BarChart3, Component: UsageDashboardTab },
      { key: 'convlog', labelKey: 'admin.logs', descKey: 'admin.convlogDesc', icon: ScrollText, Component: ConversationLogTab },
      { key: 'logs', labelKey: 'admin.audit', descKey: 'admin.auditDesc', icon: FileText, Component: LogsTab },
    ],
  },
  {
    key: 'data',
    labelKey: 'admin.groupData',
    icon: Database,
    items: [
      { key: 'backup', labelKey: 'admin.backup', descKey: 'admin.backupDesc', icon: Database, Component: BackupTab },
      { key: 'cleanup', labelKey: 'admin.cleanup', descKey: 'admin.cleanupDesc', icon: Eraser, Component: CleanupTab },
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
