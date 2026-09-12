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
