# Movie-Alert

Get a **Telegram** ping the moment a specific **movie + theatre + date** opens
for booking on BookMyShow. Runs on **GitHub Actions**, triggered every ~10 min
by **cron-job.org** — nothing to keep running on your own machine.

<img width="1220" height="1076" alt="Media" src="https://github.com/user-attachments/assets/3c6a8f8e-5458-42a5-a870-9001a9990de3" />


## How it works

1. **cron-job.org** triggers the workflow every ~10 min (GitHub's own scheduler
   is unreliable, so we trigger it externally).
2. `bot_watch.py` reads Telegram commands and checks every saved movie using
   `poller.py`, which fetches the BMS page through **ScraperAPI** (an India IP — BMS
   blocks foreign/datacenter IPs and returns 403 otherwise).
3. It checks whether your target is open and, on the `False → True` flip, sends
   one Telegram message per watch. The watchlist, per-watch availability, and
   Telegram update cursor live in `watchlist.json`, saved back to the repo.

## 1. Telegram bot

- Message **@BotFather** → `/newbot` → copy the **token**.
- Send your new bot any message, then open
  `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the `chat.id` — that's
  your **chat id**.

## 2. ScraperAPI key

Sign up at **scraperapi.com** (free tier) and copy your API key.

## 3. Add repo secrets

**Settings → Secrets and variables → Actions:**

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `SCRAPERAPI_KEY`

## 4. Manage movies through Telegram

After deploying this version to the branch your cron job runs, send `/help` to
your bot and trigger the workflow once (or wait for the next scheduled run).
Your existing `config.json` movie and `state.json` availability are imported
automatically on the first run. No new secrets or always-running server needed.

Add a movie with four fields separated by `|`:

```text
/add Avengers: Doomsday | 20261217 | PRHN | https://in.bookmyshow.com/movies/hyderabad/avengers-doomsday/buytickets/ET00526306/20261217
```

For mobile, copy the normal movie-page link from your browser; you do not need
to extract the event code or open the booking page. `Prasads` is an alias for
Prasads Multiplex, Hyderabad, and dates also accept `YYYY-MM-DD`:

```text
/add Avengers: Doomsday | 2026-12-17 | Prasads | https://in.bookmyshow.com/movies/hyderabad/avengers-doomsday/ET00526306 | PCX Screen
```

Shortened app share links are not supported yet. Use the full
`https://in.bookmyshow.com/movies/...` movie-page or booking link.

The optional fifth field filters by an exact screen label. `PCX Screen` and
`PCX HDR by BARCO` are treated as different screens. To change an existing watch:

```text
/screen 1 PCX Screen
/screen 1 ANY
```

`ANY` removes the screen filter. `/list` includes the filter and latest check
status. A screen filter requires a specific theatre.

**Screen verification is provisional:** the adapter reads structured JSON show
records embedded in the page, requiring matching movie, venue, date, screen,
session, and explicit bookability. It has automated fixture tests but has not
been validated against live ScraperAPI HTML. A direct inspection returned 403.
If those records are absent or use an unsupported schema, the bot sends a
one-time "cannot verify" message and `/list` shows `screen unverified`. This
does not mean bookings are closed. A page-wide screen label is never sufficient
to trigger an alert; a real fetched page is needed to validate or extend the
adapter before relying on screen alerts.

- `/list` shows saved watches and their IDs.
- `/remove 1` removes watch ID 1.
- `/help` shows command instructions.
- Use `ANY` instead of a venue code to watch any theatre, or `PRHN,INPR` to
  watch multiple venues. Any matching venue triggers the alert.

Use a full BookMyShow movie-page or booking URL. The `/add` date overrides the
booking URL's date; query filters are preserved.
Movie names are labels, so use the correct movie's booking URL.
See below for how to find venue codes. Structured commands do not require AI.

Commands and replies are processed on the **next workflow run**, usually about
10 minutes later. Only messages from `TELEGRAM_CHAT_ID` are accepted; use your
private bot chat if only you should control the list. All saved movies are
checked each run, so ScraperAPI usage grows with the number of watches.

