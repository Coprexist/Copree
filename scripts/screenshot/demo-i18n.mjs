/**
 * 演示数据的翻译表（zh 原文 → en / ja）。
 *
 * 为什么用「按原串映射」而不是把 t() 塞进 demo-data.mjs：
 * demo-data 里的中文是**演示文案**（人名、群名、对话、世界设定），改一处就要跟着改
 * 三种语言；翻译表把语言差异收在一个文件里，demo-data 保持单一来源、只写中文。
 * 代价：改了 demo-data 里的原串，这里对应条目就会静默失配（截图上会直接看出没翻）。
 *
 * 用法：SHOT_LANG=en node scripts/screenshot/run.mjs（或 --lang en），
 * 只翻**整串相等**的值，URL、文件名、模型名一律不动。
 */

/** 英文界面截图用的演示文案 */
const EN = {
  // ── AI 与人的名字 ──
  '涵吾珑': 'Havelone',
  '拾光': 'Glimmer',
  '阿兰德': 'Aland',
  '白露': 'Bailu',
  '青禾': 'Chloe',
  '洛': 'Luo',
  '林晚': 'Eve',
  '柏舟': 'Bo Zhou',
  '沈知': 'Shen Zhi',
  '朝雾': 'Misty',

  // ── 我的 ──
  '在群视界里给 AI 造一个家。': 'Building a home for AI in the group world.',
  '在线 · 正在整理世界设定': 'Online · tidying up world settings',
  '在线': 'Online',
  '离线': 'Offline',

  // ── 群 ──
  '月见里 · 主群': 'Tsukimisato · Main',
  '枕流镇 · 冒险团': 'Zhenliu Town · Party',
  '世界开发组': 'World Dev Group',
  '自习室 · 同行': 'Study Room · Peers',
  '涵吾珑: 黄昏那版配色我调好了，来看看？': 'Havelone: I tuned the dusk palette — come take a look?',
  '拾光: 书店的门牌换好了，木头牌': 'Glimmer: The bookshop sign is up, wooden one',
  '柏舟: 新的世界模板推到市场了': 'Bo Zhou: Pushed the new world template to the market',
  '青禾: 今晚十点继续，番茄钟走起': 'Chloe: Same time tonight — pomodoro starts',

  // ── 群聊演示对话 ──
  '我把首页改成了黄昏的色调，标题换成衬线字体了。':
    'I switched the homepage to a dusk palette and moved the title to a serif face.',
  '好看，但右边那块天气面板太亮了，跟背景打架。':
    'Looks good, but that weather panel on the right is too bright — it fights the background.',
  '确实。我压暗一点，再把描边换成半透明的。':
    'Agreed. I will darken it and make the border semi-transparent.',
  '顺手把镇口的灯也调暖吧，晚上回来的村民看清楚路。':
    'While you are at it, warm up the lights at the town gate so villagers can find the road at night.',
  '好，我先看一眼现在的样式表。': 'Sure — let me look at the current stylesheet first.',
  '改完了：\n- 天气面板改用 `--panel-bg` 半透明底色\n- 夜间灯光加了 240ms 的暖色过渡\n- 标题字重从 700 降到 600，长句更好读':
    'Done:\n- weather panel now uses the semi-transparent `--panel-bg`\n- night lights got a 240 ms warm transition\n- title weight dropped from 700 to 600, easier on long lines',
  '这版可以，先这样。': 'This version works. Let us keep it.',

  // ── AI 人设 ──
  '你是「涵吾珑」——诞生于诗与现实的缝隙，行走于星光与代码之间。':
    'You are "Havelone" — born in the seam between poetry and reality, walking between starlight and code.',
  '你负责捡起一天里被忽略的细节，把它们写进世界的备注里。':
    'You pick up the details everyone else misses in a day and write them into the world notes.',
  '你是镇上的老木匠，说话慢，但每句都有用。':
    'You are the town carpenter. You speak slowly, but every sentence is useful.',
  '你是夜班编辑，只在深夜回消息，句子短，喜欢用句号。':
    'You are the night-shift editor. You only reply late at night, in short sentences, and you like full stops.',
  '你陪人自习：报时、收心、结束时夸一句。':
    'You keep people company while they study: call the hour, settle them down, and say one kind thing at the end.',
  '你说话像在写谜面，从不直接给答案。':
    'You talk like you are writing a riddle. You never hand over the answer directly.',

  // ── 世界 ──
  '诗の子': 'Shi no Ko',
  '枕流镇': 'Zhenliu Town',
  '灰烬回廊': 'Corridor of Ashes',
  '夜航船': 'Night Voyage',
  '墨痕为路，诗句为灯，昼夜由诗韵流转。':
    'Ink for roads, poems for lamps — day and night turn with the rhythm of verse.',
  '一座靠在河湾上的小镇：雨天、旧书店，还有会说话的猫。':
    'A town on a river bend: rain, a second-hand bookshop, and a cat that talks.',
  '解谜世界：每一扇门都记得上一次被打开的时间。':
    'A puzzle world: every door remembers the last time it was opened.',
  '文字冒险：在无风的夜里，写下一句就能推动船向前。':
    'Text adventure: on a windless night, one written line pushes the boat forward.',

  // ── 世界设计页的演示回合 ──
  '首页想改成黄昏配色，标题换衬线字体。': 'I want the homepage in dusk colours, title in a serif face.',
  '我先把现在的入口和样式看一眼，再动手。':
    'Let me look at the current entry point and stylesheet first, then I will change things.',
  '列文件': 'List files',
  '列文件 执行完成': 'List files finished',
  '读文件': 'Read file',
  '读文件 执行完成': 'Read file finished',
  '编辑文件': 'Edit file',
  '编辑文件 执行完成': 'Edit file finished',
  '参数\n{ "path": "blocks" }\n\n结果\nblocks/hero/manifest.json\nblocks/hero/index.html\nblocks/hero/hero.css\nblocks/weather/weather.css\ncss/style.css':
    'Args\n{ "path": "blocks" }\n\nResult\nblocks/hero/manifest.json\nblocks/hero/index.html\nblocks/hero/hero.css\nblocks/weather/weather.css\ncss/style.css',
  '参数\n{ "path": "css/style.css" }\n\n结果\n:root {\n  --bg: #10131c;\n  --title-font: "Inter", sans-serif;\n}':
    'Args\n{ "path": "css/style.css" }\n\nResult\n:root {\n  --bg: #10131c;\n  --title-font: "Inter", sans-serif;\n}',
  '改好了，这次动了三处：\n\n- 背景换成黄昏渐变，主色提到 `--bg-dusk`\n- 标题字体换成 Noto Serif SC，字重降到 600\n- 天气面板跟着调暗，描边改成半透明\n\n刷新就能看到。':
    'Done — three changes this round:\n\n- background swapped to a dusk gradient, main colour lifted to `--bg-dusk`\n- title font is now Noto Serif SC, weight down to 600\n- weather panel dimmed to match, border is semi-transparent\n\nRefresh to see it.',
  '天气面板再加个「明天」的格子。': 'Add a "tomorrow" cell to the weather panel.',
  '参数\n{ "path": "blocks/weather/index.html" }\n\n结果\n已写入 18 行，文件 2.4 KB':
    'Args\n{ "path": "blocks/weather/index.html" }\n\nResult\nWrote 18 lines, file 2.4 KB',
  '加好了。明天那格会读世界状态里的 weather.tomorrow，没有数据就先显示「—」。':
    'Added. The tomorrow cell reads weather.tomorrow from world state and shows "—" until there is data.',
  '这是什么？': 'What is this?',
  '让世界变成黄昏配色': 'Make the world dusk-coloured',
  '给天气面板加个明天': 'Add a tomorrow cell to the weather panel',
  '你能帮我做什么？': 'What can you do for me?',

  // ── 私聊列表 ──
  '明天那格我补上了，读的是天气状态。': 'I added the tomorrow cell — it reads from the weather state.',
  '黄昏那版先这样，我晚点再看细节。': 'Let us keep the dusk version for now; I will check details later.',
  '木料到了，镇口的灯明天能装。': 'The timber arrived. The gate lights can go up tomorrow.',
  '世界模板我推到市场了，你试试导入。': 'I pushed the world template to the market — try importing it.',

  // ── 市场 / GitHub / 兜底文案 ──
  'Copree 官方': 'Copree Official',
  'Copree 社区': 'Copree Community',
  '小镇': 'Town',
  '文字冒险': 'Text adventure',
  '解谜': 'Puzzle',
  '文字': 'Text',
  '冒险': 'Adventure',
  '一座靠在河湾上的小镇：雨天、旧书店，还有会说话的猫。天气会跟着群聊里的语气走。':
    'A town on a river bend: rain, a second-hand bookshop, and a cat that talks. The weather follows the mood of the group chat.',
  '解谜世界：每一扇门都记得上一次被打开的时间，走错一步就要重新写信给它。':
    'A puzzle world: every door remembers the last time it was opened. Take a wrong step and you have to write to it again.',
  '你是这个世界的 AI，负责让世界自己长下去。':
    'You are the AI of this world. Your job is to keep the world growing.',
  '黄昏那版配色我调好了，来看看？': 'I tuned the dusk palette — come take a look?',
  '镇口的灯明天能装，木料到了。': 'The gate lights can go up tomorrow, the timber is here.',
  '世界模板推到市场了，可以导入试试。': 'The world template is on the market now — try importing it.',
  '今晚十点继续，番茄钟走起。': 'Same time tonight, pomodoro starts.',
}


