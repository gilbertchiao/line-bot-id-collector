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
