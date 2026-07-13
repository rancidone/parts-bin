import importlib
import io
import json
import logging

import log as log_module


def _reset_logger(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def test_init_adds_file_and_telemetry_handlers_with_existing_root_handler(tmp_path, monkeypatch):
    app_log = tmp_path / "app.jsonl"
    telemetry_log = tmp_path / "telemetry.jsonl"

    root = logging.getLogger()
    telemetry_logger = logging.getLogger("parts_bin.telemetry")
    root_old_handlers = list(root.handlers)
    telemetry_old_handlers = list(telemetry_logger.handlers)
    root_old_level = root.level
    telemetry_old_level = telemetry_logger.level
    telemetry_old_propagate = telemetry_logger.propagate

    try:
        _reset_logger(root)
        _reset_logger(telemetry_logger)
        root.addHandler(logging.StreamHandler(io.StringIO()))
        monkeypatch.setenv("LOG_FILE", str(app_log))
        monkeypatch.setenv("TELEMETRY_LOG_FILE", str(telemetry_log))

        importlib.reload(log_module)
        log_module.init()
        log_module.get_logger("parts_bin.test").info("hello")
        log_module.emit_telemetry("llm_call", request_id="req_1", prompt_tokens=5, completion_tokens=2)

        assert app_log.exists()
        assert telemetry_log.exists()

        app_entry = json.loads(app_log.read_text().strip().splitlines()[-1])
        telemetry_entry = json.loads(telemetry_log.read_text().strip().splitlines()[-1])

        assert app_entry["msg"] == "hello"
        assert telemetry_entry["event"] == "llm_call"
        assert telemetry_entry["telemetry_version"] == 1
        assert telemetry_entry["request_id"] == "req_1"
        assert telemetry_entry["prompt_tokens"] == 5
        assert telemetry_entry["completion_tokens"] == 2
    finally:
        _reset_logger(root)
        _reset_logger(telemetry_logger)
        for handler in root_old_handlers:
            root.addHandler(handler)
        for handler in telemetry_old_handlers:
            telemetry_logger.addHandler(handler)
        root.setLevel(root_old_level)
        telemetry_logger.setLevel(telemetry_old_level)
        telemetry_logger.propagate = telemetry_old_propagate
