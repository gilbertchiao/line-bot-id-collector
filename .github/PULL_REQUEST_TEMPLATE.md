## 變更摘要

<!-- 簡述這個 PR 做了什麼、為什麼需要這個變更 -->

## 相關 issue

<!-- 例如 Closes #123，若無相關 issue 可省略此節 -->

## 測試方式

<!-- 說明如何驗證這個變更，例如新增/修改了哪些測試、如何本機手動測試 -->

## Checklist

- [ ] `uv run pytest -q` 通過
- [ ] `uv run ruff check .` 通過
- [ ] `uv run ruff format --check .` 通過
- [ ] `sam validate --lint --region ap-northeast-1` 通過
- [ ] 若修改了 `pyproject.toml` 的 dependencies，已重新產生並提交 `src/requirements.txt`
- [ ] 行為變更已附上對應測試
- [ ] 未包含任何真實憑證（channel secret、access token、AWS 憑證等）
