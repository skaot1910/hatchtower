# Changelog

Newest entries first. The "## <version>" sections are used automatically as the description of the GitHub release
when publishing (`tools/release.sh`).

## 0.38.1-pre.2 – 2026-10-20

- Audit log: the user filter suggests every current account plus everything else that can appear in the user
  column (internal actors like System or Automation, deleted accounts, failed logins, entries without a user)
- Audit log: search by IP address – part of an address is enough, e.g. `192.168.`
- Audit log: the action filter is sorted alphabetically and searchable – type a keyword and every action whose name
  contains it is suggested and matched
- Registration through an invitation link is now listed as "User created via invitation" (it was logged before, but
  only as "Invitation accepted"); CSV/TXT export uses the same filters

## 0.38.1-pre.1 – 2026-10-20

- Installed web app (Android): Hatchtower now has a small service worker (`/sw.js`). Chrome on Android only offers
  "Install app" with a worker that handles page loads; without it there was at most a home screen shortcut. Nothing
  is cached – if the server cannot be reached, the installed app shows a short note instead of Chrome's error page
- The manifest is now requested with cookies, so installing also works behind a reverse proxy with its own login
- Note: installing as an app needs HTTPS (or localhost) – over plain `http://` with an IP address, Chrome never
  offers it

## 0.38.0 – 2026-10-19

- Pre-releases: new switch "Receive pre-releases" under System → Updates. When it is on, the update check and
  `hatchtower update` also offer pre-releases (versions like `0.39.0-pre.1` or `0.39.0-rc.1`), clearly marked as
  such; when it is off, only regular releases are offered, as before. Turning it off never downgrades – a server on
  a pre-release stays there until the next regular release. Like all updates, nothing is installed on its own
- Versions are now compared by semver precedence (`0.39.0-pre.1 < 0.39.0-rc.1 < 0.39.0`), in the web interface and
  in `hatchtower update`; `HATCHTOWER_PRERELEASES=1|0` overrides the switch for a single command
- `tools/release.sh` publishes a version with a `-pre.N`/`-rc.N` suffix as a GitHub pre-release (never marked as
  latest)

## 0.37.3 – 2026-10-19

- Installed web app (Android): the manifest now also has 192 px icons. Chrome only offers "Install app" when there
  is both a 192 px and a 512 px icon, so 0.37.2 could not be installed as an app

## 0.37.2 – 2026-10-19

- Create server: short, collapsible guide with the essentials – where to find eggs (pelican-eggs/steamcmd,
  pelican-eggs/games-standalone, pterodactyl/game-eggs), values to check, panel owner vs. Hatchtower users, ports,
  opening them (router at home, firewall on a VPS) and the public address for players (fixed IP/domain or DynDNS)
- Installed web app (Android): the manifest now has a fixed URL, an app ID and PNG icons (including a maskable one),
  so Chrome can install it as a real app and keep it updated; its colours follow the instance's default theme. The
  bar behind the gesture navigation should now be dark. Uninstall and reinstall the app once to pick this up

## 0.37.1 – 2026-10-19

- Footer on every page (login included): "Hatchtower" links to the GitHub repository
- Installed web app (Android): new web app manifest with the theme colours, so the bar behind the gesture
  navigation is no longer white. Reinstall the app once to pick it up
- Phone menu covers the whole page below the top bar; the page behind it no longer scrolls
- Phone overview: the public status page link sits next to the heading, both switches (Simple/Everything and
  List/Tiles) share one row
- List/Tiles switch: List comes first

## 0.37.0 – 2026-10-19

- Owners are told when someone registers through an invitation link, like with new servers: a notice in the
  overview shows which permissions the invitation gave (with "Check permissions" and "Done"), plus an entry in the
  bell. New event "New user registered" for webhooks and e-mail; webhooks that report new servers get it as well
- System → Notifications → Notices for owners: switch the notices about new servers and new users on or off

## 0.36.0 – 2026-10-19

- New **Players** tab on the server page: shows whether anyone is online and how that was detected. Three sources
  are asked in order: query protocol (Steam A2S, Minecraft Java, Minecraft Bedrock), console lines of known games
  (joins/leaves and player counts, e.g. Minecraft, Terraria, Valheim, Factorio, Palworld, 7 Days to Die), and as a
  last resort an estimate from network traffic. The deciding source, its evidence and a 30-day history are shown.
  Needs the new permission **View players** (owners always see it)
- Server log tab: filter by user and sort by user
- Permission groups: one comparison table (permissions as rows, groups as columns) instead of separate cards; the
  edit form lists each permission in a tidy row
- Server page: switch between the simple and the full view there as well
- Simple overview: "Select all" and checkboxes for shared start/stop/restart, as in the full view
- System → Updates: the "Check for updates now" button is highlighted
- Server list: name on its own line with status and game always together below it
- The Minecraft Java ping is only sent to Minecraft eggs, so other games no longer log it as a connecting player

