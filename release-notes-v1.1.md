## What's new

### Microsoft Teams notifications
- Alerts on bandwidth change failure and pending timeout
- Debug mode sends a status card for every change lifecycle event (started / successful / failed)
- Webhook URL configured via `.env` — no credentials in config files
- Notification failures never crash the scheduler

### `list-services` command
- Query your Lumen account for available service IDs before configuring
- Installer runs this automatically if credentials are present and `service_id` is still a placeholder

### Self-extracting installer
- `create_portable_zip.sh` now produces a single `.sh` file
- Transfer one file to the target Mac and run: `bash lumen-scheduler-macos-*.sh`
- No separate unzip step required

### Interactive installer (`install_macos.sh`)
- Detects fresh install vs. existing install
- Update menu: update scripts only / full reinstall (with backup option) / cancel
- Stops and restarts the dashboard if it was running
- Warns about new config sections or `.env` variables added since last install

### Bug fix: inventory `serviceType` HTTP 500
- Lumen's inventory API only accepts `serviceType=Internet` or `Port`
- The scheduler was sending `Internet On-Demand`, causing a 500 on the fallback lookup each cycle

## New files
- `INSTALL.md` — step-by-step setup guide for first installs
- `test_installer.sh` — 31 integration tests for the packaging and installer

## Upgrading from v1.0.x

Run the new installer against your existing directory:

```bash
bash lumen-scheduler-macos-*.sh ~/path/to/lumen-scheduler
```

Select **Option 1** (update scripts, keep config). Then add to your `config.json` if missing:

```json
"notifications": {
  "teams_webhook_url": "${TEAMS_WEBHOOK_URL:-}",
  "on_apply_failure": true,
  "on_pending_timeout": true,
  "on_recovery": false
}
```

Add to `.env` if missing:

```
TEAMS_WEBHOOK_URL=
```

Update `product_name` in `config.json` from `"Internet On-Demand"` to `"Internet"`.
