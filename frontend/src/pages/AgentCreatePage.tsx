/**
 * 创建 AI 的整页流程 —— 入口三选一 + 左助手右表单。
 *
 * 对话、表单、档位三样塞一个弹窗太挤，所以整页做：没选草稿时是入口面板，
 * 选完就地换成编辑器（同一路由，不跳转）。表单提交仍走既有的 POST /agents，
 * 这一页只负责把「填」的一段做完整。
 *
 * 也是**唯一**的创建入口：设置向导那一步不再自己塞一个弹窗，而是跳到这页、
 * 创建完按 returnTo 回到原来那一步（流程不被打断，创建逻辑也只剩这一份）。
 */
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import CreateAgentPanel from '../components/CreateAgentPanel'
import CreationDraftPicker from '../components/agent-create/CreationDraftPicker'
import type { CreationDraft } from '../components/agent-create/creationApi'
import { CHAT_REFRESH_EVENT } from '../constants'

export default function AgentCreatePage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [draft, setDraft] = useState<CreationDraft | null>(null)
  const [plain, setPlain] = useState(false)

  // 从别处（设置向导）跳来的：创建完、取消都回那个地址；常规入口回 AI 列表
  const backTo = params.get('returnTo') || '/agents'
  const backToList = () => navigate(backTo)

  if (!draft && !plain) {
    return <CreationDraftPicker onPick={setDraft} onPlain={() => setPlain(true)} />
  }

  return (
    <CreateAgentPanel
      draft={draft}
      onClose={backToList}
      onCreated={() => {
        // 新 AI 会带一条私聊开场，侧栏要重拉列表才看得到
        window.dispatchEvent(new CustomEvent(CHAT_REFRESH_EVENT))
        backToList()
      }}
    />
  )
}
