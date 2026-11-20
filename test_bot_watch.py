import copy
import unittest
from unittest.mock import patch

import requests

import bot_watch
import poller
from screen_detector import ScreenDataUnavailable, screen_available
import json


ADD = "/add Test movie | 20261217 | PRHN | https://in.bookmyshow.com/movies/hyderabad/test/buytickets/ET12345/20261201?language=english"


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.data = {"next_id": 1, "watches": []}

    def test_add_list_remove_and_stable_ids(self):
        self.assertIn("Added watch 1", bot_watch.command(self.data, ADD))
        cfg = self.data["watches"][0]["config"]
        self.assertIn("/20261217?language=english", cfg["url_template"].format(date=cfg["requested_date"]))
        self.assertIn("Already watching", bot_watch.command(self.data, ADD))
        self.assertEqual(len(self.data["watches"]), 1)
        self.assertIn("Test movie", bot_watch.command(self.data, "/list"))
        self.assertIn("Removed", bot_watch.command(self.data, "/remove 1"))
        self.assertIn("Added watch 2", bot_watch.command(self.data, ADD))

    def test_validation_and_any_venue(self):
        for text in (ADD.replace("20261217", "20260230"),
                     ADD.replace("in.bookmyshow.com", "example.com"),
                     ADD.replace("PRHN", "PRHN,"), "/add incomplete"):
            with self.assertRaises(ValueError):
                bot_watch.command(self.data, text)
        bot_watch.command(self.data, ADD.replace("PRHN", "ANY"))
        self.assertEqual(self.data["watches"][0]["config"]["detector"], "bms_date")

    def test_mobile_movie_link_and_screen_commands(self):
        text = ADD.replace("20261217", "2026-12-17").replace("PRHN", "Prasads")
        text = text.replace("/buytickets/ET12345/20261201", "/ET12345")
        bot_watch.command(self.data, text + " | PCX Screen")
        cfg = self.data["watches"][0]["config"]
        self.assertEqual(cfg["venue_codes"], ["PRHN"])
        self.assertEqual(cfg["requested_date"], "20261217")
        self.assertEqual(cfg["screen"], "PCX Screen")
        self.assertIn("PCX Screen", bot_watch.command(self.data, "/list"))
        bot_watch.command(self.data, "/screen 1 ANY")
        self.assertNotIn("screen", cfg)
        bot_watch.command(self.data, "/screen 1 PCX Screen")
        self.assertEqual(cfg["screen"], "PCX Screen")

    def test_screen_requires_matching_movie_venue_date_and_session(self):
        cfg = bot_watch.parse_add(ADD[5:] + " | PCX Screen")
        cfg["target_url"] = cfg["url_template"].format(date=cfg["requested_date"])
        record = {"eventCode": "ET12345", "venueCode": "PRHN",
                  "showDate": "2026-12-17", "screenName": "PCX Screen",
                  "sessionId": "123", "status": "AVAILABLE"}
        def page(records):
            return '<script type="application/json">' + json.dumps(records) + '</script>'
        self.assertTrue(screen_available(page([record]), cfg))
        for key, value in (("eventCode", "ET99999"), ("venueCode", "OTHER"),
                           ("showDate", "2026-12-18"), ("sessionId", None)):
            changed = dict(record, **{key: value})
            with self.assertRaises(ScreenDataUnavailable):
                screen_available(page([changed]), cfg)
        self.assertFalse(screen_available(page([dict(record, screenName="PCX HDR by BARCO")]), cfg))
        self.assertFalse(screen_available(page([dict(record, isBookable=False)]), cfg))
        self.assertFalse(screen_available(page([dict(record, status="SOLDOUT")]), cfg))
        with self.assertRaises(ScreenDataUnavailable):
            screen_available('<p>PCX Screen</p><a href="/PRHN/20261217">Book</a>', cfg)

    def test_screen_context_never_leaks_between_movies(self):
        cfg = bot_watch.parse_add(ADD[5:] + " | PCX Screen")
        cfg["target_url"] = cfg["url_template"].format(date=cfg["requested_date"])
        data = {"venueCode": "PRHN", "showDate": "20261217", "movies": [
            {"eventCode": "ET12345", "sessions": [{"sessionId": "1", "screenName": "Screen 2", "status": "AVAILABLE"}]},
            {"eventCode": "ET99999", "sessions": [{"sessionId": "2", "screenName": "PCX Screen", "status": "AVAILABLE"}]},
        ]}
        self.assertFalse(screen_available('<script>' + json.dumps(data) + '</script>', cfg))

    def test_authorization_and_update_replay(self):
        updates = [
            {"update_id": 1, "message": {"chat": {"id": 99}, "text": ADD}},
            {"update_id": 2, "message": {"chat": {"id": 42}, "text": ADD}},
        ]
        replies = []
        bot_watch.process_updates(self.data, updates, "42", replies.append)
        bot_watch.process_updates(self.data, updates, "42", replies.append)
        self.assertEqual(len(replies), 1)
        self.assertEqual(len(self.data["watches"]), 1)
        self.assertEqual(self.data["telegram_offset"], 3)

    def test_failed_reply_preserves_command_for_retry(self):
        before = copy.deepcopy(self.data)
        def fail(text):
            raise requests.ConnectionError()
        with self.assertRaises(requests.ConnectionError):
            bot_watch.process_updates(self.data, [
                {"update_id": 2, "message": {"chat": {"id": 42}, "text": ADD}}
            ], "42", fail)
        self.assertEqual(self.data, before)

    def test_migration_keeps_config_and_state_without_credentials(self):
        with patch.object(poller, "load_json", side_effect=[
            {"movie": "Existing", "telegram_bot_token": "secret", "telegram_chat_id": "42"},
            {"available": True},
        ]):
            data = bot_watch.migrate()
        self.assertEqual(data["watches"][0]["state"], {"available": True})
        self.assertNotIn("telegram_bot_token", data["watches"][0]["config"])

    def test_alerts_are_independent_and_retry_failed_send(self):
        cfg = bot_watch.parse_add(ADD[5:])
        cfg.update(target_url="https://example.com", telegram_bot_token="token", telegram_chat_id="42")
        with patch.object(poller, "fetch", return_value="/PRHN/20261217"), patch.object(poller, "send_telegram") as send:
            state = poller.check_target(cfg, {"available": False})
            self.assertTrue(state["available"])
            poller.check_target(cfg, state)
            self.assertEqual(send.call_count, 1)
            poller.check_target(cfg, {"available": False})
            self.assertEqual(send.call_count, 2)
            state = {"available": False}
            send.side_effect = requests.ConnectionError()
            with self.assertRaises(requests.ConnectionError):
                poller.check_target(cfg, state)
            self.assertFalse(state["available"])


if __name__ == "__main__":
    unittest.main()