## 0.35.3 – 2026-10-18

- Phones: the server list is compact again (about a quarter less height per server) and keeps all values:
  address and password side by side, CPU/RAM/storage in one row, power buttons and links in one line
- Server list (all screen sizes) and public status page: every second server has a slightly different
  background, so rows are easier to tell apart; only the servers currently shown by the filter count
- Android: the page now extends behind the gesture bar, so the bar shows the page colour instead of white;
  content keeps clear of it

## 0.35.2 – 2026-10-18

- Phones: the menu opens again. Since 0.34.1 the top bar clipped the opened menu, so it stayed invisible
- Phones: the browser's own bars (address bar, bottom toolbar) take the theme colour instead of white, so the
  system buttons stay visible; they switch along with the theme

## 0.35.1 – 2026-10-18

- Login page: the uploaded logo gets rounded corners and softly faded edges, so a logo with its own background
  blends into the login card instead of sitting on it as a hard rectangle. Transparent logos look as before

## 0.35.0 – 2026-10-18

**File search**
- The file manager can search all files and folders of a server at once, as you type. Several words narrow the
  search (e.g. `essentials config`), `*.yml` finds by pattern; matches show their folder, size and download link,
  text files open directly in the editor
- Hatchtower reads the folder tree once in the background and searches its own index, so results appear
  instantly. Reading is throttled against the panel's rate limit (at most 2 requests per second, pauses when less
  than half of the limit is left, one server at a time); what is found is searchable right away
- The index survives restarts and is rebuilt at most once a day, only when someone opens the files of that
  server. Folders opened in the file manager update their part of the index at no extra cost; "Rescan" reads
  everything again
- Up to 5000 folders and 200,000 entries per server; `node_modules`, `.git` and similar folders are listed but
  not searched inside

**Branding**
- The sidebar always shows the Hatchtower mark and name; your own name appears as a short tagline below it.
  The upload for a sidebar logo was removed (a previously uploaded one is no longer used), the login page logo
  stays and is also used in e-mails
- The browser tab is always titled "Hatchtower"
- Sidebar: the group headings Operations and Management stand out more clearly from the links below them

## 0.34.1 – 2026-10-17

- Sidebar: an uploaded logo gets its own line and the community name wraps below it, so the name is always
  readable and the sidebar never scrolls sideways
- Overview list from 1280 px: one header row and fixed columns, so all rows line up; rows only show values
  (address above password, usage as value and bar) and are noticeably lower. The server name opens the details
- Simple view: narrower list with low rows; address and password side by side
- The Simple/Everything switch moved from the sidebar into the overview, next to Tiles/List. The top row of the
  overview (status page link, both switches, monitoring) now looks the same in both views
- Browser tab icon: the link carries a version, so browsers replace the old cached icon

## 0.34.0 – 2026-10-17

**New interface**
- Navigation moved into a sidebar (a top bar with a menu on phones). Owners see it in two groups, Operations and
  Management
- Two themes: Dark (default) and Light, both with a bordeaux accent. The former Medium theme is mapped to Dark.
  Switching takes effect immediately and is stored per user
- Own typeface (IBM Plex Sans, shipped with Hatchtower, no external requests) and a consistent set of line icons
  instead of text symbols
- Cards are panels with a header line, fewer nested frames; status badges and usage bars are calmer
- The server list shows one compact row per server with address, password, CPU, RAM and storage, so twenty
  servers fit on one screen. Long names wrap instead of being cut off, addresses are never shortened
- Phones: no page scrolls sideways any more (checked at 320 and 375 px), filters scroll in one line, start and
  stop share a row with the links

**Notifications inbox**
- New bell with a counter in the sidebar and a history page. Owners see panel outages, new servers, failed or
  partial backups, failed webhook or e-mail delivery, problems with the Discord server status and new versions.
  Users see crashes, restarts, failed schedules and failed backups of the servers they may see
- Read state per user, "mark all as read", entries are removed after 30 days

**Backups**
- Backup targets are listed right below running tasks
- Stored backups and restores are shown ten per page and can be filtered by server, job and result

**Management**
- New tab Server → All servers: every server with direct links to Manage, Panel settings and Files
- "Presentation" is now called "Community" (name and logo, status page, announcements, legal pages)

**Updates**
- System → Updates links the installed version to its GitHub release and to the full changelog, and shows the
  release notes of a new version before you install it

## 0.33.0 – 2026-10-17

**Server administration (optional admin key)**
- With an additional key that has write access, owners can create servers in Hatchtower and change name,
  description, resources (RAM, CPU, disk, limits), ports and startup parameters (startup command, Docker image,
  variables, egg), reinstall, suspend and delete servers – for Pterodactyl, Pelican and Calagopus
