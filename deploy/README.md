# Putting LeadSage online

The website and the web app run on a small free server (Oracle Cloud Always Free, Ubuntu 24.04) behind Caddy, which gets the HTTPS certificate on its own. The models stay on your laptop: the server reaches the laptop's Ollama over Tailscale, a free private network between your own devices. When the laptop is off, the app still shows leads and drafts, and emails can still be written through Groq, but scoring and finding contacts wait for the laptop.

Each step says what it does, how to check it worked, and how to undo it. About an hour the first time.

## 1. The server

- **Do:** in Oracle Cloud, create an instance: image **Ubuntu 24.04**, shape **VM.Standard.A1.Flex** (2 OCPUs, 12 GB is plenty). Add your SSH key.
- **Open the web ports in Oracle's own firewall:** the instance's subnet, Security List, add two Ingress rules: source `0.0.0.0/0`, TCP, ports `80` and `443`. Nothing else.
- **Check:** `ssh ubuntu@<server address>` logs you in.
- **Undo:** terminate the instance.

## 2. A name for it

Caddy needs a domain name to get a certificate.

- **Free:** at duckdns.org, sign in, create a subdomain (say `leadsage`), and point it at the server's public address. `app.leadsage.duckdns.org` then works too.
- **Check:** `nslookup leadsage.duckdns.org` and `nslookup app.leadsage.duckdns.org` both give the server's address.

## 3. Tailscale on the laptop and the server

- **Do:** install Tailscale on the laptop (tailscale.com/download) and sign in. On the server: `curl -fsSL https://tailscale.com/install.sh | sh` then `sudo tailscale up`, and sign in with the same account.
- **Find the laptop's Tailscale address:** `tailscale ip -4` on the laptop. It starts with `100.`.
- **Lock it down (recommended):** in the Tailscale admin page, Access controls, allow only the server to reach the laptop on port 11434:
  ```json
  {"acls": [{"action": "accept", "src": ["<server tailscale address>"], "dst": ["<laptop tailscale address>:11434"]},
            {"action": "accept", "src": ["<laptop tailscale address>"], "dst": ["*:*"]}]}
  ```
- **Check:** on the server, `tailscale ping <laptop tailscale address>` answers.
- **Undo:** `sudo tailscale down`, or remove the device in the admin page.

## 4. Let the laptop's Ollama answer the server (Windows)

Ollama has no password, so two locks keep it private: it is only reachable over Tailscale, and the Windows firewall only lets Tailscale addresses in.

- **Do (PowerShell as administrator on the laptop):**
  ```powershell
  [Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "User")
  New-NetFirewallRule -DisplayName "Ollama from Tailscale only" -Direction Inbound -Protocol TCP -LocalPort 11434 -RemoteAddress 100.64.0.0/10 -Action Allow
  Get-NetFirewallRule | Where-Object DisplayName -like "*ollama*" | Select-Object DisplayName, Enabled, Action
  ```
  Then quit Ollama from the tray and start it again.
- **Check the list:** only "Ollama from Tailscale only" should allow. If Windows created an older "ollama" rule that allows any address, disable it: `Disable-NetFirewallRule -DisplayName "<its name>"`.
- **Check from the server (after step 5):** `/opt/leadsage/venv/bin/python /opt/leadsage/app/deploy/check_ollama.py` prints `OK`.
- **Undo:** `[Environment]::SetEnvironmentVariable("OLLAMA_HOST", $null, "User")`, `Remove-NetFirewallRule -DisplayName "Ollama from Tailscale only"`, restart Ollama.

## 5. Install the app on the server

```bash
sudo git clone https://github.com/Sp-dev5/LeadSage /opt/leadsage/app
sudo bash /opt/leadsage/app/deploy/setup.sh leadsage.duckdns.org
```

`setup.sh` installs Python and Caddy, makes a `leadsage` user that can't log in, installs the Python packages, copies the website (pointing "Open the app" at `https://app.<your domain>/`), writes Caddy's settings, installs the service and opens ports 80 and 443 in the server's own firewall. It is safe to run again.

Then write the settings file. It never leaves the server and is never in git:

```bash
sudo nano /opt/leadsage/app/.env
```

```
LEADSAGE_PASSWORD=<a long password your team will use>
LEADSAGE_SECRET=<run: python3 -c "import secrets; print(secrets.token_hex(32))">
OLLAMA_HOST=http://<laptop tailscale address>:11434
GROQ_API_KEY=<optional, for email drafts>
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=<your address>
SMTP_PASSWORD=<an app password, not your normal password>
IMAP_HOST=imap.gmail.com
```

The SMTP and IMAP lines are optional: without them, the app works as before and you send emails yourself. For Gmail, turn on 2-step verification, then make an app password at myaccount.google.com/apppasswords.

Then run `setup.sh` again (it starts the app once `.env` exists), or:

```bash
sudo systemctl enable --now leadsage && sudo systemctl reload caddy
```

- **Check:** `systemctl status leadsage` says active (running). Open `https://leadsage.duckdns.org` (the website) and `https://app.leadsage.duckdns.org` (the login page). The padlock shows.
- **Logs:** `journalctl -u leadsage -n 50`.
- **Undo:** `sudo systemctl disable --now leadsage`, `sudo rm /etc/systemd/system/leadsage.service /etc/caddy/Caddyfile`, `sudo rm -r /opt/leadsage /var/www/leadsage-site`.

## 6. Your existing leads (optional)

The server starts with an empty lead store. To bring the laptop's leads, copy the store once (from the laptop):

```bash
scp data/leadsage.db ubuntu@<server address>:/tmp/
ssh ubuntu@<server address> "sudo install -o leadsage -g leadsage -m 600 /tmp/leadsage.db /opt/leadsage/app/data/ && rm /tmp/leadsage.db && sudo systemctl restart leadsage"
```

Copy `data/icp.json` and `data/outreach.json` the same way if you changed them. From then on the server's copy is the one in use.

## 7. Updating

```bash
cd /opt/leadsage/app && sudo -u leadsage git pull && sudo bash deploy/setup.sh leadsage.duckdns.org
```

## What is protected, and what is not

- HTTPS everywhere, with HSTS; the session cookie is sent over HTTPS only; the shared password locks an address for 15 minutes after 5 wrong tries (the app sees each visitor's real address through Caddy).
- The app listens on 127.0.0.1 only; Caddy is the only way in. The service runs as a user with no login, and can write only to `data/`.
- Ollama on the laptop is reachable only from your Tailscale devices.
- Not done: backups. Copy `data/leadsage.db` off the server now and then.
