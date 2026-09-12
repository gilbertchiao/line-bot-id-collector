# LINE Webhook ID Collector — 產品需求文件（PRD）

- 版本：1.0
- 日期：2026-09-12
- 狀態：已確認，進入實作

---

## 1. 概述

**Repository：** `line-webhook-id-collector`

**一句話描述：**

> 一個輕量的 serverless LINE Messaging API webhook，用來收集 userId、groupId、roomId，並讓開發者透過 1:1 對話查詢已收集的 ID 清單。支援多個 LINE Bot 共用同一組部署。

LINE Messaging API 發送 push message 時需要指定目的地 ID：

- `userId`：一對一使用者
- `groupId`：群組
- `roomId`：多人聊天室

除了開發者自己的 userId 可在 LINE Developers Console 看到之外，其他人的 userId 與群組的 groupId 在實務上只能從 webhook 事件中得知。開發者在使用者加 Bot 好友、或 Bot 被邀進群組後，通常沒有簡單的方法知道對應的 ID。

本專案提供一個最小化的 serverless webhook 接收器，自動發現並儲存這些 ID，並提供開發者專用的文字指令查詢清單。

本專案刻意**不提供**任何 Web 管理介面。

**適用情境：** 本專案是一個**獨立的收集器 Bot**。每個 LINE channel 只能設定一個 webhook URL，若將既有 Bot 的 webhook 改指向本專案，原後端就收不到事件。既有 Bot 若要收集 ID，應將收集邏輯整合進原後端，而非使用本專案。

---

## 2. 目標

系統 SHALL：

1. 接收 LINE Messaging API webhook 事件。
2. 驗證 webhook 請求確實來自 LINE。
3. 支援多個 LINE Bot，各 Bot 以 webhook URL 的 path 區分。
4. 從事件中抽取 `userId`、`groupId`、`roomId`。
5. 將發現的 ID 以 Bot 為單位儲存至 DynamoDB。
6. 自動去重，重複或重送的事件不產生重複記錄，也不使狀態倒退。
7. 記錄每個 ID 首次與最近一次被觀察到的時間。
8. 在使用者封鎖 Bot 或 Bot 離開群組時，將對應 ID 標記為失效。
9. 對一般使用者與群組**完全靜默**，不做任何回覆。
10. 僅對「開發者」在 1:1 對話中的特定指令回覆，包含查詢自己的 ID、列出已收集的 ID 清單、管理開發者名單。
11. 可用 AWS SAM 部署至 API Gateway HTTP API、AWS Lambda、DynamoDB、Secrets Manager。
12. 不需要任何 Web 管理介面。
13. 提供文件說明如何用 AWS CLI 直接查詢與刪除已收集的 ID。

實作規模應維持在「讀少數幾個檔案就能理解並部署」的程度。

**可靠性承諾：** 本專案是「盡力收集」。LINE webhook 重送需在 LINE Developers Console 手動啟用，且啟用後也不保證必達；本專案不做持久化的事件去重與離線補收。

---

## 3. 非目標

以下功能明確**不在**本版範圍內：

- 管理後台、Web 前端、任何 UI
- 使用者認證系統、Cognito
- CRUD 管理 API、公開查詢 API
- 聯絡人管理、LINE CRM 功能
- 訊息廣播 UI、排程訊息
- 訊息歷史儲存、對話記錄
- 分析儀表板
- 使用者 profile 或群組成員的**持久化**同步（本專案僅在 `/list` 時即時查詢名稱，不落地儲存）
- 持久化的 webhook 事件去重（以 `webhookEventId` 為 key）
- 自動清理過期 target
- Rich menu、LIFF
- SQS / SNS / EventBridge / Step Functions 等非同步架構
- 多租戶 SaaS（多 Bot 是同一個擁有者的多個 Bot，不是多租戶）
- 群組內的任何指令回應

除非未來有獨立 issue 要求，否則不要引入這些元件。

---

## 4. 目標架構

```text
LINE Platform (Bot A) ──POST /webhook/alert-bot──┐
LINE Platform (Bot B) ──POST /webhook/ops-bot ───┤
                                                  ▼
                                   ┌──────────────────────────────┐
                                   │ API Gateway HTTP API          │
                                   │ POST /webhook/{bot_id}        │
                                   └───────────────┬──────────────┘
                                                   ▼
                                   ┌──────────────────────────────┐
                                   │ AWS Lambda                    │
                                   │ 1. 取得 bot_id、檢查簽章標頭  │
                                   │ 2. 讀取該 Bot 的 secret（快取）│
                                   │ 3. 以 raw body 驗證簽章       │
                                   │ 4. 逐一處理 events（先收集）  │
                                   │    - upsert target            │
                                   │    - unfollow/leave → inactive│
                                   │ 5. 最後執行指令回覆（若有）   │
                                   └────┬──────────┬──────────┬───┘
                                        ▼          ▼          ▼
                              Secrets Manager   DynamoDB    LINE Messaging API
                              (每 Bot 一個 secret) (targets)  (reply / profile / group summary)
```

使用 **API Gateway HTTP API**，不使用 REST API，因為本專案只需要簡單的 Lambda proxy integration。

---

## 5. Webhook 端點

服務只公開一個應用端點：

```text
POST /webhook/{bot_id}
```

- `bot_id` 為部署者自訂的短代號，僅允許 `[a-z0-9-]`，長度 1 到 64 字元。範例：`alert-bot`、`ops-bot`。
- `bot_id` 同時作為 Secrets Manager secret 名稱的後綴與 DynamoDB 的 partition key。
- 不需要公開的讀取或查詢 API。

