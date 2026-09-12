# 安全政策（Security Policy）

## 支援版本

以下版本會收到安全性修補：

| 版本 | 支援狀態 |
|---|---|
| `main` 分支 | 支援 |
| 最新 release（1.x） | 支援 |
| 其他舊版 | 不支援 |

## 回報安全漏洞

**請勿在公開 issue 中揭露安全漏洞。** 請改用 GitHub 的
**Private vulnerability reporting**（在此 repository 的 **Security → Report a vulnerability**）提交回報。

回報時請盡量包含：

- 漏洞類型與影響範圍（例如簽章驗證繞過、IAM 權限過大、資訊洩漏等）
- 重現步驟或概念性驗證（proof of concept）
- 受影響的版本或 commit

**請勿在回報中附上真實的 `channel secret`、`channel access token` 或完整的 webhook body**，
這些屬於敏感憑證與可能包含個資的內容。如需示範問題，請使用假資料或遮蔽後的內容。

## 回應時程

- **3 個工作天內**：確認收到回報並開始評估。
- **30 天內**：依嚴重度提出修補或緩解方案（此為盡力目標，實際時程依複雜度而定）。

修補發布後，會視情況於 release note 或 [CHANGELOG.md](CHANGELOG.md) 中致謝回報者（除非回報者要求匿名）。

## 回報範圍

本專案處理 LINE channel secret、channel access token 與使用者 / 群組 / 聊天室 ID，
因此特別歡迎以下類型的回報：

- **簽章驗證**：`x-line-signature` 驗證邏輯是否可被繞過或偽造
- **IAM 權限**：Lambda execution role 是否有超出最小權限原則的授權
- **日誌外洩**：是否有機會讓 secret、access token 或不應記錄的 ID 寫入 CloudWatch Logs
- **Secrets Manager 使用方式**：secret 的讀取、快取、命名前綴（`SECRET_NAME_PREFIX`）是否有安全疑慮

一般的功能請求或非安全性 bug，請改用一般 [issue](../../issues)，不需要透過 private reporting。
