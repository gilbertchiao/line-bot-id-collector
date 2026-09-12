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
