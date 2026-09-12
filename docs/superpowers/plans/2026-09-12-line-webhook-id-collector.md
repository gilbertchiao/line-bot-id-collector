# LINE Webhook ID Collector 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一個多 Bot 的 serverless LINE webhook，收集 userId / groupId / roomId 到 DynamoDB，並提供開發者專用的 1:1 指令查詢清單。

**Architecture:** API Gateway HTTP API → 單一 Lambda（Python 3.14）→ DynamoDB 單表（`bot_id` + `target_id`）。每個 Bot 的憑證存在 Secrets Manager，以 path 中的 `bot_id` 查找並快取。Lambda 先收集所有事件的 ID，最後才對開發者的指令回覆。群組與非開發者永遠靜默。

**Tech Stack:** Python 3.14、uv、line-bot-sdk v3（Messaging API 呼叫）、boto3、AWS SAM、pytest、moto、ruff、GitHub Actions。

**Spec:** `docs/PRD.md`

## Global Constraints

- Python `>=3.14`，Lambda runtime `python3.14`
- 執行期依賴僅 `boto3` 與 `line-bot-sdk`；開發依賴 `pytest`、`moto`、`ruff`
- 程式碼註解與 docstring 使用繁體中文，專業術語可用英文
- 日誌為每行一筆 JSON；絕不輸出 secret、token、raw body、訊息內容
- `target_id` 與指令 ID 參數只在 `LOG_TARGET_IDS=true` 時輸出
- DynamoDB expression 中 `status`、`role` 必須以 `#status`、`#role` 引用
- 所有 DynamoDB 寫入用 `UpdateItem`，不用 `PutItem`、`DeleteItem`
- 時間格式 UTC ISO 8601 `YYYY-MM-DDTHH:MM:SSZ`；事件時間取自 LINE `timestamp`（毫秒）
- LINE 文字長度以 UTF-16 code unit 計算，單則上限 5000，單次 reply 上限 5 則
- 指令關鍵字大小寫不敏感，ID 參數保留原值
- userId 格式：`^U[0-9a-f]{32}$`；`bot_id` 格式：`^[a-z0-9-]{1,64}$`
- 測試 fixture 全部使用假 ID 與測試 secret，CI 不需真實憑證
- Commit 格式：Conventional Commits，結尾加 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`

---

## 檔案結構

| 檔案 | 職責 |
|---|---|
| `pyproject.toml` | uv 專案設定、ruff、pytest 設定 |
| `src/line_webhook_id_collector/config.py` | 讀環境變數成 `Settings` |
| `src/line_webhook_id_collector/logging_setup.py` | JSON logger 與 `log_event()` 輔助函式 |
| `src/line_webhook_id_collector/signature.py` | body 還原、簽章標頭前置檢查、HMAC 驗證 |
| `src/line_webhook_id_collector/secrets.py` | `BotSecret`、`SecretCache`（含負快取） |
| `src/line_webhook_id_collector/events.py` | 純函式：分析單一事件 → `EventOutcome` |
| `src/line_webhook_id_collector/repository.py` | DynamoDB 存取 |
| `src/line_webhook_id_collector/line_client.py` | LINE API 封裝（reply、名稱查詢） |
| `src/line_webhook_id_collector/text_utils.py` | UTF-16 長度、訊息切段 |
| `src/line_webhook_id_collector/commands.py` | 指令解析、授權、執行、回覆文字 |
| `src/line_webhook_id_collector/handler.py` | Lambda 進入點 |
| `template.yaml` | SAM |
| `README.md`、`LICENSE`、`.github/` | 文件與 CI |

模組依賴方向：`handler → commands → repository, line_client, text_utils`；`handler → events, signature, secrets, repository, config, logging_setup`。

---

### Task 1: 專案骨架、工具鏈、CI

**Files:**
- Create: `pyproject.toml`、`src/line_webhook_id_collector/__init__.py`、`tests/__init__.py`、`tests/conftest.py`、`tests/test_smoke.py`、`LICENSE`、`.github/workflows/test.yml`、`.github/dependabot.yml`

**Interfaces:**
- Produces: 可執行 `uv run pytest`、`uv run ruff check`；套件名 `line_webhook_id_collector`

- [ ] **Step 1: 建立 pyproject.toml**

```toml
[project]
name = "line-webhook-id-collector"
version = "1.0.0"
description = "Serverless LINE webhook that collects userId / groupId / roomId into DynamoDB."
readme = "README.md"
requires-python = ">=3.14"
license = { text = "MIT" }
dependencies = [
    "boto3>=1.35",
    "line-bot-sdk>=3.14",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "moto[dynamodb,secretsmanager]>=5.0",
    "ruff>=0.8",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/line_webhook_id_collector"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
target-version = "py314"
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]
```

- [ ] **Step 2: 建立套件與 smoke test**

`src/line_webhook_id_collector/__init__.py`：

```python
"""LINE Webhook ID Collector。"""

__version__ = "1.0.0"
```

`tests/__init__.py`：空檔。

`tests/conftest.py`：

```python
"""共用 pytest fixture。"""

import os

import pytest


@pytest.fixture(autouse=True)
def _aws_dummy_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """讓 boto3 / moto 在沒有真實憑證時也能運作。"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    os.environ.pop("AWS_PROFILE", None)
```

`tests/test_smoke.py`：

```python
from line_webhook_id_collector import __version__


def test_version() -> None:
    assert __version__ == "1.0.0"
```

- [ ] **Step 3: 安裝依賴並跑測試**

Run: `uv sync && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: 1 passed，ruff 無錯誤。若 `line-bot-sdk` 或 `moto` 在 3.14 無法安裝，記錄錯誤訊息並回報，不要自行降版 Python。

- [ ] **Step 4: 建立 LICENSE（MIT）**

```text
MIT License

Copyright (c) 2026 Gilbert Chiao

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 5: 建立 GitHub Actions 與 Dependabot**

`.github/workflows/test.yml`：

```yaml
name: test

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.14"
      - name: Install dependencies
        run: uv sync --frozen
      - name: Lint
        run: |
          uv run ruff check .
          uv run ruff format --check .
      - name: Test
        run: uv run pytest -q
      - uses: aws-actions/setup-sam@v2
        with:
          use-installer: true
      - name: Validate SAM template
        run: sam validate --lint --region ap-northeast-1
        if: hashFiles('template.yaml') != ''
```

`.github/dependabot.yml`：

```yaml
version: 2
updates:
  - package-ecosystem: "uv"
    directory: "/"
    schedule:
      interval: "monthly"
    groups:
      python-dependencies:
        patterns: ["*"]
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "monthly"
    groups:
      github-actions:
        patterns: ["*"]
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src tests LICENSE .github
git commit -m "chore: scaffold project with uv, ruff, pytest, and CI"
```

---

### Task 2: config.py 與 logging_setup.py

**Files:**
- Create: `src/line_webhook_id_collector/config.py`、`src/line_webhook_id_collector/logging_setup.py`
- Test: `tests/test_config.py`、`tests/test_logging_setup.py`

**Interfaces:**
- Produces:
  - `Settings`（frozen dataclass）欄位：`table_name: str`、`secret_name_prefix: str`、`secret_cache_ttl_seconds: int`、`name_lookup_limit: int`、`name_lookup_budget_seconds: float`、`log_level: str`、`log_target_ids: bool`
  - `load_settings(env: Mapping[str, str] | None = None) -> Settings`；缺 `DYNAMODB_TABLE_NAME` 拋 `ConfigError`
  - `configure_logging(level: str) -> logging.Logger`：回傳名為 `line_webhook_id_collector` 的 logger，handler 輸出單行 JSON；同時把 `boto3`、`botocore`、`urllib3`、`linebot` 的 logger 固定為 `WARNING`
  - `log_event(logger, result: str, **fields) -> None`：以 INFO 輸出 `{"result": ..., **fields}`；`fields` 中值為 `None` 的鍵不輸出

- [ ] **Step 1: 寫 config 測試**

`tests/test_config.py`：

```python
import pytest

from line_webhook_id_collector.config import ConfigError, load_settings


def test_defaults() -> None:
    s = load_settings({"DYNAMODB_TABLE_NAME": "t"})
    assert s.table_name == "t"
    assert s.secret_name_prefix == "line-webhook-id-collector/"
    assert s.secret_cache_ttl_seconds == 300
    assert s.name_lookup_limit == 50
    assert s.name_lookup_budget_seconds == 8.0
    assert s.log_level == "INFO"
    assert s.log_target_ids is False


def test_overrides() -> None:
    s = load_settings(
        {
            "DYNAMODB_TABLE_NAME": "t",
            "SECRET_NAME_PREFIX": "custom/",
            "SECRET_CACHE_TTL_SECONDS": "10",
            "NAME_LOOKUP_LIMIT": "5",
            "NAME_LOOKUP_BUDGET_SECONDS": "2.5",
            "LOG_LEVEL": "debug",
            "LOG_TARGET_IDS": "TRUE",
        }
    )
    assert s.secret_name_prefix == "custom/"
    assert s.secret_cache_ttl_seconds == 10
    assert s.name_lookup_limit == 5
    assert s.name_lookup_budget_seconds == 2.5
    assert s.log_level == "DEBUG"
    assert s.log_target_ids is True


def test_missing_table_name() -> None:
    with pytest.raises(ConfigError):
        load_settings({})
```

- [ ] **Step 2: 執行確認失敗**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 實作 config.py**

```python
"""從環境變數載入設定。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(Exception):
    """必要設定缺少或格式錯誤。"""


@dataclass(frozen=True)
class Settings:
    table_name: str
    secret_name_prefix: str = "line-webhook-id-collector/"
    secret_cache_ttl_seconds: int = 300
    name_lookup_limit: int = 50
    name_lookup_budget_seconds: float = 8.0
    log_level: str = "INFO"
    log_target_ids: bool = False


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """讀取環境變數；`env` 未給時使用 `os.environ`。"""
    source = os.environ if env is None else env
    table_name = source.get("DYNAMODB_TABLE_NAME", "").strip()
    if not table_name:
        raise ConfigError("DYNAMODB_TABLE_NAME is required")
    try:
        return Settings(
            table_name=table_name,
            secret_name_prefix=source.get("SECRET_NAME_PREFIX", "line-webhook-id-collector/"),
            secret_cache_ttl_seconds=int(source.get("SECRET_CACHE_TTL_SECONDS", "300")),
            name_lookup_limit=int(source.get("NAME_LOOKUP_LIMIT", "50")),
            name_lookup_budget_seconds=float(source.get("NAME_LOOKUP_BUDGET_SECONDS", "8")),
            log_level=source.get("LOG_LEVEL", "INFO").upper(),
            log_target_ids=_as_bool(source.get("LOG_TARGET_IDS", "false")),
        )
    except ValueError as exc:
        raise ConfigError(f"invalid numeric setting: {exc}") from exc
```

- [ ] **Step 4: 寫 logging 測試**

`tests/test_logging_setup.py`：

```python
import json
import logging

from line_webhook_id_collector.logging_setup import configure_logging, log_event


def test_log_event_emits_single_line_json(capsys) -> None:
    logger = configure_logging("INFO")
    log_event(logger, "stored", bot_id="b", event_type="follow", target_id=None)
    out = capsys.readouterr().err.strip().splitlines()
    record = json.loads(out[-1])
    assert record["result"] == "stored"
    assert record["bot_id"] == "b"
    assert record["event_type"] == "follow"
    assert "target_id" not in record
    assert record["level"] == "INFO"


def test_third_party_loggers_are_quiet() -> None:
    configure_logging("DEBUG")
    for name in ("boto3", "botocore", "urllib3", "linebot"):
        assert logging.getLogger(name).level == logging.WARNING
```

- [ ] **Step 5: 實作 logging_setup.py**

```python
"""JSON 格式的 CloudWatch 日誌設定。"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

LOGGER_NAME = "line_webhook_id_collector"
_QUIET_LOGGERS = ("boto3", "botocore", "urllib3", "linebot")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {"level": record.levelname}
        if isinstance(record.msg, dict):
            payload.update(record.msg)
        else:
            payload["message"] = record.getMessage()
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> logging.Logger:
    """設定本專案 logger；第三方 logger 固定 WARNING 以免外洩 HTTP 內容。"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return logger


def log_event(logger: logging.Logger, result: str, **fields: Any) -> None:
    """輸出一筆結構化事件；值為 None 的欄位省略。"""
    payload = {"result": result}
    payload.update({k: v for k, v in fields.items() if v is not None})
    logger.info(payload)
```

- [ ] **Step 6: 執行測試與 lint**

Run: `uv run pytest tests/test_config.py tests/test_logging_setup.py -q && uv run ruff check . && uv run ruff format .`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add src tests
git commit -m "feat: add settings loader and JSON logging"
```