Webhook URL 需手動設定於 LINE Developers Console，每個 Bot 各自設定：

```text
https://xxxxxxxx.execute-api.ap-northeast-1.amazonaws.com/webhook/alert-bot
https://xxxxxxxx.execute-api.ap-northeast-1.amazonaws.com/webhook/ops-bot
```

`bot_id` 不符合格式、或對應的 secret 不存在時，回 HTTP 404，不做任何處理。

---

## 6. 憑證管理（Secrets Manager）

每個 Bot 對應一個 AWS Secrets Manager secret，名稱慣例：

```text
line-webhook-id-collector/{bot_id}
```

前綴可由環境變數 `SECRET_NAME_PREFIX` 覆寫。secret 內容為 JSON：

```json
{
  "channel_secret": "LINE Channel Secret",
  "channel_access_token": "LINE Channel Access Token (long-lived)",
  "bootstrap_admin_user_id": "Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

| 欄位 | 必填 | 說明 |
|---|---|---|
| `channel_secret` | 是 | 簽章驗證用。缺少時視為設定錯誤，回 HTTP 500 並記 log |
| `channel_access_token` | 否 | 開發者指令回覆與名稱查詢用。缺少時本 Bot 退化為純收集器，收到指令也靜默 |
| `bootstrap_admin_user_id` | 否 | 種子開發者的 userId，可從 LINE Developers Console「Basic settings → Your user ID」取得。缺少時，本 Bot 沒有任何開發者，直到用 AWS CLI 直接在 DynamoDB 設定 `role=admin`（第 23 節） |

規則：

- Secret 由部署者以 AWS CLI 建立（README 提供指令），SAM template **不**建立 secret，只授予讀取權限。
- 本版限定 secret 使用 Secrets Manager 預設的 AWS managed key（`aws/secretsmanager`）加密。若使用 customer managed KMS key，需自行補上 `kms:Decrypt` 權限，本版不支援。
- 新增 Bot 只需建立新 secret 並在 LINE Developers Console 設定 webhook URL，**不需要重新部署**。
- Lambda 以 `bot_id` 為 key 在全域變數中快取 secret，TTL 由 `SECRET_CACHE_TTL_SECONDS` 控制（預設 300 秒）。輪換 token 或更換 `bootstrap_admin_user_id` 後最多 TTL 秒生效。
- 「secret 不存在」的結果也要快取（負快取），TTL 同上，快取項目上限 100 筆，避免大量隨機 `bot_id` 打爆 Secrets Manager。
- Secret 內容絕不寫入 log、絕不進入 git。

---

## 7. Webhook 安全

每個請求在處理事件前 MUST 驗證 `x-line-signature` 標頭。

驗證方式：

```text
HMAC-SHA256
key     = 該 Bot 的 channel_secret
message = 原始 HTTP request body（逐位元組一致）
```

實作 MUST NOT 在驗證前對 body 做反序列化、正規化、格式化、修改或重新序列化。

處理順序（刻意把不需要 secret 的檢查放在前面，減少未驗證請求造成的 Secrets Manager 呼叫）：

```text
收到請求
  ↓
從 path 取得 bot_id，驗證格式（失敗 → 404）
  ↓
檢查 x-line-signature 標頭存在且為合法 base64（失敗 → 401）
  ↓
讀取 raw body（若 API Gateway 標示 isBase64Encoded，先 base64 解碼還原；解碼失敗 → 400）
  ↓
讀取該 Bot 的 secret（不存在 → 404；缺 channel_secret → 500）
  ↓
驗證簽章（失敗 → 401）
  ↓
解析 JSON（失敗 → 400）
  ↓
