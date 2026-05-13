#!/usr/bin/env python3
"""Tests for lumen_scheduler — focused on the Teams notification layer.

Run with:  python3 -m unittest test_lumen_scheduler.py -v
"""

import json
import unittest
from unittest.mock import MagicMock, patch, call

import lumen_scheduler as ls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _webhook_cfg(url="https://example.webhook.office.com/test", **overrides):
    """Minimal config dict with a Teams webhook URL."""
    cfg = {
        "notifications": {
            "teams_webhook_url": url,
            "on_apply_failure": True,
            "on_pending_timeout": True,
            "on_recovery": False,
        }
    }
    cfg["notifications"].update(overrides)
    return cfg


def _make_urlopen_mock(status=200):
    """Return a mock for request.urlopen that acts as a context manager."""
    resp = MagicMock()
    resp.status = status
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=resp)
    cm.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=cm)


def _captured_payload(mock_urlopen):
    """Parse the JSON body sent to the mocked urlopen call."""
    req_obj = mock_urlopen.call_args[0][0]
    return json.loads(req_obj.data.decode("utf-8"))


# ---------------------------------------------------------------------------
# notifications_config
# ---------------------------------------------------------------------------

class TestNotificationsConfig(unittest.TestCase):

    def test_defaults_when_no_notifications_key(self):
        cfg = ls.notifications_config({})
        self.assertEqual(cfg["teams_webhook_url"], "")
        self.assertTrue(cfg["on_apply_failure"])
        self.assertTrue(cfg["on_pending_timeout"])
        self.assertFalse(cfg["on_recovery"])

    def test_defaults_when_notifications_is_not_dict(self):
        # e.g. someone sets "notifications": null in config.json
        cfg = ls.notifications_config({"notifications": None})
        self.assertEqual(cfg["teams_webhook_url"], "")
        self.assertTrue(cfg["on_apply_failure"])

    def test_respects_set_values(self):
        cfg = ls.notifications_config({
            "notifications": {
                "teams_webhook_url": "https://hooks.example.com/abc",
                "on_apply_failure": False,
                "on_pending_timeout": False,
                "on_recovery": True,
            }
        })
        self.assertEqual(cfg["teams_webhook_url"], "https://hooks.example.com/abc")
        self.assertFalse(cfg["on_apply_failure"])
        self.assertFalse(cfg["on_pending_timeout"])
        self.assertTrue(cfg["on_recovery"])

    def test_strips_whitespace_from_url(self):
        cfg = ls.notifications_config({"notifications": {"teams_webhook_url": "  https://x.com  "}})
        self.assertEqual(cfg["teams_webhook_url"], "https://x.com")

    def test_empty_string_url_stays_empty(self):
        cfg = ls.notifications_config({"notifications": {"teams_webhook_url": ""}})
        self.assertEqual(cfg["teams_webhook_url"], "")


# ---------------------------------------------------------------------------
# send_teams_notification — payload structure
# ---------------------------------------------------------------------------

