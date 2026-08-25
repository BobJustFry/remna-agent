#!/bin/bash
# Install Cloudflare WARP as a host WireGuard iface named "warp" (wgcf + wg-quick).
# Intended for Xray freedom + sockopt.interface = "warp" — NOT a default route.
# Env:
#   FORCE=1     recreate iface even if warp already exists
#   WGCF_VERSION=  pin wgcf (empty = latest GitHub release)

set -euo pipefail

for _locale_candidate in C.UTF-8 C.utf8 en_US.UTF-8 en_US.utf8 C; do
  if LC_ALL="$_locale_candidate" LANG="$_locale_candidate" locale >/dev/null 2>&1; then
    export LC_ALL="$_locale_candidate"
    export LANG="$_locale_candidate"
    break
  fi
done
unset _locale_candidate

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_SUSPEND=1
export NEEDRESTART_MODE=l

case "${EUID:-}" in
0) SUDO_CMD="" ;;
*) SUDO_CMD="sudo" ;;
esac

FORCE="${FORCE:-0}"
WGCF_VERSION="${WGCF_VERSION:-}"
WGCF_BUNDLE="${WGCF_BUNDLE:-}"
WORKDIR="/opt/warp-wgcf"
CONF="/etc/wireguard/warp.conf"

log() { printf '%s\n' "$*"; }

run() {
  if [ -n "$SUDO_CMD" ]; then
    # shellcheck disable=SC2086
    $SUDO_CMD "$@"
  else
    "$@"
  fi
}

apt_locked() {
  for f in /var/lib/apt/lists/lock /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock; do
    [ -e "$f" ] || continue
    if command -v fuser >/dev/null 2>&1 && fuser "$f" >/dev/null 2>&1; then
      return 0
    fi
    if command -v lsof >/dev/null 2>&1 && lsof "$f" >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

wait_apt() {
  local i
  for i in $(seq 1 90); do
    if ! apt_locked; then
      return 0
    fi
    if [ "$i" = 1 ] || [ $((i % 6)) -eq 0 ]; then
      log "→ apt занят другим процессом, жду… (${i}/90)"
    fi
    sleep 2
  done
  log "✗ apt так и не освободился (unattended-upgrades / другой apt-get)"
  return 1
}

apt_install() {
  wait_apt || return 1
  run apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=3 update -y -qq || {
    wait_apt || true
    run apt-get -o DPkg::Lock::Timeout=120 update -y -qq
  }
  wait_apt || return 1
  run apt-get -o DPkg::Lock::Timeout=120 install -y -qq \
    wireguard wireguard-tools curl ca-certificates iproute2
}

iface_exists() {
  [ -d /sys/class/net/warp ]
}

if iface_exists && [ "$FORCE" != "1" ]; then
  log "✓ Интерфейс warp уже есть — пропуск (FORCE=1 для переустановки)"
  ip -br link show warp || true
  ip -4 -br addr show warp || true
  exit 0
fi

if ! command -v apt-get >/dev/null 2>&1; then
  log "✗ Нужен apt-get (Debian/Ubuntu)"
  exit 1
fi

if command -v curl >/dev/null 2>&1 && command -v wg >/dev/null 2>&1 && command -v wg-quick >/dev/null 2>&1; then
  log "✓ wireguard-tools и curl уже стоят — apt пропускаю"
else
  log "→ Пакеты: wireguard-tools curl ca-certificates"
  apt_install
fi
modprobe wireguard 2>/dev/null || true

arch=$(uname -m)
case "$arch" in
  x86_64 | amd64) goarch=amd64 ;;
  aarch64 | arm64) goarch=arm64 ;;
  *)
    log "✗ Архитектура не поддерживается: $arch"
    exit 1
    ;;
esac

wgcf_bin=/usr/local/bin/wgcf

install_wgcf_from_bundle() {
  [ -n "$WGCF_BUNDLE" ] && [ -f "$WGCF_BUNDLE" ] || return 1
  run install -m 0755 "$WGCF_BUNDLE" "$wgcf_bin"
}

github_ip() {
  nslookup github.com 1.1.1.1 2>/dev/null | awk '/^Address: / && $2 !~ /#/ {print $2; exit}'
}

curl_github() {
  local url="$1" dest="$2"
  if curl -fsSL --retry 2 --connect-timeout 15 -o "$dest" "$url"; then
    return 0
  fi
  local ip
  ip=$(github_ip || true)
  [ -n "$ip" ] || return 1
  log "→ GitHub через 1.1.1.1 / $ip"
  curl -fsSL --retry 2 --connect-timeout 15 --resolve "github.com:443:${ip}" -o "$dest" "$url"
}

if install_wgcf_from_bundle; then
  log "✓ wgcf установлен с панели"
