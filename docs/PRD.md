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

這些 ID 只能從 webhook 事件中得知。開發者在使用者加 Bot 好友、或 Bot 被邀進群組後，通常沒有簡單的方法知道對應的 ID。

本專案提供一個最小化的 serverless webhook 接收器，自動發現並儲存這些 ID，並提供開發者專用的文字指令查詢清單。

本專案刻意**不提供**任何 Web 管理介面。

---

## 2. 目標

系統 SHALL：

1. 接收 LINE Messaging API webhook 事件。
2. 驗證 webhook 請求確實來自 LINE。
3. 支援多個 LINE Bot，各 Bot 以 webhook URL 的 path 區分。
4. 從事件中抽取 `userId`、`groupId`、`roomId`。
5. 將發現的 ID 以 Bot 為單位儲存至 DynamoDB。
6. 自動去重，重複事件不產生重複記錄。
7. 記錄每個 ID 首次與最近一次被觀察到的時間。
8. 在使用者封鎖 Bot 或 Bot 離開群組時，將對應 ID 標記為失效。
9. 對一般使用者與群組**完全靜默**，不做任何回覆。
10. 僅對「開發者」在 1:1 對話中的特定指令回覆，包含查詢自己的 ID、列出已收集的 ID 清單、管理開發者名單。
11. 可用 AWS SAM 部署至 API Gateway HTTP API、AWS Lambda、DynamoDB、Secrets Manager。
12. 不需要任何 Web 管理介面。
13. 提供文件說明如何用 AWS CLI 直接查詢已收集的 ID。

實作規模應維持在「讀少數幾個檔案就能理解並部署」的程度。

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
                                   │ 1. 取得 bot_id                │
                                   │ 2. 讀取該 Bot 的 secret（快取）│
                                   │ 3. 以 raw body 驗證簽章       │
                                   │ 4. 逐一處理 events            │
                                   │    - upsert target            │
                                   │    - unfollow/leave → inactive│
                                   │    - 1:1 + 開發者 + 指令 → 回覆│
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
| `channel_secret` | 是 | 簽章驗證用 |
| `channel_access_token` | 否 | 開發者指令回覆與名稱查詢用。缺少時本 Bot 退化為純收集器，收到指令也靜默 |
| `bootstrap_admin_user_id` | 否 | 種子開發者的 userId，可從 LINE Developers Console「Basic settings → Your user ID」取得。缺少時，本 Bot 沒有任何開發者，直到用 AWS CLI 直接在 DynamoDB 設定 `role=admin` |

規則：

- Secret 由部署者以 AWS CLI 建立（README 提供指令），SAM template **不**建立 secret，只授予讀取權限。
- 新增 Bot 只需建立新 secret 並在 LINE Developers Console 設定 webhook URL，**不需要重新部署**。
- Lambda 以 `bot_id` 為 key 在全域變數中快取 secret，TTL 由 `SECRET_CACHE_TTL_SECONDS` 控制（預設 300 秒）。輪換 token 後最多 TTL 秒生效。
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

處理順序：

```text
收到請求
  ↓
從 path 取得 bot_id，驗證格式
  ↓
讀取該 Bot 的 secret（不存在 → 404）
  ↓
讀取 raw body（若 API Gateway 標示 isBase64Encoded，先 base64 解碼還原）
  ↓
讀取 x-line-signature（缺少 → 401）
  ↓
驗證簽章（失敗 → 401）
  ↓
解析 JSON（失敗 → 400）
  ↓
處理 events
```

- 簽章無效 MUST NOT 造成任何 DynamoDB 寫入或 LINE API 呼叫。
- 簽章比對使用 constant-time 比較（`hmac.compare_digest`）。

---

## 8. 支援的 LINE 事件

所有事件的 target 抽取規則一致：從 `source` 中取出存在的 `userId`、`groupId`、`roomId`，逐一 upsert。`userId` 在群組與聊天室事件中可能不存在，MUST 容忍缺少。

### 8.1 `follow`

使用者加 Bot 好友。`source.type = user`。儲存 `userId`，`status = active`。

### 8.2 `unfollow`

使用者封鎖或刪除 Bot。儲存（更新）`userId`，`status = inactive`。

### 8.3 `join`

Bot 加入群組（`source.type = group`）或聊天室（`source.type = room`）。儲存 `groupId` 或 `roomId`，`status = active`。**不做任何回覆。**

