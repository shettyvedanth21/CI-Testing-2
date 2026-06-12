import pytest


def test_set_tariff_rate(api):
    result = api.reporting.set_tariff({"rate_per_kwh": 8.50, "currency": "INR"})
    assert result["rate"] == 8.50
    assert result["currency"] == "INR"


def test_tariff_readable_after_save(api):
    result = api.reporting.get_tariff()
    assert result["rate"] == 8.50


def test_tariff_is_positive(api):
    result = api.reporting.get_tariff()
    assert float(result["rate"]) > 0


def test_create_email_notification_channel(api, state):
    result = api.reporting.create_notification_channel({"email": "e2e-test@example.com"})
    assert result["id"] is not None
    state["channel_id"] = result["id"]


def test_notification_channel_in_list(api, state):
    channels = api.reporting.get_notification_channels()
    ids = [str(item["id"]) for item in channels]
    assert str(state["channel_id"]) in ids
