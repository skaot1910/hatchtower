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
#  Nur für ein privates Repository (z. B. eigener Fork): GITHUB_TOKEN setzen (wird nur bei Zustimmung gespeichert).
# =============================================================================
set -euo pipefail

REPO="${HATCHTOWER_REPO:-skaot1910/hatchtower}"

say()  { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }
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

[ "$(id -u)" -eq 0 ] || fail "Please run as root on the Proxmox host."
command -v pct >/dev/null && command -v pveam >/dev/null || fail "This is not a Proxmox VE host (pct/pveam are missing)."

printf '\n\033[1;35mHatchtower – installation on Proxmox\033[0m\n'
echo "Press Enter to accept the suggestion in brackets."
echo

# --- Vorschläge ermitteln ----------------------------------------------------------
NEXT_ID="$(pvesh get /cluster/nextid 2>/dev/null || echo 120)"
DEF_STORAGE="$(pvesm status -content rootdir 2>/dev/null | awk 'NR>1 && $3=="active" {print $1; exit}')"
[ -n "$DEF_STORAGE" ] || DEF_STORAGE="local-lvm"
TPL_STORAGE="$(pvesm status -content vztmpl 2>/dev/null | awk 'NR>1 && $3=="active" {print $1; exit}')"
[ -n "$TPL_STORAGE" ] || TPL_STORAGE="local"

ask HT_CTID     "Container ID"                              "$NEXT_ID"
ask HT_HOSTNAME "Hostname"                                  "hatchtower"
ask HT_CORES    "CPU cores"                                 "1"
ask HT_RAM      "Memory (RAM) in MB"                        "512"
ask HT_DISK     "Disk size in GB"                           "4"
ask HT_STORAGE  "Storage for the container"                 "$DEF_STORAGE"
ask HT_BRIDGE   "Network bridge"                            "vmbr0"
ask HT_IP       "IP address (dhcp or e.g. 192.168.1.50/24)" "dhcp"
if [ "$HT_IP" != "dhcp" ]; then
  ask HT_GW     "Gateway"                                   "$(ip route | awk '/^default/ {print $3; exit}')"
fi
ask HT_PORT     "Port of the web interface"                 "8000"
ask HT_ALLOW    "Only allow these IPs (comma-separated, empty = everyone)" ""
HT_VLAN="${HT_VLAN:-}"