---

### Task 3: signature.py

**Files:**
- Create: `src/line_webhook_id_collector/signature.py`
- Test: `tests/test_signature.py`

**Interfaces:**
- Produces:
  - `class BodyDecodeError(Exception)`
  - `decode_body(event: Mapping[str, Any]) -> bytes`：從 API Gateway v2 事件取 `body`，`isBase64Encoded` 為 true 時 base64 解碼；`body` 缺少視為空 bytes；解碼失敗拋 `BodyDecodeError`
  - `is_plausible_signature(signature: str | None) -> bool`：非空且為合法 base64 且解碼後長度為 32
  - `verify_signature(channel_secret: str, body: bytes, signature: str) -> bool`：HMAC-SHA256 + `hmac.compare_digest`

- [ ] **Step 1: 寫測試**

`tests/test_signature.py`：

```python
import base64
import hashlib
import hmac

import pytest

from line_webhook_id_collector.signature import (
    BodyDecodeError,
    decode_body,
    is_plausible_signature,
    verify_signature,
)

SECRET = "test-channel-secret"
BODY = b'{"destination":"U0","events":[]}'


def sign(body: bytes, secret: str = SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def test_valid_signature() -> None:
    assert verify_signature(SECRET, BODY, sign(BODY)) is True


def test_invalid_signature() -> None:
    assert verify_signature(SECRET, BODY, sign(BODY, "other")) is False


def test_modified_body() -> None:
    assert verify_signature(SECRET, BODY + b" ", sign(BODY)) is False


def test_garbage_signature_does_not_raise() -> None:
    assert verify_signature(SECRET, BODY, "not base64!!") is False


def test_plausible_signature() -> None:
    assert is_plausible_signature(sign(BODY)) is True
    assert is_plausible_signature(None) is False
    assert is_plausible_signature("") is False
    assert is_plausible_signature("abc") is False
    assert is_plausible_signature(base64.b64encode(b"x" * 10).decode()) is False


def test_decode_plain_body() -> None:
    assert decode_body({"body": BODY.decode(), "isBase64Encoded": False}) == BODY


def test_decode_base64_body() -> None:
    encoded = base64.b64encode(BODY).decode()
    assert decode_body({"body": encoded, "isBase64Encoded": True}) == BODY


def test_decode_missing_body() -> None:
    assert decode_body({}) == b""


def test_decode_bad_base64() -> None:
    with pytest.raises(BodyDecodeError):
        decode_body({"body": "%%%", "isBase64Encoded": True})


def test_base64_body_signature_roundtrip() -> None:
    event = {"body": base64.b64encode(BODY).decode(), "isBase64Encoded": True}
    assert verify_signature(SECRET, decode_body(event), sign(BODY)) is True
```

- [ ] **Step 2: 執行確認失敗**

Run: `uv run pytest tests/test_signature.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 實作**

```python
"""LINE webhook 簽章驗證與 request body 還原。

刻意不使用 SDK 的 SignatureValidator：它接受 str 再重新編碼，
這裡直接以 bytes 驗證，保證使用逐位元組一致的原始 body。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping
from typing import Any


class BodyDecodeError(Exception):
    """API Gateway 標示 base64 但內容無法解碼。"""


def decode_body(event: Mapping[str, Any]) -> bytes:
    """從 API Gateway HTTP API (payload v2) 事件取出原始 body bytes。"""
    raw = event.get("body")
    if raw is None:
        return b""
    if not isinstance(raw, str):
        raise BodyDecodeError("body is not a string")
    if event.get("isBase64Encoded"):
        try:
            return base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BodyDecodeError("body is not valid base64") from exc
    return raw.encode("utf-8")


def is_plausible_signature(signature: str | None) -> bool:
    """在讀 secret 之前先便宜地過濾明顯不合法的簽章標頭。"""
    if not signature:
        return False
    try:
        return len(base64.b64decode(signature, validate=True)) == hashlib.sha256().digest_size
    except (binascii.Error, ValueError):
        return False


def verify_signature(channel_secret: str, body: bytes, signature: str) -> bool:
    """HMAC-SHA256(channel_secret, body) 的 base64 是否等於 signature（constant-time）。"""
    try:
        provided = base64.b64decode(signature, validate=True)
    except (binascii.Error, ValueError):
        return False
    expected = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(expected, provided)
```

- [ ] **Step 4: 執行測試與 lint**

Run: `uv run pytest tests/test_signature.py -q && uv run ruff check . && uv run ruff format .`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat: add LINE signature verification with raw body handling"
```

---

### Task 4: secrets.py

**Files:**
- Create: `src/line_webhook_id_collector/secrets.py`
- Test: `tests/test_secrets.py`

**Interfaces:**
- Produces:
  - `BotSecret`（frozen dataclass）：`channel_secret: str`、`channel_access_token: str | None`、`bootstrap_admin_user_id: str | None`
  - `class SecretNotFound(Exception)`、`class SecretConfigError(Exception)`
  - `SecretCache(client, prefix: str, ttl_seconds: int, max_entries: int = 100, clock=time.monotonic)`
  - `SecretCache.get(bot_id: str) -> BotSecret`

- [ ] **Step 1: 寫測試**

`tests/test_secrets.py`：

```python
import json

import boto3
import pytest
from moto import mock_aws

from line_webhook_id_collector.secrets import (
    SecretCache,
    SecretConfigError,
    SecretNotFound,
)

PREFIX = "line-webhook-id-collector/"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def sm():
    with mock_aws():
        yield boto3.client("secretsmanager", region_name="ap-northeast-1")


def put(sm, bot_id: str, payload: dict | str) -> None:
    value = payload if isinstance(payload, str) else json.dumps(payload)
    sm.create_secret(Name=f"{PREFIX}{bot_id}", SecretString=value)


def test_get_full_secret(sm) -> None:
    put(sm, "alert-bot", {"channel_secret": "s", "channel_access_token": "t",
                          "bootstrap_admin_user_id": "U" + "a" * 32})
    cache = SecretCache(sm, PREFIX, ttl_seconds=300)
    secret = cache.get("alert-bot")
    assert secret.channel_secret == "s"
    assert secret.channel_access_token == "t"
    assert secret.bootstrap_admin_user_id == "U" + "a" * 32


def test_optional_fields_default_none(sm) -> None:
    put(sm, "b", {"channel_secret": "s"})
    secret = SecretCache(sm, PREFIX, 300).get("b")
    assert secret.channel_access_token is None
    assert secret.bootstrap_admin_user_id is None


def test_missing_channel_secret_is_config_error(sm) -> None:
    put(sm, "b", {"channel_access_token": "t"})
    with pytest.raises(SecretConfigError):
        SecretCache(sm, PREFIX, 300).get("b")


def test_invalid_json_is_config_error(sm) -> None:
    put(sm, "b", "not json")
    with pytest.raises(SecretConfigError):
        SecretCache(sm, PREFIX, 300).get("b")


def test_not_found(sm) -> None:
    with pytest.raises(SecretNotFound):
        SecretCache(sm, PREFIX, 300).get("nope")


def test_cache_hit_avoids_second_call(sm) -> None:
    put(sm, "b", {"channel_secret": "s"})
    clock = FakeClock()
    cache = SecretCache(sm, PREFIX, 300, clock=clock)
    cache.get("b")
    sm.delete_secret(SecretId=f"{PREFIX}b", ForceDeleteWithoutRecovery=True)
    assert cache.get("b").channel_secret == "s"


def test_cache_expires_after_ttl(sm) -> None:
    put(sm, "b", {"channel_secret": "s"})
    clock = FakeClock()
    cache = SecretCache(sm, PREFIX, 300, clock=clock)
    cache.get("b")
    sm.update_secret(SecretId=f"{PREFIX}b", SecretString=json.dumps({"channel_secret": "s2"}))
    clock.now += 301
    assert cache.get("b").channel_secret == "s2"


def test_negative_cache(sm) -> None:
    clock = FakeClock()
    cache = SecretCache(sm, PREFIX, 300, clock=clock)
    with pytest.raises(SecretNotFound):
        cache.get("later")
    put(sm, "later", {"channel_secret": "s"})
    with pytest.raises(SecretNotFound):
        cache.get("later")
    clock.now += 301
    assert cache.get("later").channel_secret == "s"


def test_cache_bounded(sm) -> None:
    cache = SecretCache(sm, PREFIX, 300, max_entries=3)
    for i in range(10):
        with pytest.raises(SecretNotFound):
            cache.get(f"bot-{i}")
    assert len(cache._entries) <= 3
```

- [ ] **Step 2: 執行確認失敗**

Run: `uv run pytest tests/test_secrets.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 實作**

```python
"""以 bot_id 讀取 Secrets Manager 中的 Bot 憑證，含 TTL 快取與負快取。"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError


class SecretNotFound(Exception):
    """對應 bot_id 的 secret 不存在。"""


class SecretConfigError(Exception):
    """secret 存在但內容不合法（非 JSON 或缺 channel_secret）。"""


@dataclass(frozen=True)
class BotSecret:
    channel_secret: str
    channel_access_token: str | None = None
    bootstrap_admin_user_id: str | None = None


def _parse(raw: str) -> BotSecret:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SecretConfigError("secret is not valid JSON") from exc
    if not isinstance(data, dict):
        raise SecretConfigError("secret must be a JSON object")
    channel_secret = data.get("channel_secret")
    if not isinstance(channel_secret, str) or not channel_secret:
        raise SecretConfigError("channel_secret is required")
    return BotSecret(
        channel_secret=channel_secret,
        channel_access_token=data.get("channel_access_token") or None,
        bootstrap_admin_user_id=data.get("bootstrap_admin_user_id") or None,
    )


class SecretCache:
    """bot_id → BotSecret 的快取。找不到的結果也快取（負快取），避免被隨機 bot_id 打爆。"""

    def __init__(
        self,
        client: Any,
        prefix: str,
        ttl_seconds: int,
        max_entries: int = 100,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._prefix = prefix
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        # value: (expires_at, BotSecret | None)；None 代表負快取
        self._entries: OrderedDict[str, tuple[float, BotSecret | None]] = OrderedDict()

    def get(self, bot_id: str) -> BotSecret:
        now = self._clock()
        cached = self._entries.get(bot_id)
        if cached is not None and cached[0] > now:
            self._entries.move_to_end(bot_id)
            if cached[1] is None:
                raise SecretNotFound(bot_id)
            return cached[1]

        try:
            response = self._client.get_secret_value(SecretId=f"{self._prefix}{bot_id}")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                self._store(bot_id, None, now)
                raise SecretNotFound(bot_id) from exc
            raise

        secret = _parse(response.get("SecretString") or "")
        self._store(bot_id, secret, now)
        return secret

    def _store(self, bot_id: str, secret: BotSecret | None, now: float) -> None:
        self._entries[bot_id] = (now + self._ttl, secret)
        self._entries.move_to_end(bot_id)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)
```

- [ ] **Step 4: 執行測試與 lint**

Run: `uv run pytest tests/test_secrets.py -q && uv run ruff check . && uv run ruff format .`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat: add per-bot secret cache with negative caching"
```

---

### Task 5: events.py

**Files:**
- Create: `src/line_webhook_id_collector/events.py`
- Test: `tests/test_events.py`、`tests/fixtures/*.json`

**Interfaces:**
- Produces:
  - `Target`（frozen dataclass）：`target_id: str`、`target_type: str`（`user` / `group` / `room`）
  - `CommandCandidate`（frozen dataclass）：`user_id: str`、`text: str`、`reply_token: str`
  - `EventOutcome`（frozen dataclass）：`event_type: str`、`source_type: str | None`、`active_targets: tuple[Target, ...]`、`inactive_targets: tuple[Target, ...]`、`event_ts: int`、`is_redelivery: bool`、`command: CommandCandidate | None`
  - `analyze_event(event: Any, now_ms: int) -> EventOutcome | None`：非 dict 或缺 `type` 回 `None`
  - `iso_from_ms(ms: int) -> str`：`2026-09-12T06:30:00Z`
  - `USER_ID_RE`：編譯後的 `^U[0-9a-f]{32}$`

- [ ] **Step 1: 建立 fixtures**

`tests/fixtures/` 下各檔案（假 ID：`U` + 32 個 `a`、`C` + 32 個 `b`、`R` + 32 個 `c`）：

`follow.json`：
```json
{"type":"follow","timestamp":1789194900000,"replyToken":"rt-1","mode":"active",
 "webhookEventId":"01","deliveryContext":{"isRedelivery":false},
 "source":{"type":"user","userId":"Uaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}
```

`unfollow.json`：
```json
{"type":"unfollow","timestamp":1789195000000,"mode":"active","webhookEventId":"02",
 "deliveryContext":{"isRedelivery":false},
 "source":{"type":"user","userId":"Uaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}
```

