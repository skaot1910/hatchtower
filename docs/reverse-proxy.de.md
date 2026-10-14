# Reverse-Proxy und Pangolin

[English](reverse-proxy.md) · **Deutsch**

So machst du Hatchtower aus dem Internet erreichbar, während dein Panel zu Hause bleibt.

## Warum sich das Panel selbst schlecht durchreichen lässt

Öffnest du in Pterodactyl, Pelican oder Calagopus einen Server, spricht der **Browser direkt mit
Wings**, nicht nur mit dem Panel:

- die Konsole ist ein Websocket zu `wss://<wings>:8080`,
- Datei-Uploads und -Downloads laufen über signierte Links, die auf Wings zeigen,
- Wings braucht dafür ein eigenes gültiges HTTPS-Zertifikat.

Veröffentlichst du nur das Panel (z. B. als Pangolin-Ressource), lädt die Seite, aber die Konsole
bleibt leer, Knöpfe reagieren nicht und Dateiübertragungen brechen ab – weil die Browser deiner
Freunde Wings nicht erreichen. Abhilfe wäre, jeden Wings-Node ebenfalls zu veröffentlichen, mit
eigener Domain und eigenem Zertifikat.

**Hatchtower umgeht das:** Es spricht von innen mit Panel und Wings und reicht Konsole (als normalen
HTTP-Stream) und Dateien selbst durch. Von außen veröffentlichst du genau **einen** HTTP-Dienst:
Hatchtower. Panel und Wings bleiben privat.

```
Freunde ──HTTPS──▶ Reverse-Proxy / Pangolin ──HTTP──▶ Hatchtower ──▶ Panel + Wings (nur Heimnetz)
```

## Pangolin

1. Hatchtower im Heimnetz installieren (LXC, vorhandener Server oder Docker – siehe README).
2. In Pangolin eine **Ressource** vom Typ HTTP anlegen, mit einer Domain wie `games.example.com`.
3. Ziel: der Standort mit dem Newt-Connector im Heimnetz, `http://<hatchtower-ip>:8000`.
4. Optional: Pangolins eigene Anmeldung davor. Hatchtower hat ein eigenes Login, nötig ist das also
   nicht – mit ihr müssen sich Freunde zweimal anmelden.
5. `https://games.example.com` öffnen. Solange der Proxy nicht eingetragen ist, sehen Owner in
   Hatchtower einen gelben Hinweis: *„Hatchtower bekommt Anfragen über einen Reverse-Proxy (IP), der
   nicht eingetragen ist.“* Diese IP ist die Adresse, die Hatchtower als Absender sieht – bei Pangolin
   meist der Rechner, auf dem **Newt** läuft.
6. Genau diese IP unter **System → Netzwerk → IP des Reverse-Proxys** eintragen. Der Hinweis
   verschwindet.

Erst nach Schritt 6 erkennt Hatchtower HTTPS (geschützte Cookies, HSTS) und die echten
Besucher-IPs (Audit-Log, Sperre nach Fehlversuchen). Ohne ihn sperrt ein Fremder mit zehn
Fehlversuchen alle aus.

## Caddy, Nginx, Traefik

Caddy (holt das Zertifikat selbst):

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

  client_max_body_size 201m;          # Datei-Uploads bis 200 MB

  location / {
    proxy_pass http://192.168.1.50:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;              # Live-Konsole (Server-Sent Events)
    proxy_read_timeout 1h;
  }
}
```

Traefik und Nginx Proxy Manager funktionieren genauso: ein HTTP-Ziel, keine
Websocket-Einstellungen nötig. Danach die IP des Proxys unter **System → Netzwerk** eintragen
(Schritt 6 oben).

## Checkliste

- [ ] Nur Hatchtower ist veröffentlicht; Ports von Panel und Wings sind **nicht** weitergeleitet
- [ ] HTTPS funktioniert (`https://…`) und die Proxy-IP ist in Hatchtower eingetragen (kein gelber Hinweis)
- [ ] Der Port von Hatchtower ist nur für den Proxy erreichbar (`127.0.0.1`, Firewall oder `--allow`)
- [ ] Im Panel ist beim Client-Key *Allowed IP* auf die Adresse von Hatchtower gesetzt
- [ ] Freunde bekommen einen Einladungslink statt eines Passworts
- [ ] Optional: Freunde bekommen die **einfache Ansicht** (Auftritt → Erscheinungsbild oder pro Benutzer)

## Fehlersuche

| Symptom | Ursache |
|---|---|
| Gelber Hinweis „Reverse-Proxy … nicht eingetragen“ | Proxy-IP fehlt unter System → Netzwerk |
| Konsole bleibt bei „verbinde …“ | Der Proxy puffert Antworten – Nginx: `proxy_buffering off` |
| Upload scheitert mit 413 | Upload-Grenze des Proxys – Nginx: `client_max_body_size 201m` |
| Nach ein paar Fehlversuchen sind alle ausgesperrt | Proxy-IP nicht eingetragen, alle Besucher teilen sich eine IP |
| Konsole oder Dateien gehen nicht, obwohl die Seite lädt | Hatchtower erreicht Wings nicht (Port `8080`) – `hatchtower selftest` ausführen |
