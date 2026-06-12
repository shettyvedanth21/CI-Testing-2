from __future__ import annotations

import importlib
import logging
import os
from unittest.mock import MagicMock, patch

import pytest

from services.shared.debug_bootstrap import init_debug


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("DEBUGPY_ENABLE", "DEBUGPY_PORT", "DEBUGPY_HOST", "DEBUGPY_WAIT_FOR_CLIENT"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def log_records(caplog):
    with caplog.at_level(logging.DEBUG, logger="debug_bootstrap"):
        yield caplog


def test_no_op_when_enable_unset(log_records):
    init_debug()
    assert not any("debugpy" in r.message.lower() for r in log_records.records)


def test_no_op_when_enable_false(log_records, monkeypatch):
    monkeypatch.setenv("DEBUGPY_ENABLE", "false")
    init_debug()
    assert not any("debugpy" in r.message.lower() for r in log_records.records)


def test_no_op_when_enable_random_value(log_records, monkeypatch):
    monkeypatch.setenv("DEBUGPY_ENABLE", "yes")
    init_debug()
    assert not any("debugpy" in r.message.lower() for r in log_records.records)


def test_warns_on_missing_port(log_records, monkeypatch):
    monkeypatch.setenv("DEBUGPY_ENABLE", "true")
    monkeypatch.delenv("DEBUGPY_PORT", raising=False)
    init_debug()
    assert any("invalid" in r.message.lower() and "debugpy" in r.message.lower() for r in log_records.records)


def test_warns_on_zero_port(log_records, monkeypatch):
    monkeypatch.setenv("DEBUGPY_ENABLE", "true")
    monkeypatch.setenv("DEBUGPY_PORT", "0")
    init_debug()
    assert any("invalid" in r.message.lower() for r in log_records.records)


def test_warns_on_negative_port(log_records, monkeypatch):
    monkeypatch.setenv("DEBUGPY_ENABLE", "true")
    monkeypatch.setenv("DEBUGPY_PORT", "-1")
    init_debug()
    assert any("invalid" in r.message.lower() for r in log_records.records)


def test_warns_on_non_numeric_port(log_records, monkeypatch):
    monkeypatch.setenv("DEBUGPY_ENABLE", "true")
    monkeypatch.setenv("DEBUGPY_PORT", "abc")
    init_debug()
    assert any("not an integer" in r.message.lower() for r in log_records.records)


@patch("services.shared.debug_bootstrap.debugpy", create=True)
def _run_listen_test(mock_debugpy, monkeypatch, log_records, port_val, host_val="0.0.0.0"):
    monkeypatch.setenv("DEBUGPY_ENABLE", "true")
    monkeypatch.setenv("DEBUGPY_PORT", port_val)
    if host_val != "0.0.0.0":
        monkeypatch.setenv("DEBUGPY_HOST", host_val)

    import sys

    sys.modules["debugpy"] = mock_debugpy
    try:
        importlib.reload(importlib.import_module("services.shared.debug_bootstrap"))
        from services.shared.debug_bootstrap import init_debug as reloaded_init

        reloaded_init()
    finally:
        sys.modules.pop("debugpy", None)
        importlib.reload(importlib.import_module("services.shared.debug_bootstrap"))

    return mock_debugpy


def test_calls_listen_with_valid_port(monkeypatch, log_records):
    mock_debugpy = MagicMock()
    with patch.dict("sys.modules", {"debugpy": mock_debugpy}):
        monkeypatch.setenv("DEBUGPY_ENABLE", "true")
        monkeypatch.setenv("DEBUGPY_PORT", "5678")

        from services.shared.debug_bootstrap import init_debug as fresh_init

        fresh_init()

    mock_debugpy.listen.assert_called_once_with(("0.0.0.0", 5678))
    assert any("listening" in r.message.lower() for r in log_records.records)


def test_calls_listen_with_custom_host(monkeypatch, log_records):
    mock_debugpy = MagicMock()
    with patch.dict("sys.modules", {"debugpy": mock_debugpy}):
        monkeypatch.setenv("DEBUGPY_ENABLE", "true")
        monkeypatch.setenv("DEBUGPY_PORT", "5678")
        monkeypatch.setenv("DEBUGPY_HOST", "127.0.0.1")

        from services.shared.debug_bootstrap import init_debug as fresh_init

        fresh_init()

    mock_debugpy.listen.assert_called_once_with(("127.0.0.1", 5678))


def test_wait_for_client_not_called_by_default(monkeypatch, log_records):
    mock_debugpy = MagicMock()
    with patch.dict("sys.modules", {"debugpy": mock_debugpy}):
        monkeypatch.setenv("DEBUGPY_ENABLE", "true")
        monkeypatch.setenv("DEBUGPY_PORT", "5678")

        from services.shared.debug_bootstrap import init_debug as fresh_init

        fresh_init()

    mock_debugpy.wait_for_client.assert_not_called()


def test_wait_for_client_called_when_enabled(monkeypatch, log_records):
    mock_debugpy = MagicMock()
    with patch.dict("sys.modules", {"debugpy": mock_debugpy}):
        monkeypatch.setenv("DEBUGPY_ENABLE", "true")
        monkeypatch.setenv("DEBUGPY_PORT", "5678")
        monkeypatch.setenv("DEBUGPY_WAIT_FOR_CLIENT", "true")

        from services.shared.debug_bootstrap import init_debug as fresh_init

        fresh_init()

    mock_debugpy.wait_for_client.assert_called_once()
    assert any("waiting for client" in r.message.lower() for r in log_records.records)
    assert any("client attached" in r.message.lower() for r in log_records.records)


def test_wait_for_client_not_called_when_false(monkeypatch, log_records):
    mock_debugpy = MagicMock()
    with patch.dict("sys.modules", {"debugpy": mock_debugpy}):
        monkeypatch.setenv("DEBUGPY_ENABLE", "true")
        monkeypatch.setenv("DEBUGPY_PORT", "5678")
        monkeypatch.setenv("DEBUGPY_WAIT_FOR_CLIENT", "false")

        from services.shared.debug_bootstrap import init_debug as fresh_init

        fresh_init()

    mock_debugpy.wait_for_client.assert_not_called()


def test_listen_exception_does_not_crash(monkeypatch, log_records):
    mock_debugpy = MagicMock()
    mock_debugpy.listen.side_effect = RuntimeError("port already in use")
    with patch.dict("sys.modules", {"debugpy": mock_debugpy}):
        monkeypatch.setenv("DEBUGPY_ENABLE", "true")
        monkeypatch.setenv("DEBUGPY_PORT", "5678")

        from services.shared.debug_bootstrap import init_debug as fresh_init

        fresh_init()

    assert any("failed" in r.message.lower() for r in log_records.records)


def test_listen_exception_prevents_wait_for_client(monkeypatch, log_records):
    mock_debugpy = MagicMock()
    mock_debugpy.listen.side_effect = OSError("address already in use")
    with patch.dict("sys.modules", {"debugpy": mock_debugpy}):
        monkeypatch.setenv("DEBUGPY_ENABLE", "true")
        monkeypatch.setenv("DEBUGPY_PORT", "5678")
        monkeypatch.setenv("DEBUGPY_WAIT_FOR_CLIENT", "true")

        from services.shared.debug_bootstrap import init_debug as fresh_init

        fresh_init()

    mock_debugpy.wait_for_client.assert_not_called()
    assert any("failed" in r.message.lower() for r in log_records.records)


def test_no_module_level_debugpy_import():
    import ast
    import inspect

    from services.shared import debug_bootstrap

    source = inspect.getsource(debug_bootstrap)
    tree = ast.parse(source)

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "debugpy", "debugpy must not be imported at module level"
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "debugpy", "debugpy must not be imported at module level"


def test_case_insensitive_enable(monkeypatch, log_records):
    monkeypatch.setenv("DEBUGPY_ENABLE", "True")
    monkeypatch.setenv("DEBUGPY_PORT", "0")
    init_debug()
    assert any("invalid" in r.message.lower() for r in log_records.records)


def test_whitespace_trimmed_on_env_vars(monkeypatch, log_records):
    monkeypatch.setenv("DEBUGPY_ENABLE", " true ")
    monkeypatch.setenv("DEBUGPY_PORT", " 0 ")
    init_debug()
    assert any("invalid" in r.message.lower() for r in log_records.records)
