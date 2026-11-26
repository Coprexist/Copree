/**
 * 创建 AI 的整页流程 —— 入口三选一 + 左助手右表单。
 *
 * 对话、表单、档位三样塞一个弹窗太挤，所以整页做：没选草稿时是入口面板，
 * 选完就地换成编辑器（同一路由，不跳转）。表单提交仍走既有的 POST /agents，
 * 这一页只负责把「填」的一段做完整。
 *
 * 也是**唯一**的创建入口：设置向导那一步不再自己塞一个弹窗，而是跳到这页、
 * 创建完按 returnTo 回到原来那一步（流程不被打断，创建逻辑也只剩这一份）。
 *
 * 第三个入口是「从聊天记录导入」：先在 ChatImportPanel 里把别处的对话解析成统一结构，
 * 再去填表单；**建成之后**才把那段历史写进这个 AI 与创建者的私信（/chat-import/apply）。
 * 顺序不能反——AI 得先有 id，历史才有地方落。
 */
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import CreateAgentPanel from '../components/CreateAgentPanel'
import CreationDraftPicker from '../components/agent-create/CreationDraftPicker'
import ChatImportPanel, { type ResolvedImport } from '../components/agent-create/ChatImportPanel'
import SoulImportDialog from '../components/agent-create/SoulImportDialog'
import { createDraft, patchDraft, type CreationDraft } from '../components/agent-create/creationApi'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { CHAT_REFRESH_EVENT } from '../constants'

export default function AgentCreatePage() {
  const t = useT()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [draft, setDraft] = useState<CreationDraft | null>(null)
  const [plain, setPlain] = useState(false)
  const [importing, setImporting] = useState(false)
  // 灵魂档案导入：那份文件本身就是一个完整的 AI，导完即建好，不经过表单
  const [importingSoul, setImportingSoul] = useState(false)
  const [resolved, setResolved] = useState<ResolvedImport | null>(null)

  // 从别处（设置向导）跳来的：创建完、取消都回那个地址；常规入口回 AI 列表
  const backTo = params.get('returnTo') || '/agents'
  const backToList = () => navigate(backTo)

  const finish = async (agent?: { id: number; name: string }) => {
    if (resolved && agent) {
      try {
        await api.post('/chat-import/apply', {
          agent_id: agent.id,
          transcript: resolved.transcript,
          // 智能整理里勾选要留的那部分；没整理过就是空数组
          memories: resolved.organized?.memories || [],
          knowledge: resolved.organized?.knowledge || [],
        })
      } catch (err: any) {
        // AI 已经建好了，只是历史没落地：把它说出来，别让用户以为导进去了
        window.alert(t('modal:importApplyFailed').replace('{err}', err?.message || ''))
      }
    }
    // 新 AI 会带一条私聊开场，导入的历史也进了那个会话，侧栏要重拉列表
    window.dispatchEvent(new CustomEvent(CHAT_REFRESH_EVENT))
    backToList()
  }

  if (importingSoul) {
    return (
      <SoulImportDialog
        onClose={() => setImportingSoul(false)}
        onImported={async () => {
          setImportingSoul(false)
          await finish()
        }}
      />
    )
  }

  if (importing) {
    return (
      <ChatImportPanel
        onBack={() => setImporting(false)}
        onReady={async (r) => {
          setResolved(r)
          setImporting(false)
          try {
            const fresh = await createDraft()
            // 智能整理出来的人设直接预填进表单——用户还看得见、改得动，
            // 不在这里替他把提示词定死
            const prompt = r.organized?.prompt?.trim()
            setDraft(prompt
              ? await patchDraft(fresh.id, { form: { ...(fresh.form || {}), systemPrompt: prompt } })
              : fresh)
          } catch {
            setPlain(true)   // 草稿存不下也不该挡住创建：直接进空表单，历史照样带着
          }
        }}
      />
    )
  }

  if (!draft && !plain) {
    return (
      <CreationDraftPicker
        onPick={setDraft}
        onPlain={() => setPlain(true)}
        onImport={() => setImporting(true)}
        onImportSoul={() => setImportingSoul(true)}
      />
    )
  }

  return (
    <CreateAgentPanel
      draft={draft}
      onClose={backToList}
      onCreated={finish}
    />
  )
}
