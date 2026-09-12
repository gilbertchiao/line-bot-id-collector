# LINE Webhook ID Collector

一個輕量的 serverless LINE Messaging API webhook，用來收集 `userId`、`groupId`、`roomId`，
並讓開發者透過 1:1 對話查詢已收集的 ID 清單。支援多個 LINE Bot 共用同一組部署。

詳細需求請參考 [docs/PRD.md](docs/PRD.md)。

## 部署

本專案使用 [AWS SAM](https://docs.aws.amazon.com/serverless-application-model/) 部署，
`template.yaml` 定義了 Lambda Function、HTTP API、DynamoDB Table 與最小權限 IAM Policy。

### 1. 產生 `src/requirements.txt`

`sam build` 需要一份純第三方相依套件清單（不含專案本身、不含 dev 依賴）才能打包 Lambda layer。
每次修改 `pyproject.toml` 的 `dependencies` 後，都要重新執行：

```bash
uv export --no-dev --no-hashes --no-emit-project -o src/requirements.txt
```

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

部署完成後，將 Outputs 中的 `WebhookBaseUrl` 加上 `/webhook/<bot_id>` 設定到 LINE Developers
Console 的 Webhook URL。每個 Bot 對應的 Channel Secret / Access Token 需另外存入
AWS Secrets Manager，密鑰名稱為 `<SecretNamePrefix><bot_id>`（預設前綴為 `line-webhook-id-collector/`）。