The workflow commits `watchlist.json` automatically. Allow GitHub Actions to
write repository contents and ensure branch rules permit the bot's push.
Watch details will be visible in your repository. After migration, manage movies
through Telegram; editing `config.json` no longer changes the active watchlist.

Do not run another Telegram update consumer or configure a webhook for this bot.
Telegram keeps pending commands for at most 24 hours, so keep the scheduler
running. If no reply arrives, check the latest Actions run and the configured
chat ID.

### Legacy single-movie configuration

`python poller.py` still supports the original single-movie mode. The workflow
uses `python bot_watch.py` for Telegram commands and multiple watches.

Pick a `detector`. The page URL is built from `url_template` + `requested_date`.
After editing, reset `state.json` to `{"available": false}`.

**`venue_date`** — a specific theatre opens for a specific date (most precise):
```json
{
  "detector": "venue_date",
  "movie": "The Odyssey (IMAX 2D)",
  "requested_date": "20260722",
  "venue_code": "INPR",
  "venue_label": "INOX: LUXE Phoenix Market City, Velachery",
  "url_template": "https://in.bookmyshow.com/movies/chennai/the-odyssey/buytickets/ET00480917/{date}"
}
```
Use `"venue_codes": ["PVPZ","INPR"]` to fire when *any* of several theatres open.

**`bms_date`** — a date opens at *any* theatre (date-dominance on the page):
```json
{ "detector": "bms_date", "requested_date": "20260722",
  "url_template": ".../buytickets/ET00480917/{date}", "min_references": 10 }
```

**Finding the values:** read `<city>/<slug>/<ETcode>/<date>` from the movie's
"Book tickets" URL. For `venue_code`, open a date where the theatre *is* open and
read its cinema link `.../cinemas/<city>/<venue-slug>/buytickets/<CODE>/<date>` —
the `<CODE>` (e.g. `PVPZ`, `INPR`) is the value.

## 5. Schedule it with cron-job.org

**a. GitHub token** — *Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate*. Scope it to this repo, permission
**Actions: Read and write**. Copy the token.

**b. cron-job.org** — create a cronjob:

| Field | Value |
|---|---|
| URL | `https://api.github.com/repos/<you>/<repo>/actions/workflows/booking-watch.yml/dispatches` |
| Schedule | every 10 minutes |
| Method | `POST` |
| Header | `Accept: application/vnd.github+json` |
| Header | `Authorization: Bearer <your-token>` |
| Header | `X-GitHub-Api-Version: 2022-11-28` |
| Body | `{"ref":"main"}` |

Save, then **Run now**. GitHub returns `204`; a run appears under **Actions**.
From then on it fires every 10 min. Keep the token only in cron-job.org.

## Geo-block (why ScraperAPI)

BMS only serves India and blocks datacenter IPs, so GitHub's US runners get a
**403** on a direct request. `SCRAPERAPI_KEY` routes through an India IP and fixes
it. Alternatives: set a `PROXY_URL` secret (India proxy), or run from a machine in
India. It's IP/geo-based — headers alone won't get past it.

## Reuse it (fork)

Fork the repo, **enable Actions** on the fork (off by default), add your own
secrets, edit `config.json`, reset `state.json`, and set up your own cron-job.org
trigger. Each fork is independent with its own Telegram chat. No branches needed —
date-only vs theatre-specific is just the `detector` field.

## Files

| File | Purpose |
|------|---------|
| `poller.py` | Fetch page, detect availability, send Telegram alert |
| `bot_watch.py` | Read Telegram commands and check saved watches |
| `screen_detector.py` | Verify structured show records for an exact screen |
| `watchlist.json` | Auto-created watchlist, per-watch state, Telegram update cursor |
| `config.json` | Initial movie imported on first run; legacy single-movie config |
| `.github/workflows/booking-watch.yml` | The runner (dispatched by cron-job.org) |
| `requirements.txt` | Python deps (`requests`) |
| `state.json` | Initial availability imported on first run; legacy single-movie state |

Run local checks with `python3 -m unittest -v`.