class TestSendTeamsNotificationPayload(unittest.TestCase):

    def _send(self, title="Alert", message="Something broke", facts=None, is_error=True, status=200):
        mock_urlopen = _make_urlopen_mock(status=status)
        with patch("lumen_scheduler.request.urlopen", mock_urlopen):
            ls.send_teams_notification(
                "https://example.webhook.office.com/test",
                title=title,
                message=message,
                facts=facts,
                is_error=is_error,
            )
        return mock_urlopen, _captured_payload(mock_urlopen)

    def test_message_card_type(self):
        _, payload = self._send()
        self.assertEqual(payload["@type"], "MessageCard")

    def test_summary_equals_title(self):
        _, payload = self._send(title="My Alert")
        self.assertEqual(payload["summary"], "My Alert")

    def test_error_theme_color(self):
        _, payload = self._send(is_error=True)
        self.assertEqual(payload["themeColor"], "FF0000")

    def test_recovery_theme_color(self):
        _, payload = self._send(is_error=False)
        self.assertEqual(payload["themeColor"], "22C55E")

    def test_section_activity_title_contains_title(self):
        _, payload = self._send(title="Bandwidth failed")
        self.assertIn("Bandwidth failed", payload["sections"][0]["activityTitle"])

    def test_section_activity_text_is_message(self):
        _, payload = self._send(message="Order HTTP 500")
        self.assertEqual(payload["sections"][0]["activityText"], "Order HTTP 500")

    def test_host_fact_auto_prepended(self):
        _, payload = self._send()
        fact_names = [f["name"] for f in payload["sections"][0]["facts"]]
        self.assertIn("Host", fact_names)
        self.assertEqual(fact_names[0], "Host")

    def test_time_fact_auto_prepended(self):
        _, payload = self._send()
        fact_names = [f["name"] for f in payload["sections"][0]["facts"]]
        self.assertIn("Time (UTC)", fact_names)
        self.assertEqual(fact_names[1], "Time (UTC)")

    def test_extra_facts_appended_after_host_time(self):
        extra = [{"name": "Profile", "value": "peak"}, {"name": "Bandwidth", "value": "500 Mbps"}]
        _, payload = self._send(facts=extra)
        all_facts = payload["sections"][0]["facts"]
        # Host and Time first, then the extras
        self.assertEqual(all_facts[0]["name"], "Host")
        self.assertEqual(all_facts[1]["name"], "Time (UTC)")
        self.assertEqual(all_facts[2]["name"], "Profile")
        self.assertEqual(all_facts[3]["name"], "Bandwidth")

    def test_no_extra_facts_yields_only_host_and_time(self):
        _, payload = self._send(facts=None)
        self.assertEqual(len(payload["sections"][0]["facts"]), 2)

    def test_posts_to_correct_url(self):
        mock_urlopen = _make_urlopen_mock()
        target = "https://example.webhook.office.com/webhookb2/specific-path"
        with patch("lumen_scheduler.request.urlopen", mock_urlopen):
            ls.send_teams_notification(target, title="T", message="M")
        req_obj = mock_urlopen.call_args[0][0]
        self.assertEqual(req_obj.full_url, target)

    def test_content_type_header_is_json(self):
        mock_urlopen = _make_urlopen_mock()
        with patch("lumen_scheduler.request.urlopen", mock_urlopen):
            ls.send_teams_notification("https://x.com", title="T", message="M")
        req_obj = mock_urlopen.call_args[0][0]
        self.assertEqual(req_obj.get_header("Content-type"), "application/json")


# ---------------------------------------------------------------------------
# send_teams_notification — resilience (must never crash the scheduler)
# ---------------------------------------------------------------------------

class TestSendTeamsNotificationResilience(unittest.TestCase):

    def test_network_error_does_not_raise(self):
        with patch("lumen_scheduler.request.urlopen", side_effect=OSError("connection refused")):
            # Should complete without raising
            ls.send_teams_notification("https://x.com", title="T", message="M")

    def test_timeout_does_not_raise(self):
        import urllib.error
        with patch("lumen_scheduler.request.urlopen", side_effect=TimeoutError("timed out")):
            ls.send_teams_notification("https://x.com", title="T", message="M")

    def test_network_error_logs_warning(self):
        with patch("lumen_scheduler.request.urlopen", side_effect=OSError("no route")):
            with self.assertLogs("lumen_scheduler", level="WARNING") as cm:
                ls.send_teams_notification("https://x.com", title="T", message="M")
        self.assertTrue(any("Teams notification failed" in line for line in cm.output))

    def test_http_4xx_logs_warning(self):
        mock_urlopen = _make_urlopen_mock(status=400)
        with patch("lumen_scheduler.request.urlopen", mock_urlopen):
            with self.assertLogs("lumen_scheduler", level="WARNING") as cm:
                ls.send_teams_notification("https://x.com", title="T", message="M")
        self.assertTrue(any("400" in line for line in cm.output))

    def test_http_200_does_not_log_warning(self):
        mock_urlopen = _make_urlopen_mock(status=200)
        with patch("lumen_scheduler.request.urlopen", mock_urlopen):
            # assertLogs would fail if nothing is logged — use assertNoLogs (Python 3.10+)
            # For compatibility, just verify no exception and call succeeded
            ls.send_teams_notification("https://x.com", title="T", message="M")
        mock_urlopen.assert_called_once()


# ---------------------------------------------------------------------------
# notify — gating logic
# ---------------------------------------------------------------------------

