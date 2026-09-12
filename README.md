# LINE Webhook ID Collector

一個輕量的 serverless LINE Messaging API webhook，用來收集 `userId`、`groupId`、`roomId`，
並讓開發者透過 1:1 對話查詢已收集的 ID 清單。支援多個 LINE Bot 共用同一組部署。

詳細需求請參考 [docs/PRD.md](docs/PRD.md)，本文件僅摘要部署與操作所需資訊。

## 專案概述

本專案是一個**獨立的收集器 Bot**，提供：

- 接收多個 LINE Bot 的 webhook 事件（以 `POST /webhook/{bot_id}` 依 path 區分）
- 自動從事件中抽取 `userId`、`groupId`、`roomId` 並儲存到 DynamoDB
- 讓開發者在與 Bot 的 1:1 對話中，用文字指令查詢已收集的 ID

**適用情境：** 每個 LINE channel 只能設定一個 webhook URL。若將既有 Bot 的 webhook 改指向本專案，
原後端就收不到事件。**既有 Bot 若要收集 ID，應將收集邏輯整合進原後端，而非使用本專案。**
本專案適合「還沒有後端、只是想知道使用者或群組 ID」的場景，例如要架設一個新的告警 / 通知用 Bot。

## 為什麼需要這個專案

LINE Messaging API 發送 push message 時，必須指定目的地 ID（`userId` / `groupId` / `roomId`）。
除了開發者自己的 `userId` 可以在 LINE Developers Console 的 Basic settings 頁面直接看到之外，
**其他使用者的 `userId` 與群組的 `groupId`，實務上只能從 webhook 事件中得知**——LINE 並未提供
任何主動查詢的 API。開發者在使用者加 Bot 好友、或 Bot 被邀進群組之後，通常沒有簡單的方法知道
對應的 ID，只能自己接一個 webhook 把 ID 記下來。本專案就是這個「記錄 ID」的最小化實作。

## 架構

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

使用 API Gateway **HTTP API**（不是 REST API），因為只需要簡單的 Lambda proxy integration。
整條路徑沒有任何佇列或非同步元件：Webhook → Lambda → DynamoDB → 200。

## 功能

- 支援任意數量的 LINE Bot 共用同一組部署，各 Bot 以 webhook path 中的 `bot_id` 區分
- 驗證每個請求的 `x-line-signature`（HMAC-SHA256，逐位元組比對原始 body）
- 自動抽取並 upsert `userId` / `groupId` / `roomId`，重複或重送事件不會產生重複記錄
- 記錄每個 ID 首次與最近一次被觀察到的時間
- 使用者封鎖 Bot 或 Bot 離開群組時，自動將對應 ID 標記為 `inactive`
- 對一般使用者與**群組完全靜默**，不做任何回覆
- 僅在與開發者的 1:1 對話中回覆特定指令：`/id`、`/list`（含各種篩選）、`/admin`、`/help`
- 開發者名單可動態管理（`/admin add` / `/admin remove`），無需重新部署
- 不提供任何 Web 管理介面；管理一律透過 LINE 指令或 AWS CLI

## 前置需求

- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)（已設定好有權限的 profile）
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- [uv](https://docs.astral.sh/uv/)（Python 套件與虛擬環境管理）
- Python 3.14（本專案的 `requires-python` 下限）
- 一個或多個 LINE Official Account，且已在 [LINE Developers Console](https://developers.line.biz/console/) 建立對應的 Messaging API channel

## AWS 部署步驟

本專案使用 [AWS SAM](https://docs.aws.amazon.com/serverless-application-model/) 部署，
`template.yaml` 定義了 Lambda Function、HTTP API、DynamoDB Table 與最小權限 IAM Policy。
SAM template **不會**建立 Secrets Manager secret，僅授予讀取權限；secret 需自行以 AWS CLI 建立
（見下一節）。

### 1. 產生 `src/requirements.txt`

`sam build` 需要一份純第三方相依套件清單（不含專案本身、不含 dev 依賴）才能打包進 Lambda function
本身的部署套件（並非獨立的 Lambda layer）。每次修改 `pyproject.toml` 的 `dependencies` 後，
都要重新執行：

```bash
uv export --no-dev --no-hashes --no-emit-project --frozen -o src/requirements.txt
```

CI 會執行相同指令並以 `git diff --exit-code src/requirements.txt` 檢查此檔案是否與
`uv.lock` 同步；若忘記重新產生，CI 會失敗並提示執行上述指令後重新提交。

### 2. 驗證 template

```bash
sam validate --lint --region ap-northeast-1
```

### 3. 本機 build

```bash
sam build --use-container
```

因為 `template.yaml` 的 Lambda 是 `arm64` 架構，其中 `pydantic-core`、`aiohttp`、`multidict`、
`yarl`、`frozenlist` 等套件含有 native 相依，**務必**使用 `--use-container`（或在
`samconfig.toml` 設定 `[default.build.parameters]` 的 `use_container = true`，
`samconfig.example.toml` 已內建此設定）打包，才能確保產出的是 arm64 wheel，避免在
x86_64 本機直接 build 混入不相容的 wheel，導致部署後 Lambda 冷啟動失敗。
若本機本身不是 arm64（例如一般 x86_64 開發機），需要 Docker 具備 arm64 QEMU 模擬能力，
可明確指定容器內的 build 架構：

```bash
sam build --use-container --use-container-arch arm64
```

若本機沒有 Docker 或缺乏 arm64 QEMU 模擬能力而完全無法使用 `--use-container`，
可先將 `template.yaml` 的 `Architectures` 改為 `x86_64` 再本機直接 build。**此不含容器的
`sam build` 僅適合本機驗證或 CI 診斷用途（例如確認相依套件能正確解析、程式碼能被打包），
並非正式部署路徑**；實際部署前務必改回 `arm64` 並透過容器 build 出正確架構的產物。

### 4. 部署

複製 `samconfig.example.toml` 為 `samconfig.toml`（已加入 `.gitignore`，不會進版控），
依環境調整參數後執行：

```bash
cp samconfig.example.toml samconfig.toml
sam deploy --guided
```

部署完成後，`sam deploy` 會印出以下 Outputs：

| Output | 說明 |
|---|---|
| `WebhookBaseUrl` | Webhook 的基底 URL，各 Bot 的 webhook URL 為 `<WebhookBaseUrl>/webhook/<bot_id>` |
| `TableName` | DynamoDB targets table 名稱，用於第「以 AWS CLI 操作資料」節的指令 |
| `SecretNamePrefix` | Secrets Manager secret 名稱前綴（預設 `line-webhook-id-collector/`） |

## 建立第一個 Bot 的 Secret

1. 在 [LINE Developers Console](https://developers.line.biz/console/) 建立或選擇一個 Messaging API channel。
2. 在該 channel 的 **Basic settings** 頁籤，取得：
   - `Channel secret`
   - **Your user ID**（這是你自己的 `userId`，作為此 Bot 的種子開發者）
3. 在該 channel 的 **Messaging API** 頁籤，發行一組 **Channel access token（long-lived）**。
4. 用 AWS CLI 建立此 Bot 對應的 secret（secret 名稱固定為 `<SecretNamePrefix><bot_id>`，
   `bot_id` 需與稍後設定的 webhook path 一致）：

```bash
aws secretsmanager create-secret \
  --name "line-webhook-id-collector/alert-bot" \
  --secret-string '{
    "channel_secret": "<LINE Channel Secret>",
    "channel_access_token": "<LINE Channel Access Token>",
    "bootstrap_admin_user_id": "<Your user ID from LINE Developers Console>"
  }'
```

三個欄位的必要性：

| 欄位 | 必填 | 說明 |
|---|---|---|
| `channel_secret` | 是 | 簽章驗證用。缺少時視為設定錯誤，回 HTTP 500 |
| `channel_access_token` | 否 | 開發者指令回覆與名稱查詢用。缺少時本 Bot 退化為純收集器，收到指令也靜默 |
| `bootstrap_admin_user_id` | 否 | 種子開發者的 `userId`。缺少時，本 Bot 沒有任何開發者，需改用「以 AWS CLI 操作資料」節的指令手動設定 |

## LINE Developers Console 設定

secret 建立後，回到 LINE Developers Console 完成以下設定，**缺一不可**：

- [ ] 在 **Messaging API** 頁籤，將 Webhook URL 設為 `<WebhookBaseUrl>/webhook/<bot_id>`，
      啟用 **Use webhook**，並按 **Verify** 確認可連線
- [ ] 啟用 **Webhook redelivery**（LINE 送達失敗時會重送；本專案不做持久化去重，重送不會造成重複記錄）
- [ ] 關閉 **Auto-reply messages** 與 **Greeting message**（於 [LINE Official Account Manager](https://manager.line.biz/) 設定；
      否則官方帳號會自動回覆一般使用者，破壞本專案「群組與一般使用者完全靜默」的設計）
- [ ] 開啟 **Allow bot to join group chats**（若需要收集群組 `groupId`）
- [ ] 確認同一群組同時只邀請這一個 LINE 官方帳號（**同一群組同時只能有一個 LINE 官方帳號**，
      這是 LINE 平台本身的限制，不是本專案的限制）

## 新增第二個 Bot

新增 Bot **不需要重新部署**，也不需要修改 `template.yaml`：

1. 依「建立第一個 Bot 的 Secret」節的步驟，為新 Bot 建立一個新的 secret（`bot_id` 换成新 Bot 的短代號）。
2. 在 LINE Developers Console 將新 Bot 的 Webhook URL 設為 `<WebhookBaseUrl>/webhook/<bot_id>`。
3. 按 **Verify** 確認連線成功，並依「LINE Developers Console 設定」節完成其餘設定。

> 若 Lambda 已對「此 secret 不存在」做過負快取（例如你先用錯誤的 `bot_id` 測試過），
> 需等待 `SECRET_CACHE_TTL_SECONDS`（預設 300 秒）後負快取才會過期，新 Bot 才能立即生效；
> 一般情況下（尚未快取過）新 secret 建立後即可直接使用。

## 開發者指令

指令僅在**與開發者的 1:1 對話**中才會處理與回覆；群組、聊天室內的任何訊息一律靜默，
非開發者的任何訊息也一律靜默。「開發者」的判定：`userId` 等於 secret 中的
`bootstrap_admin_user_id`，或 DynamoDB 中該 `(bot_id, userId)` 記錄的 `role = admin`。

| 指令 | 行為 |
|---|---|
| `/id` | 回覆自己的 `userId` |
| `/list` | 列出所有 `status = active` 的 target（含名稱） |
| `/list groups` | 只列 group |
| `/list users` | 只列 user |
| `/list rooms` | 只列 room |
| `/list all` | 列出全部 target，含 `inactive`，並標註狀態 |
| `/admin list` | 列出目前的開發者名單（種子開發者標註 `(bootstrap)`） |
| `/admin add Uxxxx` | 新增一個開發者 |
| `/admin remove Uxxxx` | 移除一個開發者（無法移除種子開發者或自己） |
| `/help` | 列出以上指令與說明 |

指令關鍵字大小寫不敏感（`/ID`、`/List Groups` 皆可），但 ID 參數保留原值。
`/list` 只顯示「曾在事件中觀察到」的 ID（`status = active`），**不代表一定可以 push**——
只在群組中觀察到、從未加 Bot 好友的使用者，push 會失敗。

**更換種子開發者提醒：** 更換 secret 中的 `bootstrap_admin_user_id` 後，DynamoDB 中舊種子開發者
若曾被 `/admin add` 加入過，其 `role = admin` 不會被自動清除。更換種子開發者後，
請務必用 `/admin list` 檢查目前的開發者名單是否符合預期，必要時用 `/admin remove` 手動移除。

## 以 AWS CLI 操作資料

本專案不提供管理 UI，所有查詢與資料維護都透過 AWS CLI 直接操作 DynamoDB。
以下指令中的 `{TABLE}`、`{BOT_ID}` 請替換為實際部署的 table 名稱（`TableName` output）與 Bot 代號。

```bash
# 列出某個 Bot 的全部 target
aws dynamodb query \
  --table-name {TABLE} \
  --key-condition-expression "bot_id = :b" \
  --expression-attribute-values '{":b":{"S":"{BOT_ID}"}}'
```

```bash
# 手動將某個 userId 設為開發者（bootstrap_admin_user_id 遺漏時的救援方式）
# 與 /admin add 相同語意：不存在時建立完整記錄
aws dynamodb update-item \
  --table-name {TABLE} \
  --key '{"bot_id":{"S":"{BOT_ID}"},"target_id":{"S":"Uxxxx"}}' \
  --update-expression "SET #r = :admin, target_type = if_not_exists(target_type, :t), #s = if_not_exists(#s, :a), first_seen_at = if_not_exists(first_seen_at, :now), last_seen_at = if_not_exists(last_seen_at, :now), last_event_ts = if_not_exists(last_event_ts, :ts), last_event_type = if_not_exists(last_event_type, :e)" \
  --expression-attribute-names '{"#r":"role","#s":"status"}' \
  --expression-attribute-values '{":admin":{"S":"admin"},":t":{"S":"user"},":a":{"S":"active"},":now":{"S":"2026-09-12T00:00:00Z"},":ts":{"N":"1789171200000"},":e":{"S":"admin_add"}}'
```

```bash
# 手動刪除單一 target
aws dynamodb delete-item \
  --table-name {TABLE} \
  --key '{"bot_id":{"S":"{BOT_ID}"},"target_id":{"S":"Uxxxx"}}'
```

```bash
# 停用某個 Bot 後，清除該 Bot 的全部資料（query 後逐筆 delete）
BOT_ID=alert-bot; TABLE=line-webhook-targets
aws dynamodb query --table-name "$TABLE" \
  --key-condition-expression "bot_id = :b" \
  --expression-attribute-values "{\":b\":{\"S\":\"$BOT_ID\"}}" \
  --projection-expression "target_id" --output json \
| jq -r '.Items[].target_id.S' \
| while read -r id; do
    aws dynamodb delete-item --table-name "$TABLE" \
      --key "{\"bot_id\":{\"S\":\"$BOT_ID\"},\"target_id\":{\"S\":\"$id\"}}"
  done
```

## 設定變數

Lambda 環境變數，皆由 SAM parameter 傳入（見 `template.yaml`）：

| 變數（SAM parameter） | 必填 | 預設 | 說明 |
|---|---|---|---|
| `DYNAMODB_TABLE_NAME`（`TableName`） | 是 | `line-webhook-targets` | targets table 名稱，由 template 自動帶入 |
| `SECRET_NAME_PREFIX`（`SecretNamePrefix`） | 否 | `line-webhook-id-collector/` | Secrets Manager secret 名稱前綴 |
| `SECRET_CACHE_TTL_SECONDS`（`SecretCacheTtlSeconds`） | 否 | `300` | secret 快取秒數（含負快取） |
| `NAME_LOOKUP_LIMIT`（`NameLookupLimit`） | 否 | `50` | `/list` 名稱查詢筆數上限 |
| `NAME_LOOKUP_BUDGET_SECONDS`（`NameLookupBudgetSeconds`） | 否 | `8` | `/list` 名稱查詢累計時間預算（秒） |
| `LOG_LEVEL`（`LogLevel`） | 否 | `INFO` | 本專案 logger 的等級 |
| `LOG_TARGET_IDS`（`LogTargetIds`） | 否 | `false` | 是否在 log 中輸出 target ID 與指令 ID 參數 |
| （`LogRetentionDays`） | 否 | `30` | CloudWatch Log Group 保留天數 |

LINE 相關憑證（`channel_secret`、`channel_access_token`、`bootstrap_admin_user_id`）一律放
Secrets Manager，不放環境變數，也絕不寫入 log 或進版控。

## 安全考量

- 每個請求在處理事件前，一律先驗證 `x-line-signature`（HMAC-SHA256，key 為該 Bot 的
  `channel_secret`，message 為**原始** HTTP request body，逐位元組一致，比對前不對 body 做任何
  反序列化或格式化）。簽章比對使用 `hmac.compare_digest` 做 constant-time 比較。
- 簽章驗證失敗**不會**造成任何 DynamoDB 寫入或 LINE API 呼叫。
- 檢查順序刻意把不需要 secret 的檢查（`bot_id` 格式、簽章標頭格式）放在最前面，減少未驗證請求
  造成的 Secrets Manager 呼叫。
- IAM execution role 採最小權限：僅 `dynamodb:UpdateItem` / `Query` / `GetItem`（限定 targets table）
  與 `secretsmanager:GetSecretValue`（限定 `SECRET_NAME_PREFIX` 前綴的 secret），不授予
  `dynamodb:Scan`、`dynamodb:DeleteItem`、`dynamodb:PutItem`、`secretsmanager:*`、`kms:*` 或萬用 `*`。
- 本版限定 secret 使用 Secrets Manager 預設的 AWS managed key（`aws/secretsmanager`）加密；
  若改用 customer managed KMS key，需自行補上 `kms:Decrypt` 權限，本版不支援。
- Secret 內容絕不寫入 log、絕不進 git；DynamoDB 中的 target ID 預設也不寫入 log
  （`LOG_TARGET_IDS=false`），開啟後才會在 log 中輸出 ID 與指令參數，僅建議除錯時暫時開啟。
- API Gateway HTTP API 保留預設的帳號層級節流，本版不另設 per-route 節流。

## 隱私與資料生命週期

本專案的目的是**識別碼發現**，不是對話收集。

**資料流：** LINE ID 會出現在三個地方：DynamoDB 記錄、開發者的 1:1 LINE 對話
（`/list` 回覆會留在 LINE 對話中）、以及 `LOG_TARGET_IDS=true` 時的 CloudWatch Logs。
使用者顯示名稱 / 群組名稱只出現在 `/list` 回覆中，**不會**寫入 DynamoDB 或 log。

**資料最小化規則：**

- 不持久化 LINE 訊息內容
- 不持久化 raw webhook body
- 不持久化使用者顯示名稱或群組名稱
- DynamoDB 記錄僅包含 ID、類型、狀態、時間戳等欄位（見 `docs/PRD.md` 第 10 節）
- 群組內完全靜默，群組成員不會收到任何回覆或 ID

**部署者責任：** LINE ID 與活動時間可連結到個人。部署者**應**依所在地法規，以適當方式
（官方帳號簡介、群組公告等）告知收集目的、範圍、存取者與保存期限。**本專案不代為處理告知。**

**資料生命週期：**

- 本版**不會**自動清理 `inactive` 或過期記錄
- 部署者應定期用「以 AWS CLI 操作資料」節的指令檢視並刪除不再需要的記錄
- 停用某個 Bot 時，應刪除該 Bot 的 secret，並用上面的清除迴圈刪除 DynamoDB 中該 `bot_id` 的全部記錄
- 刪除整個 CloudFormation stack 時，DynamoDB table 的 `DeletionPolicy` 為 `Delete`，
  資料會隨 stack 一併清除；**若要保留資料，需先用「以 AWS CLI 操作資料」節的查詢指令匯出**

## 本機測試

### 執行單元測試

```bash
uv sync
uv run pytest
```

所有測試 fixture 都使用假 ID 與測試用 secret（透過 `moto` 模擬 DynamoDB / Secrets Manager），
不需要任何真實 LINE 或 AWS 憑證。

### 用 `sam local invoke` 本機呼叫 Lambda

`events/sample-follow.json` 是一個範例 API Gateway HTTP API 事件（`follow` 事件，全部使用假 ID），
其中 `x-line-signature` 標頭已用假的 `channel_secret`（`local-test-secret`）預先計算好，
可搭配同樣內容的本機 secret 直接驗證通過。

若要改用自己的 body 或 secret 重新計算簽章，可用以下一行指令（`$CHANNEL_SECRET` 為
`channel_secret`，`$BODY` 為與事件 `body` 欄位**完全一致**的原始字串）：

```bash
python3 -c 'import hmac,hashlib,base64,sys;print(base64.b64encode(hmac.new(sys.argv[1].encode(),sys.argv[2].encode(),hashlib.sha256).digest()).decode())' "$CHANNEL_SECRET" "$BODY"
```

`sam local invoke` 需要能讀到 Secrets Manager 中對應的 secret，並設定好第「設定變數」節列出的
環境變數，例如：

```bash
sam build --use-container
sam local invoke WebhookFunction \
  --event events/sample-follow.json \
  --env-vars env.local.json
```

`env.local.json` 範例（不進版控，內容依實際測試環境調整）：

```json
{
  "WebhookFunction": {
    "DYNAMODB_TABLE_NAME": "line-webhook-targets",
    "SECRET_NAME_PREFIX": "line-webhook-id-collector/"
  }
}
```

## 執行測試

CI（`.github/workflows/test.yml`）在每次 `push` 與 `pull_request` 時，於 Python 3.14 上依序執行：

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
sam validate --lint
```

CI 全程不需要任何真實 LINE 或 AWS 憑證。送 PR 前建議在本機先跑過同一組指令。

## 專案限制

- 聊天室（room）沒有名稱可查詢，`/list` 只能顯示 room ID
- 只在群組中觀察到、從未加 Bot 好友的使用者，名稱固定顯示 `(unknown)`
  （LINE 的個人 profile API 只對已加好友的使用者有效，本專案不記錄使用者與群組的關聯）
- `status = active` 只代表「曾在事件中觀察到」，**不保證可以 push**；只在群組中觀察到、
  從未加 Bot 好友的使用者，push 會失敗
- 群組內完全靜默，即使訊息來自開發者、即使內容是指令，一律不回覆
- 非開發者在 1:1 對話中的任何訊息一律靜默
- 本專案是「盡力收集」：LINE webhook 重送需在 LINE Developers Console 手動啟用，且啟用後也不
  保證必達；本專案不做持久化的事件去重與離線補收

## 授權

[MIT License](LICENSE)