處理 events
```

- 簽章無效 MUST NOT 造成任何 DynamoDB 寫入或 LINE API 呼叫。
- 簽章比對使用 constant-time 比較（`hmac.compare_digest`）。
- API Gateway HTTP API 保留預設的帳號層級節流；本版不另設 per-route 節流。

---

## 8. 支援的 LINE 事件

### 8.1 通用規則：target 抽取

**所有**事件（包含不支援的事件類型）都先套用同一條抽取規則：從 `event.source` 中取出存在的 `userId`、`groupId`、`roomId`，逐一 upsert 為 `status = active`。事件缺少 `source`（例如失敗的 `accountLink`）或 `source` 非物件時，跳過抽取，不視為錯誤。

抽取之後，再依事件類型決定是否有**額外動作**。

### 8.2 `follow`

使用者加 Bot 好友。無額外動作（抽取規則已儲存 `userId`）。

### 8.3 `unfollow`

使用者封鎖 Bot。額外動作：將 `userId` 標記 `status = inactive`。此事件**不**套用 8.1 的 active upsert。

### 8.4 `join`

Bot 加入群組或聊天室。無額外動作。**不做任何回覆。**

### 8.5 `leave`

Bot 被移出群組或聊天室。額外動作：將 `groupId` 或 `roomId` 標記 `status = inactive`。此事件**不**套用 8.1 的 active upsert。

### 8.6 `memberJoined`

新成員加入 Bot 所在的群組或聊天室。額外動作：`joined.members[]` 內每個 `userId` 各自 upsert。

### 8.7 `message`

- `source.type = user`：若訊息為 text、發送者為開發者、且非重送事件，進入指令處理（第 11 節）。
- `source.type = group` 或 `room`：無額外動作。**永不回覆。**

### 8.8 其他事件

`postback`、`beacon`、`videoPlayComplete`、`unsend`、`memberLeft`、`accountLink`、`things` 等：只套用 8.1 的抽取規則，無額外動作，記 log `ignored`，回 HTTP 200。

### 8.9 重送事件

`event.deliveryContext.isRedelivery = true` 的事件：套用 8.1 抽取與 8.3、8.5 的狀態標記，但**不執行指令**。重送的 reply token 已失效，且執行舊的管理指令可能恢復已撤銷的權限。

---

## 9. 請求格式與多事件

- Body MUST 為 JSON 物件且 `events` 為陣列，否則回 400。
- `events` 內非物件的元素、或缺少 `type` 的元素，跳過並記 log，不影響其他事件。
- 單一 webhook 請求可能包含多個事件，MUST 以集合方式處理，不可假設只有 `events[0]`。
- **逐事件捕捉例外**：一個事件的處理失敗（含 DynamoDB 錯誤）記 log 後繼續處理下一個事件。全部處理完後，若有任何事件的 DynamoDB 寫入失敗，回 500；否則回 200。
- LINE Developers Console 的 webhook 驗證會送 `{"destination": "...", "events": []}`，這是合法請求：驗簽、接受空陣列、回 200、不寫 DynamoDB。

---

## 10. DynamoDB 資料模型

單一 table，建議名稱 `line-webhook-targets`，on-demand 計費。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `bot_id` | String, **Partition Key** | webhook path 中的 Bot 代號 |
| `target_id` | String, **Sort Key** | LINE userId / groupId / roomId |
| `target_type` | String | `user`、`group`、`room` |
| `status` | String | `active`、`inactive`。**僅代表觀察狀態**，不代表可推播，也不影響開發者授權 |
| `role` | String，選填 | 僅開發者記錄有此欄位，值固定為 `admin` |
| `first_seen_at` | String | 首次觀察到的**事件時間**，UTC ISO 8601，僅首次建立時寫入 |
| `last_seen_at` | String | 最近一次觀察到的**事件時間**，UTC ISO 8601 |
| `last_event_ts` | Number | `last_seen_at` 對應的 LINE 事件 `timestamp`（毫秒 epoch），用於條件更新 |
| `last_event_type` | String | 最近一次暴露此 ID 的事件類型 |

事件時間取自 LINE 事件的 `timestamp` 欄位（毫秒 epoch）。事件缺少 `timestamp` 時，以接收時間代替。

`status`、`role` 是 DynamoDB 保留字，所有 expression MUST 透過 `ExpressionAttributeNames`（`#status`、`#role`）引用。

範例：

```json
{
  "bot_id": "alert-bot",
  "target_id": "C1234567890abcdef1234567890abcdef",
  "target_type": "group",
  "status": "active",
  "first_seen_at": "2026-09-12T06:30:00Z",
  "last_seen_at": "2026-09-12T06:35:10Z",
  "last_event_ts": 1789194910000,
  "last_event_type": "message"
}
```

```json
{
  "bot_id": "alert-bot",
  "target_id": "U1234567890abcdef1234567890abcdef",
  "target_type": "user",
  "status": "active",
  "role": "admin",
  "first_seen_at": "2026-09-12T06:30:00Z",
  "last_seen_at": "2026-09-12T06:35:10Z",
  "last_event_ts": 1789194910000,
  "last_event_type": "message"
}
```

### 10.1 Upsert 行為（觀察事件）

所有觀察寫入一律使用 `UpdateItem`，不做 read → check → write：

```text
SET target_type     = :type,
    #status         = :status,
    last_seen_at    = :event_time,
    last_event_ts   = :event_ts,
    last_event_type = :event_type,
    first_seen_at   = if_not_exists(first_seen_at, :event_time)
ConditionExpression:
    attribute_not_exists(last_event_ts) OR last_event_ts <= :event_ts
```

- ID 不存在：`first_seen_at = last_seen_at = 事件時間`。
- ID 已存在且事件較新或同時：`first_seen_at` 不變，更新 `last_seen_at`、`status`、`last_event_type`。
- 事件比已記錄的更舊（亂序或重送）：`ConditionalCheckFailedException`，視為 `stale` 並忽略，不算錯誤。這防止舊的 `message` 覆寫新的 `unfollow`。
- `role` 欄位不在 upsert 表達式內，觀察事件永不覆蓋或移除開發者身分。

### 10.2 狀態轉換

- `unfollow`、`leave`：同 10.1 的 `UpdateItem`，`:status = inactive`，同樣受條件表達式保護。
- 之後收到該 ID 較新的任何事件：`status` 回到 `active`。
- 不使用 `DeleteItem`。手動刪除見第 23 節。

### 10.3 查詢

`/list` 使用 `Query` 以 `bot_id` 為條件，MUST 追蹤 `LastEvaluatedKey` 直到讀完所有分頁，再於程式內依 `status` 與 `target_type` 過濾、依 `last_event_ts` 排序。不建立 GSI。

---

## 11. 開發者指令

### 11.1 開發者判定

訊息發送者符合以下任一條件即為開發者：

1. `userId` 等於該 Bot secret 中的 `bootstrap_admin_user_id`（種子開發者）。
2. DynamoDB 中 `(bot_id, userId)` 記錄存在且 `role = admin`。

判定用的 `GetItem` MUST 使用 `ConsistentRead = True`，確保 `/admin remove` 後下一個請求立即生效。

