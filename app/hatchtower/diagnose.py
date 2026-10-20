"""Verbindungsprüfung für Assistent, Panel-Seite und `hatchtower selftest`.

Prüft eine (noch nicht gespeicherte) Verbindung Schritt für Schritt und erklärt jedes Ergebnis:
Panel-Adresse, Panel-Art und Schnittstelle, Rechte der API-Keys, Wings-Erreichbarkeit.

Regeln:
- Es wird nur gelesen (GET bzw. TCP-Verbindungsaufbau). Der Prüf-Client läuft immer im Read-Only-Modus,
  an Panel, Wings und Servern wird nichts verändert, und gespeichert wird hier nichts.
- API-Keys erscheinen nie in Meldungen: Alles, was aus Antworten des Panels übernommen wird, läuft durch
  `redact()`. Keys gehen nur als Authorization-Header ans Panel, nie in eine URL oder ins Log.

Jedes Ergebnis hat eine Stufe:
  error     echter Fehler – so funktioniert Hatchtower nicht, Speichern ist gesperrt
  warning   funktioniert, aber etwas ist eingeschränkt oder riskant
  optional  nur für eine Zusatzfunktion wichtig oder ein Hinweis; muss nicht behoben werden
  ok        in Ordnung
"""
from __future__ import annotations

import asyncio
import dataclasses
import ipaddress
import logging
import re
import ssl
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import setup
from .i18n import Msg
from .panels import PANELS, Panel
from .ptero import PteroClient, PteroError
from . import wings

log = logging.getLogger("hatchtower.diagnose")

ERROR, WARNING, OPTIONAL, OK = "error", "warning", "optional", "ok"
LEVEL_ORDER = {ERROR: 0, WARNING: 1, OPTIONAL: 2, OK: 3}

# Bereiche in der Reihenfolge des Assistenten (Schlüssel = Schritt, in dem man es ändert)
AREAS = [("panel", "Panel-Adresse und Schnittstelle"), ("app", "Application-Key"), ("client", "Client-Key"),
         ("wings", "Wings-Server")]

# Bekannte Key-Präfixe, um vertauschte oder fremde Keys zu erkennen
_PREFIXES = {p: (panel.key, kind) for panel in PANELS.values()
             for p, kind in ((panel.app_prefix, "app"), (panel.client_prefix, "client")) if p}
_KEY_PATTERN = re.compile(r"\b(?:ptl[ac]|papp|pacc)_[A-Za-z0-9]{8,}")

PROBE_TIMEOUT = httpx.Timeout(8.0, connect=5.0)


# -- Datenmodell ---------------------------------------------------------------------------

@dataclass
class Candidate:
    """Die zu prüfende Verbindung (Werte aus dem Formular oder dem Entwurf, noch nicht gespeichert)."""
    panel_type: str
    url: str
    app_key: str = ""
    client_key: str = ""
    node_overrides: dict = field(default_factory=dict)

    @property
    def panel(self) -> Panel:
        return PANELS.get(self.panel_type) or PANELS["pterodactyl"]

    @property
    def effective_client_key(self) -> str:
        return self.app_key if self.panel.single_key else self.client_key

    def secrets(self) -> list[str]:
        return [k for k in (self.app_key, self.client_key) if k]


@dataclass
class Check:
    area: str
    level: str
    title: Msg
    detail: Msg | str = ""
    why: Msg | str = ""
    fix: list = field(default_factory=list)


@dataclass
class Report:
    panel_type: str
    url: str
    checks: list[Check] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)       # Bereiche, die wegen eines Fehlers nicht geprüft wurden
    caps: dict[str, bool | None] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    nodes: list[dict] = field(default_factory=list)
    finished: str = ""

    def add(self, area: str, level: str, title: Msg, detail: Msg | str = "", why: Msg | str = "",
            fix: list | None = None) -> Check:
        check = Check(area, level, title, detail, why, list(fix or []))
        self.checks.append(check)
        return check

    def area(self, name: str) -> list[Check]:
        return sorted((c for c in self.checks if c.area == name), key=lambda c: LEVEL_ORDER[c.level])

    def count(self, level: str, areas: tuple[str, ...] | None = None) -> int:
        return sum(1 for c in self.checks if c.level == level and (areas is None or c.area in areas))

    @property
    def errors(self) -> int:
        return self.count(ERROR)

    @property
    def warnings(self) -> int:
        return self.count(WARNING)

    @property
    def ok_to_save(self) -> bool:
        return self.errors == 0 and not self.skipped

    def has_errors(self, *areas: str) -> bool:
        return self.count(ERROR, areas) > 0

    @property
    def features(self) -> list[dict]:
        """Was mit dieser Verbindung in Hatchtower funktioniert (für den Ergebnisbericht)."""
        c = self.caps

        def state(*needed: str) -> str:
            values = [c.get(n) for n in needed]
            if any(v is False for v in values):
                return ERROR
            if any(v is None for v in values):
                return OPTIONAL
            return OK

        wings = c.get("wings")
        rows = [
            ("Server-Übersicht und Status", state("app", "status"), ""),
            ("Konsole mitlesen", state("console", "wings"),
             "braucht Wings-Erreichbarkeit" if wings is False else ""),
            ("Dateien ansehen", state("files"), ""),
            ("Dateien herunterladen und hochladen", state("files", "wings"),
             "braucht Wings-Erreichbarkeit" if wings is False else ""),
            ("Starten, Stoppen, Konsolenbefehle", OPTIONAL if self.facts.get("read_only") else state("status"),
             "nur im Modus „Lesen und steuern“ und mit passendem Recht"),
            ("Protokoll und Erkennung von Neustarts", state("activity"), ""),
            ("Startvariablen (z. B. Beitrittspasswort)", state("startup"), ""),
        ]
        # Fehlt eine Zusatzfunktion, ist das eine Einschränkung, kein Fehler der Verbindung
        result = []
        for name, level, note in rows:
            if level == ERROR and name not in ("Server-Übersicht und Status",):
                level = WARNING
            result.append({"name": name, "level": level, "note": note})
        return result


