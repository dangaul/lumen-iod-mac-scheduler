# Installation Guide — Lumen Bandwidth Scheduler

This guide covers getting the scheduler running on a Mac from scratch.  
For day-to-day usage, overrides, and dashboard features see [README.md](README.md).

---

## What you need before starting

- A Mac running macOS (the target machine does not need Xcode, git, or an Apple ID)
- Python 3.10 or later — the installer will check and offer to install it via Homebrew if missing
- Your Lumen IoD API credentials (client ID + secret or basic secret) from the [Lumen Dev Center](https://developer.lumen.com)
- Your Lumen customer number and billing account ID
- A Microsoft Teams incoming webhook URL if you want failure alerts (optional)

---

## Step 1 — Get the package

Download the latest release zip from GitHub:

**[https://github.com/dangaul/lumen-iod-mac-scheduler/releases/latest](https://github.com/dangaul/lumen-iod-mac-scheduler/releases/latest)**

Click the `lumen-scheduler-macos-<date>.zip` asset to download it.  
No account or login is required to download a public release.

---

## Step 2 — Transfer to the target Mac

If you downloaded it on a different machine, copy the zip over. No git or Apple ID required:

| Method | How |
|---|---|
| **USB drive** | Copy zip to USB, plug into target Mac, copy to Desktop |
| **AirDrop** | Works without Apple ID — set Receiving to `Everyone` in Finder → AirDrop |
| **Shared network folder** | Drop zip in a shared folder both machines can see |
| **Email** | Email the zip to an account accessible on the target Mac |

---

## Step 3 — Extract the zip

Open Terminal on the target Mac and run:

```bash
cd ~/Desktop
unzip lumen-scheduler-macos-*.zip -d lumen-scheduler
cd lumen-scheduler
```

Or double-click the zip in Finder to extract it, then open Terminal and `cd` into the folder.

---

## Step 4 — Run the install script

```bash
bash install_macos.sh
```

The installer will:
1. Verify Python 3.10+ is available (offers to install via Homebrew if not)
2. Detect whether this is a **fresh install** or an **update to an existing install**
3. Create `config.json` from the template (fresh install only)
4. Create `.env` from the template (fresh install only)
5. Warn about any new config sections or env variables needed (update only)
6. Warn if `lumen_iod.service_id` is still a placeholder, and query the Lumen API for your real service IDs if credentials are already present
7. Run a syntax check and the full unit test suite

If Python is not installed and you have Homebrew:

```bash
AUTO_INSTALL_PYTHON=1 bash install_macos.sh
```

**Update mode:** If `config.json` already exists, the installer presents a menu:
- **Option 1** — Update scripts only, keep your existing `config.json` and `.env`
- **Option 2** — Full reinstall from templates (offers to back up existing files first)
- **Option 3** — Cancel

---

## Step 5 — Fill in your credentials

Open `.env` in a text editor:

```bash
nano .env
```

Fill in your Lumen API credentials. Use one of these three auth options:

```
# Option A — Basic secret (pre-encoded client_id:secret)
LUMEN_BASIC_SECRET=your_base64_encoded_secret

# Option B — Client ID + API key
LUMEN_CLIENT_ID=your_client_id
LUMEN_API_KEY=your_api_key

# Option C — Client ID + client secret
LUMEN_CLIENT_ID=your_client_id
LUMEN_CLIENT_SECRET=your_client_secret
```

Also set:

```
LUMEN_CUSTOMER_NUMBER=your_customer_number
LUMEN_BILLING_ACCOUNT_ID=5-XXXXXXXX
```

And if using Teams notifications:

```
TEAMS_WEBHOOK_URL=https://your-org.webhook.office.com/webhookb2/...
```

Save and close (`Ctrl+O`, `Enter`, `Ctrl+X` in nano).

---

## Step 6 — Find and set your service ID

The `service_id` in `config.json` must be set to your real Lumen service ID before the scheduler can make any changes.

Query the API to list your available services:

```bash
python3 ./lumen_scheduler.py --config ./config.json list-services
```

Example output:
```
Found 1 Internet On-Demand service(s):

  service_id: 77133831778  status: Active  bandwidth: 500 Mbps

Set lumen_iod.service_id in config.json to one of the above.
```

Open `config.json` and update the `service_id` field:

```bash
nano config.json
```

Find the line:
```json
"service_id": "YOUR_SERVICE_ID",
```

Replace `YOUR_SERVICE_ID` with your real service ID, save, and close.

---

## Step 7 — Review your schedule

While `config.json` is open, check the `rules` and `profiles` sections match your office hours and desired bandwidth levels. The defaults are:

- **Peak** (`500 Mbps`): Mon–Thu 6:30 AM – 5:30 PM, Fri 8:00 AM – 5:00 PM
- **Off-peak** (`50 Mbps`): all other times

Adjust to fit your office. The `timezone` field (default `America/Los_Angeles`) controls all schedule evaluation.

---

## Step 8 — Validate

Check the scheduler can read your config and evaluate the current schedule:

```bash
python3 ./lumen_scheduler.py --config ./config.json status
```

Run a dry-run to confirm the full API workflow without making any changes:

```bash
python3 ./lumen_scheduler.py --config ./config.json run --dry-run --force
```

---

## Step 9 — Set up Teams notifications (optional)

If you set `TEAMS_WEBHOOK_URL` in `.env`, add the `notifications` block to `config.json`:

```json
"notifications": {
  "teams_webhook_url": "${TEAMS_WEBHOOK_URL:-}",
  "on_apply_failure": true,
  "on_pending_timeout": true,
  "on_recovery": false
}
```

To also receive a status card for every bandwidth change (started / successful / failed), set `debug_enabled` to `true` in the `dashboard` section of `config.json`:

```json
"dashboard": {
  "debug_enabled": true,
  ...
}
```

Test it without triggering a real bandwidth change:

```bash
python3 -c "
import lumen_scheduler as ls, os
from pathlib import Path
ls.load_dotenv(Path('.env'))
url = os.environ.get('TEAMS_WEBHOOK_URL', '')
config = ls.load_config(Path('config.json'))
ls.send_teams_notification(url, title='Lumen: test', message='Notifications working.', is_error=False)
print('Sent — check Teams.')
"
```

---

## Step 10 — Install the cron job

Set the scheduler to run every 5 minutes:

```bash
python3 ./lumen_scheduler.py \
  --config ./config.json \
  install-cron \
  --interval-minutes 5 \
  --log-file ./lumen-scheduler.log
```

Verify the cron entry was added:

```bash
crontab -l
```

The entry is wrapped in `# BEGIN LUMEN_BANDWIDTH_SCHEDULER` / `# END LUMEN_BANDWIDTH_SCHEDULER` tags so the installer can manage it cleanly in future.

> **Note:** Cron does not run while the Mac is asleep or powered off. Keep the machine awake during hours when bandwidth transitions need to occur.

---

## Step 11 — Launch the dashboard

```bash
./launch_web_page.sh
```

This starts the local web dashboard and opens it in your browser at `http://127.0.0.1:8787`.  
The dashboard shows live bandwidth status, the active schedule, pending changes, and quick override controls.

To keep the dashboard running after closing Terminal, launch it in the background:

```bash
nohup ./launch_web_page.sh &
```

To stop it:

```bash
./stop_web_page.sh
```

---

## Updating an existing install

1. Download the new release zip from GitHub Releases
2. Transfer it to the Mac (same methods as Step 2)
3. Extract it into the existing folder (overwriting `.py` and `.sh` files is safe — your `.env` and `config.json` are not included in the zip):
   ```bash
   unzip -o lumen-scheduler-macos-*.zip -d lumen-scheduler
   cd lumen-scheduler
   ```
4. Run the installer — it will detect the existing install and present the update menu:
   ```bash
   bash install_macos.sh
   ```
5. Follow any warnings about new config sections or env variables that need to be added manually.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| `Config not found` | Run `cp config.example.json config.json` |
| `auth failed` | Verify credentials in `.env`, check `LUMEN_CLIENT_ID` and secret are correct |
| `service_id` not found | Run `list-services` to see valid IDs |
| `JSONDecodeError` on startup | Open `config.json` and verify it is valid JSON (no trailing text outside the `{}`) |
| Dashboard won't start on port 8787 | The launcher auto-selects the next free port — check terminal output for the actual URL |
| Cron runs but no bandwidth change | Check `./lumen-scheduler.log` for `action=skip` — the scheduler only applies when a change is needed |
| Teams alerts not arriving | Verify `TEAMS_WEBHOOK_URL` in `.env` and `notifications` block in `config.json`; run the test snippet in Step 9 |
