/**
 * 创建 AI 的弹窗壳 —— 内容就是整页那套 CreateAgentPanel（同一份实现，两种壳）。
 *
 * 设置向导（SetupPage）里创建是流程中的一步，跳页会打断引导，所以留这一个弹窗壳；
 * 常规入口走整页 /agents/create。
 */
import CreateAgentPanel from './CreateAgentPanel'
import type { CreationDraft } from './agent-create/creationApi'

export default function CreateAgentModal({
  onClose,
  onCreated,
  draft = null,
}: {
  onClose: () => void
  onCreated: (agentName?: string) => void
  draft?: CreationDraft | null
}) {
  return (
    <div className="fixed inset-0 md:bg-black/70 flex items-center justify-center z-modal overflow-y-auto bg-surface" onClick={onClose}>
      <div
        className="bg-elevated border border-border rounded-none md:rounded-dialog w-full max-w-full md:max-w-5xl mx-0 md:mx-4 shadow-2xl shadow-black/30 my-0 md:my-8 h-full md:h-[85vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <CreateAgentPanel draft={draft} onClose={onClose} onCreated={onCreated} />
      </div>
    </div>
  )
}