# -- Hilfen ------------------------------------------------------------------------------

def redact(text: object, secrets: list[str]) -> str:
    """Entfernt API-Keys aus einem Text, der aus einer Antwort oder Ausnahme stammt."""
    value = str(text or "")
    for secret in secrets:
        if secret and len(secret) >= 6:
            value = value.replace(secret, "•••")
    return _KEY_PATTERN.sub("•••", value)[:300]


def _root_causes(exc: BaseException) -> list[BaseException]:
    seen, chain, current = set(), [], exc
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def classify_network_error(exc: BaseException) -> str:
    """dns | refused | timeout | tls | tls_plain | reset | other"""
    chain = _root_causes(exc)
    text = " ".join(str(e) for e in chain)
    if any(isinstance(e, httpx.TimeoutException | asyncio.TimeoutError | TimeoutError) for e in chain):
        return "timeout"
    if "WRONG_VERSION_NUMBER" in text or "wrong version number" in text or "record layer failure" in text:
        return "tls_plain"
    if any(isinstance(e, ssl.SSLError) for e in chain) or "CERTIFICATE_VERIFY_FAILED" in text or "SSL" in text:
        return "tls"
    if any(isinstance(e, socket.gaierror) for e in chain) or re.search(
            r"getaddrinfo|Name or service not known|nodename nor servname|Temporary failure in name resolution|"
            r"No address associated", text):
        return "dns"
    if any(isinstance(e, ConnectionRefusedError) for e in chain) or "refused" in text.lower():
        return "refused"
    if any(isinstance(e, ConnectionResetError) for e in chain) or "reset" in text.lower():
        return "reset"
    return "other"


def _is_local(host: str) -> bool:
    if host in ("localhost",) or host.endswith((".local", ".lan", ".home", ".internal", ".localhost")):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def _probe_path(panel: Panel) -> str:
    return "/api/admin/nodes" if panel.key == "calagopus" else "/api/application/nodes"


def _json(response: httpx.Response) -> Any:
    if "json" not in response.headers.get("content-type", ""):
        return None
    try:
        return response.json()
    except ValueError:
        return None


# -- Einzelne Prüfungen --------------------------------------------------------------------

def _network_fix(kind: str, host: str, port: int | None, what: str) -> tuple[Msg, list[Msg]]:
    """Meldung und Lösungsschritte für einen Netzwerkfehler (what = Panel oder Wings)."""
    target = f"{host}:{port}" if port else host
    if kind == "dns":
        return (Msg("Der Name „{host}“ lässt sich von diesem Server aus nicht auflösen.", host=host),
                [Msg("Prüfe die Schreibweise der Adresse."),
                 Msg("Im Heimnetz statt des Namens die IP-Adresse verwenden, z. B. http://192.168.1.10."),
                 Msg("Prüfe die DNS-Einstellungen dieses Servers (z. B. /etc/resolv.conf).")])
    if kind == "refused":
        return (Msg("{what} lehnt die Verbindung ab ({target}). Unter dieser Adresse und diesem Port läuft "
                    "nichts oder eine Firewall blockt.", what=what, target=target),
                [Msg("Prüfe, ob {what} läuft.", what=what),
                 Msg("Prüfe Port und http/https in der Adresse."),
                 Msg("Gib den Port in der Firewall für die IP dieses Servers frei.")])
    if kind == "timeout":
        return (Msg("{what} antwortet nicht rechtzeitig ({target}). Meist blockt eine Firewall oder die Adresse "
                    "ist aus diesem Netz nicht erreichbar.", what=what, target=target),
                [Msg("Teste vom Hatchtower-Server aus: curl -I {url}", url=f"http://{target}"),
                 Msg("Gib den Port in der Firewall für die IP dieses Servers frei."),
                 Msg("Liegt {what} in einem anderen Netz, die interne Adresse verwenden.", what=what)])
    if kind == "tls_plain":
        return (Msg("Unter {target} läuft kein HTTPS. Die Adresse beginnt mit https://, der Dienst spricht aber "
                    "nur http.", target=target),
                [Msg("Die Adresse mit http:// statt https:// eintragen.")])
    if kind == "tls":
        return (Msg("Das HTTPS-Zertifikat von {target} wird nicht akzeptiert (abgelaufen, selbst signiert oder für "
                    "einen anderen Namen ausgestellt).", target=target),
                [Msg("Den Namen verwenden, für den das Zertifikat ausgestellt ist, nicht die IP-Adresse."),
                 Msg("Ein gültiges Zertifikat einrichten, z. B. mit Let’s Encrypt."),
                 Msg("Im eigenen Netz ohne Zertifikat die Adresse mit http:// eintragen.")])
    if kind == "reset":
        return (Msg("Die Verbindung zu {target} wurde abgebrochen.", target=target),
                [Msg("Prüfe, ob eine Firewall oder ein Reverse-Proxy die Verbindung trennt."),
                 Msg("Prüfe http/https in der Adresse.")])
    return (Msg("{what} ist nicht erreichbar ({target}).", what=what, target=target),
            [Msg("Prüfe Adresse, Port und Firewall.")])