`join_group.json`：
```json
{"type":"join","timestamp":1789194900000,"replyToken":"rt-2","mode":"active",
 "webhookEventId":"03","deliveryContext":{"isRedelivery":false},
 "source":{"type":"group","groupId":"Cbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}
```

`join_room.json`：
```json
{"type":"join","timestamp":1789194900000,"replyToken":"rt-3","mode":"active",
 "webhookEventId":"04","deliveryContext":{"isRedelivery":false},
 "source":{"type":"room","roomId":"Rcccccccccccccccccccccccccccccccc"}}
```

`leave_group.json`：
```json
{"type":"leave","timestamp":1789195100000,"mode":"active","webhookEventId":"05",
 "deliveryContext":{"isRedelivery":false},
 "source":{"type":"group","groupId":"Cbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}
```

`member_joined.json`：
```json
{"type":"memberJoined","timestamp":1789194900000,"replyToken":"rt-4","mode":"active",
 "webhookEventId":"06","deliveryContext":{"isRedelivery":false},
 "source":{"type":"group","groupId":"Cbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
 "joined":{"members":[{"type":"user","userId":"Uaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                      {"type":"user","userId":"Udddddddddddddddddddddddddddddddd"}]}}
```

`message_user.json`：
```json
{"type":"message","timestamp":1789194900000,"replyToken":"rt-5","mode":"active",
 "webhookEventId":"07","deliveryContext":{"isRedelivery":false},
 "source":{"type":"user","userId":"Uaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
 "message":{"id":"m1","type":"text","text":"/id"}}
```

`message_group_with_user.json`：
```json
{"type":"message","timestamp":1789194900000,"replyToken":"rt-6","mode":"active",
 "webhookEventId":"08","deliveryContext":{"isRedelivery":false},
 "source":{"type":"group","groupId":"Cbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
           "userId":"Uaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
 "message":{"id":"m2","type":"text","text":"/id"}}
```

`message_group_without_user.json`：
```json
{"type":"message","timestamp":1789194900000,"replyToken":"rt-7","mode":"active",
 "webhookEventId":"09","deliveryContext":{"isRedelivery":false},
 "source":{"type":"group","groupId":"Cbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
 "message":{"id":"m3","type":"text","text":"hello"}}
```

`message_room.json`：
```json
{"type":"message","timestamp":1789194900000,"replyToken":"rt-8","mode":"active",
 "webhookEventId":"10","deliveryContext":{"isRedelivery":false},
 "source":{"type":"room","roomId":"Rcccccccccccccccccccccccccccccccc",
           "userId":"Uaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
 "message":{"id":"m4","type":"text","text":"hi"}}
```

`message_user_sticker.json`：
```json
{"type":"message","timestamp":1789194900000,"replyToken":"rt-9","mode":"active",
 "webhookEventId":"11","deliveryContext":{"isRedelivery":false},
 "source":{"type":"user","userId":"Uaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
 "message":{"id":"m5","type":"sticker","packageId":"1","stickerId":"1"}}
```

`message_user_redelivery.json`：
```json
{"type":"message","timestamp":1789194900000,"replyToken":"rt-10","mode":"active",
 "webhookEventId":"07","deliveryContext":{"isRedelivery":true},
 "source":{"type":"user","userId":"Uaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
 "message":{"id":"m1","type":"text","text":"/id"}}
```

`postback.json`：
```json
{"type":"postback","timestamp":1789194900000,"replyToken":"rt-11","mode":"active",
 "webhookEventId":"12","deliveryContext":{"isRedelivery":false},
 "source":{"type":"user","userId":"Uaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
 "postback":{"data":"x"}}
```

`account_link_no_source.json`：
```json
{"type":"accountLink","timestamp":1789194900000,"mode":"active","webhookEventId":"13",
 "deliveryContext":{"isRedelivery":false},"link":{"result":"failed","nonce":"n"}}
```

- [ ] **Step 2: 寫測試**

`tests/test_events.py`：

```python
import json
from pathlib import Path

from line_webhook_id_collector.events import Target, analyze_event, iso_from_ms

FIXTURES = Path(__file__).parent / "fixtures"
U = "U" + "a" * 32
U2 = "U" + "d" * 32
G = "C" + "b" * 32
R = "R" + "c" * 32
NOW = 1789195399000


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_iso_from_ms() -> None:
    assert iso_from_ms(1789194900000) == "2026-09-12T06:35:00Z"


def test_follow() -> None:
    out = analyze_event(load("follow.json"), NOW)
    assert out.event_type == "follow"
    assert out.source_type == "user"
    assert out.active_targets == (Target(U, "user"),)
    assert out.inactive_targets == ()
    assert out.event_ts == 1789194900000
    assert out.command is None


def test_unfollow_marks_inactive_only() -> None:
    out = analyze_event(load("unfollow.json"), NOW)
    assert out.active_targets == ()
    assert out.inactive_targets == (Target(U, "user"),)


def test_join_group_and_room() -> None:
    assert analyze_event(load("join_group.json"), NOW).active_targets == (Target(G, "group"),)
    assert analyze_event(load("join_room.json"), NOW).active_targets == (Target(R, "room"),)


def test_leave_marks_group_inactive() -> None:
    out = analyze_event(load("leave_group.json"), NOW)
    assert out.active_targets == ()
    assert out.inactive_targets == (Target(G, "group"),)


def test_member_joined_collects_members_and_group() -> None:
    out = analyze_event(load("member_joined.json"), NOW)
    assert set(out.active_targets) == {Target(G, "group"), Target(U, "user"), Target(U2, "user")}


def test_message_user_yields_command_candidate() -> None:
    out = analyze_event(load("message_user.json"), NOW)
    assert out.active_targets == (Target(U, "user"),)
    assert out.command is not None
    assert out.command.user_id == U
    assert out.command.text == "/id"
    assert out.command.reply_token == "rt-5"


def test_message_group_with_user() -> None:
    out = analyze_event(load("message_group_with_user.json"), NOW)
    assert set(out.active_targets) == {Target(G, "group"), Target(U, "user")}
    assert out.command is None


def test_message_group_without_user() -> None:
    out = analyze_event(load("message_group_without_user.json"), NOW)
    assert out.active_targets == (Target(G, "group"),)


def test_message_room() -> None:
    out = analyze_event(load("message_room.json"), NOW)
    assert set(out.active_targets) == {Target(R, "room"), Target(U, "user")}
    assert out.command is None


def test_non_text_message_has_no_command() -> None:
    assert analyze_event(load("message_user_sticker.json"), NOW).command is None


def test_redelivery_flag() -> None:
    out = analyze_event(load("message_user_redelivery.json"), NOW)
    assert out.is_redelivery is True
    assert out.command is not None  # 由 handler 決定不執行


def test_unsupported_event_still_extracts_source() -> None:
    out = analyze_event(load("postback.json"), NOW)
    assert out.event_type == "postback"
    assert out.active_targets == (Target(U, "user"),)


def test_event_without_source() -> None:
    out = analyze_event(load("account_link_no_source.json"), NOW)
    assert out.active_targets == ()
    assert out.source_type is None


def test_missing_timestamp_uses_now() -> None:
    event = load("follow.json")
    del event["timestamp"]
    assert analyze_event(event, NOW).event_ts == NOW


def test_invalid_events_return_none() -> None:
    assert analyze_event("nope", NOW) is None
    assert analyze_event({"source": {}}, NOW) is None
    assert analyze_event({"type": 5}, NOW) is None
```

- [ ] **Step 3: 執行確認失敗**