### 8.4 `leave`

Bot 被移出群組或聊天室。將 `groupId` 或 `roomId` 標記 `status = inactive`。

### 8.5 `memberJoined`

新成員加入 Bot 所在的群組或聊天室。`joined.members[]` 內每個 `userId` 各自 upsert，同時 upsert `groupId` 或 `roomId`。

### 8.6 `message`

- `source.type = user`：儲存 `userId`。若訊息為 text 且發送者為開發者，進入指令處理（見第 11 節）。
- `source.type = group`：儲存 `groupId` 與（若存在）`userId`。**永不回覆。**
- `source.type = room`：儲存 `roomId` 與（若存在）`userId`。**永不回覆。**

### 8.7 其他事件

`postback`、`beacon`、`videoPlayComplete`、`unsend`、`memberLeft`、`accountLink`、`things` 等一律忽略，記 log，回 HTTP 200。若這些事件的 `source` 帶有 ID，仍可 upsert（實作上以「先抽 target、再依事件類型決定額外動作」的方式處理，自然涵蓋）。

---

## 9. 多事件與空事件

- 單一 webhook 請求可能包含多個事件，MUST 以集合方式處理 `body["events"]`，不可假設只有 `events[0]`。
- 每個事件獨立處理，一個事件失敗不影響其他事件。
- LINE Developers Console 的 webhook 驗證會送 `{"destination": "...", "events": []}`，這是合法請求：驗簽、接受空陣列、回 200、不寫 DynamoDB。

---

## 10. DynamoDB 資料模型

單一 table，建議名稱 `line-webhook-targets`，on-demand 計費。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `bot_id` | String, **Partition Key** | webhook path 中的 Bot 代號 |
| `target_id` | String, **Sort Key** | LINE userId / groupId / roomId |
| `target_type` | String | `user`、`group`、`room` |
| `status` | String | `active`、`inactive` |
| `role` | String，選填 | 僅開發者記錄有此欄位，值固定為 `admin` |
| `first_seen_at` | String | 首次發現時間，UTC ISO 8601，僅首次建立時寫入 |
| `last_seen_at` | String | 最近一次事件時間，每次更新 |
| `last_event_type` | String | 最近一次暴露此 ID 的事件類型 |

範例：

```json
{
  "bot_id": "alert-bot",
  "target_id": "C1234567890abcdef1234567890abcdef",
  "target_type": "group",
  "status": "active",
  "first_seen_at": "2026-09-12T06:30:00Z",
  "last_seen_at": "2026-09-12T06:35:10Z",
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
  "last_event_type": "message"
}
```

### 10.1 Upsert 行為

所有寫入一律使用 `UpdateItem`，保證冪等，不做 read → check → write：

```text
SET target_type     = :type,
    status          = :status,
    last_seen_at    = :now,
    last_event_type = :event_type,
    first_seen_at   = if_not_exists(first_seen_at, :now)
```

- ID 不存在：`first_seen_at = last_seen_at = now`。
- ID 已存在：`first_seen_at` 不變，`last_seen_at = now`。
- `role` 欄位不在 upsert 表達式內，因此一般事件不會覆蓋或移除開發者身分。
- 重複或重送的 webhook 事件不會造成任何副作用。

### 10.2 狀態轉換

- `unfollow`、`leave` 事件：以 `UpdateItem` 設 `status = inactive`，同樣寫 `last_seen_at` 與 `last_event_type`。
- 之後收到該 ID 的任何其他事件：upsert 會將 `status` 設回 `active`。
- 不使用 `DeleteItem`。

### 10.3 查詢

`/list` 使用 `Query` 以 `bot_id` 為條件取出該 Bot 的全部記錄，在程式內依 `status` 與 `target_type` 過濾。不建立 GSI。

---

## 11. 開發者指令

### 11.1 開發者判定

訊息發送者符合以下任一條件即為開發者：

1. `userId` 等於該 Bot secret 中的 `bootstrap_admin_user_id`（種子開發者）。
2. DynamoDB 中 `(bot_id, userId)` 記錄存在，且 `role = admin` 且 `status = active`。

封鎖 Bot 的開發者（`inactive`）暫時失去權限，重新加好友後自動恢復。

### 11.2 觸發條件

指令僅在**全部**滿足以下條件時才處理：