async def check_address(report: Report, cand: Candidate, transport=None) -> bool:
    """Adresse gültig, erreichbar, und antwortet dort wirklich die API des gewählten Panels?"""
    panel = cand.panel
    url = setup.normalize_url(cand.url)
    why = Msg("Hatchtower liest Server und Nodes über die Schnittstelle (API) deines Panels. Dafür muss dieser "
              "Server das Panel unter der Adresse erreichen.")
    if url is None:
        report.add("panel", ERROR, Msg("Die Panel-Adresse ist ungültig."),
                   Msg("Erwartet wird nur die Startadresse, z. B. https://panel.example.com – ohne Pfad wie /admin."),
                   why, [Msg("Öffne das Panel im Browser und kopiere nur den Teil bis zum ersten Schrägstrich nach "
                             "dem Namen."), Msg("Die Adresse muss mit http:// oder https:// beginnen.")])
        return False
    parts = urlsplit(url)
    host, port = parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)
    report.facts["manager_ip"] = setup.manager_ip_towards(url)

    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, follow_redirects=False, transport=transport,
                                 headers={"Accept": "application/json", "User-Agent": "Hatchtower"}) as http:
        async def get(path: str) -> httpx.Response:
            return await http.get(url + path)

        try:
            response = await get(_probe_path(panel))
        except httpx.HTTPError as exc:
            kind = classify_network_error(exc)
            log.info("Panel-Prüfung: %s nicht erreichbar (%s)", host, kind)
            detail, fix = _network_fix(kind, host, port, panel.name)
            report.add("panel", ERROR, Msg("Das Panel ist nicht erreichbar."), detail, why, fix)
            return False

        status, body = response.status_code, _json(response)
        if 300 <= status < 400:
            target = urlsplit(response.headers.get("location", ""))
            where = f"{target.scheme}://{target.netloc}" if target.netloc else ""
            if where and target.hostname == host and target.scheme == "https" and parts.scheme == "http":
                report.add("panel", ERROR, Msg("Das Panel leitet auf HTTPS um."),
                           Msg("Unter http:// kommt nur eine Weiterleitung auf {where}.", where=where), why,
                           [Msg("Die Adresse mit https:// eintragen: {where}", where=where)])
            else:
                report.add("panel", ERROR, Msg("Unter dieser Adresse kommt nur eine Weiterleitung."),
                           Msg("Ziel der Weiterleitung: {where}", where=where or "?"), why,
                           [Msg("Die Adresse eintragen, unter der das Panel ohne Weiterleitung antwortet."),
                            Msg("Steht ein Login (z. B. Authelia, Cloudflare Access) vor dem Panel, für die API "
                                "eine Ausnahme einrichten oder die interne Adresse verwenden.")])
            return False
        if status >= 500:
            report.add("panel", ERROR, Msg("Das Panel meldet einen Fehler (HTTP {code}).", code=status),
                       Msg("Bei 502 und 504 erreicht ein Reverse-Proxy das Panel nicht, sonst ist im Panel selbst "
                           "etwas schiefgegangen."), why,
                       [Msg("Prüfe, ob das Panel im Browser funktioniert."),
                        Msg("Schau ins Log des Panels bzw. des Reverse-Proxys."),
                        Msg("Später noch einmal prüfen.")])
            return False
        if status == 429:
            report.add("panel", WARNING, Msg("Das Panel bremst gerade Anfragen (HTTP 429)."),
                       Msg("Zu viele Anfragen in kurzer Zeit. Die Prüfung läuft weiter, kann aber Lücken haben."),
                       why, [Msg("Eine Minute warten und erneut prüfen.")])
        elif body is None or status not in (200, 401, 403):
            # Antwortet hier ein anderes Panel? (z. B. Calagopus statt Pterodactyl)
            other = None
            for alt in PANELS.values():
                if _probe_path(alt) == _probe_path(panel):
                    continue
                try:
                    r = await get(_probe_path(alt))
                    if r.status_code in (401, 403) and _json(r) is not None:
                        other = alt
                        break
                except httpx.HTTPError:
                    pass
            if other is not None:
                names = " / ".join(p.name for p in PANELS.values() if _probe_path(p) == _probe_path(other))
                report.add("panel", ERROR, Msg("Hier antwortet ein anderes Panel: {other}.", other=names),
                           Msg("Ausgewählt ist {panel}, die Schnittstelle unter dieser Adresse passt aber zu {other}.",
                               panel=panel.name, other=names), why,
                           [Msg("Oben das richtige Panel auswählen und einen Key aus diesem Panel eintragen.")])
            elif body is None:
                report.add("panel", ERROR, Msg("Unter dieser Adresse antwortet keine Panel-Schnittstelle."),
                           Msg("Die Antwort ist eine normale Webseite (HTTP {code}), keine Antwort der {panel}-API.",
                               code=status, panel=panel.name), why,
                           [Msg("Prüfe, ob die Adresse wirklich die des Panels ist (dieselbe wie im Browser)."),
                            Msg("Steht ein Login (z. B. Authelia, Cloudflare Access) vor dem Panel, für die API "
                                "eine Ausnahme einrichten oder die interne Adresse verwenden.")])
            else:
                report.add("panel", ERROR, Msg("Unter dieser Adresse antwortet kein {panel}-Panel (HTTP {code}).",
                                               panel=panel.name, code=status),
                           "", why, [Msg("Prüfe die Adresse und die Auswahl des Panels.")])
            return False
        else:
            report.add("panel", OK, Msg("{panel} antwortet unter {url}.", panel=panel.name, url=url))

    if parts.scheme == "http" and not _is_local(host):
        report.add("panel", WARNING, Msg("Die Verbindung zum Panel ist nicht verschlüsselt."),
                   Msg("Mit http:// gehen die API-Keys im Klartext durchs Netz. Im eigenen Heimnetz ist das "
                       "vertretbar, über das Internet nicht."),
                   Msg("Wer den Verkehr mitlesen kann, könnte die Keys abgreifen und deine Server steuern."),
                   [Msg("Das Panel über https:// ansprechen."),
                    Msg("Oder Hatchtower im selben internen Netz wie das Panel betreiben und die interne Adresse "
                        "verwenden.")])
    return True