Run: `uv run pytest tests/test_events.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 4: 實作**

```python
"""分析單一 LINE webhook 事件，抽出要儲存的 target 與可能的指令。

此模組是純函式，不碰 AWS 或 LINE API。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

USER_ID_RE = re.compile(r"^U[0-9a-f]{32}$")

_SOURCE_ID_FIELDS: tuple[tuple[str, str], ...] = (
    ("groupId", "group"),
    ("roomId", "room"),
    ("userId", "user"),
)


@dataclass(frozen=True)
class Target:
    target_id: str
    target_type: str


@dataclass(frozen=True)
class CommandCandidate:
    user_id: str
    text: str
    reply_token: str


@dataclass(frozen=True)
class EventOutcome:
    event_type: str
    source_type: str | None
    active_targets: tuple[Target, ...]
    inactive_targets: tuple[Target, ...]
    event_ts: int
    is_redelivery: bool
    command: CommandCandidate | None


def iso_from_ms(ms: int) -> str:
    """毫秒 epoch → `YYYY-MM-DDTHH:MM:SSZ`。"""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _targets_from_source(source: Any) -> tuple[Target, ...]:
    if not isinstance(source, dict):
        return ()
    found: list[Target] = []
    for field, target_type in _SOURCE_ID_FIELDS:
        value = source.get(field)
        if isinstance(value, str) and value:
            found.append(Target(value, target_type))
    return tuple(found)


def _targets_from_members(event: dict[str, Any]) -> tuple[Target, ...]:
    members = (event.get("joined") or {}).get("members")
    if not isinstance(members, list):
        return ()
    found: list[Target] = []
    for member in members:
        if isinstance(member, dict):
            user_id = member.get("userId")
            if isinstance(user_id, str) and user_id:
                found.append(Target(user_id, "user"))
    return tuple(found)


def _command_candidate(event: dict[str, Any], source: Any) -> CommandCandidate | None:
    if not isinstance(source, dict) or source.get("type") != "user":
        return None
    message = event.get("message")
    if not isinstance(message, dict) or message.get("type") != "text":
        return None
    user_id = source.get("userId")
    text = message.get("text")
    reply_token = event.get("replyToken")
    if not (isinstance(user_id, str) and isinstance(text, str) and isinstance(reply_token, str)):
        return None
    return CommandCandidate(user_id=user_id, text=text, reply_token=reply_token)


def analyze_event(event: Any, now_ms: int) -> EventOutcome | None:
    """回傳事件分析結果；事件不是物件或缺少 type 時回 None。"""
    if not isinstance(event, dict):
        return None
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type:
        return None

    source = event.get("source")
    source_type = source.get("type") if isinstance(source, dict) else None
    if not isinstance(source_type, str):
        source_type = None

    timestamp = event.get("timestamp")
    event_ts = timestamp if isinstance(timestamp, int) and not isinstance(timestamp, bool) else now_ms

    delivery = event.get("deliveryContext")
    is_redelivery = isinstance(delivery, dict) and delivery.get("isRedelivery") is True

    source_targets = _targets_from_source(source)
    active: tuple[Target, ...]
    inactive: tuple[Target, ...]
    command: CommandCandidate | None = None

    if event_type in ("unfollow", "leave"):
        active, inactive = (), source_targets
    elif event_type == "memberJoined":
        active, inactive = source_targets + _targets_from_members(event), ()
    elif event_type == "message":
        active, inactive = source_targets, ()
        command = _command_candidate(event, source)
    else:
        active, inactive = source_targets, ()

    return EventOutcome(
        event_type=event_type,
        source_type=source_type,
        active_targets=active,
        inactive_targets=inactive,
        event_ts=event_ts,
        is_redelivery=is_redelivery,
        command=command,
    )
```

- [ ] **Step 5: 執行測試與 lint**

Run: `uv run pytest tests/test_events.py -q && uv run ruff check . && uv run ruff format .`
Expected: 17 passed

- [ ] **Step 6: Commit**

```bash
git add src tests
git commit -m "feat: add pure event analysis with target extraction"
```

---

### Task 6: repository.py

**Files:**
- Create: `src/line_webhook_id_collector/repository.py`
- Test: `tests/test_repository.py`

**Interfaces:**
- Consumes: `Target`、`iso_from_ms` from `events.py`
- Produces:
  - `TargetRepository(table_name: str, resource=None)`：`resource` 未給時 `boto3.resource("dynamodb")`
  - `upsert(bot_id, target: Target, status: str, event_ts: int, event_type: str) -> str`：回 `"stored"`、`"marked_inactive"` 或 `"stale"`
  - `get(bot_id, target_id) -> dict | None`：consistent read
  - `list_targets(bot_id) -> list[dict]`：追蹤分頁，回全部記錄
  - `add_admin(bot_id, user_id, now_ms: int) -> None`
  - `remove_admin(bot_id, user_id) -> bool`：條件失敗回 `False`
  - `create_table(resource, table_name) -> None`：測試與本機用，建立與 SAM 相同 schema 的 table

- [ ] **Step 1: 寫測試**

`tests/test_repository.py`：

```python
import boto3
import pytest
from moto import mock_aws

from line_webhook_id_collector.events import Target
from line_webhook_id_collector.repository import TargetRepository, create_table

BOT = "alert-bot"
U = "U" + "a" * 32
G = "C" + "b" * 32
T1 = 1789194900000
T2 = 1789195000000


@pytest.fixture
def repo():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="ap-northeast-1")
        create_table(resource, "targets")
        yield TargetRepository("targets", resource=resource)


def test_insert_new_target(repo) -> None:
    assert repo.upsert(BOT, Target(U, "user"), "active", T1, "follow") == "stored"
    item = repo.get(BOT, U)
    assert item["bot_id"] == BOT
    assert item["target_id"] == U
    assert item["target_type"] == "user"
    assert item["status"] == "active"
    assert item["first_seen_at"] == "2026-09-12T06:35:00Z"
    assert item["last_seen_at"] == "2026-09-12T06:35:00Z"
    assert int(item["last_event_ts"]) == T1
    assert item["last_event_type"] == "follow"
    assert "role" not in item


def test_update_preserves_first_seen(repo) -> None:
    repo.upsert(BOT, Target(U, "user"), "active", T1, "follow")
    repo.upsert(BOT, Target(U, "user"), "active", T2, "message")
    item = repo.get(BOT, U)
    assert item["first_seen_at"] == "2026-09-12T06:35:00Z"
    assert item["last_seen_at"] == "2026-09-12T06:36:40Z"
    assert item["last_event_type"] == "message"


def test_duplicate_event_is_harmless(repo) -> None:
    repo.upsert(BOT, Target(U, "user"), "active", T1, "follow")
    assert repo.upsert(BOT, Target(U, "user"), "active", T1, "follow") == "stored"
    assert len(repo.list_targets(BOT)) == 1


def test_stale_event_rejected(repo) -> None:
    repo.upsert(BOT, Target(U, "user"), "inactive", T2, "unfollow")
    assert repo.upsert(BOT, Target(U, "user"), "active", T1, "message") == "stale"
    assert repo.get(BOT, U)["status"] == "inactive"


def test_mark_inactive_then_reactivate(repo) -> None:
    repo.upsert(BOT, Target(G, "group"), "active", T1, "join")
    assert repo.upsert(BOT, Target(G, "group"), "inactive", T2, "leave") == "marked_inactive"
    assert repo.get(BOT, G)["status"] == "inactive"
    repo.upsert(BOT, Target(G, "group"), "active", T2 + 1, "join")
    assert repo.get(BOT, G)["status"] == "active"


def test_upsert_does_not_touch_role(repo) -> None:
    repo.add_admin(BOT, U, T1)
    repo.upsert(BOT, Target(U, "user"), "active", T2, "message")
    assert repo.get(BOT, U)["role"] == "admin"


def test_add_admin_creates_full_record(repo) -> None:
    repo.add_admin(BOT, U, T1)
    item = repo.get(BOT, U)
    assert item["role"] == "admin"
    assert item["target_type"] == "user"
    assert item["status"] == "active"
    assert item["first_seen_at"] == "2026-09-12T06:35:00Z"
    assert item["last_event_type"] == "admin_add"


def test_add_admin_keeps_existing_observation(repo) -> None:
    repo.upsert(BOT, Target(U, "user"), "active", T1, "follow")
    repo.add_admin(BOT, U, T2)
    item = repo.get(BOT, U)
    assert item["last_event_type"] == "follow"
    assert item["role"] == "admin"


def test_remove_admin(repo) -> None:
    repo.add_admin(BOT, U, T1)
    assert repo.remove_admin(BOT, U) is True
    assert "role" not in repo.get(BOT, U)


def test_remove_admin_missing_does_not_create(repo) -> None:
    assert repo.remove_admin(BOT, U) is False
    assert repo.get(BOT, U) is None


def test_remove_admin_non_admin_returns_false(repo) -> None:
    repo.upsert(BOT, Target(U, "user"), "active", T1, "follow")
    assert repo.remove_admin(BOT, U) is False


def test_get_missing(repo) -> None:
    assert repo.get(BOT, "U" + "f" * 32) is None


def test_list_is_scoped_by_bot(repo) -> None:
    repo.upsert(BOT, Target(U, "user"), "active", T1, "follow")
    repo.upsert("other", Target(G, "group"), "active", T1, "join")
    ids = {i["target_id"] for i in repo.list_targets(BOT)}
    assert ids == {U}


def test_list_follows_pagination(repo, monkeypatch) -> None:
    # 用假的分頁回應驗證 LastEvaluatedKey 有被追蹤（真的寫到 1MB 太慢）
    pages = [
        {"Items": [{"target_id": "a"}], "LastEvaluatedKey": {"bot_id": BOT, "target_id": "a"}},
        {"Items": [{"target_id": "b"}]},
    ]
    seen_kwargs: list[dict] = []

    def fake_query(**kwargs):
        seen_kwargs.append(kwargs)
        return pages[len(seen_kwargs) - 1]

    monkeypatch.setattr(repo._table, "query", fake_query)
    items = repo.list_targets(BOT)
    assert [i["target_id"] for i in items] == ["a", "b"]
    assert "ExclusiveStartKey" not in seen_kwargs[0]
    assert seen_kwargs[1]["ExclusiveStartKey"] == {"bot_id": BOT, "target_id": "a"}
```

- [ ] **Step 2: 執行確認失敗**

Run: `uv run pytest tests/test_repository.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 實作**

```python
"""DynamoDB targets table 的存取層。

所有寫入皆為 UpdateItem；`status` 與 `role` 是保留字，一律以 ExpressionAttributeNames 引用。
"""

from __future__ import annotations

from typing import Any

import boto3
from botocore.exceptions import ClientError

from line_webhook_id_collector.events import Target, iso_from_ms

_NAMES = {"#status": "status", "#role": "role"}


def create_table(resource: Any, table_name: str) -> None:
    """建立與 template.yaml 相同 schema 的 table（測試與本機用）。"""
    table = resource.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "bot_id", "KeyType": "HASH"},
            {"AttributeName": "target_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "bot_id", "AttributeType": "S"},
            {"AttributeName": "target_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()


class TargetRepository:
    def __init__(self, table_name: str, resource: Any = None) -> None:
        resource = resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)

    def upsert(
        self, bot_id: str, target: Target, status: str, event_ts: int, event_type: str
    ) -> str:
        """寫入觀察結果。事件比既有記錄舊時回 'stale' 且不改動。"""
        event_time = iso_from_ms(event_ts)
        try:
            self._table.update_item(
                Key={"bot_id": bot_id, "target_id": target.target_id},
                UpdateExpression=(
                    "SET target_type = :type, #status = :status, "
                    "last_seen_at = :event_time, last_event_ts = :event_ts, "
                    "last_event_type = :event_type, "
                    "first_seen_at = if_not_exists(first_seen_at, :event_time)"
                ),
                ConditionExpression=(
                    "attribute_not_exists(last_event_ts) OR last_event_ts <= :event_ts"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":type": target.target_type,
                    ":status": status,
                    ":event_time": event_time,
                    ":event_ts": event_ts,
                    ":event_type": event_type,
                },
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return "stale"
            raise
        return "marked_inactive" if status == "inactive" else "stored"

    def get(self, bot_id: str, target_id: str) -> dict[str, Any] | None:
        """強一致讀取單筆記錄。"""
        response = self._table.get_item(
            Key={"bot_id": bot_id, "target_id": target_id}, ConsistentRead=True
        )
        return response.get("Item")

    def list_targets(self, bot_id: str) -> list[dict[str, Any]]:
        """讀出該 Bot 的全部記錄，追蹤分頁。"""
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "bot_id = :b",
            "ExpressionAttributeValues": {":b": bot_id},
        }
        while True:
            response = self._table.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            kwargs["ExclusiveStartKey"] = last_key

    def add_admin(self, bot_id: str, user_id: str, now_ms: int) -> None:
        """設定 role=admin；記錄不存在時建立完整記錄。"""
        now = iso_from_ms(now_ms)
        self._table.update_item(
            Key={"bot_id": bot_id, "target_id": user_id},
            UpdateExpression=(
                "SET #role = :admin, "
                "target_type = if_not_exists(target_type, :user), "
                "#status = if_not_exists(#status, :active), "
                "first_seen_at = if_not_exists(first_seen_at, :now), "
                "last_seen_at = if_not_exists(last_seen_at, :now), "
                "last_event_ts = if_not_exists(last_event_ts, :now_ts), "
                "last_event_type = if_not_exists(last_event_type, :admin_add)"
            ),
            ExpressionAttributeNames=_NAMES,
            ExpressionAttributeValues={
                ":admin": "admin",
                ":user": "user",
                ":active": "active",
                ":now": now,
                ":now_ts": now_ms,
                ":admin_add": "admin_add",
            },
        )

    def remove_admin(self, bot_id: str, user_id: str) -> bool:
        """移除 role；記錄不存在或不是 admin 時回 False，不建立空記錄。"""
        try:
            self._table.update_item(
                Key={"bot_id": bot_id, "target_id": user_id},
                UpdateExpression="REMOVE #role",
                ConditionExpression="attribute_exists(target_id) AND #role = :admin",
                ExpressionAttributeNames={"#role": "role"},
                ExpressionAttributeValues={":admin": "admin"},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True
```

- [ ] **Step 4: 執行測試與 lint**

Run: `uv run pytest tests/test_repository.py -q && uv run ruff check . && uv run ruff format .`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat: add DynamoDB repository with conditional upsert and admin role ops"
```

---

### Task 7: line_client.py

**Files:**
- Create: `src/line_webhook_id_collector/line_client.py`
- Test: `tests/test_line_client.py`

**Interfaces:**
- Produces:
  - `class LineApiError(Exception)`
  - `LineClient(access_token: str, connect_timeout: float = 2.0, read_timeout: float = 3.0)`
  - `reply(reply_token: str, texts: list[str]) -> None`：一次呼叫送出全部，失敗拋 `LineApiError`
  - `get_group_name(group_id: str) -> str | None`
  - `get_user_name(user_id: str) -> str | None`
  - 名稱查詢任何例外皆回 `None`

- [ ] **Step 1: 寫測試（用 monkeypatch 取代 SDK 的 MessagingApi）**

`tests/test_line_client.py`：

```python
import pytest

from line_webhook_id_collector import line_client as mod
from line_webhook_id_collector.line_client import LineApiError, LineClient


class FakeApi:
    def __init__(self) -> None:
        self.replies: list = []
        self.fail_reply = False
        self.fail_lookup = False

    def reply_message(self, request, _request_timeout=None):
        if self.fail_reply:
            raise RuntimeError("boom")
        self.replies.append(request)

    def get_group_summary(self, group_id, _request_timeout=None):
        if self.fail_lookup:
            raise RuntimeError("boom")
        return type("R", (), {"group_name": f"group:{group_id}"})()

    def get_profile(self, user_id, _request_timeout=None):
        if self.fail_lookup:
            raise RuntimeError("boom")
        return type("R", (), {"display_name": f"user:{user_id}"})()


@pytest.fixture
def client(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(LineClient, "_build_api", lambda self: fake)
    c = LineClient("token")
    c.fake = fake
    return c


def test_reply_sends_all_texts_in_one_call(client) -> None:
    client.reply("rt", ["a", "b"])
    assert len(client.fake.replies) == 1
    request = client.fake.replies[0]
    assert request.reply_token == "rt"
    assert [m.text for m in request.messages] == ["a", "b"]


def test_reply_failure_raises(client) -> None:
    client.fake.fail_reply = True
    with pytest.raises(LineApiError):
        client.reply("rt", ["a"])


def test_lookups(client) -> None:
    assert client.get_group_name("C1") == "group:C1"
    assert client.get_user_name("U1") == "user:U1"


def test_lookup_failure_returns_none(client) -> None:
    client.fake.fail_lookup = True
    assert client.get_group_name("C1") is None
    assert client.get_user_name("U1") is None


def test_real_api_is_constructed() -> None:
    api = LineClient("token")._build_api()
    assert hasattr(api, "reply_message")
    assert mod.MessagingApi is not None
```

- [ ] **Step 2: 執行確認失敗**

Run: `uv run pytest tests/test_line_client.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 實作**

```python
"""LINE Messaging API 的薄封裝：回覆與名稱查詢。

