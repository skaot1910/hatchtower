<div align="center">

<img src="app/hatchtower/static/favicon.svg" width="88" alt="Hatchtower logo">

# Hatchtower

**English** · [Deutsch](README.de.md)

**Give your friends a simple, safe page for their game servers –
without putting your panel on the internet.**

Hatchtower sits on top of Pterodactyl, Pelican or Calagopus. Your friends see whether their
server is up, start it, restart it or change a config file. Your panel stays in your home network.

![Pterodactyl](https://img.shields.io/badge/Pterodactyl-1.15-0ea5e9)
![Pelican](https://img.shields.io/badge/Pelican-1.0-2563eb)
![Calagopus](https://img.shields.io/badge/Calagopus-1.2-f97316)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ed)
![Languages](https://img.shields.io/badge/Languages-EN%20%7C%20DE-22c55e)
![License](https://img.shields.io/badge/License-AGPL--3.0-8b5cf6)
![Built with Claude](https://img.shields.io/badge/Built%20with-Claude-d97757)

<table>
  <tr>
    <td width="32%"><img src="docs/screenshots/simple-mobile.gif" alt="A friend restarts a server on the phone"></td>
    <td width="68%"><img src="docs/screenshots/simple.png" alt="Simple view: status, address, password, start and stop"><br>
      <sub>This is what your friends see: the <b>simple view</b>. Status, address, password and big
      buttons – nothing else. You (the owner) get everything else: charts, console, files, backups,
      automations, permissions.</sub></td>
  </tr>
</table>

</div>

---

## Why not just use subusers?

If your friends are happy with the panel and can reach it, **subusers are enough** – you don't
need Hatchtower. It was made for the cases where that doesn't work:

| | Panel with subusers | Hatchtower |
|---|---|---|
| **Panel stays private** – only Hatchtower is reachable from outside | ✗ friends need access to the panel | ✓ |
| **Works behind Pangolin / a reverse proxy** without fiddling with websockets, uploads and Wings ports | often painful | ✓ one plain HTTPS app, console and files are proxied through it |
| **Simple view** for friends who just want to play | ✗ full panel interface | ✓ |
| **Groups across many servers** ("Player", "Moderator", "all servers, including future ones") | per server and user | ✓ |
| **Invite links** you can drop into Discord – no mail server, no passwords sent around | ✗ | ✓ |
| **Discord**: live server status in your channels, crash and disk alerts | ✗ | ✓ |
| **Restart after a crash** even when Wings gives up, scheduled restarts with in-game warning | partly | ✓ |
| **Switch panels** (Pterodactyl → Pelican or Calagopus) and keep users, permissions and schedules | ✗ start over | ✓ |

**How it started:** I host game servers for my friends at home. Putting the panel behind
Pangolin never worked properly – the page loaded, but buttons did nothing and file transfers
failed. Hatchtower talks to the panel from inside the network and is the only thing friends
reach from outside. See [Reverse proxy and Pangolin](docs/reverse-proxy.md).

<sub>🤖 Hatchtower was built together with <a href="https://claude.ai">Claude</a> (Anthropic) –
without it, this project would not have been possible in this form. The code is open
(AGPL-3.0), every route is covered by an automated permission test (`tests/`, 800+ tests) and
read-only mode is enforced in code, not just in the interface.</sub>

---

## What is Hatchtower?

Hatchtower **complements** your panel, it doesn't replace it. Your servers keep running in the
panel; Hatchtower sits on top and adds:

- 🙂 a **simple view** for friends and an **everything view** for you
- 👥 **its own users** with exactly the permissions you give them – per server
- 📈 **live monitoring** with CPU, RAM and network history
- ⏰ **automations** such as restart after a crash or nightly restarts
- 💾 **backups** of whole servers or just the save games – to a drive, an SMB share or straight to your browser
- 📜 an **audit log** that records who did what and when
- 🔒 a **read-only mode** that guarantees nothing is changed on the panel

### Supported panels

| Panel | Tested with | Connection |
|---|---|---|
| [Pterodactyl](https://pterodactyl.io) | Panel 1.15.1, Wings 1.13.3 | Application key (read only) and client key |
| [Pelican](https://pelican.dev) | Panel and Wings 1.0.0-rc.1 | Application key (read only) and client key |
| [Calagopus](https://calagopus.com) | Panel and Wings 1.2.5 (built-in or separate Wings) | one API key with admin read and server permissions |

One installation manages **one** panel. You pick it during setup and can switch later under
**System → Panel**.

> **Up and running in 5 minutes:** one command installs everything, then a wizard in your
> browser walks you through the setup.

---

## A look inside

### All servers at a glance

List or tiles, filter by status and your own tags, favorites on top. Start, stop or restart
several servers at once.

<img src="docs/screenshots/dashboard-tiles.png" alt="Tile view" width="100%">

### Every server in detail

Status, address, uptime over the last 30 days, history of up to 72 hours and the
**live console** with search and download – in tabs, so nothing is buried at the bottom of a long page.

<img src="docs/screenshots/server.png" alt="Server detail page with history and live console" width="100%">

### Files right in the browser

View, upload, download (also as ZIP), rename, delete and edit. When you save, the previous
version is kept as `.bak` and can be restored with one click.

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/files.png" alt="File manager"></td>
    <td width="50%"><img src="docs/screenshots/editor.png" alt="Editing a file"></td>
  </tr>
</table>

### Users and permissions – as fine-grained as you like

Create groups like "Moderator" or "Player" and assign them per server. Someone who should only
see whether the server is running sees exactly that. New people join via an **invite link**,
no passwords to send around.

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/users.png" alt="User management"></td>
    <td width="50%"><img src="docs/screenshots/groups.png" alt="Permission groups"></td>
  </tr>
</table>

### Automations

Restart after a crash and schedules (weekly or one-off) to start, stop and restart – with an
in-game warning such as "Restart in 15 minutes!".

<img src="docs/screenshots/automation.png" alt="Automations and schedules" width="100%">

### Backups

Back up whole servers or just the save games – simply tick files in the file manager. Target:
a drive, an SMB share or a download in the browser. Optionally the server is stopped first and
started again afterwards.

### Audit log

Every action is recorded and cannot be changed afterwards. Filter by action, user and server,
export as CSV or TXT.

<img src="docs/screenshots/audit.png" alt="Audit log" width="100%">

### Public status page, mobile and login

A status page for your players (can be turned off, display only). On phones Hatchtower adapts
automatically.

<table>
  <tr>
    <td width="62%"><img src="docs/screenshots/status.png" alt="Public status page"><br><br>
                    <img src="docs/screenshots/login.png" alt="Login"></td>
    <td width="38%"><img src="docs/screenshots/mobile.png" alt="Mobile view"></td>
  </tr>
</table>

<sub>All screenshots come from a demo instance with made-up servers and users.</sub>

---

## All features

<details>
<summary><b>Show the full list</b></summary>

**Servers**
- Simple view (status, address, password, start/stop) or everything – per user, default per instance, can be locked by the owner
- Overview as list or tiles, status filter, sorting, live values (CPU relative to the limit, RAM, storage)
- Start, stop, restart, kill (owner only), console commands
- Start, stop or restart several servers at once
- Live console and CPU, RAM and network history (live up to 72 hours)
- Favorites and your own tags
- Availability: uptime and crashes over the last 30 days
- Log archive: the console of running servers is recorded (1–90 days or off), searchable, errors highlighted, download as .txt (separate permission "View console archive")

**Files**
- View, download (several as ZIP), upload, edit, rename, delete
- Automatic `.bak` copy when editing, restore with one click

**Users and security**
- Own users and per-server permissions, custom permission groups
- Invite links instead of initial passwords: valid 1–90 days or unlimited, single to unlimited use, with an internal description; use and expiry are recorded in the audit log
- Optional email: own address per user with confirmation, "forgot password", invites by email
- Audit log that cannot be altered
- Read-only mode: Hatchtower can run entirely read-only

**Automations and notifications**
- Restart after a crash
- Weekly and one-off schedules with warning
- Notifications via Discord/webhook and email: crash, restart, restart failed,
  storage almost full, RAM constantly high, schedule failed

**Backups**
- Backup jobs for one, several or all servers (including future ones), whole servers or selected files and folders, with exclusions (e.g. `logs/`, `*.log`)
- Add files and folders to a backup job right from the file manager
- Targets: a drive in the Hatchtower container (e.g. Proxmox mount point, USB, NFS), an SMB share (Windows, NAS, Samba) or direct download in the browser
- Manual or scheduled, with progress display; running backups can be cancelled
- Stop the server first (with in-game warning) and start it again afterwards
- Retention by count or days, free-space check beforehand, report via webhook/email
- Separate permissions: view, run and download backups, manage backup jobs
- Respects the panel's request limit so status updates don't drop out

**Community**
- Public status page (can be turned off): name, game, status and address of selected servers
- Server status in Discord, also in several channels – each with its own server selection and heading
- Announcements at the top of every page, with scheduled start and end

**Look and language**
- 3 themes (dark, medium, light), saved per user
- Your own name and logos (e.g. your community's)
- English and German, automatically by browser language or via a switch
- Optimized for phones and tablets

**Privacy and migration**
- Configurable retention (audit log 30 days by default)
- Legal notice and privacy policy with templates, data export per user
- Migration: export all users, permissions, logos and settings encrypted and import them into a new instance

</details>

---

## Requirements

| | |
|---|---|
| **System** | Debian 12 or 13 (tested, ideally as a Proxmox LXC); Ubuntu should work too – **or Docker** with Compose |
| **Resources** | 1 CPU core, 512 MB RAM, 2 GB storage |
| **Network** | Access to the panel **and** the Wings nodes (usually ports `8080` and `2022`; with the Calagopus Wings proxy the panel is enough) |
| **Panel** | Admin access to create the API keys (see [Supported panels](#supported-panels)) |

---

## Installation

Pick **one** of the four ways. Run all commands as **root** (🅳: a user allowed to run Docker).

> 🔒 **While the repository is private**, the commands for 🅰 and 🅱 won't work directly.
> Use the [installation with access token](#installation-with-access-token) further down instead.

### 🅰 Proxmox VE: new container with one command

In the shell of the **Proxmox host** (not inside a container):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/proxmox/install.sh)"
```

The script asks for container ID, network, port etc. (**Enter** accepts the suggestion),
creates a Debian container and installs Hatchtower in it. The prompts are in German.

<details>
<summary>Install without prompts</summary>

```bash
HT_YES=1 HT_CTID=120 HT_IP=192.168.1.50/24 HT_GW=192.168.1.1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/proxmox/install.sh)"
```

All variables are listed at the top of `proxmox/install.sh`.
</details>

### 🅱 Existing server or container

In the shell of the server that should run Hatchtower:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/installer/get.sh)"
```

### 🅲 From a package (no internet access to GitHub)

Download `hatchtower-<version>.tar.gz` from **Releases**, copy it to the server, then:

```bash
tar -xzf hatchtower-<version>.tar.gz
cd hatchtower-<version>
bash installer/install.sh
```

<details>
<summary>Options for 🅱 and 🅲</summary>

| Option | Meaning |
|---|---|
| `--port 8000` | Port of the web interface |
| `--bind 0.0.0.0` | Address Hatchtower listens on |
| `--allow "192.168.1.5,192.168.1.10"` | Open the port only for these IPs (own nftables table, your firewall stays untouched) |

For 🅱 append them like this: `bash -c "$(curl …)" _ --allow "192.168.1.5"`
</details>

### 🅳 Docker Compose

In the folder with the source (Git checkout or unpacked release package):

```bash
docker compose up -d --build
```

```bash
docker compose logs hatchtower
```

The log shows the **setup code**. Then open `http://<server>:8000/setup`.

- Data (database, logos, keys) lives in the volume `hatchtower-data`. **Back up the whole volume** –
  the file `keys.env` in it is needed to decrypt the stored API keys.
- Behind a reverse proxy on the same host, publish the port only locally: `"127.0.0.1:8000:8000"`.
  Under **System → Network** enter the proxy IP; with Docker that's usually the bridge gateway
  (e.g. `172.17.0.1`) or the network `172.16.0.0/12`.
- Backup targets: mount a folder into the container under `/mnt/…` (see the comment in `compose.yaml`).
- Commands: `docker compose exec hatchtower hatchtower reset-password` (also `selftest`, `version`, …).
- **Update:** get the new version, then `docker compose up -d --build`. The update button in the
  interface is only available for the classic installation.

### Installation with access token

While the repository is private, you need a **read token** from the project. The commands here
**replace** the command from 🅰 or 🅱. First enter the token (it won't be shown):

```bash
read -rsp "GitHub token: " GITHUB_TOKEN && export GITHUB_TOKEN HATCHTOWER_SAVE_TOKEN=1 && echo
```

Then **for 🅰** (shell of the Proxmox host):

```bash
bash -c "$(curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github.raw" https://api.github.com/repos/skaot1910/hatchtower/contents/proxmox/install.sh)"
```

**or for 🅱** (shell of the server):

```bash
bash -c "$(curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github.raw" https://api.github.com/repos/skaot1910/hatchtower/contents/installer/get.sh)"
```

So that later updates work, the token is stored on the Hatchtower server (with 🅰 the script asks
first, **Enter** = yes). For 🅲 or when it has expired: `hatchtower github-token`.

<details>
<summary>Create a token (for the repository owner)</summary>

GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens →
Generate new token**. Repository: only `hatchtower`, **Contents** "Read-only", set an expiry date.
</details>

---

## Setup in the browser

At the end of the installation you'll see the **address** and a **setup code**. Open the address
in your browser – a wizard guides you through five steps:

1. Create the **owner account** (with the setup code)
2. Choose the **panel** (Pterodactyl, Pelican or Calagopus), enter **address** and **API key** –
   the wizard shows for each panel where to get the key and which permissions it needs
3. Enter the **client API key** of a dedicated panel user (with *Allowed IP*; not needed for Calagopus)
4. Check the **Wings nodes** – read automatically from the panel, several are no problem
5. Choose **options**: mode, public address for players, reverse proxy, time zone

Done! 🎉

> 💡 New installations start in **Read only** mode to be safe. You can change that any time
> under **System → Panel → Mode**.
>
> API keys are stored encrypted and never shown again.

**Your own look:** under **Presentation → Appearance** you set your community's name and logos.
Each user picks theme and language at the top right.

---

## Update

```bash
hatchtower update
```

(Docker: `docker compose up -d --build` after getting the new version.)

That's it. Hatchtower downloads the latest version, verifies the checksum, **backs up the
database first** and automatically rolls back if the new version doesn't start. Users,
permissions, logos, schedules and history are kept.

Under **System** Hatchtower shows when a new version is available. Nothing is ever installed
automatically – only when you want it.

---

## Commands on the server

| Command | What it does |
|---|---|
| `hatchtower update` | Install the latest version |
| `hatchtower update --check` | Only check whether an update is available |
| `hatchtower update <package>` | Install an existing package |
| `hatchtower selftest` | Check the connection to panel and Wings (read only) |
| `hatchtower version` | Show the installed version |
| `hatchtower reset-password` | Reset a user's password and unlock the account |
| `hatchtower create-owner` | Create an owner account on the console |
| `hatchtower setup-token` | Generate a new setup code (only while no account exists) |
| `hatchtower github-token` | Store a read token for updates while the repository is private (`--remove` deletes it) |
| `journalctl -u hatchtower -f` | Follow the service log live |

<details>
<summary>Where is what?</summary>

| Path | Contents |
|---|---|
| `/opt/hatchtower` | Program, Python environment, installer, license text (`LICENSE`) |
| `/etc/hatchtower/hatchtower.env` | internal keys and port (do not share!) |
| `/var/lib/hatchtower/hatchtower.db` | database (SQLite) incl. encrypted API keys |
| `/var/backups/hatchtower/` | daily backup at 03:30 (14 days) and backups before updates |
| `/etc/hatchtower/github-token` | optional: read token for updates while the repository is private |

</details>

---

## Security

- 🔑 Strong passwords (Argon2id), lockout after failed attempts, session timeouts
- 🛡️ CSRF protection, strict Content Security Policy, no embedding in other sites
- 🚫 "Everything not allowed is forbidden" – permissions per user and server
- 👀 Read-only mode is enforced technically: write requests never reach the panel
- 🌐 For access from outside, please use a reverse proxy with HTTPS (e.g. Pangolin, Nginx, Traefik) – see [Reverse proxy and Pangolin](docs/reverse-proxy.md)

> ⚠️ The client key of a panel **admin** can technically do everything. Protect it with
> *Allowed IP*, read-only mode and the audit log. Even safer: create a dedicated panel user and
> add it only as a subuser to the servers you want.

<details>
<summary><b>Running on a public server (VPS, dedicated server)</b></summary>

Hatchtower also runs on a server on the internet. To keep it safe there:

1. **Listen on localhost only** if the reverse proxy runs on the same server:
   `bash installer/install.sh --bind 127.0.0.1`. Otherwise open port 8000 only for the proxy
   via firewall (or `--allow`). It must not be possible to bypass the proxy.
2. **HTTPS via a reverse proxy.** Hatchtower itself only speaks HTTP. Example with Caddy
   (gets the certificate automatically):

   ```
   hatchtower.example.com {
     reverse_proxy 127.0.0.1:8000
   }
   ```

3. **Finish the setup right away.** As long as no account exists, anyone with the setup code
   can create the owner. Don't share the code and complete the setup right after installing.
4. **Enter the proxy IP** under **System → Network → Reverse proxy IPs** (for Caddy on the same
   server: `127.0.0.1`). On the internet this is mandatory, not optional. Only then does Hatchtower
   - detect HTTPS and set cookies with `Secure` as well as HSTS,
   - see the real visitor IPs. Without it, everyone comes from the proxy IP, and an attacker
     with ten failed logins locks out all visitors.

   IP addresses and networks are allowed (e.g. `172.16.0.0/12` for Docker). `X-Forwarded-For`
   is only accepted from these addresses; forged headers from other clients don't count.
5. **Connection to panel and Wings:** Hatchtower must reach the panel and the Wings nodes
   (usually port `8080`). If they are not in the same data center, better connect them through a
   VPN tunnel (e.g. WireGuard) instead of exposing the Wings port to the internet.
   In the panel, set the client key's *Allowed IP* to Hatchtower's address.

</details>

---

## For developers

<details>
<summary>Tools and structure</summary>

| Tool | Purpose |
|---|---|
| `bash tools/release.sh` | Publish a new version: tag, package from Git, checksum, GitHub release (text from `CHANGELOG.md`) |
| `tools/third_party_licenses.py` | Generates `THIRD_PARTY_LICENSES.txt` during installation |
| `tests/` | Tests with pytest, incl. the permission matrix of all routes (`tests/routes.py` → `docs/rechte-matrix.md`) |

**Structure** (`app/hatchtower/`): `main.py` (web), `panels.py` (supported panels),
`ptero.py` (adapter for Pterodactyl and Pelican with read-only lock), `calagopus.py` (adapter for
Calagopus), `wings.py` (console), `monitor.py` (sync, history), `automation.py`,
`permissions.py`, `security.py`, `setup.py` (setup wizard), `themes.py`, `db.py` (migrations).

**Code, comments and commits are in German.** German UI text is the translation key;
translations live in `app/hatchtower/locales/<language>.json`. If a translation is missing,
the German text is shown.

</details>

---

## License

Hatchtower is free software under the **GNU Affero General Public License v3.0** (AGPL-3.0),
see [`LICENSE`](LICENSE). Copyright © 2026 skaot1910.

In short: you may use, modify and share it, also commercially. Modified versions must again be
licensed under the AGPL-3.0 – even if you only offer them over a network. No warranty.

All dependencies use free licenses (MIT, BSD, Apache-2.0, PSF, MPL-2.0). The list is in the
interface under **Licenses** (footer), the full texts in `THIRD_PARTY_LICENSES.txt` on the server.

<div align="center">
<sub>Hatchtower is an independent project and is not affiliated with Pterodactyl, Pelican or Calagopus.</sub>
</div>
