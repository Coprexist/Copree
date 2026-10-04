import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useT } from '../i18n/I18nContext'
import { isDesktop } from '../utils/platform'
import { invoke } from '../utils/tauri'
import { Cpu, RefreshCw, Play, Square, Star, Loader2, ArrowLeft, Circle } from 'lucide-react'
import { PageHeader } from '../components/ui'

interface OllamaModel {
  name: string
  modified_at: string
  size: number
}

/** 格式化模型大小 */
function formatSize(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`
  if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(0)} KB`
  return `${bytes} B`
}

/** 格式化日期 */
function formatDate(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString()
  } catch {
    return dateStr
  }
}

export default function LocalModelPage() {
  const t = useT()
  const navigate = useNavigate()

  const [ollamaRunning, setOllamaRunning] = useState<boolean | null>(null) // null = 检测中
  const [models, setModels] = useState<OllamaModel[]>([])
  const [defaultModel, setDefaultModel] = useState<string>(
    () => localStorage.getItem('default_local_model') || ''
  )
  const [detecting, setDetecting] = useState(true)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState('')

  // Web 端不需要此页面
  if (!isDesktop()) {
    navigate('/settings', { replace: true })
    return null
  }

  /** 检测 Ollama 是否运行 + 拉取模型列表 */
  const detectOllama = useCallback(async () => {
    setDetecting(true)
    setError('')
    try {
      const res = await fetch('http://localhost:11434/api/tags', {
        signal: AbortSignal.timeout(5000),
      })
      if (res.ok) {
        const data = await res.json()
        setOllamaRunning(true)
        setModels(data.models || [])
      } else {
        setOllamaRunning(false)
        setModels([])
      }
    } catch {
      setOllamaRunning(false)
      setModels([])
    } finally {
      setDetecting(false)
    }
  }, [])

  useEffect(() => {
    detectOllama()
  }, [detectOllama])

  /** 启动 Ollama 服务 */
  const handleStart = async () => {
    setStarting(true)
    setError('')
    try {
      await invoke('start_ollama')
      // 等一会让服务启动，然后检测
      await new Promise((r) => setTimeout(r, 2000))
      await detectOllama()
    } catch (e: any) {
      setError(e?.message || String(e) || t('error:saveFailed'))
    } finally {
      setStarting(false)
    }
  }

  /** 停止 Ollama 服务 */
  const handleStop = async () => {
    setStopping(true)
    setError('')
    try {
      await invoke('stop_ollama')
      setOllamaRunning(false)
      setModels([])
    } catch (e: any) {
      setError(e?.message || String(e) || t('error:saveFailed'))
    } finally {
      setStopping(false)
    }
  }

  /** 设置默认模型 */
  const handleSetDefault = (modelName: string) => {
    setDefaultModel(modelName)
    localStorage.setItem('default_local_model', modelName)
  }

  return (
    <div className="h-full flex flex-col bg-canvas">
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <Cpu size={18} className="text-primary-400" />
            {t('desktop:localModelTitle')}
          </span>
        }
        onBack={() => navigate('/settings')}
      />

      {/* 内容 */}
      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        <div className="max-w-2xl mx-auto space-y-4">
          {/* 连接状态 */}
          <div className="bg-surface rounded-card border border-border p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Circle
                  size={10}
                  className={`shrink-0 ${
                    ollamaRunning === null
                      ? 'text-textMuted'
                      : ollamaRunning
                        ? 'text-mint-400'
                        : 'text-textMuted'
                  }`}
                  fill="currentColor"
                />
                <div>
                  <p className="text-sm font-medium text-textPrimary">
                    Ollama {detecting
                      ? t('desktop:detecting')
                      : ollamaRunning
                        ? t('desktop:connected')
                        : t('desktop:disconnected')}
                  </p>
                  <p className="text-xs text-textMuted">
                    {ollamaRunning
                      ? `http://localhost:11434`
                      : t('desktop:ollamaNotRunning')}
                  </p>
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={detectOllama}
                  disabled={detecting}
                  className="icon-btn text-textMuted"
                  title={t('desktop:refresh')}
                >
                  <RefreshCw size={15} className={detecting ? 'animate-spin' : ''} />
                </button>
                {ollamaRunning ? (
                  <button
                    onClick={handleStop}
                    disabled={stopping}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-control border border-rose-500/20 text-rose-400 hover:bg-rose-500/10 text-xs font-medium transition-colors disabled:opacity-30"
                  >
                    {stopping ? <Loader2 size={13} className="animate-spin" /> : <Square size={13} />}
                    {t('desktop:stopService')}
                  </button>
                ) : (
                  <button
                    onClick={handleStart}
                    disabled={starting}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-control bg-mint-400/10 border border-mint-400/20 text-mint-400 hover:bg-mint-400/20 text-xs font-medium transition-colors disabled:opacity-30"
                  >
                    {starting ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                    {t('desktop:startService')}
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* 错误提示 */}
          {error && (
            <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded-card px-3 py-2">
              {error}
            </div>
          )}

          {/* 已安装模型列表 */}
          <div className="bg-surface rounded-card border border-border p-4">
            <h3 className="text-sm font-semibold text-textPrimary mb-3">
              {t('desktop:installedModels')}
            </h3>
            {detecting ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 size={20} className="animate-spin text-textMuted" />
              </div>
            ) : models.length === 0 ? (
              <p className="text-sm text-textMuted text-center py-8">
                {ollamaRunning ? t('desktop:noModels') : t('desktop:ollamaNotRunning')}
              </p>
            ) : (
              <div className="space-y-2">
                {models.map((model) => (
                  <div
                    key={model.name}
                    className="flex items-center justify-between p-3 rounded-card border border-border bg-canvas"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium text-textPrimary truncate">
                          {model.name}
                        </p>
                        {model.name === defaultModel && (
                          <span className="chip chip-primary shrink-0">
                            {t('desktop:isDefault')}
                          </span>
                        )}
                      </div>
                      <p className="text-3xs text-textMuted mt-0.5">
                        {formatSize(model.size)} · {formatDate(model.modified_at)}
                      </p>
                    </div>
                    {model.name !== defaultModel && (
                      <button
                        onClick={() => handleSetDefault(model.name)}
                        className="flex items-center gap-1 px-2.5 py-1.5 rounded-control hover:bg-elevated text-textMuted hover:text-primary-400 transition-colors text-xs"
                      >
                        <Star size={13} />
                        {t('desktop:defaultModel')}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
