"""Conservative screen detection from structured show records in page scripts.

Context flows from parent records to their children, never across siblings.
Missing screen/session metadata is an unknown result, not a closed screen.
"""

import json
import re
from html.parser import HTMLParser


class ScreenDataUnavailable(ValueError):
    pass


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = True
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.scripts.append("".join(self.parts))
            self.active = False


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


FIELDS = {
    "event": ("eventcode", "eventid", "moviecode", "movieid"),
    "venue": ("venuecode", "cinemacode"),
    "date": ("showdate", "sessiondate", "date", "showdatetime", "sessiondatetime"),
    "screen": ("screenname", "screendescription", "auditoriumname"),
    "session": ("sessionid", "sessioncode", "showid"),
}


def records(node, context=None):
    if isinstance(node, list):
        for child in node:
            yield from records(child, context)
    elif isinstance(node, dict):
        context = dict(context or {})
        fields = {normalized(key): value for key, value in node.items()
                  if isinstance(value, (str, int, bool))}
        for field, aliases in FIELDS.items():
            for alias in aliases:
                if alias in fields:
                    context[field] = str(fields[alias])
                    break
        # Require a session identifier on this record, not an ancestor.
        if any(alias in fields for alias in FIELDS["session"]):
            yield context, fields
        for child in node.values():
            if isinstance(child, (dict, list)):
                yield from records(child, context)


def screen_available(page, cfg):
    parser = Scripts()
    parser.feed(page)
    event = re.search(r"ET\d+", cfg["target_url"])
    if not event:
        raise ScreenDataUnavailable("Movie identifier missing from booking URL.")
    venues = cfg.get("venue_codes") or [cfg.get("venue_code")]
    verified = False
    for script in parser.scripts:
        text = script.strip()
        # Support JSON scripts and common JSON assignments; never execute JS.
        assignment = re.match(r"(?:window\.)?__[A-Z_]+__\s*=\s*", text)
        if assignment:
            text = text[assignment.end():].rstrip("; \n")
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        for record, fields in records(data):
            date = re.sub(r"\D", "", record.get("date", ""))[:8]
            if (record.get("event") != event[0] or record.get("venue") not in venues
                    or date != cfg["requested_date"] or not record.get("screen")
                    or not record.get("session")):
                continue
            # Only explicit bookability is evidence; a session ID alone may be sold out.
            status = normalized(fields.get("availability", fields.get("status", "")))
            bookable = fields.get("isbookable", fields.get("bookable"))
            if bookable is None and status not in ("available", "fastfilling", "soldout", "closed"):
                continue
            verified = True
            explicitly_closed = bookable is False or normalized(bookable) == "false"
            if (normalized(record["screen"]) == normalized(cfg["screen"])
                    and not explicitly_closed
                    and (bookable is True or normalized(bookable) == "true"
                         or status in ("available", "fastfilling"))):
                return True
    if not verified:
        raise ScreenDataUnavailable("Page does not expose verifiable movie/venue/date/screen availability records.")
    return False