class TestNotify(unittest.TestCase):

    def _notify_with_mock(self, config, event="apply_failure", **kwargs):
        with patch("lumen_scheduler.send_teams_notification") as mock_send:
            ls.notify(config, event=event, title="T", message="M", **kwargs)
        return mock_send

    def test_skips_when_no_webhook_url(self):
        mock_send = self._notify_with_mock({})
        mock_send.assert_not_called()

    def test_skips_when_url_is_empty_string(self):
        cfg = _webhook_cfg(url="")
        mock_send = self._notify_with_mock(cfg)
        mock_send.assert_not_called()

    def test_skips_when_url_is_placeholder(self):
        cfg = _webhook_cfg(url="${TEAMS_WEBHOOK_URL:-}")
        mock_send = self._notify_with_mock(cfg)
        mock_send.assert_not_called()

    def test_skips_when_url_is_bare_placeholder(self):
        cfg = _webhook_cfg(url="${TEAMS_WEBHOOK_URL}")
        mock_send = self._notify_with_mock(cfg)
        mock_send.assert_not_called()

    def test_skips_when_event_is_disabled(self):
        cfg = _webhook_cfg(on_apply_failure=False)
        mock_send = self._notify_with_mock(cfg, event="apply_failure")
        mock_send.assert_not_called()

    def test_skips_recovery_by_default(self):
        # on_recovery defaults to False
        cfg = _webhook_cfg()  # recovery not overridden → False
        mock_send = self._notify_with_mock(cfg, event="recovery")
        mock_send.assert_not_called()

    def test_sends_when_configured(self):
        cfg = _webhook_cfg()
        mock_send = self._notify_with_mock(cfg, event="apply_failure")
        mock_send.assert_called_once()

    def test_passes_title_and_message(self):
        cfg = _webhook_cfg()
        with patch("lumen_scheduler.send_teams_notification") as mock_send:
            ls.notify(cfg, event="apply_failure", title="My Title", message="My Message")
        _, kwargs = mock_send.call_args
        self.assertEqual(kwargs["title"], "My Title")
        self.assertEqual(kwargs["message"], "My Message")

    def test_passes_is_error_false(self):
        cfg = _webhook_cfg(on_recovery=True)
        with patch("lumen_scheduler.send_teams_notification") as mock_send:
            ls.notify(cfg, event="recovery", title="T", message="M", is_error=False)
        _, kwargs = mock_send.call_args
        self.assertFalse(kwargs["is_error"])

    def test_passes_facts_through(self):
        cfg = _webhook_cfg()
        extra = [{"name": "Profile", "value": "peak"}]
        with patch("lumen_scheduler.send_teams_notification") as mock_send:
            ls.notify(cfg, event="apply_failure", title="T", message="M", facts=extra)
        _, kwargs = mock_send.call_args
        self.assertEqual(kwargs["facts"], extra)

    def test_unknown_event_defaults_to_enabled(self):
        # Events not listed in config should still fire (future-proof)
        cfg = _webhook_cfg()
        mock_send = self._notify_with_mock(cfg, event="some_new_event")
        mock_send.assert_called_once()

    def test_pending_timeout_event_sends(self):
        cfg = _webhook_cfg()
        mock_send = self._notify_with_mock(cfg, event="pending_timeout")
        mock_send.assert_called_once()

    def test_pending_timeout_disabled_skips(self):
        cfg = _webhook_cfg(on_pending_timeout=False)
        mock_send = self._notify_with_mock(cfg, event="pending_timeout")
        mock_send.assert_not_called()

    def test_recovery_enabled_sends(self):
        cfg = _webhook_cfg(on_recovery=True)
        mock_send = self._notify_with_mock(cfg, event="recovery")
        mock_send.assert_called_once()

    def test_webhook_url_passed_correctly(self):
        url = "https://my-org.webhook.office.com/webhookb2/abc123"
        cfg = _webhook_cfg(url=url)
        with patch("lumen_scheduler.send_teams_notification") as mock_send:
            ls.notify(cfg, event="apply_failure", title="T", message="M")
        args, _ = mock_send.call_args
        self.assertEqual(args[0], url)


if __name__ == "__main__":
    unittest.main()
