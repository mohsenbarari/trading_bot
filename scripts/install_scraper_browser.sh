#!/usr/bin/env bash
#
# Installs the browser used by the Telegram Selenium scraper.
#
# We install chrome-headless-shell (not full Chrome, not Firefox, not Edge) —
# see docs/TELEGRAM_SCRAPER_BROWSER_SELECTION.md for the measurements behind
# that choice. Both artifacts come from Google's official Chrome for Testing
# bucket, pinned to one version so the browser and the driver can never drift
# apart (a mismatched chromedriver is the usual cause of "session not created").
#
# Usage:
#   scripts/install_scraper_browser.sh                 # install to /opt/chrome-for-testing
#   SCRAPER_BROWSER_DIR=~/.cache/cft scripts/install_scraper_browser.sh
#   scripts/install_scraper_browser.sh --check         # verify an existing install

set -euo pipefail

CFT_VERSION="${CFT_VERSION:-147.0.7727.24}"
INSTALL_DIR="${SCRAPER_BROWSER_DIR:-/opt/chrome-for-testing}"
BASE_URL="https://storage.googleapis.com/chrome-for-testing-public/${CFT_VERSION}/linux64"

# sha256 of the official archives for the pinned version above. If you bump
# CFT_VERSION you must refresh these — the script refuses to install otherwise.
SHA256_SHELL="6f97e9ea2bf6a3345a00e2e8e33d002563a4baaaa47d0d0e441fac6875fbd3ed"
SHA256_DRIVER="caa45d8e6a91c6dd7a2a4de844608e474868f31ecba42828f9257cf821dc8c45"

SHELL_BIN="${INSTALL_DIR}/chrome-headless-shell-linux64/chrome-headless-shell"
DRIVER_BIN="${INSTALL_DIR}/chromedriver-linux64/chromedriver"

log() { echo "[install-scraper-browser] $*"; }
die() { echo "[install-scraper-browser] ERROR: $*" >&2; exit 1; }

# Shared libraries chrome-headless-shell needs that are not always present on a
# minimal server image. Reported, never auto-installed — installing distro
# packages is the operator's call.
check_libs() {
  local missing=()
  local out
  out="$(ldd "$SHELL_BIN" 2>/dev/null || true)"
  while read -r line; do
    [[ "$line" == *"not found"* ]] && missing+=("${line%% *}")
  done <<<"$out"

  if ((${#missing[@]})); then
    log "missing shared libraries: ${missing[*]}"
    log "on Debian/Ubuntu install them with:"
    log "  sudo apt-get install -y libnss3 libatk1.0-0 libatk-bridge2.0-0 \\"
    log "      libgbm1 libasound2t64 libxkbcommon0 libxcomposite1 libxdamage1 \\"
    log "      libxfixes3 libxrandr2 libdrm2 libxext6 libx11-6"
    return 1
  fi
  log "shared libraries: OK"
  return 0
}

verify() {
  [[ -x "$SHELL_BIN" ]] || die "not installed: $SHELL_BIN"
  [[ -x "$DRIVER_BIN" ]] || die "not installed: $DRIVER_BIN"

  local sv dv
  sv="$("$SHELL_BIN" --version 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+){3}' || true)"
  dv="$("$DRIVER_BIN" --version 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+){3}' | head -1 || true)"
  log "chrome-headless-shell: ${sv:-unknown}"
  log "chromedriver:          ${dv:-unknown}"

  [[ -n "$sv" && -n "$dv" ]] || die "could not read versions"
  # Only the major version has to match for chromedriver to drive the browser.
  [[ "${sv%%.*}" == "${dv%%.*}" ]] \
    || die "major version mismatch: browser ${sv} vs driver ${dv}"
  log "browser/driver versions are compatible"

  check_libs || die "install the packages above, then re-run with --check"
  log "OK — install is usable"
}

fetch() {
  local name="$1" want="$2" dest="$3"
  local url="${BASE_URL}/${name}"

  if [[ -f "$dest" ]] && echo "${want}  ${dest}" | sha256sum -c - >/dev/null 2>&1; then
    log "${name}: already downloaded, checksum OK"
    return
  fi

  log "downloading ${url}"
  curl --fail --location --show-error --silent --max-time 900 -o "$dest" "$url" \
    || die "download failed: ${url}"

  echo "${want}  ${dest}" | sha256sum -c - >/dev/null 2>&1 \
    || die "checksum mismatch for ${name} (expected ${want}, got $(sha256sum "$dest" | awk '{print $1}')).
       Refuse to install. If you changed CFT_VERSION, update the SHA256_* values in this script."
  log "${name}: checksum verified"
}

main() {
  if [[ "${1:-}" == "--check" ]]; then
    verify
    return
  fi

  command -v curl >/dev/null || die "curl is required"
  command -v unzip >/dev/null || die "unzip is required"
  command -v sha256sum >/dev/null || die "sha256sum is required"

  mkdir -p "${INSTALL_DIR}/dl"
  log "installing Chrome for Testing ${CFT_VERSION} into ${INSTALL_DIR}"

  fetch "chrome-headless-shell-linux64.zip" "$SHA256_SHELL" \
        "${INSTALL_DIR}/dl/chrome-headless-shell-linux64.zip"
  fetch "chromedriver-linux64.zip" "$SHA256_DRIVER" \
        "${INSTALL_DIR}/dl/chromedriver-linux64.zip"

  unzip -q -o "${INSTALL_DIR}/dl/chrome-headless-shell-linux64.zip" -d "$INSTALL_DIR"
  unzip -q -o "${INSTALL_DIR}/dl/chromedriver-linux64.zip" -d "$INSTALL_DIR"
  chmod +x "$SHELL_BIN" "$DRIVER_BIN"

  # The archives are only needed again if the install is re-verified offline.
  if [[ "${SCRAPER_BROWSER_KEEP_ARCHIVES:-0}" != "1" ]]; then
    rm -rf "${INSTALL_DIR}/dl"
    log "removed downloaded archives (set SCRAPER_BROWSER_KEEP_ARCHIVES=1 to keep)"
  fi

  verify
  log "done. Point the scraper at it with:"
  log "  export SCRAPER_BROWSER_DIR=${INSTALL_DIR}"
}

main "$@"