def _key_format(report: Report, area: str, key: str, cand: Candidate, kind: str, label: str) -> bool:
    """Leer, vertauscht, aus einem anderen Panel oder unvollständig kopiert?"""
    panel = cand.panel
    expected = panel.app_prefix if kind == "app" else panel.client_prefix
    why = (Msg("Der Application-Key erlaubt Hatchtower, die Liste der Server und Nodes zu lesen.")
           if kind == "app" and not panel.single_key else
           Msg("Mit dem API-Key liest Hatchtower Server und Nodes und steuert die Server.") if panel.single_key else
           Msg("Mit dem Client-Key liest Hatchtower Status, Konsole und Dateien und steuert die Server."))
    if not key:
        report.add(area, ERROR, Msg("Der {label} fehlt.", label=label), "", why,
                   [Msg("Den Key im Panel erstellen (Anleitung oben) und einfügen.")])
        return False
    found = next((v for p, v in _PREFIXES.items() if key.startswith(p)), None)
    if found and expected and found != (panel.key, kind):
        other_panel, other_kind = found
        if other_panel == panel.key:
            report.add(area, ERROR, Msg("Hier steht ein {other} statt eines {label}s.",
                                        other="Client-Key" if other_kind == "client" else "Application-Key",
                                        label=label),
                       Msg("Die beiden Keys wurden vermutlich vertauscht."), why,
                       [Msg("Den Key mit „{prefix}“ am Anfang hier eintragen.", prefix=expected)])
        else:
            report.add(area, ERROR, Msg("Der Key stammt aus einem {other}-Panel.", other=PANELS[other_panel].name),
                       Msg("Ausgewählt ist {panel}; dessen Keys beginnen mit „{prefix}“.", panel=panel.name,
                           prefix=expected), why,
                       [Msg("Oben das richtige Panel auswählen oder einen Key aus {panel} erstellen.",
                            panel=panel.name)])
        return False
    problem = setup.key_problem(key, expected)
    if problem:
        report.add(area, ERROR, Msg("Der {label} hat nicht das richtige Format.", label=label), problem, why,
                   [Msg("Den Key im Panel noch einmal vollständig kopieren, ohne Leerzeichen am Anfang oder Ende."),
                    Msg("Der Key wird im Panel nur einmal angezeigt. Ist er weg, einen neuen erstellen.")])
        return False
    return True


def _api_error(report: Report, area: str, exc: PteroError, cand: Candidate, label: str, what: Msg,
               why: Msg, permission: Msg | None = None) -> None:
    panel = cand.panel
    ip = report.facts.get("manager_ip")
    if exc.status == 401:
        report.add(area, ERROR, Msg("{panel} lehnt den {label} ab (HTTP 401).", panel=panel.name, label=label),
                   Msg("Der Key ist ungültig, gelöscht oder unvollständig kopiert."), why,
                   [Msg("Im Panel nachsehen, ob der Key noch existiert."),
                    Msg("Einen neuen Key erstellen und vollständig einfügen.")])
    elif exc.status == 403:
        fix = []
        if permission is not None:
            fix.append(permission)
        if area == "client" or panel.single_key:
            fix.append(Msg("Beim Key unter „Allowed IPs“ die Adresse dieses Servers eintragen: {ip}", ip=ip)
                       if ip else Msg("Beim Key unter „Allowed IPs“ die IP dieses Servers eintragen."))
        report.add(area, ERROR, Msg("{panel} verweigert: {what} (HTTP 403).", panel=panel.name, what=what),
                   Msg("Dem Key fehlt ein Recht, oder er ist auf andere IP-Adressen beschränkt."), why, fix)
    elif exc.status == 404:
        report.add(area, ERROR, Msg("Die Schnittstelle für „{what}“ fehlt (HTTP 404).", what=what),
                   Msg("Adresse oder Panel-Auswahl passen nicht, oder das Panel ist zu alt."), why,
                   [Msg("Panel-Auswahl und Adresse prüfen."), Msg("Das Panel auf eine aktuelle Version bringen.")])
    else:
        report.add(area, ERROR, Msg("{what} fehlgeschlagen.", what=what), redact(exc, cand.secrets()), why,
                   [Msg("Später erneut prüfen; bleibt der Fehler, ins Log des Panels schauen.")])