`status` 不影響授權。撤銷授權的唯一方式是 `/admin remove` 或更換 secret 中的 `bootstrap_admin_user_id`。

### 11.2 觸發條件

指令僅在**全部**滿足以下條件時才處理：

- 事件類型為 `message`，訊息類型為 `text`
- `source.type = user`（1:1 對話）
- `deliveryContext.isRedelivery` 不為 `true`
- 發送者為開發者
- 該 Bot 的 secret 含 `channel_access_token`

群組與聊天室內的任何訊息，即使來自開發者、即使內容是指令，一律靜默。

非開發者在 1:1 對話中的任何訊息一律靜默。

### 11.3 執行順序

單一 webhook 內若含指令事件，MUST 先完成該 webhook 內**所有**事件的 ID 收集，再執行指令與回覆。收集是主要職責，指令是附加功能。

### 11.4 指令比對

- 去除頭尾空白，多個空白視為一個分隔符。
- **指令關鍵字**大小寫不敏感（`/ID`、`/List Groups` 皆可）；**ID 參數**保留原值，不做大小寫轉換。
- 第一個 token 不是已知指令：靜默。
- 已知指令但參數數量錯誤或 ID 格式錯誤：回覆錯誤訊息（發送者已是開發者，給予回饋是安全的）。
- userId 格式：`U` 開頭加 32 個小寫十六進位字元。

### 11.5 指令清單

| 指令 | 行為 |
|---|---|
| `/id` | 回覆開發者自己的 userId |
| `/list` | 列出該 Bot 所有 `status = active` 的 target，含名稱 |
| `/list groups` | 只列 group |
| `/list users` | 只列 user |
| `/list rooms` | 只列 room |
| `/list all` | 列出全部 target，含 `inactive`，並標註狀態 |
| `/admin list` | 列出該 Bot 的開發者（secret 的種子開發者與 DB 中 `role = admin` 的聯集，去重），顯示名稱與 userId，種子開發者標註 `(bootstrap)` |
| `/admin add Uxxxx` | 見 11.6 |
| `/admin remove Uxxxx` | 見 11.6 |
| `/help` | 列出以上指令與一行說明 |

### 11.6 `/admin add` 與 `/admin remove` 的細節

`/admin add Uxxxx`，單次原子 `UpdateItem`：

```text
SET #role = :admin,
    target_type   = if_not_exists(target_type, :user),
    #status       = if_not_exists(#status, :active),
    first_seen_at = if_not_exists(first_seen_at, :now),
    last_seen_at  = if_not_exists(last_seen_at, :now),
    last_event_ts = if_not_exists(last_event_ts, :now_ts),
    last_event_type = if_not_exists(last_event_type, :admin_add)
```

- 記錄不存在時建立完整記錄，`last_event_type = admin_add` 表示此記錄來自人工授權而非 webhook 觀察。
- 記錄已存在時只加 `role`，不改動觀察欄位。
- 目標為種子開發者：不寫入，回覆 `Already bootstrap admin.`。
- 目標已是 admin：不寫入，回覆 `Already admin: Uxxxx`。
- 成功回覆 `Added admin: Uxxxx`。

`/admin remove Uxxxx`，單次原子 `UpdateItem`：

```text
REMOVE #role
ConditionExpression: attribute_exists(target_id) AND #role = :admin
```

- 目標為種子開發者：拒絕，回覆 `Cannot remove bootstrap admin.`。
- 目標為自己：拒絕，回覆 `Cannot remove yourself.`。
- 條件失敗（記錄不存在或不是 admin）：回覆 `Not an admin: Uxxxx`，不建立空記錄。
- 成功回覆 `Removed admin: Uxxxx`。

更換 secret 中的 `bootstrap_admin_user_id` 不會自動清除舊種子開發者在 DB 中可能存在的 `role`（若曾被 `/admin add` 加入）。README 說明更換種子時應一併檢查 `/admin list`。

### 11.7 回覆格式

`/id`：

```text
LINE Target

Type: user
User ID:
Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

`/list`（依類型分區，每區依 `last_event_ts` 新到舊；名稱查不到顯示 `(unknown)`）：

```text
Groups (2)
- 專案 A 討論群
  C1234567890abcdef1234567890abcdef
- (unknown)
  C2234567890abcdef1234567890abcdef

Rooms (1)
- R1234567890abcdef1234567890abcdef

Users (3)
- 王小明
  U1234567890abcdef1234567890abcdef
- (unknown)
  U2234567890abcdef1234567890abcdef
- 李小華 [admin]
  U3234567890abcdef1234567890abcdef