- New page Server → Ports: create ports (allocations) on the nodes and delete free ones
- The admin key is optional: add it in the setup wizard (new step) or later under System → Panel. Before saving,
  Hatchtower checks its permissions without changing anything. Without the key, the new pages are shown but locked
- New permissions per server: see panel settings, name and description, resources and ports, startup parameters,
  reinstall. Creating, deleting and suspending servers, changing owner or egg and node ports are owner-only
- All changes are written to the audit log; values of startup variables are never logged

## 0.32.2 – 2026-10-15

- Footer: support links redesigned as buttons with logos; new link to GitHub Discussions next to the support Discord
  (visible to owners)

## 0.32.1 – 2026-10-14

- Footer: link to the Hatchtower support Discord (visible to owners)
- New installations no longer come with a preset community Discord link; existing installations that still have
  the old preset get it removed (an invite entered under "Appearance" is kept)

## 0.32.0 – 2026-10-14

First public beta.

**Security and updates**
- Status and log of updates started from the web interface now live in `/var/lib/hatchtower-update` (owned by root,
  read-only for the service). The installer and updater no longer follow symlinks in the data directory
- All Python packages are pinned to fixed, tested versions; an automatic rollback after a failed update also rolls
  back the packages
- The last five database backups taken before updates are kept
- Report security vulnerabilities privately: see `SECURITY.md`

**Other**
- "About Hatchtower" links to the source code of exactly the installed version
- README: beta notice, uninstalling, which connections Hatchtower makes (no telemetry), notes on the database backups
- Docker support removed – the Debian and Proxmox installers are the officially supported ways to install
- Installation and updates no longer need a GitHub token (only for private forks)

## 0.31.0 – 2026-10-14

**File explorer**
- **Delete multiple entries:** tick files and folders and delete them together (with confirmation, one request to the
  panel)
- **Move:** tick a selection, "Move …" opens a folder picker you can click through; optionally into a new subfolder.
  The destination is shown in plain text, selected folders cannot be the destination. If an entry with the same name
  already exists at the destination, nothing is overwritten and a clear message appears
- The checkboxes are now also available to users who may only delete or rename

**Emails**
- **New layout for all emails:** logo from the appearance settings (embedded as PNG, otherwise the name), personal
  greeting, clear button with a link to copy, details as an overview, security notice, closing and footer. HTML with a
  plain-text version; applies to password reset, email confirmation, invitation, test message and notifications
- **Individual notifications:** each recipient gets their own email with their name and in their language –
  previously all recipients saw each other's addresses. With a button that goes straight to the server, backups or
  System
- **Unsubscribe link (GDPR):** notifications can be unsubscribed via a link in the email without signing in, plus
  "List-Unsubscribe" for the unsubscribe button in Gmail, Outlook & co. The link is signed and only valid for the
  current address; the unsubscribe is recorded in the audit log
- Emails keep the language they were created in; invitations are sent in the instance's default language

## 0.30.1 – 2026-10-14

- **Deactivate and reactivate accounts:** a dedicated "Deactivate account" button on a user's page (owners only, with
  confirmation) instead of the inconspicuous "Account active" checkbox. Deactivated accounts show a notice with
  "Reactivate", as does the user list in the "Disabled" section. Both are recorded as separate actions in the audit log
- An owner can no longer deactivate themselves (previously possible as long as there was a second owner)

## 0.30.0 – 2026-10-14

**Backups**
- **Result when finished:** when a restore, backup or check is done, the top of the backup page shows whether it
  worked – e.g. "Restore finished: … – 37 files restored to the folder …" or, on errors, the reason. Previously the
  task simply disappeared from "In progress"
- **Several backups at once:** stored backups can be ticked and deleted or checked together (at most 10 checks at
  once). An action is only available if it is allowed for all selected backups

**Users**
- **New tab "New users":** invitation links, open invitations, direct creation and the email requirement are now
  separate from the user list
- **Clearer user list:** split into owners, users and disabled, with counters, an "Add users" button, a notice about
  open invitations and search (from 7 users)

**Other**
- "Log archive" in the server details is now called "Console archive", as everywhere else
- Sticky selection bars (backups, file explorer) are no longer hidden by a wrapping page header

## 0.29.1 – 2026-10-14

- **Backups:** Restore, Delete and other buttons are always visible again. Some only appeared when hovering over the
  row (also affected automations and announcements); only the file list still shows Rename and Delete on hover
- **README** shortened to the essentials, with a short story of how it came about; overview screenshots in list view

## 0.29.0 – 2026-10-14

