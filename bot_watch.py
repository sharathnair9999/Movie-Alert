#!/usr/bin/env python3
"""Process Telegram watchlist commands, then check every saved target."""

import copy
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests

import poller
from screen_detector import ScreenDataUnavailable

WATCHLIST_PATH = Path(os.environ.get("WATCHLIST_PATH", poller.ROOT / "watchlist.json"))
HELP = """Movie watch commands:
/add Movie name | YYYYMMDD | VENUE_CODE | BookMyShow booking URL
Normal BookMyShow movie-page links also work. Dates can use YYYY-MM-DD.
Use Prasads instead of PRHN for Prasads Multiplex, Hyderabad.
Optionally append | PCX Screen to /add.
/screen ID PCX Screen — set an exact screen filter
/screen ID ANY — remove the screen filter
Use ANY for any theatre, or comma-separated venue codes.
Example:
/add Avengers: Doomsday | 20261217 | PRHN | https://in.bookmyshow.com/movies/hyderabad/avengers-doomsday/buytickets/ET00526306/20261217
/list — show saved watches and their IDs
/remove ID — stop watching a target
/help — show these instructions
Commands are processed on the next scheduled run."""


def parse_add(args):
    parts = [part.strip() for part in args.split("|")]
    if len(parts) not in (4, 5) or not all(parts):
        raise ValueError("Use /add Movie name | YYYYMMDD | VENUE_CODE or ANY | booking URL")
    movie, date, venues, url = parts[:4]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        date = date.replace("-", "")
    if "{" in url or "}" in url:
        raise ValueError("Send a booking URL with a numeric date, without braces.")
    if not re.fullmatch(r"\d{8}", date):
        raise ValueError("Date must use YYYYMMDD, for example 20261217.")
    try:
        datetime.strptime(date, "%Y%m%d")
    except ValueError:
        raise ValueError("Please enter a valid calendar date.") from None
    parsed = urlsplit(url)
    booking = re.fullmatch(r"(/movies/[^/]+/[^/]+)/buytickets/(ET\d+)/\d{8}/?", parsed.path)
    movie_page = re.fullmatch(r"(/movies/[^/]+/[^/]+)/(ET\d+)/?", parsed.path)
    match = booking or movie_page
    if parsed.scheme != "https" or parsed.netloc != "in.bookmyshow.com" or not match:
        raise ValueError("Paste a normal BookMyShow movie-page link or full booking URL from in.bookmyshow.com. Short share links are not supported yet.")
    path = f"{match[1]}/buytickets/{match[2]}/{{date}}"
    template = urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))
    cfg = {"movie": movie, "requested_date": date, "url_template": template}
    if venues.upper() == "ANY":
        cfg["detector"] = "bms_date"
    else:
        aliases = {"prasads": "PRHN", "prasads multiplex": "PRHN",
                   "prasads multiplex: hyderabad": "PRHN"}
        venues = aliases.get(venues.casefold(), venues)
        codes = [code.strip().upper() for code in venues.split(",")]
        if not all(re.fullmatch(r"[A-Z0-9]+", code) for code in codes):
            raise ValueError("Use venue codes such as PRHN, INPR, or ANY.")
        cfg.update(detector="venue_date", venue_codes=sorted(set(codes)))
    if len(parts) == 5:
        if cfg["detector"] != "venue_date":
            raise ValueError("Choose a theatre instead of ANY when filtering by screen.")
        cfg["screen"] = parts[4]
    return cfg


def migrate():
    cfg = poller.load_json(poller.CONFIG_PATH, {}) or {}
    # Keep credentials out of the persisted watchlist.
    cfg.pop("telegram_bot_token", None)
    cfg.pop("telegram_chat_id", None)
    state = poller.load_json(poller.STATE_PATH, {}) or {}
    return {"next_id": 2 if cfg else 1, "watches": (
        [{"id": 1, "config": cfg, "state": state}] if cfg else []
    )}


