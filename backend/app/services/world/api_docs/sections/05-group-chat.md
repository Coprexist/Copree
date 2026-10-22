# 05 群聊 API（世界 AI 的群工具）
> 读消息/发消息/成员列表/角色管理/踢人工具：参数、返回、身份与权限约定（默认作用于本世界绑定群）。

## 1. 身份与权限模型（先读这个）

- 所有群聊工具**以世界创建者（owner）身份执行**，不是以与你对话的用户身份——你代表世界说话。
- **权限按群聊角色体系**：`owner`（群主）> `admin`（管理员）> `member`（普通成员）。
  - 改角色/踢人：**仅群主（owner）可操作**（`set_group_member_role` 明确要求群主；`kick_group_member` 群主/管理员）。
- **默认绑定群原则**：所有工具默认作用于**本世界绑定的群**（一个世界可绑多个群时取第一个），**不需要也不应传群号**；显式传 `group_id` 可指定。
- 成员 id **一律以 `list_group_members` 返回为准**，不要假设、不要硬编码。

## 2. 工具详解

### 2.1 get_bound_groups — 查绑定群

```
→ { "success": true, "groups": [
    { "group_id": 12, "name": "五子棋排位赛", "is_paused": false, "member_count": 8, "created_at": "…" } ] }
```

用于了解本世界有哪些群聊入口、是否暂停。**对话开始时建议先查**，掌握自己的"地盘"。

### 2.2 get_group_messages — 读最近消息

```json
{ "group_id": 12, "limit": 20 }        // 均可选；group_id 默认绑定群，limit 默认 20 最大 50
```

```
→ { "success": true, "messages": [
    { "id": 101, "sender_type": "human", "sender_id": 5, "sender_name": "产品", "content": "…", "created_at": "…" } ] }
```

- 含发送者名字，了解群里最近聊了什么。**想参与群聊前必读**，避免重复或答非所问。

### 2.3 list_group_members — 列成员

```
→ { "success": true, "members": [
    { "type": "human", "id": 5, "name": "产品", "role": "owner" },
    { "type": "ai",    "id": 3, "name": "小助手", "role": "member" } ] }
```

- `role`：`owner / admin / member`。**管理操作前先查**，确认身份与角色。

### 2.4 send_group_message — 群里发言

```json
{ "content": "大家好，欢迎来到我的世界！" }     // group_id 可选
```

```
→ { "success": true, "message_id": 102 }
```

- 以本群**群助手**的身份发送（群里显示的发言人就是它，如「残响记录员」），实时广播给群成员；
  不是群主、也不是任何一位群成员——世界的话记在世界名下。
- **消息内容不能为空**；内容宜口语化、有世界风格（你在"扮演"这个世界）。

### 2.5 set_group_member_role — 改角色（仅群主）

```json
{ "member_type": "human", "member_id": 5, "role": "admin" }
```

```
→ { "success": true, "member_id": 5, "role": "admin" }
```

- `member_type`: `human | ai`；`role`: `admin | member`。
- **仅群主可操作**，否则返回错误。

### 2.6 kick_group_member — 踢人（群主/管理员）

```json
{ "member_type": "human", "member_id": 5 }
```

```
→ { "success": true, "member_id": 5 }
```

- 群主/管理员可操作；**不能踢自己**；不能踢群主。

## 3. 行为约定

1. **先读后写**：发消息前先 `get_group_messages` 看上下文；管理操作前先 `list_group_members` 确认身份。
2. **别把工具调用写进回复文本**：调用了工具就正常说话，不要说"我调用了 send_group_message"。
3. **群号不用管**：默认绑定群，直接说目的即可。
4. 工具结果 `success: false` 时附带 `error` 字段，根据错误信息调整（见 08 分区）。

## 4. 安全约定

- 写操作以本群群助手（世界的代言身份）执行，**不可伪造**他人身份。
- 管理类操作（改角色/踢人）仅群主/管理员可调用，接口层已强制校验。