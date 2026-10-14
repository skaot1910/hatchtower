# Security policy

Hatchtower controls game servers and stores API keys for your panel, so security reports are very welcome.

## Supported versions

Only the **latest release** gets security fixes. Please update (`hatchtower update`) before reporting.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report it privately on GitHub: **Security → Report a vulnerability** in this repository
(<https://github.com/skaot1910/hatchtower/security/advisories/new>).

Please include:

- the Hatchtower version and the panel (Pterodactyl, Pelican or Calagopus) with its version
- what an attacker needs (e.g. a normal user account, network access, nothing at all)
- steps to reproduce and what happens

Never include real API keys, passwords or the contents of `/etc/hatchtower/hatchtower.env`.

## What happens next

Hatchtower is a hobby project maintained by one person. You can expect a first answer within about a week.
Confirmed problems are fixed in a new release, and the release notes mention the fix. If you like, you will be
credited there.

## Scope

In scope: the Hatchtower web interface, its permission checks, the installer and update scripts in this repository.

Not in scope: vulnerabilities in Pterodactyl, Pelican, Calagopus or Wings themselves – please report those to the
respective project.
