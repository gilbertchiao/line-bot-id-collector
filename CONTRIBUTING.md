# 貢獻指南（Contributing）

感謝你考慮為本專案貢獻。以下說明開發環境設定、PR 流程與相關規範。

## 開發環境

前置需求請參考 [README.md](README.md#前置需求)（AWS CLI、AWS SAM CLI、uv、Python 3.14）。

```bash
# 安裝相依套件（含 dev dependencies）
uv sync

# 跑測試
uv run pytest -q

# 靜態檢查
uv run ruff check .

# 格式化
uv run ruff format .

# 驗證 SAM template
sam validate --lint --region ap-northeast-1
```

以上指令即為 CI（`.github/workflows/test.yml`）執行的同一組檢查，送出 PR 前請先在本機跑過一次。

## 修改相依套件後的必要步驟

若你修改了 `pyproject.toml` 的 `dependencies`，**必須**重新產生 `src/requirements.txt`：

```bash
uv export --no-dev --no-hashes --no-emit-project --frozen -o src/requirements.txt
```

`src/requirements.txt` 是 `sam build` 打包 Lambda function 時使用的純第三方套件清單。
CI 會以 `git diff --exit-code src/requirements.txt` 檢查此檔案是否與 `uv.lock` 同步，
若忘記重新產生並一併提交，CI 會失敗。

## Commit 訊息格式

本專案採用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
<type>(<scope>): <簡短描述>

<詳細說明（選填）>
```

常用 `type`：`feat`（新功能）、`fix`（修復 bug）、`refactor`（重構）、`chore`（雜項）、`docs`（文件）。
`scope` 為選填，例如 `backend`、`infra`、`docs`。

## Pull Request 流程

1. 從 `main` 分支開一個新分支進行修改。
2. 確保 CI 全部檢查（`ruff check`、`ruff format --check`、`pytest`、`sam validate --lint`）通過。
3. PR 描述中請說明：
   - 變更內容與動機
   - 測試方式（例如新增/修改了哪些測試、如何本機驗證）
4. **行為變更（新功能、bug 修復）必須附上對應的測試**，不接受無測試覆蓋的行為變更。
5. 若變更涉及相依套件，請確認 `src/requirements.txt` 已同步重新產生並一併提交。

### 範圍限制

請**不要**在 PR 中加入 [docs/PRD.md 第 3 節「非目標」](docs/PRD.md) 列出的功能，
例如管理 UI、Web 前端、訊息內容 / 對話記錄儲存、Rich menu、LIFF、非同步架構
（SQS / SNS / EventBridge / Step Functions）等。若你認為這類功能有其必要性，
請先開 issue 討論，取得共識後再開始實作，避免做了白工。

## 安全注意事項

- **絕對不要** commit 以下內容：
  - `work/`、`temp/`、`tmp/` 目錄（開發過程的暫存檔）
  - `samconfig.toml`（本機部署參數，已在 `.gitignore` 中排除）
  - `.env` 或任何 `.env.*`
  - 任何真實的 LINE channel secret、channel access token、AWS 憑證
- 若不慎 commit 了機密資訊，請立即通知維護者並參考 [SECURITY.md](SECURITY.md) 的回報方式，
  同時盡快在 LINE Developers Console / AWS 端撤銷並重新發行該憑證。
- 撰寫測試或範例時，一律使用假 ID 與假 secret（可參考 `tests/` 現有 fixture 的作法）。

## 回報安全漏洞

安全性問題請**不要**透過一般 issue 回報，請參考 [SECURITY.md](SECURITY.md)。