async def check_app(report: Report, cand: Candidate, client: PteroClient) -> list[str]:
    """Application-Key (bzw. Calagopus-Key): Nodes und Server lesen, nötige Teilrechte, Schnittstelle.
    Gibt die Kennungen einiger Server zurück (für die Prüfung des Client-Keys)."""
    panel = cand.panel
    label = "API-Key" if panel.single_key else "Application-Key"
    area = "app"
    why = (Msg("Mit dem API-Key liest Hatchtower Server und Nodes und steuert die Server.") if panel.single_key
           else Msg("Der Application-Key erlaubt Hatchtower, die Liste der Server und Nodes zu lesen. Er braucht "
                    "nur Leserechte."))
    if not _key_format(report, area, cand.app_key, cand, "app", label):
        return []
    perm = (lambda name: Msg("Beim Key das Recht {name} vergeben.", name=name)) if panel.single_key else \
        (lambda name: Msg("Beim Application-Key „{name}“ auf „Read“ stellen.", name=name))
    try:
        nodes = await client.list_nodes()
    except PteroError as exc:
        _api_error(report, area, exc, cand, label, Msg("Nodes lesen"), why,
                   perm("nodes.read" if panel.single_key else "Nodes"))
        return []
    report.facts["nodes"] = len(nodes)
    report.nodes = [{"name": n.get("name"), "fqdn": n.get("fqdn") or "", "scheme": n.get("scheme", "http")}
                    for n in nodes]

    idents: list[str] = []
    if panel.key == "calagopus":
        try:
            servers = await client.list_servers()
        except PteroError as exc:
            _api_error(report, area, exc, cand, label, Msg("Server lesen"), why, perm("servers.read"))
            return []
        report.facts["servers"] = len(servers)
        idents = [s.identifier for s in servers[:5]]
    else:
        try:
            data = await client._get("/api/application/servers", key="app",
                                     params={"per_page": 25, "include": "egg,allocations"})
        except PteroError as exc:
            _api_error(report, area, exc, cand, label, Msg("Server lesen"), why, perm("Servers"))
            return []
        items = [i.get("attributes", {}) for i in (data or {}).get("data", [])] if isinstance(data, dict) else []
        pagination = ((data or {}).get("meta") or {}).get("pagination") or {}
        report.facts["servers"] = int(pagination.get("total") or len(items))
        idents = [a.get("identifier") for a in items if a.get("identifier")][:5]
        if not isinstance(data, dict) or data.get("object") != "list" or not pagination:
            report.add("panel", WARNING, Msg("Die Antwort des Panels hat ein unerwartetes Format."),
                       Msg("Hatchtower ist mit {tested} getestet. Andere Versionen können sich anders verhalten.",
                           tested=panel.tested), "",
                       [Msg("Panel-Auswahl prüfen."), Msg("Das Panel auf eine getestete Version bringen.")])

        def missing(rel: str) -> bool:
            """Ohne Teilrecht liefert das Panel die Beziehung leer („null_resource“) oder gar nicht."""
            value = (items[0].get("relationships") or {}).get(rel)
            return not isinstance(value, dict) or value.get("object") == "null_resource"
        if items and missing("egg"):
            report.add(area, WARNING, Msg("Spielnamen (Eggs) sind für den Key nicht sichtbar."),
                       Msg("Die Übersicht zeigt dann nicht, welches Spiel auf einem Server läuft."), "",
                       [perm("Eggs")])
        if items and missing("allocations"):
            report.add(area, WARNING, Msg("Adressen und Ports der Server sind für den Key nicht sichtbar."),
                       Msg("Spieler sehen dann nicht, unter welcher Adresse sie sich verbinden."), "",
                       [perm("Allocations")])
    report.caps["app"] = True
    report.add(area, OK, Msg("{label} funktioniert: {nodes} Node(s), {servers} Server.", label=label,
                             nodes=len(nodes), servers=report.facts["servers"]),
               "" if panel.single_key else Msg("Hatchtower nutzt diesen Key nur lesend. „Read“ reicht überall."))
    if not nodes:
        report.add("wings", WARNING, Msg("Im Panel ist kein Node eingetragen."),
                   Msg("Ohne Node gibt es keine Server, die Hatchtower anzeigen kann."), "",
                   [Msg("Im Panel unter Admin → Nodes einen Node anlegen und Wings installieren.")])
    return idents


def _client_items(data: Any) -> tuple[list[str], int]:
    """Serverkennungen und Gesamtzahl aus einer Client-Serverliste (Pterodactyl/Pelican oder Calagopus)."""
    if not isinstance(data, dict):
        return [], 0
    if "servers" in data:                          # Calagopus
        block = data.get("servers") or {}
        rows = block.get("data") or []
        return [r.get("uuid_short") for r in rows if r.get("uuid_short")], int(block.get("total") or len(rows))
    rows = [i.get("attributes", {}) for i in data.get("data", [])]
    total = ((data.get("meta") or {}).get("pagination") or {}).get("total")
    return [r.get("identifier") for r in rows if r.get("identifier")], int(total or len(rows))