每次 HTTP 呼叫都有 timeout，不重試；名稱查詢失敗一律回 None。
"""

from __future__ import annotations

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)


class LineApiError(Exception):
    """Reply API 呼叫失敗。"""


class LineClient:
    def __init__(
        self, access_token: str, connect_timeout: float = 2.0, read_timeout: float = 3.0
    ) -> None:
        self._access_token = access_token
        self._timeout = (connect_timeout, read_timeout)
        self._api: MessagingApi | None = None

    def _build_api(self) -> MessagingApi:
        configuration = Configuration(access_token=self._access_token)
        return MessagingApi(ApiClient(configuration))

    @property
    def api(self) -> MessagingApi:
        if self._api is None:
            self._api = self._build_api()
        return self._api

    def reply(self, reply_token: str, texts: list[str]) -> None:
        """一次 reply 呼叫送出所有文字（最多 5 則，由呼叫端保證）。"""
        request = ReplyMessageRequest(
            reply_token=reply_token, messages=[TextMessage(text=t) for t in texts]
        )
        try:
            self.api.reply_message(request, _request_timeout=self._timeout)
        except Exception as exc:
            raise LineApiError(str(exc)) from exc

    def get_group_name(self, group_id: str) -> str | None:
        try:
            return self.api.get_group_summary(group_id, _request_timeout=self._timeout).group_name
        except Exception:
            return None

    def get_user_name(self, user_id: str) -> str | None:
        try:
            return self.api.get_profile(user_id, _request_timeout=self._timeout).display_name
        except Exception:
            return None
```

- [ ] **Step 4: 執行測試與 lint**

Run: `uv run pytest tests/test_line_client.py -q && uv run ruff check . && uv run ruff format .`
Expected: 5 passed。若 ruff 對 `except Exception` 報 `BLE001`，該規則不在 select 內，應不會出現；若出現則在該行加 `# noqa: BLE001`。

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat: add LINE Messaging API client wrapper with timeouts"
```

---

### Task 8: text_utils.py（UTF-16 長度與切段）

**Files:**
- Create: `src/line_webhook_id_collector/text_utils.py`
- Test: `tests/test_text_utils.py`

**Interfaces:**
- Produces:
  - `utf16_len(s: str) -> int`
  - `pack_blocks(blocks: list[str], max_len: int = 5000, max_messages: int = 5, trailer: Callable[[int], str] | None = None) -> list[str]`：把 block 用 `\n` 串接成訊息，不在 block 中間切；放不下時呼叫 `trailer(remaining_count)` 取得尾註並附在最後一則末尾，尾註長度納入計算；單一 block 超過 `max_len` 時硬切
  - 常數 `LINE_TEXT_MAX = 5000`、`LINE_REPLY_MAX_MESSAGES = 5`

- [ ] **Step 1: 寫測試**

`tests/test_text_utils.py`：

```python
from line_webhook_id_collector.text_utils import pack_blocks, utf16_len


def test_utf16_len_counts_surrogate_pairs() -> None:
    assert utf16_len("abc") == 3
    assert utf16_len("中文") == 2
    assert utf16_len("😀") == 2


def test_pack_single_message() -> None:
    assert pack_blocks(["a", "b"], max_len=100) == ["a\nb"]


def test_pack_splits_on_block_boundary() -> None:
    blocks = ["x" * 40, "y" * 40, "z" * 40]
    out = pack_blocks(blocks, max_len=85)
    assert out == ["x" * 40 + "\n" + "y" * 40, "z" * 40]


def test_pack_respects_utf16() -> None:
    out = pack_blocks(["😀" * 3, "😀" * 3], max_len=7)
    assert out == ["😀" * 3, "😀" * 3]


def test_pack_trailer_when_overflow() -> None:
    blocks = [f"item{i}" for i in range(20)]  # 每個 5~6 字
    out = pack_blocks(blocks, max_len=20, max_messages=2, trailer=lambda n: f"+{n} more")
    assert len(out) == 2
    assert out[-1].endswith(" more")
    shown = sum(msg.count("item") for msg in out)
    remaining = int(out[-1].rsplit("+", 1)[1].split()[0])
    assert shown + remaining == 20
    for msg in out:
        assert utf16_len(msg) <= 20


def test_pack_empty() -> None:
    assert pack_blocks([]) == []


def test_pack_hard_splits_oversized_block() -> None:
    out = pack_blocks(["a" * 12], max_len=5)
    assert out == ["aaaaa", "aaaaa", "aa"]
```

- [ ] **Step 2: 執行確認失敗**

Run: `uv run pytest tests/test_text_utils.py -q`
Expected: FAIL

- [ ] **Step 3: 實作**

```python
"""LINE 文字訊息的長度計算與切段。LINE 以 UTF-16 code unit 計算長度。"""

from __future__ import annotations

from collections.abc import Callable

LINE_TEXT_MAX = 5000
LINE_REPLY_MAX_MESSAGES = 5


def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _hard_split(block: str, max_len: int) -> list[str]:
    """單一 block 超過上限時依 code point 硬切（不會切在 surrogate pair 中間）。"""
    pieces: list[str] = []
    current = ""
    for ch in block:
        if utf16_len(current + ch) > max_len:
            pieces.append(current)
            current = ch
        else:
            current += ch
    if current:
        pieces.append(current)
    return pieces


def pack_blocks(
    blocks: list[str],
    max_len: int = LINE_TEXT_MAX,
    max_messages: int = LINE_REPLY_MAX_MESSAGES,
    trailer: Callable[[int], str] | None = None,
) -> list[str]:
    """把 block 串成最多 max_messages 則訊息；放不下時在最後一則附上 trailer。"""
    expanded: list[str] = []
    for block in blocks:
        expanded.extend(_hard_split(block, max_len) if utf16_len(block) > max_len else [block])

    messages: list[str] = []
    current = ""
    index = 0
    while index < len(expanded):
        block = expanded[index]
        candidate = block if not current else f"{current}\n{block}"
        if utf16_len(candidate) <= max_len:
            current = candidate
            index += 1
            continue
        messages.append(current)
        current = ""
        if len(messages) == max_messages:
            break
    else:
        if current:
            messages.append(current)
        return messages

    # 走到這裡代表已達 max_messages 但還有 block 沒放
    remaining = len(expanded) - index
    if trailer is None:
        return messages
    # 從最後一則的尾端移除 block，直到放得下尾註
    last_blocks = messages[-1].split("\n")
    while last_blocks:
        note = trailer(remaining)
        candidate = "\n".join([*last_blocks, note])
        if utf16_len(candidate) <= max_len:
            messages[-1] = candidate
            return messages
        last_blocks.pop()
        remaining += 1
    messages[-1] = trailer(remaining)[:max_len]
    return messages
```

- [ ] **Step 4: 執行測試與 lint**

Run: `uv run pytest tests/test_text_utils.py -q && uv run ruff check . && uv run ruff format .`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat: add UTF-16 aware message packing"
```

---

### Task 9: commands.py

**Files:**
- Create: `src/line_webhook_id_collector/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `TargetRepository`（Task 6）、`LineClient`（Task 7）、`BotSecret`（Task 4）、`Settings`（Task 2）、`pack_blocks`（Task 8）、`USER_ID_RE`（Task 5）
- Produces:
  - `CommandContext`（dataclass）：`bot_id: str`、`settings: Settings`、`secret: BotSecret`、`repo: TargetRepository`、`client: LineClient | None`、`sender_id: str`、`now_ms: int`、`clock: Callable[[], float] = time.monotonic`
  - `is_admin(repo, bot_id, secret, user_id) -> bool`
  - `execute_command(text: str, ctx: CommandContext) -> list[str] | None`：`None` 代表靜默；否則為要回覆的訊息清單（已切段，最多 5 則）
  - `HELP_TEXT` 常數

**行為規格**（對照 PRD 第 11 節）：
- `execute_command` 自己不檢查 `is_admin`，由 handler 先檢查（handler 需先做 consistent read；分開讓測試更簡單）
- 第一個 token 小寫化後不在 `{"/id", "/list", "/help", "/admin"}` → `None`
- `/list` 第二個 token 允許 `groups`、`users`、`rooms`、`all`，其他 → 錯誤訊息 `Usage: /list [groups|users|rooms|all]`
- `/admin` 需要 `list` / `add <id>` / `remove <id>`，其他 → `Usage: /admin list | /admin add <userId> | /admin remove <userId>`
- ID 格式錯誤 → `Invalid user ID format.`

- [ ] **Step 1: 寫測試**

`tests/test_commands.py`：

```python
import pytest

from line_webhook_id_collector.commands import CommandContext, execute_command, is_admin
from line_webhook_id_collector.config import Settings
from line_webhook_id_collector.secrets import BotSecret
from line_webhook_id_collector.text_utils import utf16_len

BOT = "alert-bot"
BOOT = "U" + "0" * 32
ADMIN2 = "U" + "1" * 32
USER = "U" + "2" * 32
G1 = "C" + "a" * 32
G2 = "C" + "b" * 32
R1 = "R" + "c" * 32
NOW = 1789195399000


class FakeRepo:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def _put(self, target_id, target_type, status="active", ts=1, role=None):
        item = {
            "bot_id": BOT, "target_id": target_id, "target_type": target_type,
            "status": status, "last_event_ts": ts, "first_seen_at": "x", "last_seen_at": "y",
            "last_event_type": "message",
        }
        if role:
            item["role"] = role
        self.items[target_id] = item

    def get(self, bot_id, target_id):
        return self.items.get(target_id)

    def list_targets(self, bot_id):
        return list(self.items.values())

    def add_admin(self, bot_id, user_id, now_ms):
        if user_id in self.items:
            self.items[user_id]["role"] = "admin"
        else:
            self._put(user_id, "user", role="admin")

    def remove_admin(self, bot_id, user_id):
        item = self.items.get(user_id)
        if not item or item.get("role") != "admin":
            return False
        del item["role"]
        return True


class FakeClient:
    def __init__(self) -> None:
        self.names = {}
        self.lookups = 0
        self.delay = 0.0
        self.time = [0.0]

    def get_group_name(self, group_id):
        self.lookups += 1
        self.time[0] += self.delay
        return self.names.get(group_id)

    def get_user_name(self, user_id):
        self.lookups += 1
        self.time[0] += self.delay
        return self.names.get(user_id)


def make_ctx(repo=None, client=None, sender=BOOT, token="tok", limit=50, budget=8.0):
    repo = repo or FakeRepo()
    client = client if client is not None else FakeClient()
    return CommandContext(
        bot_id=BOT,
        settings=Settings(table_name="t", name_lookup_limit=limit,
                          name_lookup_budget_seconds=budget),
        secret=BotSecret(channel_secret="s", channel_access_token=token,
                         bootstrap_admin_user_id=BOOT),
        repo=repo,
        client=client,
        sender_id=sender,
        now_ms=NOW,
        clock=lambda: client.time[0],
    )


# ---- is_admin ----

def test_is_admin_bootstrap() -> None:
    repo = FakeRepo()
    secret = BotSecret("s", "t", BOOT)
    assert is_admin(repo, BOT, secret, BOOT) is True
    assert is_admin(repo, BOT, secret, USER) is False


def test_is_admin_from_db_regardless_of_status() -> None:
    repo = FakeRepo()
    repo._put(ADMIN2, "user", status="inactive", role="admin")
    assert is_admin(repo, BOT, BotSecret("s", "t", None), ADMIN2) is True


# ---- /id, /help, unknown ----

def test_id() -> None:
    out = execute_command("/id", make_ctx())
    assert out == [f"LINE Target\n\nType: user\nUser ID:\n{BOOT}"]


def test_keyword_case_and_whitespace() -> None:
    assert execute_command("  /ID  ", make_ctx()) is not None


def test_unknown_is_silent() -> None:
    assert execute_command("hello", make_ctx()) is None
    assert execute_command("/nope", make_ctx()) is None
    assert execute_command("", make_ctx()) is None


def test_help() -> None:
    out = execute_command("/help", make_ctx())
    assert out and "/list" in out[0] and "/admin add" in out[0]


# ---- /list ----

def test_list_empty() -> None:
    assert execute_command("/list", make_ctx()) == ["No targets found."]


def test_list_groups_sorted_and_named() -> None:
    repo = FakeRepo()
    repo._put(G1, "group", ts=1)
    repo._put(G2, "group", ts=2)
    client = FakeClient()
    client.names[G2] = "Newer"
    out = execute_command("/list", make_ctx(repo, client))
    text = out[0]
    assert text.startswith("Groups (2)\n- Newer\n  " + G2 + "\n- (unknown)\n  " + G1)


def test_list_sections_and_admin_marker() -> None:
    repo = FakeRepo()
    repo._put(G1, "group")
    repo._put(R1, "room")
    repo._put(USER, "user")
    repo._put(ADMIN2, "user", role="admin")
    client = FakeClient()
    client.names[USER] = "王小明"
    client.names[ADMIN2] = "李小華"
    out = execute_command("/list", make_ctx(repo, client))
    text = out[0]
    assert "Groups (1)" in text
    assert "Rooms (1)\n- " + R1 in text
    assert "Users (2)" in text
    assert "- 王小明\n  " + USER in text
    assert "- 李小華 [admin]\n  " + ADMIN2 in text
    assert client.lookups == 3  # room 不查名稱


def test_list_filters_inactive_by_default_and_all_shows_it() -> None:
    repo = FakeRepo()
    repo._put(G1, "group", status="inactive")
    assert execute_command("/list", make_ctx(repo)) == ["No targets found."]
    out = execute_command("/list all", make_ctx(repo))
    assert "[inactive]" in out[0]


def test_list_type_filters() -> None:
    repo = FakeRepo()
    repo._put(G1, "group")
    repo._put(USER, "user")
    assert "Users" not in execute_command("/list groups", make_ctx(repo))[0]
    assert "Groups" not in execute_command("/list users", make_ctx(repo))[0]
    assert execute_command("/list rooms", make_ctx(repo)) == ["No targets found."]


def test_list_bad_arg() -> None:
    assert execute_command("/list foo", make_ctx()) == ["Usage: /list [groups|users|rooms|all]"]


def test_list_lookup_limit() -> None:
    repo = FakeRepo()
    for i in range(10):
        repo._put(f"C{i:032x}", "group", ts=i)
    client = FakeClient()
    execute_command("/list", make_ctx(repo, client, limit=3))
    assert client.lookups == 3


def test_list_lookup_budget() -> None:
    repo = FakeRepo()
    for i in range(10):
        repo._put(f"C{i:032x}", "group", ts=i)
    client = FakeClient()
    client.delay = 3.0
    execute_command("/list", make_ctx(repo, client, budget=8.0))
    assert client.lookups == 3  # 0→3→6→9 秒，第 4 次前已超過預算


def test_list_chunks_and_trailer() -> None:
    repo = FakeRepo()
    for i in range(700):
        repo._put(f"C{i:032x}", "group", ts=i)
    out = execute_command("/list", make_ctx(repo, limit=0))
    assert 1 < len(out) <= 5
    for msg in out:
        assert utf16_len(msg) <= 5000
    assert "aws dynamodb query" in out[-1]
    assert "--table-name t" in out[-1]
    assert f'"{BOT}"' in out[-1]


# ---- /admin ----

def test_admin_list_union_dedup() -> None:
    repo = FakeRepo()
    repo._put(BOOT, "user", role="admin")
    repo._put(ADMIN2, "user", role="admin")
    client = FakeClient()
    client.names[ADMIN2] = "Dev2"
    out = execute_command("/admin list", make_ctx(repo, client))
    text = out[0]
    assert text.count(BOOT) == 1
    assert "(bootstrap)" in text
    assert "- Dev2\n  " + ADMIN2 in text


def test_admin_add_new_and_existing() -> None:
    repo = FakeRepo()
    assert execute_command(f"/admin add {ADMIN2}", make_ctx(repo)) == [f"Added admin: {ADMIN2}"]
    assert repo.items[ADMIN2]["role"] == "admin"
    assert execute_command(f"/admin add {ADMIN2}", make_ctx(repo)) == [f"Already admin: {ADMIN2}"]


def test_admin_add_bootstrap() -> None:
    assert execute_command(f"/admin add {BOOT}", make_ctx()) == ["Already bootstrap admin."]


def test_admin_add_invalid_id_preserves_case_check() -> None:
    assert execute_command("/admin add UABC", make_ctx()) == ["Invalid user ID format."]
    assert execute_command(f"/admin add {ADMIN2.upper()}", make_ctx()) == ["Invalid user ID format."]


def test_admin_remove() -> None:
    repo = FakeRepo()
    repo._put(ADMIN2, "user", role="admin")
    assert execute_command(f"/admin remove {ADMIN2}", make_ctx(repo)) == [f"Removed admin: {ADMIN2}"]
    assert execute_command(f"/admin remove {ADMIN2}", make_ctx(repo)) == [f"Not an admin: {ADMIN2}"]


def test_admin_remove_bootstrap_and_self() -> None:
    repo = FakeRepo()
    repo._put(ADMIN2, "user", role="admin")
    assert execute_command(f"/admin remove {BOOT}", make_ctx(repo, sender=ADMIN2)) == [
        "Cannot remove bootstrap admin."
    ]
    assert execute_command(f"/admin remove {ADMIN2}", make_ctx(repo, sender=ADMIN2)) == [
        "Cannot remove yourself."
    ]


def test_admin_usage() -> None:
    out = execute_command("/admin", make_ctx())
    assert out == ["Usage: /admin list | /admin add <userId> | /admin remove <userId>"]
    assert execute_command("/admin add", make_ctx()) == out
```

- [ ] **Step 2: 執行確認失敗**

Run: `uv run pytest tests/test_commands.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 實作**

```python
"""開發者指令：解析、授權、執行、產生回覆文字。

