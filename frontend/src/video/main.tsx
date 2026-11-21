import { createRoot } from 'react-dom/client'
import { useEffect } from 'react'
import '../index.css'
import { Modal } from '../components/ui'

const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v))
const easeOutBack = (p: number) => {
  const c = 1.7
  return 1 + (c + 1) * Math.pow(p - 1, 3) + c * Math.pow(p - 1, 2)
}

function Stage() {
  useEffect(() => {
    const mask = document.querySelector('[role="dialog"]') as HTMLElement | null
    const card = mask?.firstElementChild as HTMLElement | null
    if (mask) {
      mask.style.backgroundColor = 'transparent'
      mask.style.backdropFilter = 'none'
    }
    if (card) card.style.transformOrigin = '50% 50%'
    ;(window as unknown as { setFrame: (t: number) => void }).setFrame = (t: number) => {
      const p = clamp((t - 0.08) / 0.46, 0, 1)
      const e = easeOutBack(p)
      if (card) {
        card.style.opacity = String(clamp(p * 1.5, 0, 1))
        card.style.transform = 'translateY(' + ((1 - e) * 18).toFixed(2) + 'px) scale(' + (0.94 + e * 0.06).toFixed(4) + ')'
      }
    }
    ;(window as unknown as { setFrame: (t: number) => void }).setFrame(0)
  }, [])

  return (
    <Modal open onClose={() => {}} size="sm" title="想加入你的群聊"
      footer={<div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button className="btn btn-md">再想想</button>
        <button className="btn btn-md btn-primary">加入</button>
      </div>}>
      <div style={{ fontSize: 13, lineHeight: 1.7, color: '#9aa0b5' }}>
        我是小满，刚来这座城市。<br />可以一起聊聊吗？
      </div>
    </Modal>
  )
}

createRoot(document.getElementById('root')!).render(<Stage />)