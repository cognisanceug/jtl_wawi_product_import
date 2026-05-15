#!/usr/bin/env bash
#
# Deploy-Script für das Odoo-Modul "jtl_wawi_product_import" auf den Server.
#
# Verwendung:
#   ./deploy.sh                  → Dateien syncen + Odoo neu starten
#   ./deploy.sh -u               → zusätzlich Modul-Upgrade in DB "schaltauge"
#   ./deploy.sh -u <datenbank>   → Modul-Upgrade in einer abweichenden DB
#   ./deploy.sh --dry-run        → nur anzeigen, was übertragen würde (keine Änderung)
#
set -euo pipefail

# ----------------------------------------------------------------------------
# Konfiguration
# ----------------------------------------------------------------------------
SSH_HOST="schaltauge"
MODULE="jtl_wawi_product_import"
REMOTE_ADDONS_DIR="/usr/lib/python3/dist-packages/odoo/addons"
REMOTE_OWNER="root:root"          # /usr/lib/.../addons ist ein System-Verzeichnis
ODOO_SERVICE="odoo"               # systemd-Service-Name
ODOO_CONF="/etc/odoo/odoo.conf"   # wird nur für das optionale -u Upgrade gebraucht
ODOO_DB="schaltauge"              # Default-Datenbank für 'deploy.sh -u' ohne Argument

# ----------------------------------------------------------------------------
# Argumente
# ----------------------------------------------------------------------------
DRY_RUN=""
UPDATE_DB=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN="--dry-run"; shift ;;
        -u|--update)
            # optionales DB-Argument; ohne Angabe wird $ODOO_DB verwendet
            if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
                UPDATE_DB="$2"; shift 2
            else
                UPDATE_DB="${ODOO_DB}"; shift
            fi
            ;;
        *) echo "Unbekanntes Argument: $1" >&2; exit 1 ;;
    esac
done

# ----------------------------------------------------------------------------
# Pfade lokal bestimmen
# ----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_MODULE="${SCRIPT_DIR}/${MODULE}"
REMOTE_MODULE="${REMOTE_ADDONS_DIR}/${MODULE}"

if [[ ! -f "${LOCAL_MODULE}/__manifest__.py" ]]; then
    echo "FEHLER: ${LOCAL_MODULE}/__manifest__.py nicht gefunden — falsches Verzeichnis?" >&2
    exit 1
fi

VERSION="$(grep -E '"version"' "${LOCAL_MODULE}/__manifest__.py" | head -1 | sed -E 's/.*"version"[^"]*"([^"]+)".*/\1/')"
echo "==> Deploye ${MODULE} (Version ${VERSION}) nach ${SSH_HOST}:${REMOTE_MODULE}"
[[ -n "${DRY_RUN}" ]] && echo "    (DRY-RUN — es wird nichts verändert)"

# ----------------------------------------------------------------------------
# 1) Dateien übertragen
#    --delete entfernt auf dem Server Dateien, die lokal gelöscht wurden.
#    __pycache__ / .pyc werden weder übertragen noch serverseitig gelöscht.
# ----------------------------------------------------------------------------
echo "==> rsync ..."
rsync -az --delete ${DRY_RUN} \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.DS_Store' \
    --exclude='.git' \
    "${LOCAL_MODULE}/" \
    "${SSH_HOST}:${REMOTE_MODULE}/"

if [[ -n "${DRY_RUN}" ]]; then
    echo "==> DRY-RUN beendet."
    exit 0
fi

# ----------------------------------------------------------------------------
# 2) Rechte setzen
#    Eigentümer: root:root (System-Verzeichnis), Verzeichnisse 755, Dateien 644.
#    Der Odoo-Prozess braucht hier nur Leserechte — keine Schreib-/Ausführbits.
# ----------------------------------------------------------------------------
echo "==> Rechte setzen (${REMOTE_OWNER}, dirs 755, files 644) ..."
ssh "${SSH_HOST}" "
    set -e
    chown -R ${REMOTE_OWNER} '${REMOTE_MODULE}'
    find '${REMOTE_MODULE}' -type d -exec chmod 755 {} +
    find '${REMOTE_MODULE}' -type f -exec chmod 644 {} +
"

# ----------------------------------------------------------------------------
# 3) Optionales Modul-Upgrade in der Datenbank
# ----------------------------------------------------------------------------
if [[ -n "${UPDATE_DB}" ]]; then
    echo "==> Modul-Upgrade in DB '${UPDATE_DB}' ..."
    ssh "${SSH_HOST}" "
        set -e
        systemctl stop ${ODOO_SERVICE}
        sudo -u odoo odoo -c ${ODOO_CONF} -d '${UPDATE_DB}' -u ${MODULE} --stop-after-init --no-http
        systemctl start ${ODOO_SERVICE}
    "
    echo "==> Upgrade abgeschlossen, Service läuft wieder."
else
    # ------------------------------------------------------------------------
    # 4) Nur Neustart (Code wird neu geladen; DB-Schema bleibt unverändert)
    # ------------------------------------------------------------------------
    echo "==> Odoo-Service neu starten ..."
    ssh "${SSH_HOST}" "systemctl restart ${ODOO_SERVICE}"
fi

echo "==> Fertig. Status:"
ssh "${SSH_HOST}" "systemctl is-active ${ODOO_SERVICE} && systemctl --no-pager -l status ${ODOO_SERVICE} | head -5"
