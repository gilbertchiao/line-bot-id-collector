"""測試專用的 DynamoDB 輔助函式（非生產程式碼，不應由 src/ 匯入）。"""

from __future__ import annotations

from typing import Any


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