async def check_client(report: Report, cand: Candidate, client: PteroClient, app_idents: list[str]) -> None:
    """Client-Key: Wie viele Server sieht er, und welche Teilrechte hat er an einem Beispielserver?"""
    panel = cand.panel
    area = "app" if panel.single_key else "client"
    label = "API-Key" if panel.single_key else "Client-Key"
    why = Msg("Status, Konsole und Dateien liest Hatchtower über die Client-API; Starten und Stoppen läuft auch "
              "darüber.")
    if not panel.single_key and not _key_format(report, area, cand.client_key, cand, "client", label):
        return
    try:
        if panel.key == "calagopus":
            own = await client._get("/api/client/servers", key="client", params={"page": 1, "per_page": 25})
            other = await client._get("/api/client/servers", key="client",
                                      params={"page": 1, "per_page": 25, "other": "true"})
            a, n1 = _client_items(own)
            b, n2 = _client_items(other)
            idents, total = a + b, n1 + n2
        else:
            data = await client._get("/api/client", key="client", params={"type": "admin-all", "per_page": 25})
            idents, total = _client_items(data)
    except PteroError as exc:
        _api_error(report, area, exc, cand, label, Msg("Serverliste über die Client-API"), why)
        return
    report.facts["client_servers"] = total
    app_total = report.facts.get("servers")
    if total == 0:
        report.add(area, ERROR if app_total else WARNING, Msg("Der {label} sieht keinen einzigen Server.", label=label),
                   Msg("Der Benutzer, zu dem der Key gehört, ist weder Administrator noch Subuser eines Servers."),
                   why, [Msg("Den Benutzer im Panel zum Administrator machen (sieht dann alle Server) oder als "
                             "Subuser zu den gewünschten Servern hinzufügen.")])
        return
    if app_total and total < app_total:
        report.add(area, WARNING, Msg("Der {label} sieht nur {n} von {total} Servern.", label=label, n=total,
                                      total=app_total),
                   Msg("Die übrigen Server erscheinen in Hatchtower ohne Status und lassen sich nicht steuern."),
                   Msg("Das ist gewollt, wenn der Benutzer nur Subuser einzelner Server ist."),
                   [Msg("Den Benutzer als Subuser zu den fehlenden Servern hinzufügen oder zum Administrator machen.")])
    else:
        report.add(area, OK, Msg("{label} funktioniert: {n} Server sichtbar.", label=label, n=total))

    sample = next((i for i in idents if i in app_idents), idents[0] if idents else None)
    if not sample:
        return
    report.facts["sample"] = sample
    base = f"/api/client/servers/{sample}"
    probes = [
        ("status", base + "/resources", None, ERROR, Msg("Status und Auslastung lesen"),
         Msg("Ohne dieses Recht zeigt die Übersicht keinen Status."), Msg("Recht „Control: Console/Start“ bzw. "
                                                                         "Administrator")),
        ("console", base + "/websocket", None, WARNING, Msg("Konsole verbinden"),
         Msg("Die Konsole bleibt leer."), Msg("Recht „websocket.connect“ (Konsole)")),
        ("files", base + "/files/list", {"directory": "/"}, WARNING, Msg("Dateien auflisten"),
         Msg("Der Dateibereich funktioniert nicht."), Msg("Recht „file.read“")),
        ("startup", base + "/startup", None, OPTIONAL, Msg("Startvariablen lesen"),
         Msg("Hatchtower kann z. B. Beitrittspasswörter nicht automatisch anzeigen."), Msg("Recht „startup.read“")),
        ("activity", base + "/activity", {"per_page": 1}, OPTIONAL, Msg("Aktivität lesen"),
         Msg("Protokoll und Erkennung von Neustarts im Panel fehlen."), Msg("Recht „activity.read“")),
    ]
    if panel.key == "calagopus":
        probes[2] = ("files", base + "/files/list", {"directory": "/", "page": 1, "per_page": 1}, *probes[2][3:])
        probes[4] = ("activity", base + "/activity", {"page": 1, "per_page": 1}, *probes[4][3:])
    results = await asyncio.gather(*(client._get(path, key="client", params=params)
                                     for _, path, params, *_ in probes), return_exceptions=True)
    for (cap, _path, _params, level, what, effect, right), result in zip(probes, results):
        if not isinstance(result, BaseException):
            report.caps[cap] = True
            continue
        if not isinstance(result, PteroError):
            log.warning("Prüfung %s: %s", cap, result.__class__.__name__)
            report.caps[cap] = None
            continue
        if result.status == 409:            # Server wird gerade installiert oder ist gesperrt
            report.caps[cap] = None
            continue
        report.caps[cap] = False
        if cap == "activity" and result.status == 404:
            report.add("panel", WARNING, Msg("Das Panel ist älter als erwartet."),
                       Msg("Die Aktivitäts-Schnittstelle fehlt (ab Pterodactyl 1.8). Getestet ist Hatchtower mit "
                           "{tested}.", tested=panel.tested), "",
                       [Msg("Das Panel aktualisieren. Bis dahin fehlen Protokoll und Erkennung von Neustarts.")])
            continue
        fix = [Msg("Den Benutzer des Keys zum Administrator machen oder ihm als Subuser dieses Recht geben: {right}",
                   right=right)] if result.status == 403 else [Msg("Später erneut prüfen.")]
        report.add(area, level, Msg("{what} ist nicht möglich (HTTP {code}).", what=what, code=result.status or "–"),
                   effect, Msg("Geprüft am Server {ident}.", ident=sample), fix)
    if report.caps.get("status") and report.caps.get("console") is not False and report.caps.get("files") is not False:
        report.add(area, OK, Msg("Rechte am Beispielserver {ident}: Status, Konsole und Dateien lesbar.", ident=sample))
    if not panel.single_key:
        report.add(area, OPTIONAL, Msg("Allowed IP beim Client-Key gesetzt?"),
                   Msg("Das kann Hatchtower nicht von außen prüfen."),
                   Msg("Ein Client-Key darf alles, was sein Benutzer darf. Mit „Allowed IP“ funktioniert er nur von "
                       "diesem Server aus – der wirksamste Schutz, falls er doch einmal in falsche Hände gerät."),
                   [Msg("Beim Key unter „Allowed IPs“ die Adresse dieses Servers eintragen: {ip}",
                        ip=report.facts.get("manager_ip") or "?")])


