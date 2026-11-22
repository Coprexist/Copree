import { useState, useRef, useEffect } from 'react'
import { Button, IconButton, SecretInput } from '../components/ui'
import { Send, Key, Bot, User, Trash2, Github, Sun, Moon } from 'lucide-react'

type Role = 'user' | 'assistant'
interface Msg { role: Role; content: string }

const SYS_MSG: Msg = {
  role: 'assistant',
  content: "你好！我是 Copree 演示版。输入你的 DeepSeek API Key 开始体验 AI 群聊。\n\n" +
    "💡 你的 Key 只保存在浏览器本地，不会上传到任何服务器。\n" +
    "🌐 访问 GitHub 仓库获取完整版 → [Copree](https://github.com/Coprexist/Copree)",
}

export default function DemoChat() {
  const [apiKey, setApiKey] = useState(() => localStorage.getItem('demo_api_key') || '')
  const [keyInput, setKeyInput] = useState('')
  const [msgs, setMsgs] = useState<Msg[]>([SYS_MSG])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [dark, setDark] = useState(() => {
    const t = localStorage.getItem('theme')
    return t === 'dark' || (!t && window.matchMedia('(prefers-color-scheme:dark)').matches)
  })
  const endRef = useRef<HTMLDivElement>(null)
  const chatRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem('theme', dark ? 'dark' : 'light')
  }, [dark])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs])

  const saveKey = () => {
    const k = keyInput.trim()
    if (!k) return
    localStorage.setItem('demo_api_key', k)
    setApiKey(k)
    setKeyInput('')
  }

  const clearKey = () => {
    localStorage.removeItem('demo_api_key')
    setApiKey('')
    setMsgs([SYS_MSG])
  }

  const send = async () => {
    const content = input.trim()
    if (!content || loading) return
    setInput('')
    const newMsgs: Msg[] = [...msgs, { role: 'user', content }]
    setMsgs(newMsgs)
    setLoading(true)

    try {
      const res = await fetch('https://api.deepseek.com/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${apiKey}` },
        body: JSON.stringify({
          model: 'deepseek-chat',
          messages: [{ role: 'system', content: '你是一个有用的AI助手，在一个名为Copree的AI群聊社交平台中。请友好地回复用户。' }, ...newMsgs.map(m => ({ role: m.role, content: m.content }))],
          stream: true,
        }),
      })
      if (!res.ok) {
        const err = await res.text()
        throw new Error(`API 错误 (${res.status}): ${err}`)
      }
      const reader = res.body?.getReader()
      if (!reader) throw new Error('无法读取响应')
      const decoder = new TextDecoder()
      let buf = ''
      setMsgs(prev => [...prev, { role: 'assistant', content: '' }])

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() || ''
        for (const line of lines) {
          const m = line.match(/^data: (.+)$/)
          if (!m) continue
          if (m[1] === '[DONE]') break
          try {
            const d = JSON.parse(m[1])
            const text = d.choices?.[0]?.delta?.content || ''
            if (text) {
              setMsgs(prev => {
                const next = [...prev]
                const last = next[next.length - 1]
                next[next.length - 1] = { ...last, content: last.content + text }
                return next
              })
            }
          } catch {}
        }
      }
    } catch (e: any) {
      setMsgs(prev => [...prev, { role: 'assistant', content: `${e.message || '请求失败'}` }])
    }
    setLoading(false)
  }

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  return (
    <div className={`flex flex-col h-screen ${dark ? 'dark' : ''}`}>
      {/* 顶部栏 */}
      <header className="top-bar flex items-center justify-between px-4 py-2.5 shrink-0">
        <div className="flex items-center gap-2">
          <Bot size={20} className="text-primary-400" />
          <span className="font-semibold text-sm text-textPrimary">Copree Demo</span>
          <span className="text-3xs px-1.5 py-0.5 rounded-full bg-accent-500/10 text-accent-500 border border-accent-500/20">DEMO</span>
        </div>
        <div className="flex items-center gap-2">
          <a href="https://github.com/Coprexist/Copree" target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-textMuted hover:text-textSecondary">
            <Github size={14} /> GitHub
          </a>
          <IconButton
            icon={dark ? <Sun size={14} /> : <Moon size={14} />}
            label="切换主题"
            size="sm"
            onClick={() => setDark(!dark)}
          />
        </div>
      </header>

      {/* API Key 栏 */}
      {!apiKey ? (
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border bg-accent-500/5">
          <Key size={14} className="text-accent-500 shrink-0" />
          <SecretInput value={keyInput} onChange={e => setKeyInput(e.target.value)}
            placeholder="输入你的 DeepSeek API Key 开始体验..."
            wrapperClassName="flex-1 min-w-0"
            onKeyDown={e => e.key === 'Enter' && saveKey()}
          />
          <Button variant="accent" size="xs" onClick={saveKey} disabled={!keyInput.trim()}>
            确认
          </Button>
        </div>
      ) : (
        <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-canvas/50">
          <div className="flex items-center gap-1.5 text-xs text-textMuted">
            <Key size={12} className="text-mint-400" />
            API Key 已设置
          </div>
          <button onClick={clearKey} className="flex items-center gap-1 text-xs text-rose-400 hover:text-rose-500 dark:hover:text-rose-300">
            <Trash2 size={12} /> 清除 Key
          </button>
        </div>
      )}

      {/* 聊天区 */}
      <div ref={chatRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3 bg-canvas">
        {msgs.map((m, i) => (
          <div key={i} className={`flex gap-2 ${m.role === 'user' ? 'justify-end' : ''}`}>
            {m.role === 'assistant' && (
              <div className="w-7 h-7 rounded-full bg-primary-500/15 flex items-center justify-center shrink-0 mt-0.5">
                <Bot size={14} className="text-primary-400" />
              </div>
            )}
            <div className={`max-w-[80%] rounded-dialog px-3.5 py-2 text-sm leading-relaxed whitespace-pre-wrap ${
              m.role === 'user'
                ? 'bg-primary-500 text-white rounded-br-lg'
                : 'bg-surface border border-border text-textPrimary rounded-bl-lg'
            }`}>
              {m.content}
            </div>
            {m.role === 'user' && (
              <div className="w-7 h-7 rounded-full bg-accent-500/15 flex items-center justify-center shrink-0 mt-0.5">
                <User size={14} className="text-accent-400" />
              </div>
            )}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      {/* 输入栏 */}
      <div className="px-4 py-3 border-t border-border bg-surface shrink-0">
        <div className="flex items-end gap-2 max-w-4xl mx-auto">
          <textarea
            value={input} onChange={e => setInput(e.target.value)} onKeyDown={handleKey}
            placeholder="输入消息..."
            rows={1}
            className="field flex-1 min-w-0"
          />
          <button onClick={send} disabled={!input.trim() || !apiKey || loading}
            className="icon-btn-lg icon-btn-primary">
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}