不直接呼叫 AWS 或 LINE SDK；透過 CommandContext 注入 repository 與 client。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from line_webhook_id_collector.config import Settings
from line_webhook_id_collector.events import USER_ID_RE
from line_webhook_id_collector.secrets import BotSecret
from line_webhook_id_collector.text_utils import pack_blocks

HELP_TEXT = "\n".join(
    [
        "Commands",
        "",
        "/id - show your user ID",
        "/list - list active targets",
        "/list groups|users|rooms - list one type",
        "/list all - include inactive targets",
        "/admin list - list admins",
        "/admin add <userId> - grant admin",
        "/admin remove <userId> - revoke admin",
        "/help - this message",
    ]
)

_LIST_USAGE = "Usage: /list [groups|users|rooms|all]"
_ADMIN_USAGE = "Usage: /admin list | /admin add <userId> | /admin remove <userId>"
_SECTION_ORDER = (("group", "Groups"), ("room", "Rooms"), ("user", "Users"))


@dataclass
class CommandContext:
    bot_id: str
    settings: Settings
    secret: BotSecret
    repo: Any
    client: Any
    sender_id: str
    now_ms: int
    clock: Callable[[], float] = field(default=time.monotonic)


def is_admin(repo: Any, bot_id: str, secret: BotSecret, user_id: str) -> bool:
    """種子開發者或 DB 中 role=admin；status 不影響授權。"""
    if secret.bootstrap_admin_user_id and user_id == secret.bootstrap_admin_user_id:
        return True
    item = repo.get(bot_id, user_id)
    return bool(item) and item.get("role") == "admin"


def execute_command(text: str, ctx: CommandContext) -> list[str] | None:
    """回傳要回覆的訊息清單；None 代表靜默。呼叫端須先確認 sender 是開發者。"""
    tokens = text.strip().split()
    if not tokens:
        return None
    keyword = tokens[0].lower()
    args = tokens[1:]

    if keyword == "/id":
        return [f"LINE Target\n\nType: user\nUser ID:\n{ctx.sender_id}"]
    if keyword == "/help":
        return [HELP_TEXT]
    if keyword == "/list":
        return _list(args, ctx)
    if keyword == "/admin":
        return _admin(args, ctx)
    return None


# ---------- /list ----------


class _NameResolver:
    """受筆數上限與時間預算限制的名稱查詢。"""

    def __init__(self, ctx: CommandContext) -> None:
        self._client = ctx.client
        self._limit = ctx.settings.name_lookup_limit
        self._budget = ctx.settings.name_lookup_budget_seconds
        self._clock = ctx.clock
        self._started = ctx.clock()
        self._count = 0

    def _allowed(self) -> bool:
        if self._client is None or self._count >= self._limit:
            return False
        return (self._clock() - self._started) < self._budget

    def group(self, group_id: str) -> str | None:
        if not self._allowed():
            return None
        self._count += 1
        return self._client.get_group_name(group_id)

    def user(self, user_id: str) -> str | None:
        if not self._allowed():
            return None
        self._count += 1
        return self._client.get_user_name(user_id)


def _list(args: list[str], ctx: CommandContext) -> list[str]:
    mode = args[0].lower() if args else ""
    if len(args) > 1 or mode not in ("", "groups", "users", "rooms", "all"):
        return [_LIST_USAGE]
    type_filter = {"groups": "group", "users": "user", "rooms": "room"}.get(mode)
    include_inactive = mode == "all"

    items = [
        item
        for item in ctx.repo.list_targets(ctx.bot_id)
        if (include_inactive or item.get("status") == "active")
        and (type_filter is None or item.get("target_type") == type_filter)
    ]
    if not items:
        return ["No targets found."]

    items.sort(key=lambda i: int(i.get("last_event_ts", 0)), reverse=True)
    resolver = _NameResolver(ctx)
    blocks: list[str] = []
    for target_type, title in _SECTION_ORDER:
        section = [i for i in items if i.get("target_type") == target_type]
        if not section:
            continue
        if blocks:
            blocks.append("")
        blocks.append(f"{title} ({len(section)})")
        for item in section:
            blocks.append(_format_item(item, resolver))

    def trailer(remaining: int) -> str:
        return (
            f"... and {remaining} more. Use:\n"
            f"aws dynamodb query --table-name {ctx.settings.table_name} "
            f'--key-condition-expression "bot_id = :b" '
            f"--expression-attribute-values '{{\":b\":{{\"S\":\"{ctx.bot_id}\"}}}}'"
        )

    return pack_blocks(blocks, trailer=trailer)


def _format_item(item: dict[str, Any], resolver: _NameResolver) -> str:
    target_id = item["target_id"]
    target_type = item.get("target_type")
    if target_type == "room":
        label = None
    elif target_type == "group":
        label = resolver.group(target_id)
    else:
        label = resolver.user(target_id)

    suffixes = []
    if item.get("role") == "admin":
        suffixes.append("[admin]")
    if item.get("status") == "inactive":
        suffixes.append("[inactive]")
    suffix = (" " + " ".join(suffixes)) if suffixes else ""

    if target_type == "room" and not suffixes:
        return f"- {target_id}"
    if target_type == "room":
        return f"- {target_id}{suffix}"
    return f"- {label or '(unknown)'}{suffix}\n  {target_id}"


# ---------- /admin ----------


def _admin(args: list[str], ctx: CommandContext) -> list[str]:
    sub = args[0].lower() if args else ""
    if sub == "list" and len(args) == 1:
        return _admin_list(ctx)
    if sub in ("add", "remove") and len(args) == 2:
        user_id = args[1]
        if not USER_ID_RE.match(user_id):
            return ["Invalid user ID format."]
        return _admin_add(user_id, ctx) if sub == "add" else _admin_remove(user_id, ctx)
    return [_ADMIN_USAGE]


def _admin_list(ctx: CommandContext) -> list[str]:
    bootstrap = ctx.secret.bootstrap_admin_user_id
    db_admins = [i for i in ctx.repo.list_targets(ctx.bot_id) if i.get("role") == "admin"]
    ordered: list[str] = []
    if bootstrap:
        ordered.append(bootstrap)
    for item in db_admins:
        if item["target_id"] not in ordered:
            ordered.append(item["target_id"])

    resolver = _NameResolver(ctx)
    blocks = [f"Admins ({len(ordered)})"]
    for user_id in ordered:
        name = resolver.user(user_id) or "(unknown)"
        marker = " (bootstrap)" if user_id == bootstrap else ""
        blocks.append(f"- {name}{marker}\n  {user_id}")
    return pack_blocks(blocks)


def _admin_add(user_id: str, ctx: CommandContext) -> list[str]:
    if user_id == ctx.secret.bootstrap_admin_user_id:
        return ["Already bootstrap admin."]
    existing = ctx.repo.get(ctx.bot_id, user_id)
    if existing and existing.get("role") == "admin":
        return [f"Already admin: {user_id}"]
    ctx.repo.add_admin(ctx.bot_id, user_id, ctx.now_ms)
    return [f"Added admin: {user_id}"]


def _admin_remove(user_id: str, ctx: CommandContext) -> list[str]:
    if user_id == ctx.secret.bootstrap_admin_user_id:
        return ["Cannot remove bootstrap admin."]
    if user_id == ctx.sender_id:
        return ["Cannot remove yourself."]
    if ctx.repo.remove_admin(ctx.bot_id, user_id):
        return [f"Removed admin: {user_id}"]
    return [f"Not an admin: {user_id}"]
```

- [ ] **Step 4: 執行測試與 lint**

Run: `uv run pytest tests/test_commands.py -q && uv run ruff check . && uv run ruff format .`
Expected: 全部 PASS。`test_list_lookup_budget` 依賴 `FakeClient.time` 在每次查詢後累加，若計數不符，檢查 `_allowed` 是在查詢**前**檢查預算。

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat: add developer commands (/id, /list, /admin, /help)"
```

---

### Task 10: handler.py（Lambda 進入點）

**Files:**
- Create: `src/line_webhook_id_collector/handler.py`
- Test: `tests/test_handler.py`

**Interfaces:**
- Consumes: 全部前述模組
- Produces: `lambda_handler(event, context) -> dict`（API Gateway v2 回應：`{"statusCode": int, "body": str}`）；`build_dependencies(settings) -> Dependencies`；模組層 `_deps: Dependencies | None` 供測試注入

