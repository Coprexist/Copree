"""QQ 通道的服务声明：名字、描述、配置 schema。

只放「这个插件是什么、能配什么」；连接、收发、身份这些行为不在这里。schema 是前端表单的
唯一来源，字段语义见 docs/plugin-dev/plugin-protocol-v3.md。改动这一段不影响链路。
"""
_SEC_CONNECT = {"key": "connect", "title": "接入", "title_en": "Connection", "title_ja": "接続"}
_SEC_REPLY = {"key": "reply", "title": "回复", "title_en": "Replies", "title_ja": "返信"}
_SEC_QUOTA = {"key": "quota", "title": "对话额度", "title_en": "Conversation budget", "title_ja": "会話ごとの上限"}
_SEC_DM = {"key": "dm", "title": "私聊", "title_en": "Direct messages", "title_ja": "ダイレクトメッセージ"}


SERVICE = dict(
    name="QQ 通道",
    description="一个机器人接一个 AI：群里被 @、或私聊机器人，都进 Copree；AI 的回复发回 QQ",
    multi_instance=True,
    config_schema={
        "app_id": {
            "type": "string", "title": "AppID", "required": True, "credential": True,
            "description": "QQ 开放平台 → 机器人管理页获取",
            "description_en": "From the QQ Open Platform bot settings page",
            "description_ja": "QQオープンプラットフォームのボット管理ページで取得します",
        },
        "client_secret": {
            "type": "string", "title": "ClientSecret", "secret": True, "required": True, "credential": True,
            "description": "只在这里填，加密落库、接口不回显",
            "description_en": "Enter it here only; stored encrypted and never echoed back",
            "description_ja": "ここにのみ入力します。暗号化して保存し、APIは返しません",
        },
        "target_agent": {
            "type": "string", "title": "这个机器人是谁（AI 名字）", "required": True, "managed": True,
            "description": "由平台按「这个 AI 的页面」自动填，不需要手工填；群里 @机器人 就等于在群里 @它",
        },
        "target_agent_id": {
            "type": "string", "title": "AI 的 id", "managed": True,
            "description": "平台填。认人按 id，名字只用来显示：AI 改名不影响通道",
        },
        "copree_group_id": {
            "type": "string", "title": "接入的 Copree 群 ID",
            "title_en": "Landing Copree group ID", "title_ja": "接続先 Copree グループID",
            "description": "**没单独指定落点的**群消息落到哪个群。留空也可以接群：下面开着"
                           "「QQ 群首次说话自动接上」时，每个 QQ 群会自己认领或建一个落点群；"
                           "留空 + 关掉那个开关 = 只做私聊，不接群。",
        },
        "qq_group_allowlist": {
            "type": "string", "title": "允许接入的 QQ 群",
            "title_en": "Allowed QQ groups", "title_ja": "許可する QQ グループ",
            "description": "群 openid，多个用逗号分隔；留空 = 不限制",
            "description_en": "Group openids, comma separated; empty = no limit",
            "description_ja": "グループ openid をカンマ区切り。空欄＝制限なし",
        },
        "group_map": {
            "type": "string", "title": "QQ 群 → Copree 群",
            "title_en": "QQ groups to Copree groups", "title_ja": "QQグループ → Copreeグループ",
            "description": "每个 QQ 群落到哪个 Copree 群，写成 JSON：{\"群 openid\": 群 ID}。"
                           "卡片上「最近见到过的群」可以直接给每个群选落点，不必手写这段；"
                           "没在这里的群落到上面的「接入的 Copree 群 ID」。",
        },
        "auto_create_group": {
            "section": _SEC_CONNECT,
            "type": "boolean", "title": "QQ 群首次说话自动接上", "default": True,
            "title_en": "Auto-attach a Copree group per QQ group",
            "title_ja": "QQグループごとに Copree グループを自動接続",
            "description": "开启后（默认开）：在 QQ 里把这个机器人拉进群就完事——它第一次说话时，Copree 自动"
                           "给它安排落点：同一主人的别的机器人已经接过**同名**的 QQ 群 → 接到同一个 Copree 群"
                           "（并把这个 AI 加进群）；没有对应的群 → 新建一个专属群（群名跟随 QQ 群名）。"
                           "已经手工选过落点的群不动。落点群留空也照样接；要只做私聊，把这个开关关掉。",
            "description_en": "On by default: just add the bot to a QQ group — on its first message Copree"
                              " attaches a landing by itself. If another bot of the same owner already serves"
                              " a QQ group with the same name, the same Copree group is reused (and this AI is"
                              " added to it); otherwise a dedicated Copree group is created. Groups you mapped"
                              " by hand are left alone. Works even with no landing group above; turn this off"
                              " for DM-only.",
            "description_ja": "既定でオン：QQ でボットをグループに入れるだけで、初回発言時に Copree が落点を"
                              "用意します。同じオーナーの別のボットが**同名**の QQ グループを既に接続していれば"
                              "同じ Copree グループを再利用し（この AI も参加させます）、無ければ専用グループを"
                              "新規作成します。手動で落点を指定したグループは変更しません。上の落点グループが"
                              "空でも接続します。DM 専用にしたいときはオフにしてください。",
        },
        "body_format": {
            "section": _SEC_REPLY,
            "type": "string", "title": "正文格式",
            "title_en": "Body format", "title_ja": "本文の形式",
            "description": "默认：先按 Markdown 发，机器人没权限时自动降级纯文本；"
                           "也可以固定成纯文本（有些不渲染 Markdown 的客户端）或固定成 Markdown。"
                           "注意：腾讯的纯文本消息**渲染不了内联 @**（实测两种写法都显示成尖括号原文），"
                           "所以纯文本模式下 @ 会退成 @名字（看得懂但不会提醒对方）",
            "description_en": "Default: try Markdown first and fall back to plain text; "
                              "or pin plain text / Markdown",
            "description_ja": "既定：まず Markdown、権限が無ければ自動でプレーンテキスト。"
                              "プレーン／Markdown に固定も可",
            "options": [
                {"value": "", "label": "默认（Markdown 优先，没权限自动降级）",
                 "label_en": "Default (Markdown first, fall back to text)",
                 "label_ja": "既定（まず Markdown、権限が無ければテキスト）"},
                {"value": "plain", "label": "纯文本", "label_en": "Plain text",
                 "label_ja": "プレーンテキスト"},
                {"value": "markdown", "label": "Markdown", "label_en": "Markdown",
                 "label_ja": "Markdown"},
            ],
        },
        "quota_reset_enabled": {
            "section": _SEC_QUOTA,
            "type": "boolean", "title": "额度自动重置", "default": False,
            "inline": {
                "key": "quota_reset",
                "label": "额度自动重置",
                "label_en": "Budget reset",
                "label_ja": "上限の自動リセット",
                "hint": "开启后按「多久 + 几点」换窗口：新窗口从 0 开始算，旧窗口的数留在账里；"
                        "关着就一直累计，直到手动重置。窗口是算出来的，不需要定时任务，重启也不丢账。",
                "hint_en": "When on, the window rolls over per the period/time on the right: a new window "
                           "starts from zero and old numbers stay on record. When off it accumulates until "
                           "you reset manually. Windows are computed, so restarts change nothing.",
                "hint_ja": "オンにすると右の周期・時刻で窓が切り替わり、新しい窓は 0 から数えます（旧い数は記録に残ります）。"
                           "オフなら手動リセットまで累積します。窓は計算で決まるため再起動しても失いません。",
            },
            "title_en": "Reset the budget automatically",
            "title_ja": "上限を自動でリセット",
            "description": "开启后按下面的周期与时间点「换窗口」：新窗口从 0 开始算，旧窗口的数留在账里。"
                           "窗口是算出来的，不需要定时任务，重启也不丢账；关着就一直累计，直到手动重置。",
            "description_en": "When on, the budget window rolls over at the period/time below: a new window "
                              "starts from zero and the old numbers stay on record. Windows are computed, "
                              "not scheduled, so restarts change nothing; when off it accumulates until you "
                              "reset manually.",
            "description_ja": "オンにすると下の周期と時刻で窓が切り替わり、新しい窓は 0 から数えます（旧い数は記録として残ります）。"
                              "窓は計算で決まるため再起動しても失いません。オフなら手動リセットまで累積します。",
        },
        "quota_reset_period": {
            "section": _SEC_QUOTA,
            "type": "string", "title": "多久重置一次", "default": "day",
            "inline": {"key": "quota_reset"},
            "show_if": {"field": "quota_reset_enabled", "equals": "true"},
            "title_en": "How often to reset", "title_ja": "リセットの間隔",
            "description": "每天 / 每 2 天 / 每周都以周一为基准；每小时只认「几点重置」里的分钟。",
            "description_en": "Daily, every 2 days and weekly are anchored on Monday; hourly only uses the "
                              "minute of the reset time.",
            "description_ja": "毎日・2 日ごと・毎週は月曜基準です。毎時は「リセット時刻」の分だけを使います。",
            "options": [
                {"value": "hour", "label": "每小时", "label_en": "Every hour", "label_ja": "1 時間ごと"},
                {"value": "day", "label": "每天", "label_en": "Every day", "label_ja": "毎日"},
                {"value": "2days", "label": "每 2 天", "label_en": "Every 2 days", "label_ja": "2 日ごと"},
                {"value": "week", "label": "每周（周一）", "label_en": "Every week (Monday)",
                 "label_ja": "毎週（月曜）"},
            ],
        },
        "quota_reset_anchor": {
            "section": _SEC_QUOTA,
            "type": "string", "title": "几点重置（HH:MM）", "default": "00:00",
            "inline": {"key": "quota_reset"},
            "title_en": "Reset at (HH:MM)", "title_ja": "リセット時刻（HH:MM）",
            "show_if": [
                {"field": "quota_reset_enabled", "equals": "true"},
                {"field": "quota_reset_period", "in": ["day", "2days", "week"]},
            ],
            "description": "只在「每天 / 每 2 天 / 每周」时用得上（按小时是整点换窗口）。"
                           "按平台展示时区解释，每周固定以周一为基准。",
            "description_en": "Only used for daily / every 2 days / weekly (hourly rolls over on the hour). "
                              "Interpreted in the platform display timezone; weekly is anchored on Monday.",
            "description_ja": "「毎日 / 2 日ごと / 毎週」のときだけ使います（毎時は正時に切り替わります）。"
                              "プラットフォームの表示タイムゾーンで解釈し、毎週は月曜基準です。",
        },
        "quota_meter_mode": {
            "section": _SEC_QUOTA,
            "type": "string", "title": "对话额度按什么算",
            "title_en": "How conversation budget is metered",
            "title_ja": "会話ごとの上限をどう数えるか",
            "description": "每个 QQ 群 / 每个私聊单独记账。默认按交涉 token（只要真花钱的部分："
                           "未命中的输入 + 输出）；也可以按全部 token（含缓存命中）。",
            "description_en": "Each QQ group and each DM is metered separately. Default counts billable "
                              "tokens only (uncached input + output); alternatively count every token "
                              "(including cache hits).",
            "description_ja": "QQ グループと DM をそれぞれ個別に計上します。既定は課金対象トークン"
                              "（未キャッシュの入力＋出力）。キャッシュ命中を含む全トークンでも数えられます。",
            "default": "billable",
            "options": [
                {"value": "billable", "label": "交涉 token（未命中输入 + 输出，默认）",
                 "label_en": "Billable (uncached input + output)",
                 "label_ja": "課金対象（未キャッシュ入力＋出力）"},
                {"value": "total", "label": "全部 token（含缓存命中）",
                 "label_en": "All tokens (including cache hits)",
                 "label_ja": "全トークン（キャッシュ命中を含む）"},
            ],
        },
        "quota_limit_tokens": {
            "section": _SEC_QUOTA,
            "type": "string", "title": "每个对话的默认额度（token）",
            "title_en": "Default budget per conversation (tokens)",
            "title_ja": "会話ごとの既定上限（トークン）",
            "description": "留空 = 1000000（100 万）。单个对话可以在下面的「对话额度」里单独调整；"
                           "用完之后这条通道不再把消息交给 AI（可选自动回一句说明）。",
            "description_en": "Empty = 1,000,000. A single conversation can be adjusted below; once the "
                              "budget is used up this channel stops forwarding messages (optionally with "
                              "an auto reply).",
            "description_ja": "空欄＝1,000,000。会話ごとに下で個別調整できます。使い切るとこのチャンネルは"
                              "メッセージを AI に渡さなくなります（任意で自動返信）。",
            "default": "1000000",
        },
        "quota_blocked_reply": {
            "section": _SEC_QUOTA,
            "type": "boolean", "title": "额度用完后自动回一句", "default": True,
            "title_en": "Reply automatically when the budget is used up",
            "title_ja": "上限に達したら自動で返信する",
            "description": "默认开启：同一个对话被拦下时回一句说明（10 分钟内最多一次，免得刷屏）。"
                           "关掉就完全静默。",
            "description_en": "On by default: when a conversation is blocked, reply once (at most once "
                              "per 10 minutes). Turn it off for complete silence.",
            "description_ja": "既定でオン：ブロック時に一度だけ説明を返します（10 分に 1 回まで）。"
                              "オフにすると完全に無言になります。",
        },
        "quota_blocked_reply_text": {
            "section": _SEC_QUOTA,
            "type": "string", "title": "自动回复内容",
            "title_en": "Auto reply text", "title_ja": "自動返信の文面",
            "description": "留空 = 平台默认的官方口吻那一句。",
            "description_en": "Empty = the platform's default wording.",
            "description_ja": "空欄＝プラットフォーム既定の文面。",
        },
        "quote_replies": {
            "section": _SEC_REPLY,
            "type": "boolean", "title": "引用回复",
            "title_en": "Quote the replied message", "title_ja": "引用返信",
            "description": "回复群里某条消息时以引用形式展示（官方 message_reference）。"
                           "它是**独立维度**：跟上面选哪种正文格式都能叠",
            "description_en": "Show the replied message as a quote (official message_reference). "
                              "Independent from the body format above",
            "description_ja": "返信時に対象メッセージを引用表示（公式 message_reference）。"
                              "本文の形式とは独立して設定できます",
        },
        "dm_policy": {
            "section": _SEC_DM,
            "type": "string", "title": "私聊策略（pairing / owner / open / off）",
            "description": "默认 pairing：陌生人私聊只会收到一个配对码，批准之后才能跟 AI 说话；"
                           "owner 只认已配对的人（陌生人静默忽略）；open 谁都能聊；off 关闭私聊",
        },
    },
)
