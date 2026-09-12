---
name: Bug report
about: 回報本專案的錯誤或非預期行為
labels: bug
---

**請勿在此 issue 中貼上任何真實的 `channel secret`、`channel access token` 或完整的 webhook body。**
若你懷疑此問題涉及安全漏洞（例如簽章驗證、IAM 權限、資訊洩漏），請改用
[Private vulnerability reporting](../../security/advisories/new)（見 [SECURITY.md](../../SECURITY.md)），不要在此開公開 issue。

## 環境

- AWS Region：<!-- 例如 ap-northeast-1 -->
- Lambda Architecture：<!-- arm64 / x86_64 -->
- SAM CLI 版本：<!-- `sam --version` 輸出 -->
- 其他相關版本（Python、uv 等，選填）：

## 重現步驟

<!-- 請盡量提供可重現問題的最小步驟，例如觸發的 LINE event 類型、使用的指令等 -->

1.
2.
3.

## 預期行為

<!-- 你原本預期會發生什麼 -->

## 實際行為

<!-- 實際發生了什麼，包含錯誤訊息（若有） -->

## 相關 log

<!--
請貼上相關的 CloudWatch Logs 片段，並務必先遮蔽或移除其中的 ID
（例如將 userId / groupId / roomId 替換為 U_xxx、G_xxx 之類的佔位字串）。
若 LOG_TARGET_IDS 設為 true，請特別注意 log 中可能包含未遮蔽的 target ID。
-->

```text
（貼上遮蔽後的 log）
```