- 事件類型為 `message`，訊息類型為 `text`
- `source.type = user`（1:1 對話）
- 發送者為開發者
- 該 Bot 的 secret 含 `channel_access_token`

群組與聊天室內的任何訊息，即使來自開發者、即使內容是指令，一律靜默。

非開發者在 1:1 對話中的任何訊息一律靜默。

### 11.3 指令比對

- 去除頭尾空白。
- 大小寫不敏感（`/ID`、`/List Groups` 皆可）。
- 多個空白視為一個分隔符。
- 不認得的指令或格式錯誤：靜默，不回覆「未知指令」。

### 11.4 指令清單

| 指令 | 行為 |
|---|---|
| `/id` | 回覆開發者自己的 userId |
| `/list` | 列出該 Bot 所有 `status = active` 的 target，含名稱 |
| `/list groups` | 只列 group |
| `/list users` | 只列 user |
| `/list rooms` | 只列 room |
| `/list all` | 列出全部 target，含 `inactive`，並標註狀態 |
| `/admin list` | 列出該 Bot 的開發者，顯示名稱與 userId，種子開發者標註 `(bootstrap)` |
| `/admin add Uxxxx` | 將該 userId 設為 `role = admin`。記錄不存在時先建立（`target_type = user`，`status = active`，`last_event_type = admin_add`）再設定 |
| `/admin remove Uxxxx` | 移除該記錄的 `role` 欄位。種子開發者不可移除；不可移除自己 |
| `/help` | 列出以上指令與一行說明 |

### 11.5 回覆格式

`/id`：

```text
LINE Target

Type: user
User ID:
Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

`/list`（依類型分區，每區依 `last_seen_at` 新到舊；名稱查不到顯示 `(unknown)`）：

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

`/admin add` 成功回覆 `Added admin: Uxxxx`，`/admin remove` 成功回覆 `Removed admin: Uxxxx`；拒絕時回覆原因（`Cannot remove bootstrap admin.`、`Cannot remove yourself.`、`Invalid user ID format.`）。

### 11.6 名稱查詢

- Group：`GET /v2/bot/group/{groupId}/summary` 取 `groupName`。
- User：`GET /v2/bot/profile/{userId}` 取 `displayName`。
- Room：LINE 不提供名稱 API，只顯示 ID。
- 名稱僅供即時顯示，**不寫入 DynamoDB**。
- 任一查詢失敗（使用者封鎖、Bot 已離開群組、API 錯誤）顯示 `(unknown)`，不影響其他項目，不讓整個指令失敗。
- 逐筆循序查詢，不做並行；Lambda timeout 設 15 秒以容納數十筆查詢。

### 11.7 訊息長度限制

- LINE Reply API 單次最多 5 則訊息，每則文字最多 5000 字。
- 回覆內容超過 5000 字時，以「項目」為單位切段，不在項目中間切斷，最多 5 則。
- 5 則仍放不下時，在最後一則結尾加註：

```text
... and N more. Use: aws dynamodb query --table-name line-webhook-targets --key-condition-expression "bot_id = :b" --expression-attribute-values '{":b":{"S":"alert-bot"}}'
```

---

## 12. 日誌

使用 Python 內建 `logging`，輸出至 CloudWatch Logs，每筆一行 JSON。

必要欄位：

```text
request_id, bot_id, event_type, source_type, target_type, result
```

`result` 範例值：`stored`、`marked_inactive`、`ignored`、`replied`、`reply_failed`、`invalid_signature`、`unknown_bot`。

規則：

- `target_id` 僅在 `LOG_TARGET_IDS=true` 時輸出，預設 `false`。
- 絕不輸出：Channel Secret、Channel Access Token、完整 raw body、使用者訊息內容、指令參數以外的文字。
- 指令處理時可記錄指令名稱（`/list`、`/admin add`），不記錄非指令訊息的內容。

---

## 13. 錯誤處理與 HTTP 回應碼

| 情境 | 回應 | 副作用 |
|---|---|---|
| `bot_id` 格式錯誤或 secret 不存在 | 404 | 無 |
| 缺少 `x-line-signature` 或簽章錯誤 | 401 | 無，不寫 DB、不呼叫 LINE |
| Body 非合法 JSON 或缺少 `events` | 400 | 記 log |
| 空 `events` | 200 | 無 |
| 不支援的事件類型 | 200 | 記 log，若 source 有 ID 仍 upsert |
| 缺少選填 ID（如群組訊息無 userId） | 200 | 儲存有的 ID |
| DynamoDB 寫入失敗 | 500 | 記 log；讓 Lambda 失敗，LINE 會重送 |
| Secrets Manager 讀取失敗（非 NotFound） | 500 | 記 log |
| 指令回覆失敗（LINE API 錯誤、token 無效） | 200 | 記 log `reply_failed`；ID 已儲存，不值得讓 LINE 重送 |
| 名稱查詢失敗 | 200 | 該項顯示 `(unknown)` |

原則：**ID 收集是主要職責，指令回覆是附加功能。** 收集成功但回覆失敗不算失敗；收集失敗必須讓 LINE 知道。

---

## 14. 效能

- 處理路徑：Webhook → Lambda → DynamoDB → 200，無任何佇列或非同步元件。
- Lambda 設定：記憶體 256 MB，timeout 15 秒（一般事件在 1 秒內完成，15 秒是給 `/list` 名稱查詢用）。
- Secret 快取避免每次請求都讀 Secrets Manager。
- 開發者判定需要一次 `GetItem`，僅在 `source.type = user` 且訊息為 text 時執行。

---

## 15. 設定

Lambda 環境變數（由 SAM parameter 傳入）：

| 變數 | 必填 | 預設 | 說明 |
|---|---|---|---|
| `DYNAMODB_TABLE_NAME` | 是 | 由 template 自動帶入 | targets table 名稱 |
| `SECRET_NAME_PREFIX` | 否 | `line-webhook-id-collector/` | Secrets Manager secret 名稱前綴 |
| `SECRET_CACHE_TTL_SECONDS` | 否 | `300` | secret 快取秒數 |
| `LOG_LEVEL` | 否 | `INFO` | 日誌等級 |
| `LOG_TARGET_IDS` | 否 | `false` | 是否在 log 中輸出 target ID |

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

不授予 `dynamodb:*`、`dynamodb:Scan`、`dynamodb:DeleteItem`、`dynamodb:PutItem`、`secretsmanager:*`、`*`。

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
LogLevel                  預設 INFO
LogTargetIds              預設 false
LogRetentionDays          預設 30
```

