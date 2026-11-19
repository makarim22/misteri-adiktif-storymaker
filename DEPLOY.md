# Deploying Story Studio on a free Google Cloud VM

A step-by-step runbook for one small Linux VM that runs the app behind HTTPS. **You run these commands; nothing
here has been executed on a real server.** What was and was not checked is listed in section 0.

## 0. What is verified and what is not

Verified on the development machine (automated tests):
- Session cookie gets `Secure` over HTTPS (when the proxy says so), security headers and a Content-Security-Policy
  are sent, and the real UI loads under that policy with zero violations.
- Login is rate limited (10 failures per IP, 20 per username, per 15 minutes); the first admin can require a
  setup token; `X-Forwarded-For` is only believed from a trusted proxy.
- `backup.py` makes a consistent copy while the server is writing, rotates old copies, and a backup restores and
  logs in.

The systemd unit passes `systemd-analyze verify` (syntax only; it was not started under a real sandbox).

Checked against official docs only (not run): Caddy install and reverse-proxy steps, Google's free-tier limits.

Not verified: the Google Cloud console screens, the unit's sandbox settings on a real VM, Caddy obtaining a certificate,
how the free VM copes with several simultaneous AI calls, and the first real call to the Anthropic API.

Placeholders you fill in: `[DOMAIN]`, `[SETUP-TOKEN]`, `[ANTHROPIC-KEY]`, `[YOUR-USER]` (your SSH user on the VM)
and `[VM-IP]`. Never paste the Anthropic key into chat or into the repo.

## 1. Google Cloud account and VM

Free-tier facts from Google's documentation (re-check them, Google can change them with 30 days' notice):
- One non-preemptible `e2-micro` VM per month is free, in `us-west1` (Oregon), `us-central1` (Iowa) or
  `us-east1` (South Carolina). Latency from Indonesia will be higher than a nearby region.
- 30 GB-months of **standard** persistent disk and 1 GB of outbound traffic per month (North America
  destinations) are included. Choose the **Standard persistent disk** type, not Balanced or SSD.
- A card is required. If you stay on the *Free Trial* billing account, resources are stopped after 90 days or
  when the $300 credit is gone, then deleted after a 30-day grace period. Upgrade to a **Paid billing account**
  (you keep the Free Tier) to avoid that.

Steps in the Google Cloud console (screens can differ):
1. Create a project and attach a billing account. Upgrade from Free Trial to Paid if prompted.
2. **Set a budget alert first** (Billing > Budgets & alerts), for example $5 with email alerts at 50% and 100%.
   An alert warns you; it does not stop charges.
3. Compute Engine > create VM: machine type `e2-micro`, a free-tier US region, boot disk **Ubuntu 24.04 LTS**
   (the app is tested on Python 3.12, which Ubuntu 24.04 ships), disk type Standard, size 10-30 GB.
4. Allow HTTP and HTTPS traffic in the VM's firewall options. Do not open any other port. SSH uses keys by
   default on Google's images (confirm in the console).
5. Reserve a **static external IP** and attach it, so DNS keeps working after a restart. Check the current price
   of external IPs before you do; I did not verify it.
6. Buy a domain, then add an **A record** `[DOMAIN]` pointing at that IP. Wait until it resolves
   (`nslookup [DOMAIN]`) before section 5, otherwise HTTPS certificate issuance fails.

Connect with the console's SSH button or `gcloud compute ssh`. All commands below run on the VM.

## 2. Install the app

```bash
sudo apt update && sudo apt install -y git
sudo useradd --system --create-home --home-dir /opt/story-studio --shell /usr/sbin/nologin storystudio
sudo -u storystudio git clone https://github.com/makarim22/misteri-adiktif-storymaker.git /opt/story-studio/app
sudo install -d -o storystudio -g storystudio -m 700 /var/lib/story-studio /var/backups/story-studio
python3 --version        # expect 3.12.x
free -m                  # note the memory you actually have
```

The repository is public, so no GitHub login is needed to clone it.

## 3. Configuration (secrets live here, not in the repo or database)

```bash
sudo install -m 600 -o root -g root /dev/null /etc/story-studio.env
sudoedit /etc/story-studio.env
```

Contents:

```
STUDIO_HOST=127.0.0.1
STUDIO_PORT=8000
STUDIO_DB=/var/lib/story-studio/studio.db
STUDIO_SETUP_TOKEN=[SETUP-TOKEN]
ANTHROPIC_API_KEY=[ANTHROPIC-KEY]
```

- Generate the token with `python3 -c "import secrets;print(secrets.token_urlsafe(24))"`. It only matters while
  there are no users; if you copy in your existing database (section 6) it is not used.
- Listening on `127.0.0.1` means only Caddy (on the same VM) can reach the app.
- Create the Anthropic key in the console for this app only, and set a spending limit there. Keep it only in
  this file. The app also lets an admin save a key in its database; prefer the environment file.

## 4. Run it as a service

```bash
sudoedit /etc/systemd/system/story-studio.service
```

```
[Unit]
Description=Story Studio
After=network.target

[Service]
User=storystudio
Group=storystudio
WorkingDirectory=/opt/story-studio/app
EnvironmentFile=/etc/story-studio.env
Environment=PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /opt/story-studio/app/server.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/story-studio
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now story-studio
systemctl status story-studio --no-pager
curl -s http://127.0.0.1:8000/api/state       # should print JSON
journalctl -u story-studio -n 30 --no-pager   # failed logins and server errors appear here
```

`enable` makes it start after a reboot; `Restart=on-failure` restarts it after a crash.

## 5. HTTPS with Caddy