```

`/list all` 在 `inactive` 記錄的名稱後加 `[inactive]`。

空清單回覆 `No targets found.`

**注意：** `status = active` 只代表「曾在事件中觀察到」，不保證可以 push。只在群組中觀察到、從未加 Bot 好友的使用者，push 會失敗。README 需說明此限制。

### 11.8 名稱查詢

- Group：`GET /v2/bot/group/{groupId}/summary` 取 `groupName`。
- User：`GET /v2/bot/profile/{userId}` 取 `displayName`。此 API 只對「已加 Bot 好友」的使用者有效；只在群組中觀察到的使用者需要群組成員 profile API，但本專案不記錄使用者與群組的關聯，因此這類使用者固定顯示 `(unknown)`。這是已知限制，README 需說明。
- Room：LINE 不提供名稱 API，只顯示 ID。
- 名稱僅供即時顯示，**不寫入 DynamoDB**。
- 任一查詢失敗顯示 `(unknown)`，不影響其他項目，不讓整個指令失敗。
- 逐筆循序查詢，不做並行。每次 HTTP 呼叫 connect timeout 2 秒、read timeout 3 秒、**不重試**。
- 名稱查詢筆數上限由 `NAME_LOOKUP_LIMIT` 控制（預設 50）。超過上限的項目直接顯示 `(unknown)`，不再查詢。
- 整體時間預算：名稱查詢累計超過 `NAME_LOOKUP_BUDGET_SECONDS`（預設 8 秒）即停止查詢，剩餘項目顯示 `(unknown)`，確保 Lambda 15 秒 timeout 內能完成回覆。

### 11.9 訊息長度限制

- LINE Reply API 單次最多 5 則訊息，每則文字最多 5000 字，長度以 **UTF-16 code unit** 計算，實作 MUST 依此計算而非 Python `len()`。
- 回覆內容超過上限時，以「項目」為單位切段，不在項目中間切斷，最多 5 則。
- 所有分段 MUST 在**一次** Reply API 呼叫中送出（reply token 只能用一次）。
- 5 則仍放不下時，在最後一則結尾加註提示。提示長度 MUST 納入分段計算，並依實際設定產生：

```text
... and N more. Use:
aws dynamodb query --table-name {TABLE} --key-condition-expression "bot_id = :b" --expression-attribute-values '{":b":{"S":"{BOT_ID}"}}'
```

---

## 12. 日誌

使用 Python 內建 `logging`，輸出至 CloudWatch Logs，每筆一行 JSON。

必要欄位：

```text
request_id, bot_id, event_type, source_type, target_type, result
```

`result` 範例值：`stored`、`marked_inactive`、`stale`、`ignored`、`replied`、`reply_failed`、`invalid_signature`、`unknown_bot`、`config_error`。

規則：

- `target_id` 與**指令中的 ID 參數**僅在 `LOG_TARGET_IDS=true` 時輸出，預設 `false`。
- 指令處理時記錄指令關鍵字（`/list`、`/admin add`），不記錄非指令訊息的內容。
- 絕不輸出：Channel Secret、Channel Access Token、完整 raw body、使用者訊息內容。
- `line-bot-sdk` 與 `boto3` 的 debug log 一律關閉（固定 `WARNING` 以上），避免 HTTP request / response 內容外洩；`LOG_LEVEL` 只影響本專案自己的 logger。
- 例外堆疊可記錄，但 MUST 不含 body 或 secret 內容。

---

## 13. 錯誤處理與 HTTP 回應碼

| 情境 | 回應 | 副作用 |
|---|---|---|
| `bot_id` 格式錯誤 | 404 | 無，不讀 secret |
| 缺少 `x-line-signature` 或非合法 base64 | 401 | 無，不讀 secret |
| base64 body 解碼失敗 | 400 | 無 |
| secret 不存在 | 404 | 記 log `unknown_bot` |
| secret 缺 `channel_secret` 或非合法 JSON | 500 | 記 log `config_error` |
| Secrets Manager 讀取失敗（非 NotFound） | 500 | 記 log |
| 簽章錯誤 | 401 | 無，不寫 DB、不呼叫 LINE |
| Body 非合法 JSON、非物件、`events` 非陣列 | 400 | 記 log |
| 空 `events` | 200 | 無 |
| 事件非物件或缺 `type` | 200 | 跳過該事件，記 log |
| 不支援的事件類型 | 200 | 套用抽取規則，記 log `ignored` |
| 缺少選填 ID | 200 | 儲存有的 ID |
| 事件較舊（條件更新失敗） | 200 | 記 log `stale` |
| 任一事件 DynamoDB 寫入失敗 | 500 | 其他事件照常處理；記 log。Lambda MUST 明確回傳 `statusCode: 500`，不可拋出未捕捉例外（那會變成 API Gateway 的 502） |
| 指令回覆失敗（LINE API 錯誤、token 無效） | 200 | 記 log `reply_failed`；ID 已儲存 |
| 名稱查詢失敗或逾時 | 200 | 該項顯示 `(unknown)` |

原則：**ID 收集是主要職責，指令回覆是附加功能。** 收集成功但回覆失敗不算失敗；收集失敗回 500，讓已啟用重送的 LINE 有機會重送。

---

## 14. 效能

- 處理路徑：Webhook → Lambda → DynamoDB → 200，無任何佇列或非同步元件。
- Lambda 設定：記憶體 256 MB，timeout 15 秒。一般事件在 1 秒內完成；15 秒與第 11.8 節的查詢上限、時間預算共同確保 `/list` 不會被 timeout 中斷。
- Secret 快取（含負快取）避免每次請求都讀 Secrets Manager。
- 開發者判定需要一次 consistent `GetItem`，僅在 `source.type = user`、訊息為 text、非重送時執行。

---

## 15. 設定

Lambda 環境變數（由 SAM parameter 傳入）：

| 變數 | 必填 | 預設 | 說明 |
|---|---|---|---|
| `DYNAMODB_TABLE_NAME` | 是 | 由 template 自動帶入 | targets table 名稱 |
| `SECRET_NAME_PREFIX` | 否 | `line-webhook-id-collector/` | Secrets Manager secret 名稱前綴 |
| `SECRET_CACHE_TTL_SECONDS` | 否 | `300` | secret 快取秒數（含負快取） |
| `NAME_LOOKUP_LIMIT` | 否 | `50` | `/list` 名稱查詢筆數上限 |
| `NAME_LOOKUP_BUDGET_SECONDS` | 否 | `8` | `/list` 名稱查詢累計時間預算 |
| `LOG_LEVEL` | 否 | `INFO` | 本專案 logger 的等級 |
| `LOG_TARGET_IDS` | 否 | `false` | 是否在 log 中輸出 target ID 與指令 ID 參數 |

LINE 相關憑證一律放 Secrets Manager，不放環境變數。

---

## 16. IAM 最小權限

Lambda execution role 僅授予：

| 動作 | 資源 |
|---|---|
| `dynamodb:UpdateItem` | 僅 targets table |
| `dynamodb:Query` | 僅 targets table |
| `dynamodb:GetItem` | 僅 targets table |
| `secretsmanager:GetSecretValue` | 僅 `arn:aws:secretsmanager:{region}:{account}:secret:{SECRET_NAME_PREFIX}*` |
| CloudWatch Logs 基本權限 | SAM 預設 |

不授予 `dynamodb:*`、`dynamodb:Scan`、`dynamodb:DeleteItem`、`dynamodb:PutItem`、`secretsmanager:*`、`kms:*`、`*`。

---

## 17. 基礎設施即程式碼（AWS SAM）

`template.yaml` 負責建立：

- API Gateway HTTP API，路由 `POST /webhook/{bot_id}`
- Lambda function（Python 3.14 runtime、256 MB、15 秒 timeout）
- DynamoDB table（`bot_id` + `target_id` 複合主鍵，on-demand）
- IAM policy（第 16 節）
- CloudWatch Log Group（保留 30 天，可用 parameter 調整）

SAM parameters：

```text
TableName                 預設 line-webhook-targets
SecretNamePrefix          預設 line-webhook-id-collector/
SecretCacheTtlSeconds     預設 300
NameLookupLimit           預設 50
NameLookupBudgetSeconds   預設 8
LogLevel                  預設 INFO
LogTargetIds              預設 false
LogRetentionDays          預設 30
```

SAM template **不**建立 Secrets Manager secret，由部署者以 AWS CLI 建立。

Outputs 輸出 webhook base URL 與 table 名稱，方便組出每個 Bot 的完整 URL 與 CLI 指令。

---

## 18. 專案結構

```text
line-webhook-id-collector/
├── README.md
├── LICENSE                      # MIT
├── template.yaml                # AWS SAM
├── pyproject.toml               # uv 管理，requires-python >= 3.14
├── uv.lock
├── .gitignore
├── src/
│   └── line_webhook_id_collector/
│       ├── __init__.py
│       ├── handler.py           # Lambda 進入點：取 bot_id、驗簽、解析、分派、彙總結果、組回應
│       ├── config.py            # 讀環境變數
│       ├── secrets.py           # Secrets Manager 讀取與 TTL 快取（含負快取）
│       ├── signature.py         # HMAC-SHA256 驗簽，處理 base64 body
│       ├── events.py            # 純函式：從事件抽取 target、判斷事件類型與額外動作
│       ├── repository.py        # DynamoDB：upsert、mark_inactive、add_admin、remove_admin、get、list（分頁）
│       ├── commands.py          # 指令解析與執行，產生回覆文字，UTF-16 長度切段
│       └── line_client.py       # LINE API 封裝：reply、get_profile、get_group_summary（timeout、不重試）
├── tests/
│   ├── fixtures/                # webhook JSON 範例，全部使用假 ID
│   ├── conftest.py
│   ├── test_signature.py
│   ├── test_events.py
│   ├── test_repository.py
│   ├── test_commands.py
│   ├── test_secrets.py
│   └── test_handler.py
├── docs/
│   └── PRD.md
└── .github/
    ├── dependabot.yml
    └── workflows/
        └── test.yml