async def _wings_http(scheme: str, host: str, port: int, transport=None) -> str:
    """Antwortet unter der Adresse wirklich Wings? ok | tls | tls_plain | other | <netzwerkfehler>"""
    url = f"{scheme}://{host}:{port}/api/system"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=False,
                                     transport=transport, headers={"Accept": "application/json"}) as http:
            response = await http.get(url)
    except httpx.HTTPError as exc:
        return classify_network_error(exc)
    return "ok" if response.status_code in (200, 401, 403) and _json(response) is not None else "other"


async def _add_wings_http(nodes: list[dict], panel_key: str, transport=None) -> None:
    """Ergänzt je Node „http“ (Antwort von Wings, siehe _wings_http) und „connected“ (Hatchtower erreicht Wings)."""
    async def http_state(n: dict) -> str:
        if panel_key == "calagopus" or not n["daemon_ok"]:
            return ""
        override = n.get("override") or ""
        scheme = urlsplit(override).scheme if "://" in override else n.get("scheme") or "http"
        return await _wings_http(scheme, n["host"], n["daemon_port"], transport)

    states = await asyncio.gather(*(http_state(n) for n in nodes))
    for n, state in zip(nodes, states):
        n["http"] = state
        n["connected"] = bool(n["daemon_ok"]) and state in ("", "ok")


async def wings_overview(client: PteroClient, overrides: dict, panel_key: str) -> list[dict]:
    """Alle Wings-Nodes mit Adresse und Verbindungsstand (nur lesend, für die Panel-Seite)."""
    nodes = await setup.node_checks(client, overrides)
    await _add_wings_http(nodes, panel_key)
    return nodes


async def check_wings(report: Report, cand: Candidate, client: PteroClient, transport=None) -> None:
    """Erreichbarkeit jedes Wings-Nodes – nur so weit, wie Hatchtower es braucht (Konsole, Dateiübertragung).
    SFTP nutzt Hatchtower nicht und prüft es deshalb auch nicht."""
    why = Msg("Konsole, Downloads und Uploads laufen direkt zwischen Hatchtower und Wings. Übersicht, Status, "
              "Starten und Stoppen gehen auch ohne.")
    try:
        nodes = await setup.node_checks(client, cand.node_overrides)
    except PteroError:
        return                                  # schon bei der Prüfung des Keys gemeldet
    report.nodes = nodes
    any_ok, any_bad = False, False
    await _add_wings_http(nodes, cand.panel.key, transport)
    for n in nodes:
        state = n["http"]
        name, target = n.get("name") or n["fqdn"], f"{n['host']}:{n['daemon_port']}"
        override_fix = Msg("Unter Wings eine andere Adresse für „{name}“ eintragen, die dieser Server erreicht "
                           "(z. B. die interne IP).", name=name)
        if n.get("maintenance"):
            report.add("wings", WARNING, Msg("Node „{name}“ ist im Wartungsmodus.", name=name),
                       Msg("Server auf diesem Node lassen sich im Panel gerade nicht steuern."), "",
                       [Msg("Wartungsmodus im Panel beenden, wenn die Arbeiten fertig sind.")])
        if not n["daemon_ok"]:
            any_bad = True
            report.add("wings", WARNING, Msg("Wings auf „{name}“ ist nicht erreichbar ({target}).", name=name,
                                             target=target),
                       Msg("Konsole, Downloads und Uploads funktionieren für Server auf diesem Node nicht."), why,
                       [override_fix, Msg("Den Port {port} in der Firewall für die IP dieses Servers freigeben.",
                                          port=n["daemon_port"]), Msg("Prüfen, ob Wings läuft: systemctl status wings")])
        elif state in ("tls", "tls_plain", "other") or state in ("dns", "refused", "timeout", "reset"):
            any_bad = True
            if state in ("tls", "tls_plain"):
                detail, fix = _network_fix(state, n["host"], n["daemon_port"], "Wings")
            else:
                detail, fix = (Msg("Der Port ist offen, aber dort antwortet nicht Wings."),
                               [override_fix, Msg("Port und http/https der anderen Adresse prüfen.")])
            report.add("wings", WARNING, Msg("Wings auf „{name}“ antwortet nicht wie erwartet.", name=name),
                       detail, why, fix)
        else:
            any_ok = True
            report.add("wings", OK, Msg("Wings auf „{name}“ ist erreichbar ({target}).", name=name, target=target))
    sample = report.facts.get("sample")
    if transport is None and sample and any_ok and report.caps.get("console"):
        await check_console_socket(report, client, sample)
    if nodes:
        report.caps["wings"] = False if any_bad and not any_ok else (True if any_ok else None)


