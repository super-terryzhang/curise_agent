"""The temporary setup surface may only point at the named clean database."""

from unittest.mock import MagicMock

import pytest

from apps.http import startup


def test_database_setup_gate_is_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(startup.settings, "TEMP_DATABASE_SETUP_ENABLED", False)
    engine = MagicMock()
    startup.verify_database_setup_target(engine)
    engine.connect.assert_not_called()


@pytest.mark.parametrize("actual", ["postgres", "cruise_v3", "cruise_v3_clean_copy"])
def test_database_setup_gate_rejects_any_database_except_exact_name(monkeypatch, actual):
    monkeypatch.setattr(startup.settings, "TEMP_DATABASE_SETUP_ENABLED", True)
    monkeypatch.setattr(
        startup.settings, "TEMP_DATABASE_SETUP_EXPECTED_DATABASE", "cruise_v3_clean"
    )
    connection = MagicMock()
    connection.scalar.return_value = actual
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    with pytest.raises(RuntimeError, match="clean database"):
        startup.verify_database_setup_target(engine)


def test_database_setup_gate_accepts_exact_database_name(monkeypatch):
    monkeypatch.setattr(startup.settings, "TEMP_DATABASE_SETUP_ENABLED", True)
    monkeypatch.setattr(
        startup.settings, "TEMP_DATABASE_SETUP_EXPECTED_DATABASE", "cruise_v3_clean"
    )
    connection = MagicMock()
    connection.scalar.return_value = "cruise_v3_clean"
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    startup.verify_database_setup_target(engine)


def test_database_setup_gate_rejects_misconfigured_expected_name(monkeypatch):
    monkeypatch.setattr(startup.settings, "TEMP_DATABASE_SETUP_ENABLED", True)
    monkeypatch.setattr(
        startup.settings, "TEMP_DATABASE_SETUP_EXPECTED_DATABASE", "cruise_v3"
    )
    with pytest.raises(RuntimeError, match="expected clean database"):
        startup.verify_database_setup_target(MagicMock())