**流程**（對照 PRD 第 7、9、11.3、13 節）：

1. `bot_id` 來自 `event["pathParameters"]["bot_id"]`，不符 `^[a-z0-9-]{1,64}$` → 404
2. 簽章標頭 `event["headers"]` 中 key 不分大小寫找 `x-line-signature`；`is_plausible_signature` 失敗 → 401
3. `decode_body` 失敗 → 400
4. `secret_cache.get(bot_id)`：`SecretNotFound` → 404；`SecretConfigError` → 500；其他例外 → 500
5. `verify_signature` 失敗 → 401
6. `json.loads`；非 dict 或 `events` 非 list → 400
7. 逐事件：`analyze_event`；`None` 就 log `ignored` 跳過；非重送且有 command 且尚未有 pending command 則記下（一個 webhook 只執行第一個指令）；所有 targets upsert，每筆 upsert 用 try/except 包住，失敗記 `write_failed` 並設 `had_write_failure = True`
8. 全部收集完後，若有 pending command 且 secret 有 token：`is_admin` → `execute_command` → `client.reply`；reply 失敗記 `reply_failed`；`is_admin` 或 `execute_command` 拋例外也只記 log
9. `had_write_failure` → 500，否則 200

- [ ] **Step 1: 寫測試**

`tests/test_handler.py`：

```python
import base64
import hashlib
import hmac
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from line_webhook_id_collector import handler as handler_mod
from line_webhook_id_collector.repository import TargetRepository, create_table

FIXTURES = Path(__file__).parent / "fixtures"
BOT = "alert-bot"
SECRET = "test-channel-secret"
BOOT = "U" + "a" * 32
G = "C" + "b" * 32
PREFIX = "line-webhook-id-collector/"


def sign(body: bytes) -> str:
    return base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def make_request(events: list | str, bot_id: str = BOT, signature: str | None = "auto",
                 base64_body: bool = False) -> dict:
    raw = events.encode() if isinstance(events, str) else json.dumps(
        {"destination": BOOT, "events": events}).encode()
    body = base64.b64encode(raw).decode() if base64_body else raw.decode()
    headers = {}
    if signature == "auto":
        headers["x-line-signature"] = sign(raw)
    elif signature is not None:
        headers["x-line-signature"] = signature
    return {
        "pathParameters": {"bot_id": bot_id},
        "headers": headers,
        "body": body,
        "isBase64Encoded": base64_body,
        "requestContext": {"requestId": "req-1"},
    }


class FakeLineClient:
    def __init__(self) -> None:
        self.replies = []
        self.fail = False

    def reply(self, reply_token, texts):
        if self.fail:
            raise handler_mod.LineApiError("boom")
        self.replies.append((reply_token, texts))

    def get_group_name(self, group_id):
        return None

    def get_user_name(self, user_id):
        return None


@pytest.fixture
def env(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("DYNAMODB_TABLE_NAME", "targets")
        monkeypatch.setenv("SECRET_NAME_PREFIX", PREFIX)
        resource = boto3.resource("dynamodb", region_name="ap-northeast-1")
        create_table(resource, "targets")
        sm = boto3.client("secretsmanager", region_name="ap-northeast-1")
        sm.create_secret(Name=f"{PREFIX}{BOT}", SecretString=json.dumps({
            "channel_secret": SECRET, "channel_access_token": "tok",
            "bootstrap_admin_user_id": BOOT}))
        sm.create_secret(Name=f"{PREFIX}no-token", SecretString=json.dumps({
            "channel_secret": SECRET}))
        sm.create_secret(Name=f"{PREFIX}broken", SecretString="{}")
        line = FakeLineClient()
        handler_mod._deps = None
        monkeypatch.setattr(handler_mod, "_make_line_client", lambda token: line)
        yield {
            "repo": TargetRepository("targets", resource=resource),
            "line": line,
            "sm": sm,
        }
        handler_mod._deps = None


def call(request: dict) -> dict:
    return handler_mod.lambda_handler(request, None)


# ---- HTTP 層 ----

def test_empty_events_returns_200_without_writes(env) -> None:
    assert call(make_request([]))["statusCode"] == 200
    assert env["repo"].list_targets(BOT) == []


def test_bad_bot_id_format_404(env) -> None:
    assert call(make_request([], bot_id="Bad_ID"))["statusCode"] == 404


def test_unknown_bot_404(env) -> None:
    assert call(make_request([], bot_id="nope"))["statusCode"] == 404


def test_broken_secret_500(env) -> None:
    assert call(make_request([], bot_id="broken"))["statusCode"] == 500


def test_missing_signature_401_and_secret_not_read(env, monkeypatch) -> None:
    called = []
    original = env["sm"].get_secret_value
    monkeypatch.setattr(handler_mod, "_make_secrets_client",
                        lambda: type("C", (), {"get_secret_value": lambda self, **kw: called.append(1) or original(**kw)})())
    assert call(make_request([load("follow.json")], signature=None))["statusCode"] == 401
    assert called == []


def test_invalid_signature_401_no_writes(env) -> None:
    resp = call(make_request([load("follow.json")], signature=sign(b"other")))
    assert resp["statusCode"] == 401
    assert env["repo"].list_targets(BOT) == []


def test_invalid_json_400(env) -> None:
    assert call(make_request("{not json"))["statusCode"] == 400


def test_events_not_list_400(env) -> None:
    assert call(make_request('{"events": "x"}'))["statusCode"] == 400


def test_base64_body(env) -> None:
    resp = call(make_request([load("follow.json")], base64_body=True))
    assert resp["statusCode"] == 200
    assert env["repo"].get(BOT, BOOT)["status"] == "active"


# ---- 收集 ----

def test_multiple_events_all_stored(env) -> None:
    resp = call(make_request([load("follow.json"), load("join_group.json")]))
    assert resp["statusCode"] == 200
    assert {i["target_id"] for i in env["repo"].list_targets(BOT)} == {BOOT, G}


def test_invalid_event_entries_skipped(env) -> None:
    resp = call(make_request(["junk", {"no": "type"}, load("follow.json")]))
    assert resp["statusCode"] == 200
    assert env["repo"].get(BOT, BOOT) is not None


def test_unsupported_event_200(env) -> None:
    assert call(make_request([load("postback.json")]))["statusCode"] == 200


def test_unfollow_marks_inactive(env) -> None:
    call(make_request([load("follow.json")]))
    call(make_request([load("unfollow.json")]))
    assert env["repo"].get(BOT, BOOT)["status"] == "inactive"


def test_write_failure_returns_500_but_continues(env, monkeypatch) -> None:
    calls = {"n": 0}
    original = TargetRepository.upsert

    def flaky(self, bot_id, target, status, event_ts, event_type):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("dynamo down")
        return original(self, bot_id, target, status, event_ts, event_type)

    monkeypatch.setattr(TargetRepository, "upsert", flaky)
    resp = call(make_request([load("follow.json"), load("join_group.json")]))
    assert resp["statusCode"] == 500
    assert env["repo"].get(BOT, G) is not None


# ---- 指令 ----

def test_admin_id_command_replies(env) -> None:
    resp = call(make_request([load("message_user.json")]))
    assert resp["statusCode"] == 200
    assert env["line"].replies == [("rt-5", [f"LINE Target\n\nType: user\nUser ID:\n{BOOT}"])]


def test_non_admin_silent(env) -> None:
    ev = load("message_user.json")
    ev["source"]["userId"] = "U" + "9" * 32
    call(make_request([ev]))
    assert env["line"].replies == []
    assert env["repo"].get(BOT, "U" + "9" * 32) is not None


def test_group_command_silent(env) -> None:
    call(make_request([load("message_group_with_user.json")]))
    assert env["line"].replies == []


def test_redelivery_collects_but_does_not_reply(env) -> None:
    call(make_request([load("message_user_redelivery.json")]))
    assert env["line"].replies == []
    assert env["repo"].get(BOT, BOOT) is not None


def test_no_token_silent(env) -> None:
    resp = call(make_request([load("message_user.json")], bot_id="no-token"))
    assert resp["statusCode"] == 200
    assert env["line"].replies == []


def test_reply_failure_still_200(env) -> None:
    env["line"].fail = True
    assert call(make_request([load("message_user.json")]))["statusCode"] == 200


def test_command_runs_after_all_events_collected(env, monkeypatch) -> None:
    order = []
    original_upsert = TargetRepository.upsert
    original_reply = env["line"].reply

    def tracking(self, *a, **kw):
        order.append("upsert")
        return original_upsert(self, *a, **kw)

    def reply(reply_token, texts):
        order.append("reply")
        return original_reply(reply_token, texts)

    monkeypatch.setattr(TargetRepository, "upsert", tracking)
    monkeypatch.setattr(env["line"], "reply", reply)
    call(make_request([load("message_user.json"), load("join_group.json")]))
    assert order == ["upsert", "upsert", "reply"]
```

- [ ] **Step 2: 執行確認失敗**

