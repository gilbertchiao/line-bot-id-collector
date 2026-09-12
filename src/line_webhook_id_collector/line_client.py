"""LINE Messaging API 的薄封裝：回覆與名稱查詢。

每次 HTTP 呼叫都有 timeout，不重試；名稱查詢失敗一律回 None。
"""

from __future__ import annotations

from types import TracebackType

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
            # LINE SDK 的例外訊息（str(exc)）可能包含 LINE 回應的原始 body（可能夾帶
            # reply token 等敏感內容），因此只保留例外類別名稱與（若有）HTTP 狀態碼，
            # 絕不把 str(exc) 往外傳。
            status = getattr(exc, "status", None)
            label = (
                f"{type(exc).__name__} (status={status})"
                if status is not None
                else type(exc).__name__
            )
            raise LineApiError(label) from exc

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

    def close(self) -> None:
        """關閉底層 `ApiClient`（若已建立），釋放底層 HTTP connection pool。

        `self._api` 只在第一次存取 `api` property 時才會建立（lazy），因此若從未
        呼叫過任何 API（例如指令不需要送出 reply），這裡就不需要（也無法）關閉。
        """
        if self._api is not None:
            api_client = getattr(self._api, "api_client", None)
            close = getattr(api_client, "close", None)
            if callable(close):
                close()

    def __enter__(self) -> LineClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