```

模組依賴方向（只能往下，不回頭）：

```text
handler → commands → repository, line_client
handler → events, signature, secrets, repository, config
```

`events.py` 與 `commands.py` 不直接呼叫 AWS 或 LINE SDK，改由呼叫端注入 repository 與 client 物件，測試時可用 fake 物件替換。

避免過度抽象。

---

## 19. 實作語言與依賴

- Python 3.14（AWS Lambda `python3.14` runtime）
- 套件管理：`uv`
- 執行期依賴：`boto3`（Lambda 內建，仍列於 pyproject 以利本機測試）、`line-bot-sdk`（簽章驗證與 Messaging API）
- 開發依賴：`pytest`、`moto`、`ruff`

簽章驗證優先使用 `line-bot-sdk` 提供的簽章驗證工具，但 MUST 以原始 body 驗證，並有完整單元測試。

---

## 20. 測試需求

使用 `pytest`，所有 fixture 使用假 ID 與測試用 secret，CI 不需要任何真實憑證。

| 檔案 | 覆蓋範圍 |
|---|---|
| `test_signature.py` | 正確簽章、錯誤簽章、缺少簽章、簽章非 base64、body 被修改、base64 編碼的 body、base64 解碼失敗 |
| `test_events.py` | follow、unfollow、join、leave、memberJoined、message（user / group 有無 userId / room）的 target 抽取與額外動作；不支援事件仍抽取 source ID 但無額外動作；缺 `source` 的事件回傳空集合；`isRedelivery` 標記；缺 `timestamp` 的處理 |
| `test_repository.py` | 以 `moto` 測試：新增、更新保留 `first_seen_at`、更新 `last_seen_at`、重複事件、較舊事件被拒絕（stale）、mark_inactive、舊 message 不覆寫新 unfollow、add_admin 對新記錄與既有記錄、remove_admin 條件失敗不建立空記錄、upsert 不覆蓋 role、list 追蹤分頁、list 依 status 與 type 過濾、consistent read |
| `test_commands.py` | 以 fake repository 與 fake line_client 測試：`/id`；`/list` 各變體與排序；`/admin` 三指令與所有拒絕情境；種子與 DB 名單聯集去重；名稱查不到顯示 `(unknown)`；查詢筆數上限與時間預算；UTF-16 長度切段（含 emoji）與 5 則上限；尾註納入長度且依設定產生；關鍵字大小寫不敏感但 ID 保留原值；參數錯誤回覆錯誤訊息；未知指令靜默；非開發者靜默；群組內指令靜默；缺 token 靜默；重送事件不執行指令 |
| `test_secrets.py` | 快取命中、TTL 過期重讀、secret 不存在回 None 並負快取、負快取上限、JSON 格式錯誤、缺 `channel_secret` |
| `test_handler.py` | 端到端：空 events 回 200；多事件；成功與失敗事件混合時回 500 且成功者已寫入；非法 JSON 回 400；`events` 非陣列回 400；簽章錯誤回 401 且無 DB 寫入且未讀 secret；未知 bot 回 404；`bot_id` 格式錯誤回 404；secret 缺 `channel_secret` 回 500；reply 失敗仍回 200；不支援事件回 200；指令在所有事件收集後才執行 |

---

## 21. GitHub CI

GitHub Actions 於 `push` 與 `pull_request` 執行：

1. `uv sync`
2. `ruff check` 與 `ruff format --check`
3. `pytest`
4. `sam validate --lint`

Python 版本 3.14。CI SHALL NOT 需要真實 LINE 或 AWS 憑證。

Dependabot：每月檢查一次，同批次相依更新群組化為單一 PR，涵蓋 `pip`（uv）與 `github-actions` 兩個 ecosystem。

---

## 22. README 需求

README SHALL 包含：

1. 專案概述與適用情境（獨立收集器 Bot；每個 channel 只能有一個 webhook URL）
2. 為什麼需要這個專案
3. 架構圖
4. 功能
5. 前置需求（AWS CLI、SAM CLI、uv、Python 3.14、LINE Official Account）
6. AWS 部署步驟
7. 建立第一個 Bot 的 secret（含如何從 LINE Developers Console 取得 Channel Secret、Access Token、Your user ID）
8. LINE Developers Console 與 LINE Official Account Manager 設定，MUST 明列：
   - 啟用 `Use webhook`
   - 啟用 `Webhook redelivery`
   - 關閉 `Auto-reply messages` 與 `Greeting message`（否則官方帳號會自動回覆，破壞靜默）
   - 開啟 `Allow bot to join group chats`
   - 同一群組同時只能有一個 LINE 官方帳號
9. 新增第二個 Bot 的步驟
10. 開發者指令說明（`/id`、`/list`、`/admin`、`/help`），含更換種子開發者時檢查 `/admin list`
11. 如何用 AWS CLI 查詢、手動設定 admin、手動刪除 target（第 23 節）
12. 設定變數
13. 安全考量
14. 隱私與資料生命週期（第 24 節）
15. 本機測試
16. 執行測試
17. 專案限制（room 無名稱、僅群組觀察到的使用者名稱為 unknown、active 不等於可推播、群組完全靜默、非開發者靜默、盡力收集不保證必達）
18. 授權（MIT）

---

## 23. 以 AWS CLI 操作資料

不提供管理 UI。文件 SHALL 說明以下操作（`{TABLE}`、`{BOT_ID}` 依實際部署替換）：

```bash
# 列出某個 Bot 的全部 target
aws dynamodb query \
  --table-name {TABLE} \
  --key-condition-expression "bot_id = :b" \
  --expression-attribute-values '{":b":{"S":"{BOT_ID}"}}'

