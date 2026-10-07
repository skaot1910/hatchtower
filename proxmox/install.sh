#!/usr/bin/env bash
# =============================================================================
#  Hatchtower – Installation auf Proxmox VE mit einem Befehl
#
#  Auf dem Proxmox-Host (Shell als root) ausführen. Das Skript
#    1. fragt die Eckdaten ab (jeweils mit Vorschlag, Enter übernimmt ihn),
#    2. legt einen Debian-Container (LXC, unprivilegiert) an,
#    3. lädt darin die neueste Hatchtower-Version von GitHub und installiert sie,
#    4. zeigt Adresse und Einrichtungscode an.
#
#  Ohne Rückfragen (alle Werte über Umgebungsvariablen, sonst Vorschlag):
#      HT_YES=1 HT_CTID=120 HT_IP=dhcp bash install.sh
#  Variablen: HT_CTID HT_HOSTNAME HT_CORES HT_RAM HT_DISK HT_STORAGE HT_BRIDGE
#             HT_IP (dhcp oder 192.168.1.50/24) HT_GW HT_ALLOW HT_PORT HT_VLAN
#  Privates Repository: GITHUB_TOKEN setzen (wird nur bei Zustimmung im Container gespeichert).
# =============================================================================
set -euo pipefail

REPO="${HATCHTOWER_REPO:-skaot1910/hatchtower}"