pct status "$HT_CTID" >/dev/null 2>&1 && fail "Container $HT_CTID already exists. Please choose another ID."
[[ "$HT_CTID" =~ ^[0-9]+$ ]] || fail "The container ID must be a number."
if [ "$HT_IP" != "dhcp" ]; then
  [[ "$HT_IP" == */* ]] || fail "Please give a fixed IP with its netmask, e.g. 192.168.1.50/24"
fi

# --- Token nur für ein privates Repository (z. B. eigener Fork) ------------------------
TOKEN="${GITHUB_TOKEN:-}"
code=$(curl -s -o /dev/null -w '%{http_code}' "https://api.github.com/repos/$REPO" || echo 000)
[ "$code" = "000" ] && fail "GitHub cannot be reached. Check the internet connection and DNS of this Proxmox host."
if [ "$code" != "200" ] && [ -z "$TOKEN" ]; then
  if [ -t 0 ] && [ "${HT_YES:-0}" != "1" ]; then
    echo
    echo "The repository $REPO was not found or is private. For a private repository, enter a GitHub token with read access"
    echo "(it is not shown while typing):"
    read -rs TOKEN || true   # auch ohne Zeilenumbruch am Ende
    echo
  fi
  [ -n "$TOKEN" ] || fail "Without a token the private repository cannot be reached."
fi
SAVE_TOKEN=0
if [ -n "$TOKEN" ]; then
  if [ "${HT_SAVE_TOKEN:-}" = "1" ]; then SAVE_TOKEN=1
  elif [ -t 0 ] && [ "${HT_YES:-0}" != "1" ]; then
    read -rp "Store the token in the container so that \"hatchtower update\" works without asking? [Y/n]: " a
    case "${a:-y}" in [jJyY]*) SAVE_TOKEN=1;; esac
  fi
fi

# --- Debian-Vorlage ----------------------------------------------------------------
say "Looking for a Debian template"
pveam update >/dev/null 2>&1 || true
TEMPLATE="$(pveam available --section system 2>/dev/null | awk '{print $2}' | grep -E '^debian-13-standard_.*amd64' | sort -V | tail -1)"
[ -n "$TEMPLATE" ] || TEMPLATE="$(pveam available --section system 2>/dev/null | awk '{print $2}' | grep -E '^debian-12-standard_.*amd64' | sort -V | tail -1)"
[ -n "$TEMPLATE" ] || fail "No Debian template found (pveam available)."
if ! pveam list "$TPL_STORAGE" 2>/dev/null | grep -q "$TEMPLATE"; then
  say "Downloading template: $TEMPLATE"
  pveam download "$TPL_STORAGE" "$TEMPLATE" >/dev/null
fi

# --- Container anlegen ------------------------------------------------------------------
NET="name=eth0,bridge=$HT_BRIDGE"
if [ "$HT_IP" = "dhcp" ]; then NET="$NET,ip=dhcp"; else NET="$NET,ip=$HT_IP${HT_GW:+,gw=$HT_GW}"; fi
[ -n "$HT_VLAN" ] && NET="$NET,tag=$HT_VLAN"

say "Creating container $HT_CTID ($HT_HOSTNAME)"
pct create "$HT_CTID" "$TPL_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$HT_HOSTNAME" --cores "$HT_CORES" --memory "$HT_RAM" --swap 512 \
  --rootfs "$HT_STORAGE:$HT_DISK" --net0 "$NET" \
  --unprivileged 1 --features nesting=1 --onboot 1 \
  --description "Hatchtower – web interface for game servers (https://github.com/$REPO)" >/dev/null
pct start "$HT_CTID"

say "Waiting for the network"
for _ in $(seq 1 60); do
  pct exec "$HT_CTID" -- getent hosts api.github.com >/dev/null 2>&1 && break
  sleep 2
done
pct exec "$HT_CTID" -- getent hosts api.github.com >/dev/null 2>&1 || fail "The container cannot reach GitHub (check network/DNS)."

# Frische Debian-Vorlagen haben keine passende Sprache eingestellt – C.UTF-8 gibt es immer
CENV=(env LANG=C.UTF-8 LC_ALL=C.UTF-8 DEBIAN_FRONTEND=noninteractive)

say "Installing basic packages in the container"
pct exec "$HT_CTID" -- "${CENV[@]}" bash -c "apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends curl ca-certificates python3 >/dev/null 2>&1"

# --- Hatchtower im Container installieren ---------------------------------------------
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
AUTH=()
[ -n "$TOKEN" ] && AUTH=(-H "Authorization: Bearer $TOKEN")
curl -fsSL -H "Accept: application/vnd.github.raw" "${AUTH[@]}" \
  "https://api.github.com/repos/$REPO/contents/installer/get.sh" -o "$WORK/get.sh" \
  || fail "Could not download the install script from GitHub."
pct push "$HT_CTID" "$WORK/get.sh" /root/hatchtower-get.sh --perms 700
if [ -n "$TOKEN" ]; then
  umask 077; printf '%s' "$TOKEN" > "$WORK/token"
  pct push "$HT_CTID" "$WORK/token" /root/.hatchtower-token --perms 600
fi

ARGS=(--port "$HT_PORT")
[ -n "$HT_ALLOW" ] && ARGS+=(--allow "$HT_ALLOW")
say "Installing Hatchtower"
pct exec "$HT_CTID" -- "${CENV[@]}" HATCHTOWER_REPO="$REPO" GITHUB_TOKEN_FILE=/root/.hatchtower-token \
  HATCHTOWER_SAVE_TOKEN="$SAVE_TOKEN" bash /root/hatchtower-get.sh "${ARGS[@]}"
pct exec "$HT_CTID" -- rm -f /root/hatchtower-get.sh /root/.hatchtower-token

echo
printf '\033[1;32mDone.\033[0m Container %s is running Hatchtower.\n' "$HT_CTID"
echo "  Container console:  pct enter $HT_CTID"
echo "  Update later:       pct exec $HT_CTID -- hatchtower update"
echo