# 手動將某個 userId 設為開發者（bootstrap_admin_user_id 遺漏時的救援方式）
# 與 /admin add 相同語意：不存在時建立完整記錄
aws dynamodb update-item \
  --table-name {TABLE} \
  --key '{"bot_id":{"S":"{BOT_ID}"},"target_id":{"S":"Uxxxx"}}' \
  --update-expression "SET #r = :admin, target_type = if_not_exists(target_type, :t), #s = if_not_exists(#s, :a), first_seen_at = if_not_exists(first_seen_at, :now), last_seen_at = if_not_exists(last_seen_at, :now), last_event_ts = if_not_exists(last_event_ts, :ts), last_event_type = if_not_exists(last_event_type, :e)" \
  --expression-attribute-names '{"#r":"role","#s":"status"}' \
  --expression-attribute-values '{":admin":{"S":"admin"},":t":{"S":"user"},":a":{"S":"active"},":now":{"S":"2026-09-12T00:00:00Z"},":ts":{"N":"1789171200000"},":e":{"S":"admin_add"}}'

# 手動刪除單一 target
aws dynamodb delete-item \
  --table-name {TABLE} \
  --key '{"bot_id":{"S":"{BOT_ID}"},"target_id":{"S":"Uxxxx"}}'

# 停用某個 Bot 後，清除該 Bot 的全部資料（query 後逐筆 delete 的腳本範例）
```

---

## 24. 隱私與資料生命週期

本專案的目的是識別碼發現，不是對話收集。

**資料流：** LINE ID 會出現在三個地方：DynamoDB 記錄、開發者的 1:1 LINE 對話（`/list` 回覆會留在 LINE 對話中）、以及 `LOG_TARGET_IDS=true` 時的 CloudWatch Logs。名稱只出現在 `/list` 回覆中。

規則：

- 不持久化 LINE 訊息內容。
- 不持久化 raw webhook body。
- 不持久化使用者顯示名稱或群組名稱。
- 記錄僅包含第 10 節列出的欄位。
- 群組內完全靜默，群組成員不會收到任何回覆或 ID。

**部署者責任：** LINE ID 與活動時間可連結到個人。部署者 SHOULD 依所在地法規，以適當方式（官方帳號簡介、群組公告等）告知收集目的、範圍、存取者與保存期限。本專案不代為處理告知。

**資料生命週期：**

- 本版不自動清理 `inactive` 或過期記錄。
- 部署者 SHOULD 定期用第 23 節的指令檢視並刪除不再需要的記錄。
- 停用某個 Bot 時，SHOULD 刪除該 Bot 的 secret 與 DynamoDB 中該 `bot_id` 的全部記錄。
- 刪除整個 stack 時，DynamoDB table 的 `DeletionPolicy` 設為 `Delete`，資料隨 stack 一併清除；README 說明若要保留需先匯出。

---

## 25. 驗收標準

### 多 Bot

```text
建立 alert-bot 與 ops-bot 兩個 secret
  ↓