**Invite friends in one step**
- **Invitation with access:** the invitation form lets you set a permission group and servers (or "All servers,
  including future ones"). Whoever uses the link sees the servers right after signing in – without the owner having to
  assign permissions afterwards. Open invitations and the audit log show the access. If the group has been deleted in
  the meantime, no access is granted. "All servers" with control or write permissions shows a warning
- **Suggested groups:** create "Viewer" (see, address, password), "Player" (additionally start and restart, not stop)
  and "Moderator" (additionally stop, console, log) with one click – under Users → Permission groups and in the
  invitation form, as long as no group exists yet
- **Understandable errors when starting and stopping:** instead of "Pterodactyl responded with HTTP 409 …" you now
  see e.g. "The server is busy right now" or "The panel has received too many requests right now". Owners also see the
  technical message, the audit log stays complete

**Permission system reviewed**
- Schedules and backup jobs now follow the same rule: they only run as long as their creator exists and has the
  required permissions for the server (previously schedules of a deleted creator kept running)
- Permission groups show who uses them for "All servers, including future ones"; when deleting, the confirmation warns
  that these users lose access, and the audit log records it
- Notes on permissions that allow more than their name suggests: start/download backups and restore backups (bypass
  the file permissions), download (configuration files), send commands
- Users without the owner role only see notifications in the notification settings that they can actually receive
- New tests: user who may only restart, against all other routes; creator rules; deleting a group; invitation with
  access

## 0.28.3 – 2026-10-14

- The SFTP port (2022) is no longer checked – neither in the self-test under System → Status nor in the Wings overview
  or the setup wizard. Hatchtower does not use SFTP; a closed port used to look like an error. Files, console, uploads
  and downloads still go through the Wings daemon port

## 0.28.2 – 2026-10-14

- **Update successful:** after an update via the web interface, a clear green message with the new version appears
  and the log collapses. If the update fails, the message is red
- **Wings nodes under System → Panel:** each node with address and port, connection state (connected, unreachable, not
  responding as expected), maintenance mode and SFTP port. System → Status additionally shows "x of y reachable"
- Wings nodes are checked in parallel, so an unreachable node no longer delays the pages several times over

## 0.28.1 – 2026-10-14

- `hatchtower github-token` now also accepts the token when it comes without a trailing newline (e.g. from a file or
  pipe). Previously the command aborted silently in that case; same in the Proxmox installer
- README: note in case `curl` is missing, and how to store the token non-interactively during the Proxmox installation

## 0.28.0 – 2026-10-14

- **Restore backups:** stored backups can be restored to the server directly from Hatchtower – either into a separate
  folder (`/hatchtower-restore/<date>`, nothing is overwritten) or to the original location (replaces files with the
  same name, only with confirmation). The backup file is checked first; if it is damaged, nothing happens on the
  server. Optionally the server is stopped beforehand and started again afterwards. Progress and cancel on the backup
  page, separate list "Restores"
  - New permission **"Restore backups"**. Existing permission groups do not get it automatically
- **Every backup is verified:** after writing, Hatchtower reads the whole ZIP file and checks every file against its
  checksum; only then is the backup considered finished. "Check" does the same for older backups. The list shows
  "verified", "not checked" and "restored"
- **More reliable backups:**
  - If Wings is unreachable, a server stopped before the backup is now always started again (previously it could stay
    off)
  - If not a single file can be backed up, that is an error and no empty ZIP is left behind
  - If a download aborts, there is a second attempt; files aborted midway are reported as "incomplete"
  - Retention never deletes the newest complete backup – a backup with errors does not push out the last good one
  - Missed schedules (Hatchtower was off) are caught up to 6 hours later; if a scheduled backup cannot start, a
    notification is sent
  - While a restore is running, no backups run on the same server and the file cannot be deleted
- **Update:** if a new version does not start, the automatic rollback now also resets the version number and the
  database. Previously `hatchtower update` afterwards considered the failed version installed
- **Install scripts in English:** the Proxmox installer, `get.sh`, `install.sh` and the `hatchtower update` command now
  always speak English, as do the descriptions of the systemd services and the Docker start
- **Interface:**
  - "Delete user" is in its own, separate section and mentions the alternative "Deactivate account"
  - A user's permissions appear in the order of the permission list instead of alphabetically by internal name
  - Missing English texts added ("Always", "Never", "Waiting" and more), "1 day" instead of "1 days", no more
    "entry/entries"
  - The export page mentions that backup jobs, backup destinations and webhooks are exported too
- **New README** (English and German): for beginners, with step-by-step installation, API keys, permissions, backups
  and restoring, troubleshooting and current screenshots
- Network errors when downloading single files lead to an understandable message instead of an internal error
- Hatchtower logs a warning if the database comes from a newer version

## 0.27.2 – 2026-10-14

- **Status page:** the "Incident and maintenance history" has been removed from the public status page. Besides public
  announcements it also showed detected server crashes – that does not belong on a public page. Active public
  announcements still appear at the top of the page, the uptime bars are unchanged.

## 0.27.1 – 2026-10-14

Replaces 0.27.0: the public demo mode ("View demo" on the sign-in page) has been removed completely again.

- **New setup wizard for the connection:** panel, keys and Wings are checked first and then saved – and only after
  explicit confirmation. Until then the input is kept encrypted in a draft in memory (30 minutes); the check changes
  nothing on the panel, Wings or servers (read-only requests)
  - Every step explains why it is needed; the new step "Check and save" shows what will be saved (keys only masked,
    with "before/after" when repairing), all check results and what works with them
  - Results in three levels: **Error** (saving blocked), **Warning** (works with limitations), **Optional** (only an
    extra feature or a hint) – each with concrete steps to fix it
  - Checked automatically: reachability of the panel (DNS, firewall, certificate, http/https, redirect, login in front
    of the panel), whether the selected panel really answers, swapped or foreign keys, permissions of the application
    key (nodes, servers, eggs, allocations), visible servers and partial permissions of the client key (status,
    console, files, startup variables, activity), missing endpoints of older panels, unencrypted http on the internet
  - Wings: daemon port and whether Wings really answers there; unreachable is a warning, not an error. SFTP is only
    shown as an optional hint – Hatchtower does not need it
  - At the end a **result report** with the checks, the chosen settings and next steps
- **System → Panel:** shows the saved connection and "Last checked", checks at the push of a button and opens the
  wizard to repair it or switch to another panel; the old connection stays active until saving
- **Security:** saved keys are never sent to a panel address on a different host (or from https to http), not even in
  the older panel form. Invalid requests only report field names, never the submitted values
- `hatchtower selftest` uses the same check, shows steps to fix problems and only fails on real errors
- Existing installations: no change to the database or the saved connection needed
- **Security:** SMTP and SMB passwords are also only sent to the server they were entered for. For a new host (SMTP
  also when switching to "no encryption") the password has to be entered again
- **Security:** the sign-in protection counts IPv6 addresses as a /64 network. Password reset links expire on password
  change, lockout and new email address. The CSV export of the audit log defuses formulas (=, +, -, @)
- **Permissions:** users can only enable restart after a crash for servers they are allowed to start themselves. The
  data access report no longer contains entries of earlier accounts with the same name or ID. Logos are limited to
  4096 × 4096 pixels

- **Docker Compose:** Hatchtower can now also run as a container (`docker compose up -d --build`). Keys are created in
  the volume on first start, the setup code is in the log, commands work with
  `docker compose exec hatchtower hatchtower …`. In the container, the update page shows the matching command
- **Server page with tabs:** overview, console, log archive and log are in tabs instead of one long page; links like
  `#protokoll` open the matching tab
- **Calmer header:** language and sign-out are in the account menu at your own name, the header is a single line on
  desktop. The theme switcher stays directly visible
- **Overview:** status filters without matches are hidden
- **Files:**
  - Clicking an editable file opens the editor; Rename and Delete only appear on desktop when hovering over the row
  - Programs and other binary files no longer offer "Edit"; text files with null bytes at the end (e.g.
    `steam_appid.txt`) can be edited again
- **Setup:** in the "Options" step you choose the view for users, "Simple" is the default
- New README: simple view first, comparison with subusers, guide for a reverse proxy and Pangolin
  (`docs/reverse-proxy.md`)

## 0.26.0 – 2026-10-14

- **Simple view:** for everyone who finds the full interface too much. Each server only shows status, address, password
  and large buttons to start and stop; resource usage, charts, log archive, log and multi-select are hidden, the
  console stays (with permission). Sorting, status and tag filters as well as tiles/list are available here too
  - Switch at the top right of the header ("Simple | Everything") or in the account under "View"
  - Default for users under Presentation → Appearance; owners see everything unless they choose otherwise
  - When creating a user directly, in invitation links and on the user page, the view can be preset and optionally
    locked – then the user cannot switch themselves
- **Better contrast:** faint text meets the WCAG minimum contrast (4.5:1) in all three themes, card borders and
  surfaces stand out more clearly, the accent text is darker in the light theme
- Presentation: selection fields in the "Name and design" form no longer overlap

## 0.25.1 – 2026-10-14

- **License:** Hatchtower is now licensed under the GNU AGPL-3.0. The license text is included as `LICENSE` in the
  package and under `/opt/hatchtower`, a summary is on the "About Hatchtower" page
- **Leaner package:** developer tools are no longer included; in operation only the script for the license list is
  needed from `tools/`. The update removes the old tools under `/opt/hatchtower/tools`
- README: installation with access token revised

## 0.25.0 – 2026-10-13

- **Email addresses in user management:** the user list shows the stored addresses (with "not confirmed", "no email address" or "email address missing"). On the user page, owners can add, change or remove an address – with a confirmation link or directly as confirmed
  - When creating a user directly, an address can be entered right away; it gets a confirmation link
- **Configurable email requirement:** if email is enabled, new users (created directly or via invitation) need an address – unless "Allow users without an email address" is ticked under Users
  - Individual users can be created "without email address" or switched to it; they don't need one and cannot add one themselves
  - If email is disabled, all fields for adding addresses are locked; existing addresses can still be removed
- **More understandable email errors:** instead of "Connection unexpectedly closed", Hatchtower explains whether the login was rejected (e.g. an app password is needed for Gmail), port and encryption don't match, or the sender is rejected
- **Sender address optional:** if left empty, the login address is the sender; a hint explains why other senders often don't work
- A failed confirmation link no longer shows a "pending confirmation"
- **Header:** your own name is now a button with a gear that clearly leads to "My account and settings". The mobile menu has its own account card for it; appearance and language wrap, and "Sign out" is no longer cut off

## 0.24.0 – 2026-10-13

- **Multiple notification webhooks:** under System → Notifications you can create any number of webhooks, each with a **title** and **description** (e.g. which Discord channel), its own format (Discord or JSON), its own events and optionally only for certain servers
  - Pause, test and remove individually; the previous webhook is taken over automatically
  - Also included in the data transfer (export/import)
- **Update window** (automations): new schedule action that starts a server on selected weekdays or once for a duration (30 minutes to 12 hours) so it can download game updates, and then stops it again
  - Only applies if the server is off at that moment; if it is started manually in Hatchtower during the window, it stays on
  - Requires the "Start" and "Stop" permissions
- **Setup:** new optional last step "Legal" – creates your own templates for the legal notice and privacy policy on request (they stay off until completed and enabled)
- **Legal:** clear notice that the templates are not legal advice and come without any guarantee of correctness or completeness
- **Network:** new section "Connect your own domain" (e.g. server at Hetzner, domain at Strato: A record, reverse proxy, address) with a check whether the domain points to this server; the proxy field is now called "Reverse proxy IP" so it is not confused with the domain

## 0.23.0 – 2026-10-12

- **Log per server:** on every server page, the new "Log" card shows what happened to the server – who started, stopped or restarted it and when, crashes, status changes (online/offline, also when triggered in the panel or in the game), console commands, file changes, schedules, backups and changed settings
  - Filter by type (start/stop/crashes, files, console, backups, settings and schedules), older entries can be loaded
  - Without IP addresses and without pure read accesses like "Files listed"
  - Goes back as far as the audit log (retention under System → Data), status changes 90 days
- **New permission "View log"** (General group), assignable per user and server or in permission groups. Existing users and groups do not get it automatically

## 0.22.0 – 2026-10-12

- **Menu reorganised:** the main bar now has the sections **Users**, **Presentation** and **System**, each with its own tabs
  - **Users:** users, permission groups and audit log
  - **Presentation** (what visitors and players see): appearance, status page, announcements and legal (legal notice, privacy policy)
  - **System:** status, panel, network, email, notifications, data and updates
- **Network** (new): the address of Hatchtower (previously under Email), the public address for players and reverse proxy IPs (previously under Connection) in one place
- **Appearance** has its own page, with default language and **time zone** in the "Language and time" section
- **Time zone:** every option shows the current offset from UTC, e.g. "Europe/Berlin (UTC+2)". Schedules show which time zone they run in
- **Updates** have their own tab; the new-version notice in the bar leads straight there
- **Data:** retention, export and import on one page (previously Privacy and Data transfer)
- The link to the public status page is now only in the overview, no longer additionally in the main bar
- Duplicate back links like "← System" removed; the tab bar now leads back everywhere

## 0.21.3 – 2026-10-12

- **Overview: "Select all"** – one tick selects all currently visible servers (matching the status and tag filter) for start, restart or stop at once
- **Running on public servers** (VPS, dedicated servers) hardened:
  - **Fixed:** anyone who knew a username could lock the account for everyone permanently with a few failed sign-ins. The lockout now only applies to the IP the failed attempts came from; a password change (also via `hatchtower reset-password`) lifts it
  - If Hatchtower runs behind a reverse proxy that is not configured, it shows the owner a warning. Without the entry, HTTPS (secure cookies, HSTS) and the real visitor IPs are not detected
  - Under System → Connection, networks can now also be entered as reverse proxy (e.g. `172.16.0.0/12` for Docker)
  - With `--bind 127.0.0.1`, the installer points to the reverse proxy instead of showing an unreachable address
  - README: new section "Running on a public server"
- On mobile, the selection ticks in the overview are a bit larger

## 0.21.2 – 2026-10-12

- **Fixed:** in the panel selection of the setup wizard and the mode selection (read only / read and control), the radio buttons were above the text instead of next to it
- **Fixed:** in the English overview, the status filter still said "wird gestartet / wird gestoppt"
- Tested versions for Pelican and Calagopus are easier to understand ("Panel/Wings 1.0.0-rc.1")

## 0.21.1 – 2026-10-12

- Server status renamed: "running" is now called **online** (German and English), "starting / stopping" is called **wird gestartet / wird gestoppt** in German – on the server cards, in the status filter and on the public status page

## 0.21.0 – 2026-10-12

- **Pelican and Calagopus are supported:** besides Pterodactyl, Hatchtower can now connect to [Pelican](https://pelican.dev) and [Calagopus](https://calagopus.com). One installation still manages the servers of one panel
  - In the setup wizard and under System → Connection you choose the panel; each one says where to get the API key and which permissions it needs
  - **Pelican:** same features as with Pterodactyl, keys start with `papp_` or `pacc_`
  - **Calagopus:** only one API key (admin permissions are only read), the "Client key" step is skipped. Console, uploads and downloads also work via the Calagopus Wings proxy
  - All texts name the connected panel instead of always "Pterodactyl" (e.g. "Pelican is not responding", "Status according to Calagopus")
  - The **About Hatchtower** page (footer → Licenses) shows the supported panels with the tested versions and which one is connected
- Existing installations stay connected to Pterodactyl without any action
- Switching panels: if you move your servers from Pterodactyl to Pelican or Calagopus, enter the new panel and its key under System → Connection. Servers with the same identifier (common with Pelican migrations) keep their permissions, schedules and history

## 0.20.1 – 2026-10-12

- **Crashes are now also detected when Wings restarts the server immediately by itself** (the default setting in Pterodactyl). Previously this was often missed between two polls: no Discord/email notification, no crash entry, no red bar in the uptime. Intended restarts (Hatchtower, panel, schedule, backup, console command `restart`) still don't count as crashes
- **Fixed:** backups showed German texts ("fertig", "4 von 4 Servern gesichert") in the English interface and in notifications
- **Fixed:** on mobile, server cards with long names stuck out over the edge of the screen
- README: backups and multiple Discord channels added

## 0.20.0 – 2026-10-09

- **Backups (new):** Hatchtower backs up servers or selected files and folders (e.g. save games) as ZIP – one file per server and run, with paths relative to the server folder so they can be restored elsewhere
  - Backup jobs for one, several or all servers (including future ones, owners only), with exclusions (e.g. `logs/`, `*.log`)
  - In the file explorer: tick files and folders → "Add to backup job" (existing or new job); you stay in the folder and can add more
  - Destinations: direct download in the browser (manual only), a drive in the Hatchtower container (choice of mounted drives under /mnt, /media, /srv, e.g. Proxmox mount point, USB, NFS) or an SMB share (Windows, NAS, Samba; credentials encrypted). The result and free space of the last check are shown with the destination
  - Start manually ("Run now", "Run now and download") or by schedule; multiple backups run one after another, running ones can be cancelled; progress with %, MB, files and speed
  - Stop the server beforehand (with an in-game warning) and start it again afterwards (only if it was running) or leave it off; crash restart does not treat this as a crash
  - Retention by count or days, space check in advance, notification via webhook/email (only on errors or always)
  - New permissions: "View backups", "Start and download backups", "Manage backup jobs"; only owners manage destinations
  - Spares the Pterodactyl request limit: backups always leave about a third free so status polls don't fail
- **Discord status in multiple channels:** any number of webhooks, each with its own server selection, its own details and its own heading – independent of the status page. A previously configured webhook is taken over during the update
- **Fixed:** messages after saving on pages with an anchor (e.g. System → Appearance) were not shown
- Update: new dependency `smbprotocol`; the service may additionally write to /mnt, /media and /srv (both come with `hatchtower update`)

## 0.19.2 – 2026-10-09

- **Open invitation links can be copied again:** in user management, the link of an open invitation can be retrieved and copied again via "Show link" (applies to invitations from this version on)

## 0.19.1 – 2026-10-08

- **Invitation links valid for longer:** additionally 30, 60 or 90 days and unlimited (never expires until revoked or used up)

## 0.19.0 – 2026-10-08

- **Configurable invitation links:** valid for 1, 3, 7 or 14 days; usable once, 2–10, 25, 50, 100 times or unlimited
  - Optional internal description (e.g. who the link is for), visible in the open invitations together with uses ("2/5") and creator
  - Audit log: every use with the new user, description, creator and counter; plus "Invitation used up" and "Invitation expired"
- **Note per user:** in the user details under "Account", only visible to owners, also in the user list
- **New permission "View console archive":** browsing and downloading the archive is now separate from "Read console". Anyone who could read the console so far gets the new permission automatically with the update

## 0.18.0 – 2026-10-08

- **Status in Discord:** Hatchtower posts the servers of the status page to a channel via a Discord webhook and keeps the message up to date (checked every minute, only edited on changes)
  - Separate choice of details for Discord (server name, in-game server name, status, start/stop time, address, password – password only with explicit confirmation, shown as a spoiler in the channel)
  - Compact layout: three servers per row with dividers, "X of Y servers online" at the top with coloured boxes and links to the status page and server management
  - Many servers: automatically split across several messages (up to about 150 servers); deleted messages are reposted, surplus ones removed
  - Settings under System → Status page; the webhook is stored encrypted and included in the data transfer
- **Displayed game can be overridden:** in the server settings ("Displayed game"); left empty, the egg name from Pterodactyl is used. Applies to the overview, server page, status page, Discord and the server lists in the admin area

## 0.17.0 – 2026-10-08

- **"All servers, including future ones"** per user: a permission group applies to every server without its own setting – including servers added to Pterodactyl later
  - A per-server setting always takes precedence; "No access" excludes individual servers
  - Clear warning if the group contains control or write permissions
- **New servers are reported:** notice in the overview for owners (with "Check permissions", "Status page", "Automations", "Done"), audit log entry and the "New server detected" notification with the list of users who got access automatically
- Status page and restart after crash stay off for new servers until an owner enables them

## 0.16.0 – 2026-10-08

- **New permission "Manage automations"** (per server): users can maintain schedules and restart after crash for their servers themselves – only with actions they are allowed to perform themselves. If the creator later loses the permission, the schedule no longer runs.
- **Pterodactyl outages are detected:** notice in the overview, servers show "no current data" instead of "online", the status page reports that the status cannot be queried right now; notification "Pterodactyl unreachable" / "reachable again"
- **CPU high for a long time** as a new notification (threshold in % of the limit and duration configurable)
- **Changed limits** (CPU, RAM, disk) from Pterodactyl arrive within 30 seconds at the latest (previously 5 minutes) and immediately after a start or restart
- **Faster status display:** every second after start/stop/restart, crashes of running servers immediately; the overview refreshes every 5 seconds
- **More compact status page:** uptime bar in the same row as name and address; player history removed
- Uptime bar (30 days) also in the server details
- Clearer password error message ("The password must not contain the username.")
- **Security:** the live console ends as soon as the permission is revoked or the session becomes invalid; uploads are only accepted after the permission check; the server password in the server settings is only visible with the "View password" permission
- Automated tests of the permission matrix (`tests/`) and documentation `docs/rechte-matrix.md`

## 0.15.0 – 2026-10-07

- **Status page extended:**
  - Uptime bars for the last 30 days per server (like Uptime Kuma), with percentage and legend
  - Player history of the last 24 hours as a small chart (peak and current number)
  - Incident and maintenance history: crashes and public maintenance announcements of the last 30 days
  - Overall state at the top ("All servers are online" or "3 of 8 servers online")
  - Everything can be switched on and off individually under System → Status page
- **Community Discord:** link at the top of the header and on the status page; configurable under System → Appearance
- Hatchtower now records the player count in the history

## 0.14.1 – 2026-10-07

- Update via the web interface: the result now reliably ends up in the audit log

## 0.14.0 – 2026-10-07

- **Update via the web interface:** under System → Version and updates, "Install update to …"; live progress and log, result in the audit log
  (the interface itself has no root privileges – it only triggers the `hatchtower-update` service, which runs exactly `hatchtower update`)
- **"Check for updates now"** – at most every 5 minutes
- Overview sorted by "Status: online first" by default

## 0.13.1 – 2026-10-07

- Installation: quieter output, no more locale warnings in fresh Debian containers

## 0.13.0 – 2026-10-07

- **One-command installation on Proxmox:** `proxmox/install.sh` creates a Debian container and installs the latest version from GitHub
- **Installation on Debian/Ubuntu** via `installer/get.sh` (downloads the release, verifies the SHA-256 checksum)
- **`hatchtower update`** now fetches new versions from GitHub itself; `hatchtower update --check` shows whether there is a new one
- **`hatchtower github-token`** stores a read token while the repository is private
- **Notice about new versions** under System (checked twice a day, never installs on its own)
- New versions are published via GitHub releases (`tools/release.sh`)

## 0.12.4 – 2026-10-07

- Licenses tab: summary, other components, trademarks and full license texts

## 0.12.3 – 2026-10-07

- Security: size limits for requests, additional headers, version and package list only when signed in,
  "Forgot password" without revealing existing accounts, stricter systemd sandboxing

## 0.12.0 – 2026-10-07

- Dark/Medium/Light themes switchable in the header
- Status page: started/stopped, server password with double confirmation, sorting by online status
- "In-game name" is now called "In-game server name"

## 0.11.0 – 2026-10-07

- Data transfer, status page, notifications (webhook/email), email with "Forgot password",
  invitation links, legal notice and privacy policy, configurable retention, audit export,
  announcements, one-time schedules, multi-select, file backup when editing,
  log archive, uptime, tags and favourites, delete your own account

## 0.10.0 – 2026-10-06

- German and English
