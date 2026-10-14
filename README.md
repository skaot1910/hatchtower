<div align="center">

<img src="app/hatchtower/static/favicon.svg" width="88" alt="Hatchtower logo">

# Hatchtower

**English** · [Deutsch](README.de.md)

**A simple, safe web page for your game servers – for you and your friends,
without putting your server panel on the internet.**

![Pterodactyl](https://img.shields.io/badge/Pterodactyl-1.15-0ea5e9)
![Pelican](https://img.shields.io/badge/Pelican-1.0-2563eb)
![Calagopus](https://img.shields.io/badge/Calagopus-1.2-f97316)
![Languages](https://img.shields.io/badge/Languages-EN%20%7C%20DE-22c55e)
![License](https://img.shields.io/badge/License-AGPL--3.0-8b5cf6)
![Built with Claude](https://img.shields.io/badge/Built%20with-Claude-d97757)

<img src="docs/screenshots/dashboard.png" alt="The Hatchtower overview with two game servers" width="100%">

</div>

---

## Contents

1. [What is Hatchtower?](#1-what-is-hatchtower)
2. [What can I do with it?](#2-what-can-i-do-with-it)
3. [Screenshots](#3-screenshots)
4. [What do I need before installing?](#4-what-do-i-need-before-installing)
5. [Installation](#5-installation)
6. [First-time setup](#6-first-time-setup)
7. [Connecting your panel (API keys explained)](#7-connecting-your-panel-api-keys-explained)
8. [Managing servers and users](#8-managing-servers-and-users)
9. [Files and permissions](#9-files-and-permissions)
10. [Backups and restoring data](#10-backups-and-restoring-data)
11. [Updating Hatchtower](#11-updating-hatchtower)
12. [Troubleshooting](#12-troubleshooting)
13. [Security recommendations](#13-security-recommendations)
14. [Frequently asked questions](#14-frequently-asked-questions)
15. [Getting help and reporting problems](#15-getting-help-and-reporting-problems)
16. [License and credits](#16-license-and-credits)

---

## 1. What is Hatchtower?

Many people run game servers (Minecraft, Valheim, Terraria, …) with a **server panel** such as
[Pterodactyl](https://pterodactyl.io), [Pelican](https://pelican.dev) or [Calagopus](https://calagopus.com).
The panel is powerful, but it is made for admins. Giving your friends access to it is often a bad idea:
they see far too much, and the panel would have to be reachable from the internet.

**Hatchtower is a small website that sits next to your panel.** It talks to the panel inside your home
network and shows each person only what you allow:

- Your friends get a **simple page**: is the server running, what is the address, what is the password,
  and big buttons to start or restart it.
- You (the **owner**) get everything else: console, files, backups, schedules, user permissions and a log
  of who did what.

Your panel keeps running exactly as before. Hatchtower does not replace it – it adds a friendly front door.

**Is it right for me?**

| Hatchtower is a good fit if … | You probably don't need it if … |
|---|---|
| you host game servers for friends or a small community | you are the only person who uses the panel |
| you don't want your panel reachable from the internet | your friends are happy using the panel's own subuser feature |
| you want friends to restart a server without asking you | you don't use Pterodactyl, Pelican or Calagopus |
| you want backups you can restore with a few clicks | |

> One Hatchtower installation connects to **one** panel. Several Wings nodes behind that panel are fine.

## 2. What can I do with it?

**For your friends (simple view)**
- See whether a server is online, its address and join password
- Start, stop or restart it – only if you allow it
- Works on phones

**For you as the owner**
- Overview of all servers with live CPU, RAM and disk usage, favourites and tags
- Server page with history charts, uptime over 30 days and a **live console**
- **File manager** in the browser: view, upload, download (also several files as a ZIP), edit, rename, delete
- **Users and permissions** per server, reusable **permission groups** (e.g. "Player", "Moderator"),
  "all servers, including future ones", and **invite links** so you never have to send passwords around
- **Automations**: restart after a crash, scheduled starts/stops/restarts with an in-game warning
- **Backups** to a drive or a network share (SMB), on a schedule or by hand – every backup file is checked
  after writing, and you can **restore** it with a few clicks
- **Notifications** to Discord (or another webhook) and by email: crash, restart failed, disk almost full,
  backup failed, …
- **Audit log**: who did what and when (exportable as CSV/TXT)
- A **public status page** for your players (optional) and Discord status messages
- **Read-only mode**: Hatchtower can be limited to *looking*; it then never changes anything on your panel
- Your own name and logo, three colour themes (dark, medium, light), English and German

## 3. Screenshots

All screenshots show the current version with a test panel and made-up servers and users.
Addresses in the screenshots are examples.

**What your friends see – the simple view**

<img src="docs/screenshots/simple.png" alt="Simple view: status, address, password, restart and stop" width="100%">

**A server in detail** – status, address, uptime, resources and history. Console, log archive and the
server's activity log are in the tabs.

<img src="docs/screenshots/server.png" alt="Server detail page" width="100%">

**Files** – work with the server's files right in the browser.

<img src="docs/screenshots/files.png" alt="File manager" width="100%">

**Permissions per user and server** – pick a group or tick single permissions. The coloured chips show
exactly what the person may do.

<img src="docs/screenshots/user-permissions.png" alt="Permissions of a user on two servers" width="100%">

**Backups** – jobs, stored backups with their check result, and restore.

<img src="docs/screenshots/backups.png" alt="Backups page" width="100%">

<table>
  <tr>
    <td width="62%"><img src="docs/screenshots/restore.png" alt="Restoring a backup"><br>
      <sub>Restoring explains what will happen and offers a safe option that overwrites nothing.</sub></td>
    <td width="38%"><img src="docs/screenshots/mobile.png" alt="Hatchtower on a phone"><br>
      <sub>On a phone.</sub></td>
  </tr>
</table>

**Light theme** – everyone can switch between dark, medium and light with the ☾ ◐ ☀ switch at the top
right. Dark is the default.

<img src="docs/screenshots/light-theme.png" alt="Overview in the light theme" width="60%">

## 4. What do I need before installing?

| What | Details |
|---|---|
| **A panel** | Pterodactyl, Pelican or Calagopus that already runs your game servers, and an **admin account** on it (you need it once, to create the API keys). |
| **A small Linux machine for Hatchtower** | Debian 12 or 13 (tested), Ubuntu should work too. A container on Proxmox is ideal. 1 CPU core, 512 MB RAM and 2 GB disk are enough. Alternatively any machine with **Docker**. |
| **Network access** | The Hatchtower machine must reach your **panel** (its web address) and the **Wings** service on each node (usually port `8080`). With Calagopus and its Wings proxy, the panel alone is enough. |
| **Root access** | You run the install commands as `root` (or with `sudo`). |

**Words you will see in this guide**

- **Panel** – the website where you manage your game servers (Pterodactyl, Pelican or Calagopus).
- **Wings / node** – the program on the machine that actually runs your game servers. The panel controls it.
- **API key** – a long password for programs. Hatchtower uses it to ask the panel for information or to
  send commands. You create it in the panel.
- **Shell / terminal** – the text window where you type commands on a Linux machine
  (on Proxmox: select the host or container → *Shell*).
- **Reverse proxy** – a program (e.g. Caddy, Nginx, Pangolin) that makes a website reachable from the
  internet with HTTPS. Only needed if your friends should reach Hatchtower from outside your home.

> 🔒 **The GitHub repository is currently private.** The one-line install commands below then need a read
> token – see [Installing while the repository is private](#installing-while-the-repository-is-private).

## 5. Installation

Pick **one** way. If you use Proxmox, take **A**. Otherwise take **B**.

### A) Proxmox: new container with one command (recommended on Proxmox)

Open the shell of your **Proxmox host** (not of a container) and run:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/proxmox/install.sh)"
```

The script asks a few questions (container ID, network, port …). **Press Enter** to accept the suggestion
in brackets – the suggestions work for most home networks. It then creates a
small Debian container and installs Hatchtower in it.

<details>
<summary>Install without questions</summary>

```bash
HT_YES=1 HT_CTID=120 HT_IP=192.168.1.50/24 HT_GW=192.168.1.1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/proxmox/install.sh)"
```

All variables are listed at the top of `proxmox/install.sh`.
</details>

### B) An existing Debian or Ubuntu machine (recommended without Proxmox)

Open a shell on the machine that should run Hatchtower and run:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/installer/get.sh)"
```

The script downloads the newest release, checks its checksum and installs it.
If you see `curl: command not found`, install curl first: `apt-get install -y curl`.

<details>
<summary>Options (port, which IPs may connect)</summary>

| Option | Meaning |
|---|---|
| `--port 8000` | Port of the web page (default 8000) |
| `--bind 127.0.0.1` | Only listen locally – use this if a reverse proxy runs on the same machine |
| `--allow "192.168.1.5,192.168.1.10"` | Only these IP addresses may open the page (adds its own small firewall table; your other firewall rules stay untouched) |

Add them after an underscore: `bash -c "$(curl …)" _ --allow "192.168.1.5"`
</details>

### C) From a downloaded package (no internet access to GitHub)

Download `hatchtower-<version>.tar.gz` from the **Releases** page, copy it to the machine, then:

```bash
tar -xzf hatchtower-<version>.tar.gz
```

```bash
cd hatchtower-<version> && bash installer/install.sh
```

### D) Docker Compose (for people who already use Docker)

In a folder with the Hatchtower source code (Git checkout or unpacked release package):

```bash
docker compose up -d --build
```

```bash
docker compose logs hatchtower
```

The log shows the **setup code**. Then open `http://<your-machine>:8000/setup`.

- All data (database, logos, keys) is stored in the Docker volume `hatchtower-data`. **Back up the whole
  volume** – the file `keys.env` in it is needed to read the stored API keys.
- For backups, mount a folder into the container under `/mnt/…` (see the comment in `compose.yaml`).
- Commands run like this: `docker compose exec hatchtower hatchtower reset-password`.
- Updates: get the new version, then `docker compose up -d --build`. The update button inside Hatchtower
  is only for installations A–C.

### What success looks like

At the end, the installer prints something like this:

```
Hatchtower 0.28.0 is installed.

  1. Open in your browser:  http://192.168.1.50:8000/setup
  2. Setup code:            ABCD-EFGH-JKLM-NPQR   (valid for 24 hours, new one: hatchtower setup-token)
```

That means: **1.** open this address in your browser, **2.** keep the setup code ready. The code is valid
for 24 hours; `hatchtower setup-token` creates a new one.

### Installing while the repository is private

You need a **read token** from the project owner. Enter it once (it is not shown while typing):

```bash
read -rsp "GitHub token: " GITHUB_TOKEN && export GITHUB_TOKEN HATCHTOWER_SAVE_TOKEN=1 && echo
```

Then, **instead of** the command from A or B, run one of these:

```bash
bash -c "$(curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github.raw" https://api.github.com/repos/skaot1910/hatchtower/contents/proxmox/install.sh)"
```

```bash
bash -c "$(curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github.raw" https://api.github.com/repos/skaot1910/hatchtower/contents/installer/get.sh)"
```

The first one is for **A** (Proxmox host), the second one for **B**. The token is stored on the Hatchtower
machine so that updates work later (with **A** the script asks first – Enter means yes; without questions,
add `HT_SAVE_TOKEN=1`). If it expires or was not stored: `hatchtower github-token`.

<details>
<summary>Creating a token (for the repository owner)</summary>

GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new
token**. Repository: only `hatchtower`, **Contents**: "Read-only", set an expiry date.
</details>

## 6. First-time setup

Open the address from the installer (`http://<address>:8000/setup`). A wizard guides you step by step.
Every step has a **"Why?"** box that explains what it is for.

1. **Your owner account** – enter the setup code and choose a username and a strong password
   (at least 15 characters). The owner may do everything, so keep it safe.
2. **Panel** – choose Pterodactyl, Pelican or Calagopus, enter the panel's address and the first API key.
   The wizard shows exactly where to click in your panel. Details in the next section.
3. **Client key** – a second key for controlling servers (not needed for Calagopus).
4. **Wings** – Hatchtower finds your nodes by itself and checks that it can reach them.
5. **Check and save** – Hatchtower tests everything **without changing anything** on your panel and shows
   the results in plain words: *Error* (must be fixed), *Warning* (works with limits) or *Optional*.
   Only after you confirm are the keys saved – encrypted.
6. **Options** – read-only or read-and-control, the address your players use, the time zone, and which
   view new users get (simple or everything). Everything can be changed later.

> 💡 For safety, Hatchtower starts in **read-only mode**: it shows everything but changes nothing. When you
> are happy, switch to **"Read and control"** under **System → Panel → Mode**.

<img src="docs/screenshots/connection-wizard.png" alt="The connection wizard explains how to get the API key" width="80%">

## 7. Connecting your panel (API keys explained)

Hatchtower needs permission to talk to your panel. You give it that permission with API keys.
The setup wizard shows these steps too, with the right names for your panel.

### Pterodactyl and Pelican: two keys

**1. Application API key (read-only)** – lets Hatchtower read the list of servers and nodes.

- Pterodactyl: click the **gear icon** (admin area) → **Application API** → **Create New**.
- Pelican: go to the **admin area** → **API Keys** → create a new key.
- Description: e.g. "Hatchtower". Set **all permissions to "Read"** – nothing else is needed.
- Copy the key. It starts with `ptla_` (Pterodactyl) and is only shown once.

**2. Client API key** – lets Hatchtower start and stop servers, read the console and work with files.

1. In the panel, create a **separate user** just for Hatchtower (e.g. `hatchtower-service`) with a long
   random password.
2. Either make it an **administrator** (then Hatchtower sees all servers, also new ones), **or** – more
   careful – add it only as a **subuser** to the servers you want.
3. Log in as this user, open the account page → **API Credentials** (Pterodactyl) or **API Keys**
   (Pelican) and create a key.
4. Under **Allowed IPs**, enter the IP address of your Hatchtower machine. Then the key only works from there.
5. Copy the key (starts with `ptlc_` on Pterodactyl).

> ⚠️ A client key can do everything its user can do – the panel cannot limit it further. That is why you
> should use a dedicated user, set **Allowed IPs**, and protect the user with two-factor authentication.

### Calagopus: one key

1. Create a separate user for Hatchtower with admin rights and a long random password.
2. Log in as this user, open **API Keys** in the account and create a key.
3. Permissions: **Admin** – read access to nodes and servers; **Server** – everything except `sftp` and
   `query-raw`. Under **Allowed IPs**, enter your Hatchtower machine's IP.
4. Hatchtower only uses the admin permissions for reading.

### Where the keys are kept

Keys are stored **encrypted** on the Hatchtower machine and are never shown in full again – not even to
owners. To change the connection later, use **System → Panel → Repair or connect a different panel**. Your old connection
keeps working until you save the new one.

## 8. Managing servers and users

### Servers

- **Overview** shows all servers you may see, as tiles or a list. Filter by status or tag, mark favourites.
- **Start / Stop / Restart** ask before doing anything. **Kill** (force stop, owners only) asks twice
  because the game cannot save.
- **Simple ↔ Everything** at the top right switches the view. As an owner you can set the default for
  everyone under **Presentation → Appearance**, or lock a user to one view.

### Users

There are two roles:

- **Owner** – may do everything, including managing users and settings.
- **User** – may only do what you allow, **per server**.

**Inviting someone:** **Users → By invitation link**. Choose how long the link is valid and how often it can
be used, then send the link (e.g. in Discord). The person picks their own username and password – you never
handle their password. Optionally pick a **permission group and servers** right in the invitation form: the person
then has exactly that access as soon as they sign up – no second step needed.

**Giving permissions:** open the user → **Permissions per server**. For each server, pick a **group**
(e.g. "Player") or tick single permissions. "All servers, including future ones" gives access to new
servers automatically. The summary line under the list says in plain words what the person can see.

**Permission groups** (Users → Permission groups) are reusable sets. With **"Create suggested groups"** you get
three to start with and can adjust them at any time:

| Suggested group | Permissions |
|---|---|
| Viewer | see the server, status, address and password |
| Player | + start and restart (not stop) |
| Moderator | + stop, read the console, send commands, console archive, server log |

Permissions that allow more than their name suggests (e.g. starting backups gives access to all backed-up files,
sending commands can be far-reaching depending on the game) carry a note on the group and user pages.

Under **"What does each permission mean?"** on the user page every permission is explained.
Hatchtower checks every permission **on the server side** for every request – hiding a button is not the
only protection. A user who may only restart a server cannot reach files, console or backups, even with
hand-made requests (this is covered by automated tests for every page and action).

**Preview as user** shows the page exactly as that person sees it.

## 9. Files and permissions

The file manager (**Files** on a server page) works through the panel and Wings, like the panel's own file
manager. What someone can do depends on these permissions:

| Permission | Allows |
|---|---|
| View files | list folders and files |
| Download | download files (several at once as a ZIP) |
| Upload | upload files (up to 200 MB per upload) |
| Edit | change text files in the browser – the previous version is kept as `.bak` and can be restored with one click |
| Rename, Create folder, Delete | what the names say |

Hatchtower does **not** offer SFTP access. Paths are always kept inside the server's own folder.

## 10. Backups and restoring data

### How it works

1. **Backup target** (owners only): where backup files go – a drive in the Hatchtower machine
   (e.g. a Proxmox mount point, USB disk or NFS under `/mnt`, `/media` or `/srv`) or an **SMB share**
   (Windows share, NAS). Create it under **Backups → Backup targets**. *Test* writes and deletes a small
   file to prove it works.
2. **Backup job**: what to save (a whole server, or selected folders – you can tick them in the file
   manager), where to (the target) and when (by hand or on a schedule, e.g. daily at 04:00).
   Optionally the server is stopped first (with an in-game warning) and started again afterwards –
   some games only write their save files cleanly when they shut down.
3. Each run creates **one ZIP file per server** on the target, in folders named after the job and the
   server, e.g. `Save_games/Terraria/2026-10-14_04-00.zip`.

### What "done", "verified" and "restored" mean

| You see | It means |
|---|---|
| **done** | The ZIP file was written completely. |
| **verified** | After writing, Hatchtower read the whole file again and every file in it matched its checksum. Every new backup is verified automatically; *Check* re-verifies an older one at any time. |
| **with errors** | Some files are missing or incomplete (e.g. a file could not be read). The list is in the row and in `ERRORS.txt` inside the ZIP. |
| **failed** | Nothing usable was saved. If not a single file could be read, Hatchtower does not keep an empty ZIP. |
| **restored** | This backup was successfully copied back to a server at least once. |

A verified file is not damaged – but only a restore proves that the backup contains what you need.
Try a restore into a separate folder once in a while; it overwrites nothing.

### Restoring a backup

**Backups → stored backup → Restore.** You choose:

- **Into a separate folder (safe)** – the files go to `/hatchtower-restore/<date>/` on the server.
  Nothing is overwritten. Look at them in the file manager and move what you need.
- **To the original place** – every file from the backup replaces the file with the same name. Changes
  since the backup (e.g. game progress) are lost. You must tick a confirmation first.
  Files that are not in the backup are left alone – nothing is deleted.

Optionally Hatchtower stops the server first and starts it again afterwards (recommended for the original
place). Before anything is copied, the backup file is checked; if it is damaged, nothing happens on the
server. You can follow the progress and cancel on the Backups page. If Hatchtower is restarted during a
restore, simply start the restore again.

Restoring needs the permission **"Restore backups"** (plus Start/Stop to stop the server first). Owners
have it automatically.

### Retention, disk space and safety

- **Keep: number / days** deletes older backups automatically, per server. The **newest backup and the
  newest complete backup are always kept**, so a backup with errors never pushes out the last good one.
- Before each run Hatchtower checks the free space on the target and stops with a clear message if it
  is too small.
- Backups on the same disk as Hatchtower itself are marked **"System disk"**: they do not protect you
  from a broken disk and can fill it up. Use a separate drive or a NAS.
- If a scheduled backup could not run because Hatchtower was off (update, power cut), it is **run late**,
  up to 6 hours after the planned time. If a job cannot start or fails, you get a notification
  (set up under **System → Notifications**; per job "only on errors", "always" or "never").
- Who sees what: *View backups*, *Start and download backups*, *Restore backups* and *Manage backup jobs*
  are separate permissions. Backup targets (folders and NAS passwords) are for owners only.

### Backing up Hatchtower itself

Hatchtower's own data (users, permissions, settings, encrypted keys) is a database at
`/var/lib/hatchtower/hatchtower.db`. A copy is made **every night at 03:30** to `/var/backups/hatchtower/`
(kept 14 days) and **before every update**.

⚠️ The stored API keys and passwords can only be read with the key in `/etc/hatchtower/hatchtower.env`.
The nightly copy does **not** include that file, and both are on the same machine. The easiest safe
backup is **System → Data → Data transfer: export**: one password-protected file with everything (users, permissions,
servers, schedules, backup jobs, settings and keys). Keep it somewhere else. To move to a new machine,
install Hatchtower there and choose **Import** in the setup wizard.

## 11. Updating Hatchtower

Hatchtower checks for new versions twice a day and shows a dot next to **System** when one is available.
Under **System → Updates** click **Install update to …**, or run this on the Hatchtower machine:

```bash
hatchtower update
```

What happens: the new version is downloaded and its checksum is verified, the **database is backed up**,
the new version is installed and started. If it does not start, Hatchtower **automatically goes back** to
the previous version and the database backup. Users, permissions, settings, logos and history are kept.
Nothing is ever updated without you asking for it.

Docker: get the new version, then `docker compose up -d --build`.

## 12. Troubleshooting

| Problem | What to do |
|---|---|
| **The page does not open** | Check the address and port (default `8000`). Is the service running? `systemctl status hatchtower`. If you installed with `--allow`, only the listed IPs may connect. |
| **The setup code is wrong or expired** | Create a new one on the Hatchtower machine: `hatchtower setup-token` (only works while no account exists). |
| **"API key rejected" / 401 or 403** | Did you paste the right key in the right field? Application keys start with `ptla_`, client keys with `ptlc_`. Is the Hatchtower IP in the key's **Allowed IPs**? Was the key deleted in the panel? Repair under **System → Panel**. |
| **Servers are listed, but console, files or downloads don't work** | Hatchtower cannot reach **Wings** (usually port `8080` on the node). Check firewalls. If the node is known under a different address from Hatchtower's point of view, enter it in the connection wizard in the **Wings** step. |
| **Start/Stop buttons are missing** | Hatchtower is in read-only mode (**System → Panel → Mode**), or the user lacks the permission. |
| **Backup: "Not a single file could be backed up"** | Hatchtower can list the files but cannot download them from Wings – same cause as above (Wings not reachable). |
| **Backup target: "Hatchtower is not allowed to write to …"** | For a Proxmox mount point in an unprivileged container, run the `chown` command from the message (also shown under *Backups → Backup targets → Mount a drive, NFS share or USB disk*) on the **Proxmox host**. |
| **Backup: "The target folder … is on the system disk – is the drive mounted?"** | The USB disk or mount point is not mounted. Mount it and click *Test* on the target. |
| **Restore: "too large for the Wings upload limit"** | A single file is bigger than the upload limit of your node (Pterodactyl default: 100 MB). Raise *Upload limit* for the node in the panel, or copy that file by hand. |
| **I forgot the owner password** | On the Hatchtower machine: `hatchtower reset-password` (also unlocks the account). |
| **Something else** | Run `hatchtower selftest` – it checks the connection and explains problems. The service log: `journalctl -u hatchtower -f`. |

## 13. Security recommendations

- **Use HTTPS from outside.** Hatchtower itself speaks plain HTTP. If friends reach it from the internet,
  put a reverse proxy with HTTPS in front (Caddy, Nginx, Traefik, Pangolin …) and enter the proxy's IP
  under **System → Network → Reverse proxy IPs**. Step by step: [Reverse proxy and Pangolin](docs/reverse-proxy.md).
- **Keep the panel private.** Only Hatchtower needs to be reachable from outside.
- **Protect the client key**: dedicated panel user, **Allowed IPs**, two-factor authentication. Even more
  careful: make that user a subuser of selected servers instead of an administrator.
- **Finish the setup right after installing.** Until the owner account exists, anyone with the setup code
  can create it.
- **Give people only what they need.** Start with "View only" or "Player" and add more later.
- **Look at the audit log** now and then (Users → Audit log).

What Hatchtower does for you: strong password hashing (Argon2id), lockout after failed logins, session
timeouts, CSRF protection, a strict Content Security Policy, server-side permission checks on every request,
read-only mode enforced in code, and encrypted storage of API keys and passwords.

<details>
<summary><b>Running on a public server (VPS, dedicated server)</b></summary>

1. Listen on localhost only if the reverse proxy runs on the same machine:
   `bash installer/install.sh --bind 127.0.0.1` – otherwise only open port 8000 for the proxy.
2. HTTPS via the proxy, e.g. Caddy (gets a certificate automatically):

   ```
   hatchtower.example.com {
     reverse_proxy 127.0.0.1:8000
   }
   ```

3. Enter the proxy IP under **System → Network → Reverse proxy IPs** (Caddy on the same machine:
   `127.0.0.1`). Only then does Hatchtower set secure cookies and see the real visitor IPs – otherwise one
   attacker with ten wrong passwords could lock out everyone.
4. Connect Hatchtower to the panel and Wings through a VPN (e.g. WireGuard) instead of opening the Wings
   port to the internet.

</details>

## 14. Frequently asked questions

**Do my friends need an account on the panel?**
No. They only get a Hatchtower account (via invite link). The panel stays yours.

**Does Hatchtower replace my panel?**
No. Creating servers, eggs, nodes and panel users still happens in the panel. Hatchtower reads that and
adds its own users and permissions on top.

**What happens in read-only mode?**
Hatchtower shows everything (status, console, files, downloads, backups) but never changes anything in the
panel – the code blocks every write request. Good for trying it out.

**Can Hatchtower manage several panels?**
One installation connects to one panel. You can switch to another panel later (System → Panel), and users,
permissions and schedules are kept.

**Where are my backups stored?**
On the backup target you created: a folder on a drive of the Hatchtower machine or an SMB share. Each
backup is a normal ZIP file you can also open on your computer.

**Does Hatchtower show the number of players?**
No. This is not reliable across games, so Hatchtower does not offer it.

**Which languages are available?**
English and German. Hatchtower follows your browser language; each user can switch in the account menu.
The install scripts always speak English.

**Is it free?**
Yes, under the AGPL-3.0 license (see below).

## 15. Getting help and reporting problems

1. Run `hatchtower selftest` and look at **System → Status** and **System → Panel → Check connection** – most
   connection problems are explained there.
2. Check [Troubleshooting](#12-troubleshooting).
3. Open an issue on GitHub. Please include the Hatchtower version (`hatchtower version`), your panel and
   its version, what you did, what you expected and what happened. **Never post API keys, passwords or the
   contents of `/etc/hatchtower/hatchtower.env`.**

<details>
<summary>Useful commands on the Hatchtower machine</summary>

| Command | What it does |
|---|---|
| `hatchtower update` | Install the newest version |
| `hatchtower update --check` | Only check whether an update is available |
| `hatchtower update <file.tar.gz>` | Install a downloaded package |
| `hatchtower selftest` | Check the connection to panel and Wings (read-only) |
| `hatchtower version` | Show the installed version |
| `hatchtower reset-password` | Reset a user's password and unlock the account |
| `hatchtower create-owner` | Create an owner account on the command line |
| `hatchtower setup-token` | New setup code (only while no account exists) |
| `hatchtower github-token` | Store a read token for updates while the repository is private (`--remove` deletes it) |
| `journalctl -u hatchtower -f` | Follow the service log |

| Path | Contents |
|---|---|
| `/opt/hatchtower` | The program |
| `/etc/hatchtower/hatchtower.env` | Internal keys and port – **never share** |
| `/var/lib/hatchtower/hatchtower.db` | Database with users, settings and encrypted API keys |
| `/var/backups/hatchtower/` | Nightly database copy (14 days) and copies before updates |

</details>

<details>
<summary>For developers</summary>

- Code: `app/hatchtower/` (FastAPI, Jinja2 templates, plain JavaScript). Panel adapters: `ptero.py`
  (Pterodactyl, Pelican) and `calagopus.py`. Backups: `backups.py`, `routes_backup.py`.
- Tests: `pytest tests` – including a permission matrix that checks every route
  (`tests/routes.py` → [`docs/rechte-matrix.md`](docs/rechte-matrix.md)).
- Code comments and commit messages are in German. German UI text is the translation key; English lives in
  `app/hatchtower/locales/en.json`.

</details>

## 16. License and credits

Hatchtower is free software under the **GNU Affero General Public License v3.0** (AGPL-3.0), see
[`LICENSE`](LICENSE). Copyright © 2026 skaot1910.

In short: you may use, change and share it, also commercially. Changed versions must again be licensed
under the AGPL-3.0 – also if you only offer them over a network. There is no warranty.

All dependencies use free licenses (MIT, BSD, Apache-2.0, PSF, MPL-2.0). The list is in the interface under
**Licenses** (footer); the full texts are in `THIRD_PARTY_LICENSES.txt` on the server.

🤖 Hatchtower was built together with [Claude](https://claude.ai) (Anthropic).

<div align="center">
<sub>Hatchtower is an independent project and is not affiliated with Pterodactyl, Pelican or Calagopus.</sub>
</div>