Run: `uv run pytest tests/test_handler.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 實作**

```python
"""AWS Lambda 進入點：驗證、收集、（可選）回覆。"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import boto3

from line_webhook_id_collector.commands import CommandContext, execute_command, is_admin
from line_webhook_id_collector.config import Settings, load_settings
from line_webhook_id_collector.events import CommandCandidate, analyze_event
from line_webhook_id_collector.line_client import LineApiError, LineClient
from line_webhook_id_collector.logging_setup import configure_logging, log_event
from line_webhook_id_collector.repository import TargetRepository
from line_webhook_id_collector.secrets import (
    BotSecret,
    SecretCache,
    SecretConfigError,
    SecretNotFound,
)
from line_webhook_id_collector.signature import (
    BodyDecodeError,
    decode_body,
    is_plausible_signature,
    verify_signature,
)

BOT_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")

__all__ = ["LineApiError", "lambda_handler"]


@dataclass
class Dependencies:
    settings: Settings
    logger: Any
    secrets: SecretCache
    repo: TargetRepository


_deps: Dependencies | None = None


def _make_secrets_client() -> Any:
    return boto3.client("secretsmanager")


def _make_line_client(token: str) -> LineClient:
    return LineClient(token)


def build_dependencies(settings: Settings) -> Dependencies:
    return Dependencies(
        settings=settings,
        logger=configure_logging(settings.log_level),
        secrets=SecretCache(
            _make_secrets_client(), settings.secret_name_prefix, settings.secret_cache_ttl_seconds
        ),
        repo=TargetRepository(settings.table_name),
    )


def _get_deps() -> Dependencies:
    global _deps
    if _deps is None:
        _deps = build_dependencies(load_settings())
    return _deps


def _response(status: int, message: str) -> dict[str, Any]:
    return {"statusCode": status, "headers": {"content-type": "application/json"},
            "body": json.dumps({"message": message})}


def _header(headers: Mapping[str, Any] | None, name: str) -> str | None:
    if not headers:
        return None
    for key, value in headers.items():
        if key.lower() == name:
            return value if isinstance(value, str) else None
    return None


def _target_id_for_log(settings: Settings, target_id: str) -> str | None:
    return target_id if settings.log_target_ids else None


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    deps = _get_deps()
    settings, logger = deps.settings, deps.logger
    request_id = (event.get("requestContext") or {}).get("requestId")

    bot_id = (event.get("pathParameters") or {}).get("bot_id", "")
    if not isinstance(bot_id, str) or not BOT_ID_RE.match(bot_id):
        log_event(logger, "unknown_bot", request_id=request_id)
        return _response(404, "unknown bot")

    signature = _header(event.get("headers"), "x-line-signature")
    if not is_plausible_signature(signature):
        log_event(logger, "invalid_signature", request_id=request_id, bot_id=bot_id)
        return _response(401, "invalid signature")

    try:
        body = decode_body(event)
    except BodyDecodeError:
        log_event(logger, "bad_request", request_id=request_id, bot_id=bot_id, reason="body")
        return _response(400, "bad body encoding")

    try:
        secret = deps.secrets.get(bot_id)
    except SecretNotFound:
        log_event(logger, "unknown_bot", request_id=request_id, bot_id=bot_id)
        return _response(404, "unknown bot")
    except SecretConfigError as exc:
        log_event(logger, "config_error", request_id=request_id, bot_id=bot_id, reason=str(exc))
        return _response(500, "bot misconfigured")
    except Exception:
        logger.exception({"result": "secret_error", "request_id": request_id, "bot_id": bot_id})
        return _response(500, "secret unavailable")

    if not verify_signature(secret.channel_secret, body, signature or ""):
        log_event(logger, "invalid_signature", request_id=request_id, bot_id=bot_id)
        return _response(401, "invalid signature")

    try:
        payload = json.loads(body)
    except ValueError:
        log_event(logger, "bad_request", request_id=request_id, bot_id=bot_id, reason="json")
        return _response(400, "invalid json")
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        log_event(logger, "bad_request", request_id=request_id, bot_id=bot_id, reason="events")
        return _response(400, "events must be a list")

    now_ms = int(time.time() * 1000)
    had_write_failure = False
    pending: CommandCandidate | None = None

    for raw_event in events:
        outcome = analyze_event(raw_event, now_ms)
        if outcome is None:
            log_event(logger, "ignored", request_id=request_id, bot_id=bot_id, reason="invalid")
            continue
        if pending is None and outcome.command is not None and not outcome.is_redelivery:
            pending = outcome.command

        for status, targets in (("active", outcome.active_targets),
                                ("inactive", outcome.inactive_targets)):
            for target in targets:
                try:
                    result = deps.repo.upsert(
                        bot_id, target, status, outcome.event_ts, outcome.event_type
                    )
                except Exception:
                    had_write_failure = True
                    logger.exception({
                        "result": "write_failed", "request_id": request_id, "bot_id": bot_id,
                        "event_type": outcome.event_type, "target_type": target.target_type,
                    })
                    continue
                log_event(
                    logger, result, request_id=request_id, bot_id=bot_id,
                    event_type=outcome.event_type, source_type=outcome.source_type,
                    target_type=target.target_type,
                    target_id=_target_id_for_log(settings, target.target_id),
                )
        if not outcome.active_targets and not outcome.inactive_targets:
            log_event(logger, "ignored", request_id=request_id, bot_id=bot_id,
                      event_type=outcome.event_type, source_type=outcome.source_type)

    if pending is not None:
        _handle_command(deps, bot_id, secret, pending, now_ms, request_id)

    if had_write_failure:
        return _response(500, "partial failure")
    return _response(200, "ok")


def _handle_command(
    deps: Dependencies, bot_id: str, secret: BotSecret, candidate: CommandCandidate,
    now_ms: int, request_id: str | None,
) -> None:
    """在所有事件收集完成後執行指令；任何失敗只記 log。"""
    logger, settings = deps.logger, deps.settings
    if not secret.channel_access_token:
        return
    try:
        if not is_admin(deps.repo, bot_id, secret, candidate.user_id):
            return
        client = _make_line_client(secret.channel_access_token)
        ctx = CommandContext(
            bot_id=bot_id, settings=settings, secret=secret, repo=deps.repo, client=client,
            sender_id=candidate.user_id, now_ms=now_ms,
        )
        messages = execute_command(candidate.text, ctx)
        if messages is None:
            return
        keyword = candidate.text.strip().split()[0].lower()
        client.reply(candidate.reply_token, messages)
        log_event(logger, "replied", request_id=request_id, bot_id=bot_id, command=keyword)
    except LineApiError as exc:
        log_event(logger, "reply_failed", request_id=request_id, bot_id=bot_id, reason=str(exc))
    except Exception:
        logger.exception({"result": "command_failed", "request_id": request_id, "bot_id": bot_id})
```

- [ ] **Step 4: 執行全部測試與 lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format .`
Expected: 全部 PASS。`test_missing_signature_401_and_secret_not_read` 依賴 `_deps` 在每個測試重建（fixture 已設 `handler_mod._deps = None`），且 `_make_secrets_client` 的 monkeypatch 要在第一次 `_get_deps()` 之前生效。

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat: add Lambda handler wiring signature, collection, and commands"
```

---

### Task 11: SAM template 與 requirements.txt

**Files:**
- Create: `template.yaml`、`src/requirements.txt`、`samconfig.example.toml`
- Modify: `.gitignore`（確認 `.aws-sam/` 與 `samconfig.toml` 已忽略）

**Interfaces:**
- Produces: `sam validate --lint` 通過；`sam build` 可打包 `src/`

- [ ] **Step 1: 產生 requirements.txt**

Run: `uv export --no-dev --no-hashes --no-emit-project -o src/requirements.txt`
Expected: 產生含 `line-bot-sdk`、`boto3` 及其相依的檔案。將此指令寫入 README「部署」章節，並在 `pyproject.toml` 加一段註解說明每次改依賴後要重新產生。

- [ ] **Step 2: 撰寫 template.yaml**

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Transform: AWS::Serverless-2016-10-31
Description: >
  LINE Webhook ID Collector - collects userId / groupId / roomId from LINE webhook events
  for multiple bots into a single DynamoDB table.

Parameters:
  TableName:
    Type: String
    Default: line-webhook-targets
  SecretNamePrefix:
    Type: String
    Default: line-webhook-id-collector/
    Description: Secrets Manager name prefix; each bot's secret is "<prefix><bot_id>".
  SecretCacheTtlSeconds:
    Type: Number
    Default: 300
  NameLookupLimit:
    Type: Number
    Default: 50
  NameLookupBudgetSeconds:
    Type: Number
    Default: 8
  LogLevel:
    Type: String
    Default: INFO
    AllowedValues: [DEBUG, INFO, WARNING, ERROR]
  LogTargetIds:
    Type: String
    Default: "false"
    AllowedValues: ["true", "false"]
  LogRetentionDays:
    Type: Number
    Default: 30

Globals:
  Function:
    Runtime: python3.14
    Architectures: [arm64]
    MemorySize: 256
    Timeout: 15

Resources:
  TargetsTable:
    Type: AWS::DynamoDB::Table
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      TableName: !Ref TableName
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - AttributeName: bot_id
          AttributeType: S
        - AttributeName: target_id
          AttributeType: S
      KeySchema:
        - AttributeName: bot_id
          KeyType: HASH
        - AttributeName: target_id
          KeyType: RANGE

  WebhookApi:
    Type: AWS::Serverless::HttpApi
    Properties:
      Description: LINE webhook receiver

  WebhookFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: src/
      Handler: line_webhook_id_collector.handler.lambda_handler
      Description: Verify LINE signature, collect IDs, answer developer commands.
      Environment:
        Variables:
          DYNAMODB_TABLE_NAME: !Ref TargetsTable
          SECRET_NAME_PREFIX: !Ref SecretNamePrefix
          SECRET_CACHE_TTL_SECONDS: !Ref SecretCacheTtlSeconds
          NAME_LOOKUP_LIMIT: !Ref NameLookupLimit
          NAME_LOOKUP_BUDGET_SECONDS: !Ref NameLookupBudgetSeconds
          LOG_LEVEL: !Ref LogLevel
          LOG_TARGET_IDS: !Ref LogTargetIds
      Policies:
        - Version: "2012-10-17"
          Statement:
            - Effect: Allow
              Action:
                - dynamodb:UpdateItem
                - dynamodb:Query
                - dynamodb:GetItem
              Resource: !GetAtt TargetsTable.Arn
            - Effect: Allow
              Action: secretsmanager:GetSecretValue
              Resource: !Sub "arn:${AWS::Partition}:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:${SecretNamePrefix}*"
      Events:
        Webhook:
          Type: HttpApi
          Properties:
            ApiId: !Ref WebhookApi
            Path: /webhook/{bot_id}
            Method: POST
      LoggingConfig:
        LogGroup: !Ref WebhookLogGroup

  WebhookLogGroup:
    Type: AWS::Logs::LogGroup
    Properties:
      LogGroupName: !Sub "/aws/lambda/${AWS::StackName}-webhook"
      RetentionInDays: !Ref LogRetentionDays

Outputs:
  WebhookBaseUrl:
    Description: Append /webhook/<bot_id> for each bot.
    Value: !Sub "https://${WebhookApi}.execute-api.${AWS::Region}.${AWS::URLSuffix}"
  TableName:
    Value: !Ref TargetsTable
  SecretNamePrefix:
    Value: !Ref SecretNamePrefix
```

- [ ] **Step 3: 建立 samconfig.example.toml**

```toml
version = 0.1

[default.deploy.parameters]
stack_name = "line-webhook-id-collector"
region = "ap-northeast-1"
resolve_s3 = true
capabilities = "CAPABILITY_IAM"
confirm_changeset = true
parameter_overrides = "LogTargetIds=\"false\""
```

- [ ] **Step 4: 驗證 template**

Run: `sam validate --lint --region ap-northeast-1`
Expected: `template.yaml is a valid SAM Template`。若 `LoggingConfig` 在目前 SAM CLI 版本不被支援，改為移除 `LoggingConfig` 與 `WebhookLogGroup`，改用 `AWS::Logs::LogGroup` 且 `LogGroupName: !Sub "/aws/lambda/${WebhookFunction}"`，並在 README 說明。

- [ ] **Step 5: 本機 build 測試**

Run: `sam build --use-container 2>&1 | tail -5`（若無 Docker，改跑 `sam build` 並確認本機 Python 3.14 可用）
Expected: `Build Succeeded`

- [ ] **Step 6: Commit**

```bash
git add template.yaml src/requirements.txt samconfig.example.toml .gitignore
git commit -m "feat: add SAM template with least-privilege IAM"
```

---

### Task 12: README 與 docs

**Files:**
- Create: `README.md`
- Modify: `docs/PRD.md`（僅在實作過程發現與 PRD 不符時更新 PRD；否則不動）

**Interfaces:** 無程式碼。

- [ ] **Step 1: 撰寫 README.md**

依 PRD 第 22 節 18 項逐一撰寫，繁體中文。必須包含以下具體內容（以下為必要片段，其餘章節依 PRD 描述補齊）：

**建立 secret（第 7 項）：**

```bash
aws secretsmanager create-secret \
  --name "line-webhook-id-collector/alert-bot" \
  --secret-string '{
    "channel_secret": "<LINE Channel Secret>",
    "channel_access_token": "<LINE Channel Access Token>",
    "bootstrap_admin_user_id": "<Your user ID from LINE Developers Console>"
  }'
```

**部署（第 6 項）：**

```bash
uv export --no-dev --no-hashes --no-emit-project -o src/requirements.txt
cp samconfig.example.toml samconfig.toml
sam build
sam deploy --guided
```

**LINE 設定清單（第 8 項）**，逐條列出 PRD 22.8 的五點。

**新增第二個 Bot（第 9 項）：** 建 secret → 在 LINE Developers Console 設 webhook URL `<WebhookBaseUrl>/webhook/<bot_id>` → 按 Verify。

**AWS CLI 操作（第 11 項）：** 直接引用 PRD 第 23 節的四段指令，並附「清除某個 Bot 全部資料」的 bash 迴圈：

```bash
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

**本機測試（第 15 項）：** 說明用 `uv run pytest`，以及用 `sam local invoke` 搭配一個範例事件檔 `events/sample-follow.json`（含正確簽章的產生方式，用一段 Python 一行指令算 HMAC）。

**專案限制（第 17 項）：** 逐條列 PRD 22.17。

- [ ] **Step 2: 檢查 README 對照 PRD 第 22 節 18 項全部存在**

Run: `grep -c "^## " README.md`
Expected: ≥ 18

- [ ] **Step 3: Commit 與 push**

```bash
git add README.md
git commit -m "docs: add README with deployment, LINE setup, and operations guide"
git push
```

---

### Task 13: 端到端驗證與收尾

**Files:** 無新增

- [ ] **Step 1: 全量測試、lint、template 驗證**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && sam validate --lint --region ap-northeast-1`
Expected: 全部通過

- [ ] **Step 2: 對照 PRD 第 25 節驗收標準，逐項確認有對應測試**

| 驗收項目 | 對應測試 |
|---|---|
| 多 Bot 分區 | `test_repository.py::test_list_is_scoped_by_bot` |
| follow / unfollow / 重新 follow | `test_handler.py::test_unfollow_marks_inactive`、`test_repository.py::test_update_preserves_first_seen` |
| 群組靜默 | `test_handler.py::test_group_command_silent` |
| memberJoined | `test_events.py::test_member_joined_collects_members_and_group` |
| leave | `test_events.py::test_leave_marks_group_inactive` |
| room fixture | `test_events.py::test_message_room` |
| 指令全套 | `test_commands.py` |
| remove 後立即靜默 | `test_repository.py::test_remove_admin` + `test_handler.py::test_non_admin_silent` |
| 重送不執行 | `test_handler.py::test_redelivery_collects_but_does_not_reply` |
| 亂序不倒退 | `test_repository.py::test_stale_event_rejected` |
| 簽章錯誤無寫入且不讀 secret | `test_handler.py::test_invalid_signature_401_no_writes`、`test_missing_signature_401_and_secret_not_read` |
| 未知 bot 404 | `test_handler.py::test_unknown_bot_404` |

- [ ] **Step 3: 確認 CI 綠燈**

Run: `gh run list --limit 1`
Expected: 最新一次 workflow `completed success`。若失敗，讀 `gh run view --log-failed` 修正後再 push。

- [ ] **Step 4: 最終 push**

```bash
git status
git push
```
