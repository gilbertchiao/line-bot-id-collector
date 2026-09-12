# Changelog

本專案的版本紀錄採用 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/) 格式，
版本號遵循 [Semantic Versioning](https://semver.org/lang/zh-TW/)。

## [1.0.0] - 2026-09-12

### 新增

- 支援多個 LINE Bot 共用同一組部署，各 Bot 以 webhook path 中的 `bot_id` 區分
- 驗證每個請求的 `x-line-signature`（HMAC-SHA256，逐位元組比對原始 body）
- 自動從 webhook 事件中抽取 `userId` / `groupId` / `roomId` 並 upsert 至 DynamoDB，記錄首次與最近觀察時間
- 使用者封鎖 Bot 或 Bot 離開群組時，自動將對應 ID 標記為 `inactive`
- 開發者 1:1 對話指令：`/id`、`/list`（含各種篩選條件）、`/admin`（動態管理開發者名單）、`/help`
- 以 AWS SAM 部署至 API Gateway **REST API**（v1），並支援 `Architecture` 參數（`arm64` / `x86_64`）
- CI（GitHub Actions）：`ruff check`、`ruff format --check`、`pytest`、`sam validate --lint`