Install (official Debian/Ubuntu package, from Caddy's docs):

```bash
sudo apt install --yes debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

The package runs Caddy as a systemd service named `caddy`. Put this in its config file (the packaged default is
`/etc/caddy/Caddyfile`; confirm with `systemctl status caddy`):

```
[DOMAIN] {
    reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo systemctl reload caddy
```

Per Caddy's docs, a real domain name gets a publicly trusted certificate automatically, as long as DNS points at
the VM and ports 80 and 443 are reachable. Caddy also adds `X-Forwarded-For` and `X-Forwarded-Proto` to proxied
requests (my understanding; section 8 checks it): the app uses them for the real client IP and the `Secure`
cookie flag, and ignores them from anyone who is not the local proxy.

## 6. Move your existing data

On your own computer (not the VM), with the local server still running is fine:

```bash
cd /home/jaka/story-studio && python3 backup.py --dir /tmp/move --keep 1
```

Never copy `studio.db` with `cp` while the server runs; the backup command makes a consistent file. Then:

```bash
scp /tmp/move/studio-*.db [YOUR-USER]@[VM-IP]:/tmp/studio.db      # or: gcloud compute scp
```

On the VM:

```bash
sudo systemctl stop story-studio
sudo install -m 600 -o storystudio -g storystudio /tmp/studio.db /var/lib/story-studio/studio.db
sudo rm -f /tmp/studio.db /var/lib/story-studio/studio.db-wal /var/lib/story-studio/studio.db-shm
sudo apt install -y sqlite3
# force everyone to sign in again, and check no API key is stored in the database:
sudo -u storystudio sqlite3 /var/lib/story-studio/studio.db "DELETE FROM sessions; SELECT key FROM settings WHERE key LIKE 'key\_%' ESCAPE '\';"
sudo systemctl start story-studio
```

If the last query lists `key_anthropic`, an API key is stored in the database. Remove it with
`DELETE FROM settings WHERE key LIKE 'key\_%' ESCAPE '\';` and use the environment file instead. Delete
`/tmp/move` on your computer afterwards. Existing user accounts and passwords come across unchanged.

## 7. Backups

Daily backup on the VM (keeps the newest 14):

```bash
sudo tee /etc/cron.d/story-studio-backup >/dev/null <<'EOF'
15 3 * * * storystudio STUDIO_DB=/var/lib/story-studio/studio.db /usr/bin/python3 /opt/story-studio/app/backup.py --dir /var/backups/story-studio --keep 14
EOF
sudo -u storystudio env STUDIO_DB=/var/lib/story-studio/studio.db python3 /opt/story-studio/app/backup.py --dir /var/backups/story-studio --keep 14   # run once now
```

A backup on the same disk does not protect you if the VM is lost. Also pull a copy to your own computer, for
example weekly:

```bash
scp "[YOUR-USER]@[VM-IP]:/var/backups/story-studio/studio-*.db" ./story-studio-backups/
```

(That needs read access to the folder; either run it as a user in the `storystudio` group or copy with `sudo`
first.) Back up your `/etc/story-studio.env` contents somewhere private too; it is not in any backup.

**Restore test (do this once, before you rely on it):**

```bash
sudo systemctl stop story-studio
sudo cp /var/backups/story-studio/studio-YYYYmmdd-HHMMSS.db /var/lib/story-studio/studio.db
sudo chown storystudio:storystudio /var/lib/story-studio/studio.db && sudo chmod 600 /var/lib/story-studio/studio.db
sudo rm -f /var/lib/story-studio/studio.db-wal /var/lib/story-studio/studio.db-shm
sudo systemctl start story-studio
```

## 8. Acceptance checks before you tell friends

- [ ] `https://[DOMAIN]` loads and shows the sign-in page; `http://[DOMAIN]` redirects to HTTPS.
- [ ] After signing in, the browser's developer tools show the `sid` cookie as `Secure` and `HttpOnly`.
- [ ] Enter a wrong password a few times, then `journalctl -u story-studio -n 20` should show
      `login: failed ... from <your real IP>` (not `127.0.0.1`). If it shows `127.0.0.1`, the proxy headers are
      not arriving and the rate limit would block everyone together; stop and fix this first.
- [ ] After 10 wrong attempts in 15 minutes the sign-in is refused with "Terlalu banyak percobaan".
- [ ] `sudo reboot`, wait, reload the site: the app is back without manual steps.
- [ ] The restore test in section 7 worked.
- [ ] Run one cheap task (for example titles on Haiku) and compare the cost shown in the app with the Anthropic
      console. This is the first real API call; estimates may differ.
- [ ] Fill in `[ALAMAT-APLIKASI]` in the user guide once it exists.

## 9. Updating the app

```bash
sudo -u storystudio env STUDIO_DB=/var/lib/story-studio/studio.db python3 /opt/story-studio/app/backup.py --dir /var/backups/story-studio --keep 14
cd /opt/story-studio/app && sudo -u storystudio git pull
sudo systemctl restart story-studio
```

The database upgrades itself on start. If a version misbehaves: `git log --oneline`, `git checkout <older-commit>`,
restart; restore the backup if data was affected. Ubuntu normally applies security updates automatically
(`unattended-upgrades`); confirm with `systemctl status unattended-upgrades`.

## 10. Limits to know

- One small VM: no failover. If it goes down, the app is down until you restart it.
- One CPU core and little memory: several long AI generations at once may feel slow. Check `free -m` and
  `journalctl` if it does.
- Free-tier terms can change; keep the budget alert.
- The app still has no CSRF token (the `SameSite=Lax` cookie and JSON-only API are its protection) and no
  per-user permissions beyond admin/member. API keys saved through the app are stored unencrypted in the database.
- You are responsible for the server: updates, disk space, and renewing nothing by hand (Caddy renews
  certificates itself), but watching the budget alert and the logs.