else
  WGCF_VERSION="${WGCF_VERSION#v}"
  [ -n "$WGCF_VERSION" ] || {
    log "✗ Нет WGCF_VERSION и нет файла с панели"
    exit 1
  }
  url="https://github.com/ViRb3/wgcf/releases/download/v${WGCF_VERSION}/wgcf_${WGCF_VERSION}_linux_${goarch}"
  log "→ Скачивание wgcf v${WGCF_VERSION} (${goarch}) с ноды"
  tmp=$(mktemp)
  if ! curl_github "$url" "$tmp"; then
    rm -f "$tmp"
    log "✗ Не удалось скачать $url (DNS github.com?)"
    exit 1
  fi
  run install -m 0755 "$tmp" "$wgcf_bin"
  rm -f "$tmp"
fi
log "✓ $($wgcf_bin version 2>/dev/null || $wgcf_bin --version 2>/dev/null || echo wgcf)"

run mkdir -p "$WORKDIR"
ver_stamp="${WGCF_VERSION#v}"
if [ -z "$ver_stamp" ]; then
  ver_stamp=$("$wgcf_bin" version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)
fi
if [ -n "$ver_stamp" ]; then
  printf '%s\n' "$ver_stamp" | run tee "$WORKDIR/version" >/dev/null
  log "✓ версия записана: $ver_stamp"
fi
cd "$WORKDIR"

if [ -n "${WGCF_ACCOUNT:-}" ] && [ -f "$WGCF_ACCOUNT" ]; then
  run cp "$WGCF_ACCOUNT" "$WORKDIR/wgcf-account.toml"
  log "✓ аккаунт WARP с панели"
fi
if [ -n "${WGCF_PROFILE:-}" ] && [ -f "$WGCF_PROFILE" ]; then
  run cp "$WGCF_PROFILE" "$WORKDIR/wgcf-profile.conf"
  log "✓ профиль WireGuard с панели"
fi

wgcf_register() {
  # Никогда не звать `wgcf register` без --accept-tos: там интерактивное TUI («Do you agree?»).
  if command -v timeout >/dev/null 2>&1; then
    timeout 45 "$wgcf_bin" register --accept-tos
  else
    "$wgcf_bin" register --accept-tos
  fi
}

if [ ! -f "$WORKDIR/wgcf-account.toml" ]; then
  log "→ Регистрация WARP на ноде (анонимно)…"
  ok=0
  i=1
  while [ "$i" -le 2 ]; do
    if wgcf_register; then
      ok=1
      break
    fi
    log "⚠ register не прошёл (попытка ${i}/2)"
    i=$((i + 1))
    sleep 3
  done
  if [ "$ok" != 1 ]; then
    rm -f "$WORKDIR/wgcf-account.toml"
    log "✗ wgcf register с ноды: api.cloudflareclient.com недоступен (типично для РФ)."
    log "  Нужна регистрация на панели — обновите API и повторите установку."
    exit 1
  fi
else
  log "✓ Аккаунт wgcf уже есть — повторная регистрация не нужна"
fi

if [ -f "$WORKDIR/wgcf-profile.conf" ]; then
  log "✓ профиль уже есть — generate пропускаю"
else
  log "→ Генерация WireGuard-профиля"
  "$wgcf_bin" generate
fi

src="$WORKDIR/wgcf-profile.conf"
[ -f "$src" ] || {
  log "✗ Нет $src"
  exit 1
}

log "→ Сборка $CONF (Table=off, без DNS hijack, без default route)"
tmp_conf=$(mktemp)
in_iface=0
while IFS= read -r line || [ -n "$line" ]; do
  s=$(printf '%s' "$line" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
  case "$s" in
    \[interface\]) in_iface=1 ;;
    \[*\]) in_iface=0 ;;
  esac
  case "$s" in
    dns=* | table=* | mtu=* ) continue ;;
  esac
  if [ "$in_iface" = 1 ]; then
    case "$s" in
      address=*)
        ipv4=$(printf '%s' "$line" | sed 's/.*=//' | tr ',' '\n' | tr -d ' ' | grep -E '^[0-9.]+/' | head -n1)
        [ -n "$ipv4" ] && printf 'Address = %s\n' "$ipv4"
        continue
        ;;
    esac
  fi
  if [ "$s" = "[peer]" ]; then
    printf '%s\n' "Table = off"
    printf '%s\n' "MTU = 1280"
  fi
  printf '%s\n' "$line"
done < "$src" > "$tmp_conf"
if ! grep -qi '^PersistentKeepalive' "$tmp_conf"; then
  printf '%s\n' "PersistentKeepalive = 25" >> "$tmp_conf"
fi
run cp "$tmp_conf" "$CONF"
rm -f "$tmp_conf"
run chmod 600 "$CONF"

if iface_exists; then
  log "→ Останавливаю существующий warp"
  run wg-quick down warp 2>/dev/null || true
  run systemctl stop wg-quick@warp 2>/dev/null || true
fi

log "→ systemctl enable --now wg-quick@warp"
run systemctl enable wg-quick@warp
run systemctl restart wg-quick@warp

sleep 1
if ! iface_exists; then
  log "✗ Интерфейс warp не появился"
  run systemctl status wg-quick@warp --no-pager -l || true
  exit 1
fi

log "✓ warp поднят"
ip -br link show warp || true
ip -4 -br addr show warp || true
log "Готово: Xray outbound freedom + sockopt.interface=warp"
