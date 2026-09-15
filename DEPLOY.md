# Deploying the Nigoria bot on an Ubuntu VPS

Runs the bot as a systemd service: background, auto-restart on crash, starts on
boot.

## 1. System packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

## 2. Create a dedicated user and fetch the code

```bash
sudo useradd --system --create-home --home-dir /opt/nigoria --shell /usr/sbin/nologin nigoria
sudo -u nigoria git clone https://github.com/philnoob/nigoria.git /opt/nigoria
cd /opt/nigoria
sudo -u nigoria git checkout claude/zealous-meitner-wg2hsb
```

## 3. Virtualenv + dependencies

```bash
sudo -u nigoria python3 -m venv /opt/nigoria/.venv
sudo -u nigoria /opt/nigoria/.venv/bin/pip install --upgrade pip
sudo -u nigoria /opt/nigoria/.venv/bin/pip install -r /opt/nigoria/requirements.txt
```

## 4. Configuration

```bash
sudo -u nigoria cp /opt/nigoria/.env.example /opt/nigoria/.env
sudo -u nigoria nano /opt/nigoria/.env      # paste your DISCORD_TOKEN
sudo chmod 600 /opt/nigoria/.env            # keep the token private
sudo -u nigoria mkdir -p /opt/nigoria/data  # persists free-tier daily counts
```

The admin ID, Pro role ID and daily limit are already defaulted; set `GUILD_ID`
to your server ID for instant slash-command availability.

## 5. Install and start the service

```bash
sudo cp /opt/nigoria/deploy/nigoria-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nigoria-bot
```

## 6. Check it

```bash
systemctl status nigoria-bot          # should be "active (running)"
journalctl -u nigoria-bot -f          # live logs; look for "Logged in as ..."
```

## Updating after new commits

```bash
cd /opt/nigoria
sudo -u nigoria git pull
sudo -u nigoria /opt/nigoria/.venv/bin/pip install -r requirements.txt
sudo systemctl restart nigoria-bot
```

## Common issues

- **Slash commands don't appear:** global sync can take up to ~1h. Set
  `GUILD_ID` in `.env` and restart for instant per-server sync.
- **Bot can't see the Pro role / everyone looks Free:** enable the **Server
  Members Intent** in the Discord Developer Portal → Bot page.
- **Upload button never receives the file:** enable the **Message Content
  Intent** (Developer Portal → Bot page).
- **Whitelist doesn't assign the Pro role:** give the bot **Manage Roles** and
  drag its role **above** the Pro role in Server Settings → Roles.
- **`active (exited)` or restart loop:** run `journalctl -u nigoria-bot -e`.
  A missing token gives "Set the DISCORD_TOKEN environment variable".
- **Firewall:** none needed. The bot makes only outbound connections to
  Discord; no inbound ports to open.