SAM template **不**建立 Secrets Manager secret，由部署者以 AWS CLI 建立。

Outputs 輸出 webhook base URL，方便組出每個 Bot 的完整 URL。

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
│       ├── handler.py           # Lambda 進入點：取 bot_id、驗簽、解析、分派、組回應
│       ├── config.py            # 讀環境變數
│       ├── secrets.py           # Secrets Manager 讀取與 TTL 快取
│       ├── signature.py         # HMAC-SHA256 驗簽，處理 base64 body
│       ├── events.py            # 純函式：從事件抽取 target、判斷事件類型
│       ├── repository.py        # DynamoDB：upsert、mark_inactive、set_role、remove_role、get、list
│       ├── commands.py          # 指令解析與執行，產生回覆文字，處理長度切段
│       └── line_client.py       # LINE API 封裝：reply、get_profile、get_group_summary
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

簽章驗證優先使用 `line-bot-sdk` 提供的 `WebhookParser` / 簽章驗證工具，但 MUST 以原始 body 驗證，並有完整單元測試。

---

## 20. 測試需求

使用 `pytest`，所有 fixture 使用假 ID 與測試用 secret，CI 不需要任何真實憑證。

| 檔案 | 覆蓋範圍 |
|---|---|
| `test_signature.py` | 正確簽章、錯誤簽章、缺少簽章、body 被修改、base64 編碼的 body |
| `test_events.py` | follow、unfollow、join、leave、memberJoined、message（user / group 有無 userId / room）的 target 抽取；不支援事件回傳空集合 |
| `test_repository.py` | 以 `moto` 測試：新增、更新保留 `first_seen_at`、更新 `last_seen_at`、重複事件、mark_inactive、set_role、remove_role、list 依 status 與 type 過濾、upsert 不覆蓋 role |
| `test_commands.py` | 以 fake repository 與 fake line_client 測試：`/id`；`/list` 各變體與排序；`/admin` 三指令；種子開發者不可移除；不可移除自己；名稱查不到顯示 `(unknown)`；5000 字切段與 5 則上限；大小寫與空白容錯；非開發者靜默；群組內指令靜默；缺 token 靜默 |
| `test_secrets.py` | 快取命中、TTL 過期重讀、secret 不存在回 None、JSON 格式錯誤 |
| `test_handler.py` | 端到端：空 events 回 200；多事件；非法 JSON 回 400；簽章錯誤回 401 且無 DB 寫入；未知 bot 回 404；`bot_id` 格式錯誤回 404；DynamoDB 失敗回 500；reply 失敗仍回 200；不支援事件回 200 |

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

