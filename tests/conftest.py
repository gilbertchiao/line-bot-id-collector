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
