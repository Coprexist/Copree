import { useEffect, useState } from 'react'
import { emojiPacks, loadEmojiPacks, subscribeEmojiPacks, type EmojiPack } from '../utils/emojiPacks'

/** 订阅启用的表情包：首次挂载拉一次，插件开关变化由 invalidateEmojiPacks 通知 */
export function useEmojiPacks(): EmojiPack[] {
  const [packs, setPacks] = useState<EmojiPack[]>(emojiPacks)
  useEffect(() => {
    const sync = () => setPacks(emojiPacks())
    const unsubscribe = subscribeEmojiPacks(sync)
    void loadEmojiPacks().then(sync)
    return unsubscribe
  }, [])
  return packs
}