say()  { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mFehler:\033[0m %s\n' "$*" >&2; exit 1; }
ask() {  # ask VARIABLE "Frage" "Vorschlag"
  local var="$1" question="$2" default="$3" value="${!1:-}"
  if [ -z "$value" ]; then
    if [ "${HT_YES:-0}" = "1" ] || [ ! -t 0 ]; then
      value="$default"
    else
      read -rp "$question [$default]: " value
      value="${value:-$default}"
    fi
  fi
  printf -v "$var" '%s' "$value"
}

[ "$(id -u)" -eq 0 ] || fail "Bitte als root auf dem Proxmox-Host ausführen."
command -v pct >/dev/null && command -v pveam >/dev/null || fail "Das ist kein Proxmox-VE-Host (pct/pveam fehlen)."

printf '\n\033[1;35mHatchtower – Installation auf Proxmox\033[0m\n'
echo "Enter übernimmt jeweils den Vorschlag in Klammern."
echo

# --- Vorschläge ermitteln ----------------------------------------------------------
NEXT_ID="$(pvesh get /cluster/nextid 2>/dev/null || echo 120)"
DEF_STORAGE="$(pvesm status -content rootdir 2>/dev/null | awk 'NR>1 && $3=="active" {print $1; exit}')"
[ -n "$DEF_STORAGE" ] || DEF_STORAGE="local-lvm"
TPL_STORAGE="$(pvesm status -content vztmpl 2>/dev/null | awk 'NR>1 && $3=="active" {print $1; exit}')"
[ -n "$TPL_STORAGE" ] || TPL_STORAGE="local"

ask HT_CTID     "Container-ID"                              "$NEXT_ID"
ask HT_HOSTNAME "Hostname"                                  "hatchtower"
ask HT_CORES    "CPU-Kerne"                                 "1"
ask HT_RAM      "Arbeitsspeicher in MB"                     "512"
ask HT_DISK     "Speicher in GB"                            "4"
ask HT_STORAGE  "Speicherort für den Container"             "$DEF_STORAGE"
ask HT_BRIDGE   "Netzwerk-Bridge"                           "vmbr0"
ask HT_IP       "IP-Adresse (dhcp oder z. B. 192.168.1.50/24)" "dhcp"
if [ "$HT_IP" != "dhcp" ]; then
  ask HT_GW     "Gateway"                                   "$(ip route | awk '/^default/ {print $3; exit}')"
fi
ask HT_PORT     "Port der Weboberfläche"                    "8000"
ask HT_ALLOW    "Port nur für diese IPs öffnen (Komma-getrennt, leer = alle)" ""
HT_VLAN="${HT_VLAN:-}"

pct status "$HT_CTID" >/dev/null 2>&1 && fail "Container $HT_CTID gibt es schon. Bitte eine andere ID wählen."
[[ "$HT_CTID" =~ ^[0-9]+$ ]] || fail "Container-ID muss eine Zahl sein."
if [ "$HT_IP" != "dhcp" ]; then
  [[ "$HT_IP" == */* ]] || fail "Feste IP bitte mit Netzmaske angeben, z. B. 192.168.1.50/24"
fi

# --- Token für ein privates Repository ------------------------------------------------
TOKEN="${GITHUB_TOKEN:-}"
code=$(curl -s -o /dev/null -w '%{http_code}' "https://api.github.com/repos/$REPO" || echo 000)
if [ "$code" != "200" ] && [ -z "$TOKEN" ]; then
  if [ -t 0 ] && [ "${HT_YES:-0}" != "1" ]; then
    echo
    echo "Das Repository $REPO ist privat. Bitte einen GitHub-Token mit Lesezugriff eingeben"
    echo "(wird nicht angezeigt):"
    read -rs TOKEN; echo
  fi
  [ -n "$TOKEN" ] || fail "Ohne Token ist das private Repository nicht erreichbar."
fi
SAVE_TOKEN=0
if [ -n "$TOKEN" ]; then
  if [ "${HT_SAVE_TOKEN:-}" = "1" ]; then SAVE_TOKEN=1
  elif [ -t 0 ] && [ "${HT_YES:-0}" != "1" ]; then
    read -rp "Token im Container speichern, damit \"hatchtower update\" ohne Eingabe funktioniert? [J/n]: " a
    case "${a:-j}" in [jJyY]*) SAVE_TOKEN=1;; esac
  fi
fi

# --- Debian-Vorlage ----------------------------------------------------------------
say "Debian-Vorlage suchen"
pveam update >/dev/null 2>&1 || true
TEMPLATE="$(pveam available --section system 2>/dev/null | awk '{print $2}' | grep -E '^debian-13-standard_.*amd64' | sort -V | tail -1)"
[ -n "$TEMPLATE" ] || TEMPLATE="$(pveam available --section system 2>/dev/null | awk '{print $2}' | grep -E '^debian-12-standard_.*amd64' | sort -V | tail -1)"
[ -n "$TEMPLATE" ] || fail "Keine Debian-Vorlage gefunden (pveam available)."
if ! pveam list "$TPL_STORAGE" 2>/dev/null | grep -q "$TEMPLATE"; then
  say "Vorlage herunterladen: $TEMPLATE"
  pveam download "$TPL_STORAGE" "$TEMPLATE" >/dev/null
fi

# --- Container anlegen ------------------------------------------------------------------
NET="name=eth0,bridge=$HT_BRIDGE"
if [ "$HT_IP" = "dhcp" ]; then NET="$NET,ip=dhcp"; else NET="$NET,ip=$HT_IP${HT_GW:+,gw=$HT_GW}"; fi
[ -n "$HT_VLAN" ] && NET="$NET,tag=$HT_VLAN"

say "Container $HT_CTID ($HT_HOSTNAME) anlegen"
pct create "$HT_CTID" "$TPL_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$HT_HOSTNAME" --cores "$HT_CORES" --memory "$HT_RAM" --swap 512 \
  --rootfs "$HT_STORAGE:$HT_DISK" --net0 "$NET" \
  --unprivileged 1 --features nesting=1 --onboot 1 \
  --description "Hatchtower – Management und Monitoring für Pterodactyl (https://github.com/$REPO)" >/dev/null
pct start "$HT_CTID"

say "Auf Netzwerk warten"
for _ in $(seq 1 60); do
  pct exec "$HT_CTID" -- getent hosts api.github.com >/dev/null 2>&1 && break
  sleep 2
done
pct exec "$HT_CTID" -- getent hosts api.github.com >/dev/null 2>&1 || fail "Der Container erreicht GitHub nicht (Netzwerk/DNS prüfen)."

say "Grundpakete im Container installieren"
pct exec "$HT_CTID" -- bash -c "export DEBIAN_FRONTEND=noninteractive; apt-get update -q >/dev/null && apt-get install -y -q --no-install-recommends curl ca-certificates python3 >/dev/null"

# --- Hatchtower im Container installieren ---------------------------------------------
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
AUTH=()
[ -n "$TOKEN" ] && AUTH=(-H "Authorization: Bearer $TOKEN")
curl -fsSL -H "Accept: application/vnd.github.raw" "${AUTH[@]}" \
  "https://api.github.com/repos/$REPO/contents/installer/get.sh" -o "$WORK/get.sh" \
  || fail "Installationsskript konnte nicht von GitHub geladen werden."
pct push "$HT_CTID" "$WORK/get.sh" /root/hatchtower-get.sh --perms 700
if [ -n "$TOKEN" ]; then
  umask 077; printf '%s' "$TOKEN" > "$WORK/token"
  pct push "$HT_CTID" "$WORK/token" /root/.hatchtower-token --perms 600
fi

ARGS=(--port "$HT_PORT")
[ -n "$HT_ALLOW" ] && ARGS+=(--allow "$HT_ALLOW")
say "Hatchtower installieren"
pct exec "$HT_CTID" -- env HATCHTOWER_REPO="$REPO" GITHUB_TOKEN_FILE=/root/.hatchtower-token \
  HATCHTOWER_SAVE_TOKEN="$SAVE_TOKEN" bash /root/hatchtower-get.sh "${ARGS[@]}"
pct exec "$HT_CTID" -- rm -f /root/hatchtower-get.sh /root/.hatchtower-token

echo
printf '\033[1;32mFertig.\033[0m Container %s läuft mit Hatchtower.\n' "$HT_CTID"
echo "  Konsole des Containers:  pct enter $HT_CTID"
echo "  Später aktualisieren:    pct exec $HT_CTID -- hatchtower update"
echo
