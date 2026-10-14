# Public demo

**English** · [Deutsch](public-demo.de.md)

With demo mode, visitors can look around Hatchtower without an account: the login page shows
**"Try the demo"**, and one click logs them in as a dedicated demo user.

## What the demo user can and can't do

- Sees exactly the servers and permissions you gave this user – like any other user.
- **Can't change anything**: every write request is refused (start/stop, console commands, files,
  own password, settings). The panel is treated as read-only for this user, no matter which mode
  the instance runs in.
- Owners are never affected: they log in normally and work as usual.
- The demo login never works for an owner account, even if you enter one by mistake.
- Every refused attempt lands in the audit log as "Blocked in the demo".

## Set it up

1. **Use made-up servers.** A demo is public. Console output can contain IP addresses, player names
   or tokens – use a separate panel or servers that contain nothing private.
2. As owner, create a **normal user** (not an owner), e.g. `demo`, and give it the permissions you
   want to show. Recommended: view, join password, server log, console (read), console archive,
   view files. Leave out anything that writes.
3. Enable demo mode with the user name:
   - Classic installation: add `HATCHTOWER_DEMO_USER=demo` to `/etc/hatchtower/hatchtower.env`,
     then `systemctl restart hatchtower`.
   - Docker: `HATCHTOWER_DEMO_USER: demo` under `environment` in `compose.yaml`, then
     `docker compose up -d`.
4. Open the login page – "Try the demo" appears above the normal login.

To turn it off, remove the line and restart.

## Things to keep in mind

- **Rate limit of the panel:** every open console and file listing costs requests. Pterodactyl allows
  about 128 requests per minute per API key, shared by all of Hatchtower. With many visitors,
  consider leaving out the console and files.
- The demo user's view (simple or everything) can be chosen and locked on the user page, so
  visitors see what you want to show.
- Run the demo behind HTTPS like any public Hatchtower – see [Reverse proxy and Pangolin](reverse-proxy.md).
