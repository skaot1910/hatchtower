# Reverse proxy and Pangolin

**English** · [Deutsch](reverse-proxy.de.md)

How to make Hatchtower reachable from the internet while your panel stays at home.

## Why the panel itself is hard to proxy

When you open a server in Pterodactyl, Pelican or Calagopus, the **browser talks to Wings
directly**, not only to the panel:

- the console is a websocket to `wss://<wings>:8080`,
- file uploads and downloads use signed links that point to Wings,
- Wings needs its own valid HTTPS certificate for that.

If you publish only the panel (e.g. as a Pangolin resource), the page loads but the console stays
empty, buttons don't react and file transfers fail – because your friends' browsers can't reach
Wings. To fix it you would have to publish every Wings node too, with its own domain and
certificate.

**Hatchtower avoids this:** it talks to the panel and Wings from inside your network and passes the
console (as a normal HTTP stream) and files through itself. From outside you publish exactly
**one** HTTP service: Hatchtower. Panel and Wings stay private.

```
friends ──HTTPS──▶ reverse proxy / Pangolin ──HTTP──▶ Hatchtower ──▶ panel + Wings (home network only)
```

## Pangolin

1. Install Hatchtower in your home network (LXC, existing server or Docker – see the README).
2. In Pangolin, create a **resource** of type HTTP with a domain such as `games.example.com`.
3. Target: the site with the Newt connector in your home network, `http://<hatchtower-ip>:8000`.
4. Optional: Pangolin's own authentication in front. Hatchtower has its own login, so this is not
   required – with it, friends have to log in twice.
5. Open `https://games.example.com`. As long as the proxy isn't configured, owners see a yellow
   banner in Hatchtower: *"Hatchtower receives requests through a reverse proxy (IP) that is not
   configured."* That IP is the address Hatchtower sees as the sender – with Pangolin usually the
   machine running **Newt**.
6. Enter exactly that IP under **System → Network → Reverse proxy IP**. The banner disappears.

Only after step 6 does Hatchtower detect HTTPS (secure cookies, HSTS) and see the real visitor IPs
(audit log, lockout after failed logins). Without it, a stranger with ten failed logins locks
everyone out.

## Caddy, Nginx, Traefik

Caddy (certificate is fetched automatically):

```
games.example.com {
  reverse_proxy 192.168.1.50:8000
}
```

Nginx:

```nginx
server {
  listen 443 ssl;
  server_name games.example.com;
  # ssl_certificate … / ssl_certificate_key …

  client_max_body_size 201m;          # file uploads up to 200 MB

  location / {
    proxy_pass http://192.168.1.50:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;              # live console (server-sent events)
    proxy_read_timeout 1h;
  }
}
```

Traefik and Nginx Proxy Manager work the same way: one HTTP target, no websocket configuration
needed. Then enter the proxy's IP under **System → Network** (step 6 above).

## Checklist

- [ ] Hatchtower is the only thing published; panel and Wings ports are **not** forwarded
- [ ] HTTPS works (`https://…`) and the proxy IP is registered in Hatchtower (no yellow banner)
- [ ] Hatchtower's port is only reachable by the proxy (bind to `127.0.0.1`, firewall or `--allow`)
- [ ] In the panel, the client key's *Allowed IP* is set to Hatchtower's address
- [ ] Friends get an invite link instead of a password
- [ ] Optional: friends get the **simple view** (Presentation → Appearance, or per user)

## Troubleshooting

| Symptom | Cause |
|---|---|
| Yellow banner "reverse proxy … not configured" | Proxy IP missing under System → Network |
| Console says "connecting …" forever | The proxy buffers responses – Nginx: `proxy_buffering off` |
| Upload fails with 413 | Upload limit of the proxy – Nginx: `client_max_body_size 201m` |
| Everyone is locked out after a few failed logins | Proxy IP not registered, all visitors share one IP |
| Console or files fail although the page loads | Hatchtower can't reach Wings (port `8080`) – run `hatchtower selftest` |