def command(data, text):
    head, _, args = text.strip().partition(" ")
    name = head.split("@", 1)[0].lower()
    if name in ("/start", "/help"):
        return HELP
    if name == "/list":
        if not data["watches"]:
            return "No movies saved. Use /add to start watching.\n" + HELP
        lines = []
        for watch in data["watches"]:
            cfg = watch["config"]
            venues = cfg.get("venue_codes") or [cfg.get("venue_code", "ANY")]
            screen = f" | Screen: {cfg['screen']}" if cfg.get("screen") else ""
            status = watch.get("state", {}).get("status", "not checked yet")
            lines.append(f"{watch['id']}: {cfg.get('movie', 'Movie')} | {cfg.get('requested_date', '')} | {', '.join(venues)}{screen} | {status}")
        return "Saved watches:\n" + "\n".join(lines)
    if name == "/add":
        cfg = parse_add(args)
        for watch in data["watches"]:
            existing = watch["config"]
            if (existing.get("url_template", "").format(date=existing.get("requested_date", ""))
                    == cfg["url_template"].format(date=cfg["requested_date"])
                    and existing.get("detector") == cfg["detector"]
                    and existing.get("screen", "").casefold() == cfg.get("screen", "").casefold()
                    and sorted(existing.get("venue_codes") or [existing.get("venue_code", "ANY")])
                    == sorted(cfg.get("venue_codes") or ["ANY"])):
                return f"Already watching this target (ID {watch['id']})."
        watch_id = data["next_id"]
        data["next_id"] += 1
        data["watches"].append({"id": watch_id, "config": cfg, "state": {"available": False}})
        return f"Added watch {watch_id}: {cfg['movie']} on {cfg['requested_date']}. Checking this run."
    if name == "/screen":
        watch_id, _, screen = args.strip().partition(" ")
        if not watch_id.isdigit() or not screen.strip():
            raise ValueError("Use /screen ID PCX Screen, or /screen ID ANY.")
        for watch in data["watches"]:
            if watch["id"] == int(watch_id):
                cfg = watch["config"]
                if screen.strip().upper() == "ANY":
                    cfg.pop("screen", None)
                elif cfg.get("detector") == "venue_date":
                    cfg["screen"] = screen.strip()
                else:
                    raise ValueError("Screen filters require a theatre-specific watch.")
                watch["state"] = {"available": False}
                return f"Watch {watch_id}: screen filter {cfg.get('screen', 'ANY')}. Checking this run."
        return f"No watch with ID {watch_id}. Use /list."
    if name == "/remove":
        if not args.strip().isdigit():
            raise ValueError("Use /remove ID. Find IDs with /list.")
        watch_id = int(args.strip())
        for watch in data["watches"]:
            if watch["id"] == watch_id:
                data["watches"].remove(watch)
                return f"Removed watch {watch_id}: {watch['config'].get('movie', 'Movie')}."
        return f"No watch with ID {watch_id}. Use /list."
    return "Unknown command.\n" + HELP


def process_updates(data, updates, chat_id, reply):
    for update in sorted(updates, key=lambda item: item["update_id"]):
        update_id = update["update_id"]
        if update_id < data.get("telegram_offset", 0):
            continue
        message = update.get("message", {})
        if str(message.get("chat", {}).get("id")) == str(chat_id) and message.get("text"):
            candidate = copy.deepcopy(data)
            try:
                response = command(candidate, message["text"])
            except ValueError as exc:
                response = str(exc)
            # Do not advance the cursor or apply the command if its reply fails.
            for start in range(0, len(response), 4000):
                reply(response[start:start + 4000])
            data.clear()
            data.update(candidate)
        data["telegram_offset"] = update_id + 1


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    data = poller.load_json(WATCHLIST_PATH)
    if data is None:
        data = migrate()
    failed = False
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": data.get("telegram_offset", 0), "timeout": 0,
                    "allowed_updates": '["message"]'}, timeout=30,
        )
        # Avoid logging request exceptions containing the bot token.
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise ValueError("Telegram rejected getUpdates")
        process_updates(data, payload["result"], chat_id,
                        lambda text: poller.send_telegram(token, chat_id, text))
    except (requests.RequestException, ValueError):
        print("Telegram command processing failed; will retry next run. Check bot credentials and ensure no webhook is set.")
        failed = True
    finally:
        poller.save_json(WATCHLIST_PATH, data)

    for watch in data["watches"]:
        cfg = dict(watch["config"], telegram_bot_token=token, telegram_chat_id=chat_id)
        if cfg.get("url_template"):
            cfg["target_url"] = cfg["url_template"].format(date=cfg.get("requested_date", ""))
        try:
            watch["state"] = poller.check_target(cfg, watch.get("state", {"available": False}))
        except ScreenDataUnavailable:
            state = watch.setdefault("state", {"available": False})
            if state.get("status") != "screen unverified":
                try:
                    poller.send_telegram(token, chat_id, f"Watch {watch['id']}: cannot verify {cfg['screen']} for {cfg.get('movie', 'Movie')} on {cfg['requested_date']}. BookMyShow did not expose enough screen-specific booking data. No screen alert will be sent until it can be verified.")
                except requests.RequestException:
                    failed = True
                else:
                    state["status"] = "screen unverified"
            print(f"Watch {watch['id']}: screen data unavailable.")
        except requests.RequestException:
            print(f"Watch {watch['id']}: notification failed; will retry next run.")
            failed = True
        finally:
            poller.save_json(WATCHLIST_PATH, data)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