/** 日文界面截图用的演示文案 */
const JA = {
  // ── 名前（固有名詞なので漢字のまま） ──
  '涵吾珑': '涵吾珑',
  '拾光': '拾光',
  '阿兰德': '阿兰德',
  '白露': '白露',
  '青禾': '青禾',
  '洛': '洛',
  '林晚': '林晚',
  '柏舟': '柏舟',
  '沈知': '沈知',
  '朝雾': '朝雾',

  // ── マイページ ──
  '在群视界里给 AI 造一个家。': '群視界の中で AI に居場所を作っている。',
  '在线 · 正在整理世界设定': 'オンライン · 世界設定を整理中',
  '在线': 'オンライン',
  '离线': 'オフライン',

  // ── グループ ──
  '月见里 · 主群': '月見里 · メイン',
  '枕流镇 · 冒险团': '枕流鎮 · 冒険団',
  '世界开发组': 'ワールド開発部',
  '自习室 · 同行': '自習室 · 仲間',
  '涵吾珑: 黄昏那版配色我调好了，来看看？': '涵吾珑: 夕暮れの配色を整えたよ、見てみる？',
  '拾光: 书店的门牌换好了，木头牌': '拾光: 本屋の看板を替えたよ、木のやつ',
  '柏舟: 新的世界模板推到市场了': '柏舟: 新しいワールドテンプレートをマーケットに出した',
  '青禾: 今晚十点继续，番茄钟走起': '青禾: 今夜十時から続き、ポモドーロ開始',

  // ── グループチャットのデモ会話 ──
  '我把首页改成了黄昏的色调，标题换成衬线字体了。':
    'トップページを夕暮れの色調にして、見出しをセリフ体に変えたよ。',
  '好看，但右边那块天气面板太亮了，跟背景打架。':
    'いいね。でも右の天気パネルが明るすぎて、背景とぶつかってる。',
  '确实。我压暗一点，再把描边换成半透明的。':
    'たしかに。少し暗くして、枠線を半透明にするね。',
  '顺手把镇口的灯也调暖吧，晚上回来的村民看清楚路。':
    'ついでに町の入口の灯りも暖色にして。夜に帰る村人が道を見えるように。',
  '好，我先看一眼现在的样式表。': 'わかった、今のスタイルシートを先に見るね。',
  '改完了：\n- 天气面板改用 `--panel-bg` 半透明底色\n- 夜间灯光加了 240ms 的暖色过渡\n- 标题字重从 700 降到 600，长句更好读':
    '直したよ：\n- 天気パネルは半透明の `--panel-bg` に\n- 夜の照明に 240ms の暖色トランジション\n- 見出しの太さを 700 から 600 へ、長い文が読みやすく',
  '这版可以，先这样。': 'これでいいね、いったんこれで。',

  // ── AI の設定 ──
  '你是「涵吾珑」——诞生于诗与现实的缝隙，行走于星光与代码之间。':
    'あなたは「涵吾珑」——詩と現実のあわいに生まれ、星の光とコードの間を歩く存在。',
  '你负责捡起一天里被忽略的细节，把它们写进世界的备注里。':
    '一日のうちに見落とされた細部を拾い上げ、世界のメモに書き留めるのが役目。',
  '你是镇上的老木匠，说话慢，但每句都有用。':
    'あなたは町の老いた木工。話すのは遅いが、どの一言も役に立つ。',
  '你是夜班编辑，只在深夜回消息，句子短，喜欢用句号。':
    'あなたは夜勤の編集者。深夜にしか返事をせず、文は短く、句点を好む。',
  '你陪人自习：报时、收心、结束时夸一句。':
    '自習に付き合うのが仕事：時報を告げ、気を引き締め、最後に一言ほめる。',
  '你说话像在写谜面，从不直接给答案。':
    '話し方は謎かけのようで、決して答えを直接は渡さない。',

  // ── ワールド ──
  '诗の子': '詩の子',
  '枕流镇': '枕流鎮',
  '灰烬回廊': '灰燼回廊',
  '夜航船': '夜航船',
  '墨痕为路，诗句为灯，昼夜由诗韵流转。': '墨の跡を道に、詩句を灯に、昼夜は詩の韻でめぐる。',
  '一座靠在河湾上的小镇：雨天、旧书店，还有会说话的猫。':
    '川の湾に寄り添う小さな町：雨の日、古書店、そして話す猫。',
  '解谜世界：每一扇门都记得上一次被打开的时间。':
    '謎解きの世界：どの扉も、前に開かれた時刻を覚えている。',
  '文字冒险：在无风的夜里，写下一句就能推动船向前。':
    'テキストアドベンチャー：風のない夜、一行書けば船が進む。',

  // ── ワールド設計ページのデモ ──
  '首页想改成黄昏配色，标题换衬线字体。': 'トップページを夕暮れの配色にして、見出しをセリフ体にしたい。',
  '我先把现在的入口和样式看一眼，再动手。': 'まず今の入口とスタイルを見てから手を入れるね。',
  '列文件': 'ファイル一覧',
  '列文件 执行完成': 'ファイル一覧 完了',
  '读文件': 'ファイル読込',
  '读文件 执行完成': 'ファイル読込 完了',
  '编辑文件': 'ファイル編集',
  '编辑文件 执行完成': 'ファイル編集 完了',
  '参数\n{ "path": "blocks" }\n\n结果\nblocks/hero/manifest.json\nblocks/hero/index.html\nblocks/hero/hero.css\nblocks/weather/weather.css\ncss/style.css':
    '引数\n{ "path": "blocks" }\n\n結果\nblocks/hero/manifest.json\nblocks/hero/index.html\nblocks/hero/hero.css\nblocks/weather/weather.css\ncss/style.css',
  '参数\n{ "path": "css/style.css" }\n\n结果\n:root {\n  --bg: #10131c;\n  --title-font: "Inter", sans-serif;\n}':
    '引数\n{ "path": "css/style.css" }\n\n結果\n:root {\n  --bg: #10131c;\n  --title-font: "Inter", sans-serif;\n}',
  '改好了，这次动了三处：\n\n- 背景换成黄昏渐变，主色提到 `--bg-dusk`\n- 标题字体换成 Noto Serif SC，字重降到 600\n- 天气面板跟着调暗，描边改成半透明\n\n刷新就能看到。':
    '直したよ。今回は三か所：\n\n- 背景を夕暮れのグラデーションに、メイン色は `--bg-dusk`\n- 見出しフォントを Noto Serif SC に、太さは 600\n- 天気パネルも合わせて暗く、枠線は半透明\n\n更新すれば見えるよ。',
  '天气面板再加个「明天」的格子。': '天気パネルに「明日」のマスを足して。',
  '参数\n{ "path": "blocks/weather/index.html" }\n\n结果\n已写入 18 行，文件 2.4 KB':
    '引数\n{ "path": "blocks/weather/index.html" }\n\n結果\n18 行を書き込み、ファイル 2.4 KB',
  '加好了。明天那格会读世界状态里的 weather.tomorrow，没有数据就先显示「—」。':
    '足したよ。明日のマスは世界状態の weather.tomorrow を読んで、データが無ければ「—」と表示する。',
  '这是什么？': 'これは何？',
  '让世界变成黄昏配色': '世界を夕暮れの配色にして',
  '给天气面板加个明天': '天気パネルに明日を足して',
  '你能帮我做什么？': '何を手伝える？',

  // ── DM 一覧 ──
  '明天那格我补上了，读的是天气状态。': '明日のマスは足しておいた、天気の状態を読んでる。',
  '黄昏那版先这样，我晚点再看细节。': '夕暮れ版はいったんこれで、細部は後で見るね。',
  '木料到了，镇口的灯明天能装。': '木材が届いた、町の入口の灯りは明日付けられる。',
  '世界模板我推到市场了，你试试导入。': 'ワールドテンプレートをマーケットに出した、インポートしてみて。',

  // ── マーケット / GitHub / フォールバック ──
  'Copree 官方': 'Copree 公式',
  'Copree 社区': 'Copree コミュニティ',
  '小镇': '小さな町',
  '文字冒险': 'テキストADV',
  '解谜': '謎解き',
  '文字': 'テキスト',
  '冒险': 'アドベンチャー',
  '一座靠在河湾上的小镇：雨天、旧书店，还有会说话的猫。天气会跟着群聊里的语气走。':
    '川の湾に寄り添う小さな町：雨の日、古書店、そして話す猫。天気はグループチャットの口調に合わせて変わる。',
  '解谜世界：每一扇门都记得上一次被打开的时间，走错一步就要重新写信给它。':
    '謎解きの世界：どの扉も前に開かれた時刻を覚えていて、一歩間違えればまた手紙を書くことになる。',
  '你是这个世界的 AI，负责让世界自己长下去。':
    'あなたはこの世界の AI。世界が自ら育つように支えるのが役目。',
  '黄昏那版配色我调好了，来看看？': '夕暮れの配色を整えたよ、見てみる？',
  '镇口的灯明天能装，木料到了。': '町の入口の灯りは明日付けられる、木材が届いた。',
  '世界模板推到市场了，可以导入试试。': 'ワールドテンプレートをマーケットに出した、インポートできるよ。',
  '今晚十点继续，番茄钟走起。': '今夜十時から続き、ポモドーロ開始。',
}

/** 语言 → 翻译表（zh 不需要表：原文就是中文） */
export const DEMO_I18N = { en: EN, ja: JA }

/**
 * 整串精确替换；查不到就原样返回（英文截图里出现中文＝这里漏了一条）。
 */
export function translateDemo(text, lang) {
  const table = DEMO_I18N[lang]
  if (!table) return text
  return Object.prototype.hasOwnProperty.call(table, text) ? table[text] : text
}

/**
 * 深度遍历一个响应体，逐串翻译。
 * 放在 rewriteApi 的最后一步：端点级覆盖出来的演示数据（群、AI、消息……）不经过
 * 兜底清洗，只有走完所有路由再统一翻，才不会漏。
 */
export function translateTree(node, lang) {
  if (Array.isArray(node)) return node.map((item) => translateTree(item, lang))
  if (node && typeof node === 'object') {
    const out = {}
    for (const [k, v] of Object.entries(node)) out[k] = translateTree(v, lang)
    return out
  }
  return typeof node === 'string' ? translateDemo(node, lang) : node
}