各自在 LINE Developers Console 設定 webhook URL
  ↓
兩個 Bot 的 webhook 驗證皆成功
  ↓
DynamoDB 中兩個 Bot 的記錄以 bot_id 分開
```

### 使用者

```text
使用者加 Bot 好友 → follow → userId 儲存，status=active
使用者封鎖 Bot   → unfollow → status=inactive
使用者重新加好友 → follow → status=active，first_seen_at 不變
```

### 群組

```text
Bot 加入群組 → join → groupId 儲存，Bot 無任何回覆
群組內有人發言 → message → groupId 與 userId 儲存，Bot 無任何回覆
開發者在群組打 /id → Bot 無任何回覆
新成員加入 → memberJoined → 成員 userId 儲存
Bot 被踢出 → leave → groupId status=inactive
```

### 聊天室

以 fixture 驗證 `room` 來源的事件可正確儲存 `roomId`。不要求手動建立 room（LINE 新建的多人對話會成為 group，room 主要是舊版相容情境）。

### 開發者指令

```text
種子開發者 1:1 打 /id    → 回覆自己的 userId
種子開發者 1:1 打 /list  → 回覆含名稱的清單
種子開發者 1:1 打 /admin add Uyyyy → Uyyyy 成為開發者
Uyyyy 1:1 打 /list       → 回覆清單
Uyyyy 1:1 打 /admin remove <種子> → 拒絕
種子開發者 1:1 打 /admin remove Uyyyy → 成功，Uyyyy 下一則指令即靜默
非開發者 1:1 打 /id      → 無任何回覆
```

### 冪等與亂序

```text
同一 webhook 重送（isRedelivery=true）→ 記錄不變，指令不執行
較舊的 message 在 unfollow 之後送達 → status 維持 inactive
```

### 安全

```text
簽章錯誤的請求 → 401，無任何寫入，未讀取 secret
未知 bot_id     → 404，無任何寫入
```

### 檢視

開發者可用第 23 節的 AWS CLI 指令看到收集到的 ID。

### CI

GitHub Actions 的 lint、test、sam validate 全部通過。

---

## 26. 完成定義

```text
✓ 可運作的 Lambda webhook handler，支援 POST /webhook/{bot_id}
✓ 每 Bot 一個 Secrets Manager secret，含 TTL 快取與負快取
✓ LINE 簽章驗證（raw body、base64 處理、簽章標頭前置檢查）
✓ userId / groupId / roomId 抽取，含 memberJoined 與不支援事件的 source
✓ DynamoDB 持久化，bot_id + target_id 複合主鍵，以事件 timestamp 做條件更新
✓ unfollow / leave 標記 inactive，亂序事件不使狀態倒退
✓ 逐事件錯誤隔離，明確回傳 500
✓ 群組與非開發者完全靜默
✓ 開發者指令：/id、/list（含名稱查詢、上限、時間預算、UTF-16 切段）、/admin、/help
✓ 重送事件不執行指令
✓ AWS SAM template，最小權限 IAM
✓ 單元測試與端到端測試
✓ GitHub Actions CI 與 Dependabot
✓ README 部署、LINE 設定、隱私與資料生命週期指南
✓ MIT LICENSE
```

實作以簡單為優先。本 PRD 未要求的功能，不要因為「將來可能有用」而加入。

---

## 27. 未來可能的擴充

以下項目不在本版範圍，僅在有獨立 issue 時考慮：

- 以 `webhookEventId` 做持久化事件去重
- 記錄使用者與群組的關聯，以群組成員 profile API 查詢名稱
- `/list` 名稱查詢並行化或快取
- 新 target 出現時通知開發者
- JSON / CSV 匯出
- 列出 target 的小型 CLI
- Terraform / CDK 部署
- 過期 target 自動清理（DynamoDB TTL）
- 非開發者自助查詢 `/id`
- `memberLeft` 事件處理
- customer managed KMS key 支援
