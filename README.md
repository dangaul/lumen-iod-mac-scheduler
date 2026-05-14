# Lumen Bandwidth Scheduler (Mac)

Simple macOS Python CLI for Lumen Internet On-Demand.

Project repository:
- [https://github.com/dangaul/lumen-iod-mac-scheduler](https://github.com/dangaul/lumen-iod-mac-scheduler)

License: MIT

Official IoD documentation:
- [Lumen Internet On-Demand Overview](https://developer.lumen.com/devcenter/docs/internet-on-demand/overview)

Recommended API product access in Dev Center for this script:
- `Service Details` (inventory lookup)
- `Quoting` (price request)
- `Ordering` (submit order update)
- `Business Management` is optional unless you need billing account API endpoints directly.
- If IoD/NaaS is not listed in products, request `Unlisted API Product` and ask Lumen to enable Internet On-Demand access for your app.

It sets bandwidth by schedule in the configured `timezone` from `config.json` (default: `America/Los_Angeles`, PST/PDT):
- Peak: `500 Mbps`
  - Monday-Thursday: `06:30` to `19:00`
  - Friday: `06:30` to `17:00`
- Off-peak: `100 Mbps`
  - All other times (including weekends)

Example service ID format: `77123456789` (11 digits). The value in `config.example.json` is a placeholder — replace it with your real service ID before running. See [Finding your service ID](#finding-your-service-id) below.

## Lumen API flow used

Matches the IoD references and docs flow:
1. `POST /oauth/v2/token`
2. `GET /ProductInventory/v1/inventory?serviceId=...`
3. `POST /Product/v1/priceRequest`
4. `POST /Customer/v3/Ordering/orderRequest`

## Setup

Requirements (macOS):
- Python `3.10+`
- `crontab` (optional, only for cron install/manage features)

Quick bootstrap on a new Mac:

```bash
./install_macos.sh
```

If Python is missing and you use Homebrew, you can let the installer add it:

```bash
AUTO_INSTALL_PYTHON=1 ./install_macos.sh
```

The install script will:
- Create `config.json` from the template if it does not exist
- Create `.env` from the template if it does not exist
- Warn about any new config sections or env variables added since the last release
- Warn if `lumen_iod.service_id` is still the placeholder value, and automatically query the Lumen API for available service IDs if credentials are already present in `.env`
- Run a syntax check and the unit test suite as a final validation step

Important:
- Cron/scheduler jobs do **not** run while the Mac is asleep or powered off.
- For reliable schedule enforcement, keep the Mac awake during windows when changes must occur.

1. Copy config:

```bash
cp ./config.example.json ./config.json
```

2. Set secrets via env (do not hardcode in JSON):

```bash
cp ./.env.example ./.env
chmod 600 ./.env
```

The script auto-loads `.env` from the config directory (or current directory), so you do not need to `source` it manually.

Use `LUMEN_CLIENT_ID` plus one of:
- `LUMEN_API_KEY`
- `LUMEN_CLIENT_SECRET`
- `LUMEN_BASIC_SECRET` (already base64 encoded `client_id:secret`)

`LUMEN_CLIENT_ID` is required unless `LUMEN_BASIC_SECRET` is provided.

`LUMEN_BILLING_ACCOUNT_ID` can be set to a value like `5-3ABCDE12`. The script can also derive billing account values from the inventory API response.

3. Find and set your service ID (see below), then validate:

```bash
python3 ./lumen_scheduler.py --config ./config.json status
python3 ./lumen_scheduler.py --config ./config.json run --dry-run --force
```

If you see `Config not found .../config.json`, create it first:

```bash
cp ./config.example.json ./config.json
```

## Finding your service ID

The `service_id` in `config.example.json` is a placeholder. Run this after filling in your `.env` credentials to list your actual Lumen Internet On-Demand services:

```bash
python3 ./lumen_scheduler.py --config ./config.json list-services
```

Output example:
```
Found 1 Internet On-Demand service(s):

  service_id: 77133831778  status: Active  bandwidth: 500 Mbps

Set lumen_iod.service_id in config.json to one of the above.
```

Copy the `service_id` value into `lumen_iod.service_id` in your `config.json`. The install script runs this automatically if credentials are present and the service ID is still a placeholder.

## Notifications (Microsoft Teams)

The scheduler can post alerts to a Microsoft Teams channel when a bandwidth change fails or times out. No Apple ID or personal account is needed on the machine — only a webhook URL stored in `.env`.

**Setup:**

1. In Teams, go to the target channel → `...` → `Connectors` → `Incoming Webhook`. Copy the webhook URL.
   - For newer Teams setups without Connectors, create a Power Automate flow with a `When an HTTP request is received` trigger and use that URL.
2. Add to `.env`:
   ```
   TEAMS_WEBHOOK_URL=https://your-org.webhook.office.com/webhookb2/...
   ```
3. Add to `config.json` (already present in `config.example.json`):
   ```json
   "notifications": {
     "teams_webhook_url": "${TEAMS_WEBHOOK_URL:-}",
     "on_apply_failure": true,
     "on_pending_timeout": true,
     "on_recovery": false
   }
   ```

**Production alert events** (always active when webhook is configured):

| Key | Default | Fires when |
|---|---|---|
| `on_apply_failure` | `true` | Lumen API rejects or errors on a bandwidth change order |
| `on_pending_timeout` | `true` | A submitted order does not confirm within the timeout window |
| `on_recovery` | `false` | A pending change successfully confirms (opt-in) |

**Debug status notifications** (requires `dashboard.debug_enabled: true`):

When debug mode is on, the scheduler also posts a status card for every bandwidth change attempt — both from scheduled cron runs and manual dashboard overrides:

| Event | Example message |
|---|---|
| Order accepted by Lumen | `Change to 500 Mbps has started @ 9:15 AM PDT` |
| Inventory confirms bandwidth active | `Change to 500 Mbps was successful @ 9:22 AM PDT` |
| API rejects the order | `Change to 500 Mbps failed: Order update failed HTTP 500 @ 9:15 AM PDT` |
| Pending change times out | `Change to 500 Mbps failed: timed out @ 9:30 AM PDT` |

The timestamp uses the `timezone` from `config.json` (e.g. `PDT`, `PST`). Debug notifications are green for success, red for failure.

To test notifications without triggering a real bandwidth change:

```bash
cd /path/to/lumen-scheduler
python3 -c "
import lumen_scheduler as ls, os
from pathlib import Path
ls.load_dotenv(Path('.env'))
url = os.environ.get('TEAMS_WEBHOOK_URL', '')
config = ls.load_config(Path('config.json'))
ls.send_teams_notification(url, title='Lumen: Bandwidth change started',
    message=f'Change to 500 Mbps has started @ {ls.local_time_str(config)}', is_error=False)
ls.send_teams_notification(url, title='Lumen: Bandwidth change successful',
    message=f'Change to 500 Mbps was successful @ {ls.local_time_str(config)}', is_error=False)
ls.send_teams_notification(url, title='Lumen: Bandwidth change failed',
    message=f'Change to 500 Mbps failed: Order update failed HTTP 500 @ {ls.local_time_str(config)}', is_error=True)
print('Done — check Teams.')
"
```

Each alert includes the machine hostname and timestamp. Sensitive values (tokens, credentials) are stripped before any message is sent. A notification failure never crashes the scheduler — it logs a warning and continues.

## How To / Usage Examples

Scheduled run:

```bash
python3 ./lumen_scheduler.py --config ./config.json run
```

Force peak temporarily (example: keep 500 Mbps for next 1 hour):

```bash
python3 ./lumen_scheduler.py --config ./config.json override --profile peak --hours 1
```

If run at 7:10pm Thursday, this keeps 500 Mbps until 8:10pm local time, then normal schedule resumes.

Force off-peak temporarily (example: holiday/no office, keep 100 Mbps for 10 hours):

```bash
python3 ./lumen_scheduler.py --config ./config.json override --profile off-peak --hours 10
```

You can use either `off-peak` or `off_peak`.

Long off-peak override from Dashboard:
- Use `Long Off-Peak Until (local)` and pick the date/time to resume normal schedule.
- This mode is intended for multi-day closures (for example holidays).
- Unlike the short hourly override, this long off-peak mode is not clipped to the next schedule boundary.

Clear manual override immediately:

```bash
python3 ./lumen_scheduler.py --config ./config.json clear-override
```

Check current matched profile/rule:

```bash
python3 ./lumen_scheduler.py --config ./config.json status
```

List available Lumen service IDs for this account:

```bash
python3 ./lumen_scheduler.py --config ./config.json list-services
```

Start local dashboard (current profile, last run, cron line, recent logs):

```bash
python3 ./dashboard.py --config ./config.json --log-file ./lumen-scheduler.log --port 8787
```

Then open: `http://127.0.0.1:8787`

Or use the launcher script:

```bash
./launch_web_page.sh
```

Stop dashboard:

```bash
./stop_web_page.sh
```

Restart dashboard:

```bash
./restart_web_page.sh
```

If port `8787` is busy, the launcher automatically tries the next free port.

Dashboard quick actions include:
- `Switch to On Peak (500 Mbps)`
- `Switch to Off Peak (100 Mbps)`
- `Clear Override`
- `Set Off-Peak Until` (long override through a selected local date/time)
- Dashboard shows a readable schedule block (12-hour format) above Quick Actions.

Navigation tabs include:
- `Dashboard`
- `Cost Analytics` (day-to-day cost graph for current month from Customer Bill API)
- `Configuration`

Debug panel actions include:
- `Test API Connection` (queries inventory and reports live mapped status: `on_peak` / `off_peak` / `unknown`)

Safety behavior:
- On first page load, manual action buttons are disabled until live status is known.
- If live status does not match schedule, only `Clear Override` is enabled.
- If an override is active, switching to the current base schedule profile is blocked; use `Clear Override`.
- Overrides auto-clear when cron/schedule reaches the same profile as the override.
- Off Peak bandwidth cannot be set higher than On Peak (equal is allowed).
- Bandwidth dropdown values come from `lumen_iod.bandwidth_options` (plus current live/config values).
- `Debug` toggle (below Quick Actions) reveals Override, Last Error, Recent Logs, and a live API response stream.

Configuration page includes:
- Logging controls (`logging.enabled`, `logging.include_sensitive`, `logging.file`)
- Core runtime settings (`timezone`, `service_id`)
- Flexible schedule rule editor:
  - Set `Default Profile` (fallback when no rule matches)
  - Add/remove rule rows
  - Per-rule: profile (`On Peak`/`Off Peak`), selected days, start/end time
  - Rules are evaluated top-to-bottom (first match wins)
- Bandwidth profile management (fetch + save)
- Cron management (check availability, list jobs, install/remove managed cron)
- Manual bandwidth options fetch (not automatic on page load)

The dashboard also queries live service inventory from Lumen API so you can compare:
- scheduled profile/bandwidth vs live status/bandwidth

Customer Bill API month-to-date cost:
- Dashboard pulls cost from:
  - `GET /Billing/v2/CustomerBillManagement/customerBill`
  - `GET /Billing/v2/CustomerBillManagement/serviceLevelChargeDetails` (per invoice)
- Required by OpenAPI: header `x-billing-account-number` and query `billingAccountNumber`
- Service-level endpoint also requires query `invoiceNumber`
- OAuth client credentials token URL in sandbox spec: `https://api-test.lumen.com/oauth/v2/token`
- For sandbox testing set `lumen_iod.customer_bill.base_url` to `https://api-test.lumen.com`

Logo support:
- Put your logo file in the project root as one of:
  - `logo.png` (recommended)
  - `logo-white.png`
  - `logo.svg`
  - `logo.jpg` / `logo.jpeg`

Install cron every 5 minutes:

```bash
python3 ./lumen_scheduler.py --config ./config.json install-cron --interval-minutes 5 --log-file ./lumen-scheduler.log
```

What managed cron does:
- Installs one managed cron entry that runs `lumen_scheduler.py run` on the selected interval.
- Each run evaluates:
  - current schedule (day/time rules)
  - active override (if any)
  - configured lead times before upcoming schedule transitions
  - any local pending Lumen change
  - then applies the target Lumen profile if a change is needed.
- It does **not** submit a Lumen change on every run.
  - If local state already matches the target profile, it skips API apply.
  - If local state is unknown (fresh machine/state reset), it does a one-time live inventory check and only applies if needed.
- Lumen changes are asynchronous. After an order is accepted, the scheduler records `pending_change` in the local state file and only polls inventory until Lumen reports the target bandwidth as active.
  - This means "order accepted" and "bandwidth active" are treated as separate states. The dashboard shows the change as pending until live inventory confirms it.
- While a change is pending, new schedule/override changes are blocked by default (`runtime.block_new_changes_while_pending=true`).
- It uses a local run lock (`runtime.lock_file`, default `./.lumen-bandwidth-run.lock`) to prevent overlapping cron runs.
- Pending change polling is controlled by `runtime.pending_poll_seconds` (default `30`) and `runtime.pending_timeout_minutes` (default `15`).
- Peak/off-peak lead times are controlled by `runtime.peak_lead_minutes` (default `15`) and `runtime.off_peak_lead_minutes` (default `0`).
  - Lead time lets cron submit a change before a scheduled boundary so Lumen has time to apply it by the intended start time.
- Yes: if interval is `60`, it checks once every 60 minutes.
  - That means a peak/off-peak transition can be applied up to ~60 minutes late.
  - For tighter transitions, use a smaller interval (commonly `5` or `10` minutes).
- Yes: if an override is active, cron respects it until it expires (or is manually cleared).
- After override expiration, the next cron run applies the normal scheduled profile.
- Manual overrides are clipped so they do not overlap into time where base schedule already matches that profile.
  - Example: if Off Peak starts at `6:00 PM`, an Off Peak override started at `5:00 PM` is capped to `1 hour`.
- If a bandwidth change fails or times out, a Teams alert is sent if `notifications` is configured in `config.json`.

## Tests

A unit test suite covers the notification layer (config defaults, Teams payload structure, resilience, and gating logic):

```bash
python3 -m unittest test_lumen_scheduler.py -v
```

Run this before committing or deploying an update. The install script also runs it automatically.

## Packaging / Deploying to Another Mac

The target machine does not need git or any account login.

**Build:**

```bash
bash create_portable_zip.sh
```

This produces two files in `./dist/`:

| File | Purpose |
|---|---|
| `lumen-scheduler-macos-<timestamp>.sh` | Self-extracting installer — transfer this single file |
| `lumen-scheduler-macos-<timestamp>.zip` | Plain zip — attach to a GitHub Release |

Both contain templates only — no `.env`, no `config.json`, no logs or state. Nothing sensitive is bundled.

The `.sh` is a bash script with the zip appended after `exit 0`. When run, it extracts the zip to a temp file using `dd` (offset baked in at build time), unpacks it to the target directory with `unzip`, then runs `install_macos.sh`.

**Transfer options (no Apple ID required):**
- USB drive
- AirDrop (works without Apple ID — Discovery set to `Everyone`, same network)
- Shared network folder
- Email

**First install on the target Mac:**

```bash
bash lumen-scheduler-macos-*.sh
```

Installs to `~/lumen-scheduler` by default. Pass a path to install elsewhere:

```bash
bash lumen-scheduler-macos-*.sh /path/to/install/dir
```

Then edit `.env` with real credentials and `TEAMS_WEBHOOK_URL`, and update `config.json` with your `service_id`.

**Updating an existing install:**

Run the new installer against the same directory:

```bash
bash lumen-scheduler-macos-*.sh ~/lumen-scheduler
```

The installer detects an existing `config.json` and presents an update menu:
- **Option 1** — Update scripts only, keep existing `config.json` and `.env` (recommended)
- **Option 2** — Full reinstall from templates (offers to back up existing files first)
- **Option 3** — Cancel

It will warn about any new config sections or env variables that need to be added manually, for example:

```
[install] ⚠  config.json is missing new section(s): notifications
[install]    Add the following to config.json (see config.example.json for full structure):
  "notifications": { ... }

[install] ⚠  .env is missing new variable(s): TEAMS_WEBHOOK_URL
[install]    Add the following line(s) to .env:
             TEAMS_WEBHOOK_URL=
```

**Verify the package:**

```bash
bash test_installer.sh
```

Checks that the build artifacts are correct, the zip contains the right files (and excludes sensitive ones), the embedded payload offset is valid, and a fresh install completes successfully.

## Notes

- `config.example.json` includes a rate reference from your portal screenshot:
  - `500 Mbps`: `$0.79/hr`
  - `100 Mbps`: `$0.47/hr`
- Rates are informational only; the API payload uses bandwidth values.
- Runs are logged to `./lumen-scheduler.log` by default. You can set a custom path with `--log-file`.
- `related_contact` is used for Lumen order payload contact details when values are provided.
- Scheduler and dashboard timestamps both use `timezone` from `config.json`.

## Author

- Creator: Dan Gaul