1. 專案概述
2. 為什麼需要這個專案
3. 架構圖
4. 功能
5. 前置需求（AWS CLI、SAM CLI、uv、Python 3.14、LINE Official Account）
6. AWS 部署步驟
7. 建立第一個 Bot 的 secret（含如何從 LINE Developers Console 取得 Channel Secret、Access Token、Your user ID）
8. LINE Developers Console 設定與 webhook URL
9. 新增第二個 Bot 的步驟
10. 開發者指令說明（`/id`、`/list`、`/admin`、`/help`）
11. 如何用 AWS CLI 直接查詢已收集的 ID
12. 設定變數
13. 安全考量
14. 本機測試
15. 執行測試
16. 專案限制（room 無名稱、群組完全靜默、非開發者靜默、名稱查詢逐筆等）
17. 授權（MIT）

---

## 23. 以 AWS CLI 查詢 ID

不提供管理 UI。文件 SHALL 說明如何用 AWS CLI 查詢：

```bash
# 列出某個 Bot 的全部 target
aws dynamodb query \
  --table-name line-webhook-targets \
  --key-condition-expression "bot_id = :b" \
  --expression-attribute-values '{":b":{"S":"alert-bot"}}'

# 手動將某個 userId 設為開發者（bootstrap_admin_user_id 遺漏時的救援方式）
aws dynamodb update-item \
  --table-name line-webhook-targets \
  --key '{"bot_id":{"S":"alert-bot"},"target_id":{"S":"Uxxxx"}}' \
  --update-expression "SET #r = :admin" \
  --expression-attribute-names '{"#r":"role"}' \
  --expression-attribute-values '{":admin":{"S":"admin"}}'
```

---

## 24. 隱私

本專案的目的是識別碼發現，不是對話收集。

- 不持久化 LINE 訊息內容。
- 不持久化 raw webhook body。
- 不持久化使用者顯示名稱或群組名稱（僅在 `/list` 時即時查詢並回覆）。
- 記錄僅包含第 10 節列出的欄位。
- 群組內完全靜默，群組成員不會知道 Bot 在收集 ID，也不會看到任何 ID。
- 使用者的 userId 只會出現在開發者的 1:1 對話中。

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

```text
Bot 加入或收到訊息 → roomId 儲存
```

### 開發者指令

```text
種子開發者 1:1 打 /id    → 回覆自己的 userId
種子開發者 1:1 打 /list  → 回覆含名稱的清單
種子開發者 1:1 打 /admin add Uyyyy → Uyyyy 成為開發者
Uyyyy 1:1 打 /list       → 回覆清單
Uyyyy 1:1 打 /admin remove <種子> → 拒絕
非開發者 1:1 打 /id      → 無任何回覆
```

### 安全

```text
簽章錯誤的請求 → 401，無任何寫入
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
✓ 每 Bot 一個 Secrets Manager secret，含 TTL 快取
✓ LINE 簽章驗證（raw body、base64 處理）
✓ userId / groupId / roomId 抽取，含 memberJoined
✓ DynamoDB 持久化，bot_id + target_id 複合主鍵，冪等 upsert
✓ unfollow / leave 標記 inactive
✓ 群組與非開發者完全靜默
✓ 開發者指令：/id、/list（含名稱查詢與切段）、/admin、/help
✓ AWS SAM template，最小權限 IAM
✓ 單元測試與端到端測試
✓ GitHub Actions CI 與 Dependabot
✓ README 部署與設定指南
✓ MIT LICENSE
```

實作以簡單為優先。本 PRD 未要求的功能，不要因為「將來可能有用」而加入。

---

## 27. 未來可能的擴充

以下項目不在本版範圍，僅在有獨立 issue 時考慮：

- `/list` 名稱查詢並行化或快取
- 新 target 出現時通知開發者
- JSON / CSV 匯出
- 列出 target 的小型 CLI
- Terraform / CDK 部署
- 過期 target 自動清理
- 非開發者自助查詢 `/id`
- `memberLeft` 事件處理