async def check_console_socket(report: Report, client: PteroClient, sample: str) -> None:
    """Real websocket handshake with the sample server: Wings compares the Origin with `remote` in its config.yml,
    which the HTTP check above cannot see."""
    try:
        origin = await wings.probe(client, sample)
    except wings.WingsRejected as exc:
        if exc.status != 403:
            report.add("wings", WARNING, Msg("Die Konsole lässt sich nicht verbinden (HTTP {code}).", code=exc.status),
                       Msg("Die Konsole bleibt leer."), Msg("Geprüft am Server {ident}.", ident=sample))
            return
        url = client.settings.pterodactyl_url
        report.add("wings", WARNING, Msg("Wings lehnt die Konsole ab (HTTP 403)."),
                   Msg("Wings nimmt die Verbindung nur an, wenn sie von der Panel-Adresse kommt, die in seiner "
                       "config.yml unter „remote“ steht. Hatchtower hat {url} und die Adresse aus dem Panel "
                       "probiert, beide passen nicht.", url=url),
                   Msg("Die Konsole und das Konsolen-Archiv bleiben leer. Geprüft am Server {ident}.", ident=sample),
                   [Msg("In /etc/pterodactyl/config.yml von Wings ergänzen: allowed_origins: [\"{url}\"], danach "
                        "Wings neu starten.", url=url),
                    Msg("Oder in Hatchtower als Panel-Adresse genau den Wert von „remote“ aus dieser Datei eintragen.")])
        return
    except PteroError:
        return                                  # unreachable Wings is already reported above
    if origin != client.settings.pterodactyl_url:
        report.add("wings", OK, Msg("Konsole verbindet sich (Wings erwartet die Panel-Adresse {origin}).",
                                    origin=origin))
    else:
        report.add("wings", OK, Msg("Konsole verbindet sich."))


# -- Gesamtablauf ------------------------------------------------------------------------

def _client_for(settings, cand: Candidate, transport=None) -> PteroClient:
    """API-Client für die Prüfung: immer Read-Only, unabhängig vom eingestellten Modus."""
    from .panels import make_client
    temp = dataclasses.replace(settings, panel_type=cand.panel.key, pterodactyl_url=setup.normalize_url(cand.url) or "",
                               app_key=cand.app_key, client_key=cand.effective_client_key,
                               node_overrides=dict(cand.node_overrides), manager_mode="read-only")
    client = make_client(temp)
    if transport is not None:
        client._http = httpx.AsyncClient(base_url=temp.pterodactyl_url, transport=transport,
                                         headers={"Accept": "application/json"}, follow_redirects=False)
    return client


async def run(settings, cand: Candidate, *, transport=None) -> Report:
    """Prüft alles, was mit den vorhandenen Angaben prüfbar ist. Ein Fehler in einem Bereich überspringt die
    Bereiche, die darauf aufbauen (ohne erreichbares Panel kein Key-Test)."""
    report = Report(panel_type=cand.panel.key, url=cand.url)
    report.facts["read_only"] = settings.read_only
    try:
        if not await check_address(report, cand, transport):
            report.skipped = ["app", "client", "wings"]
            return report
        client = _client_for(settings, cand, transport)
        try:
            idents = await check_app(report, cand, client)
            if report.has_errors("app"):
                report.skipped = ["client", "wings"] if not cand.panel.single_key else ["wings"]
                return report
            if cand.panel.single_key or cand.client_key:
                await check_client(report, cand, client, idents)
            else:
                report.skipped.append("client")
            await check_wings(report, cand, client, transport)
        finally:
            await client.close()
    except Exception as exc:   # noqa: BLE001 – die Prüfung darf nie mit einer Fehlerseite enden
        log.warning("Verbindungsprüfung abgebrochen: %s", exc.__class__.__name__)
        report.add("panel", ERROR, Msg("Die Prüfung wurde unerwartet abgebrochen."),
                   redact(exc.__class__.__name__, cand.secrets()), "",
                   [Msg("Erneut prüfen. Bleibt der Fehler, steht mehr im Log: journalctl -u hatchtower")])
    finally:
        report.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return report


def candidate_from_settings(settings) -> Candidate:
    return Candidate(panel_type=settings.panel_type, url=settings.pterodactyl_url, app_key=settings.app_key,
                     client_key="" if settings.panel.single_key else settings.client_key,
                     node_overrides=dict(settings.node_overrides))


def summary(report: Report) -> dict:
    """Kurzfassung zum Speichern (ohne Texte, ohne Keys)."""
    return {"at": report.finished, "errors": report.errors, "warnings": report.warnings,
            "panel": report.panel_type, "nodes": report.facts.get("nodes"), "servers": report.facts.get("servers")}
