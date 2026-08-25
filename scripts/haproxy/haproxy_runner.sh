#!/bin/bash
# Install / apply / control HAProxy on a Debian/Ubuntu node.
# Env:
#   ACTION=install|apply|reload|start|stop|bbr
#   FORCE=1              overwrite config / stop nginx|caddy on bind port
#   HAPROXY_CFG_SRC=     uploaded config to install/apply
#   BIND_PORT=           used only for conflict check (install)
# install/apply always enable BBR+fq (sysctl + persist). ACTION=bbr — только это.

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

ACTION="${ACTION:-install}"
FORCE="${FORCE:-0}"
CFG_SRC="${HAPROXY_CFG_SRC:-}"
CFG_DST="/etc/haproxy/haproxy.cfg"
BIND_PORT="${BIND_PORT:-}"

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

haproxy_bin() {
  if command -v haproxy >/dev/null 2>&1; then
    command -v haproxy
    return 0
  fi
  if [ -x /usr/sbin/haproxy ]; then
    printf '%s\n' /usr/sbin/haproxy
    return 0
  fi
  return 1
}

validate_cfg() {
  local bin
  bin=$(haproxy_bin) || {
    log "✗ haproxy не найден"
    return 1
  }
  log "→ haproxy -c -f $CFG_DST"
  run "$bin" -c -f "$CFG_DST"
}

apply_cfg() {
  [ -n "$CFG_SRC" ] && [ -f "$CFG_SRC" ] || {
    log "✗ Нет файла конфига с панели"
    return 1
  }
  run mkdir -p /etc/haproxy /run/haproxy
  if [ -f "$CFG_DST" ]; then
    run cp -a "$CFG_DST" "${CFG_DST}.bak"
    log "✓ бэкап ${CFG_DST}.bak"
  fi
  run cp "$CFG_SRC" "$CFG_DST"
  run chmod 644 "$CFG_DST"
  # HAProxy 2.2+ : last line must end with LF (https://github.com/haproxy/haproxy/issues/704)
  run sed -i 's/\r$//' "$CFG_DST"
  if [ -n "$(tail -c 1 "$CFG_DST")" ]; then
    printf '\n' | run tee -a "$CFG_DST" >/dev/null
    log "→ дописал LF в конец конфига"
  fi
  if ! validate_cfg; then
    if [ -f "${CFG_DST}.bak" ]; then
      run cp -a "${CFG_DST}.bak" "$CFG_DST"
      log "→ вернул предыдущий конфиг"
    fi
    return 1
  fi
  log "✓ конфиг валиден"
}

maybe_free_ports() {
  local raw="${BIND_PORTS:-$BIND_PORT}"
  local p
  raw="${raw//,/ }"
  for p in $raw; do
    maybe_free_port "$p" || return 1
  done
}

maybe_free_port() {
  local port="$1"
  [ -n "$port" ] || return 0
  local holders
  holders=$(ss -lntp 2>/dev/null | awk -v p=":${port}" '
    $0 ~ p {
      if ($0 ~ /haproxy/) next
      print
    }
  ' || true)
  if [ -z "$holders" ]; then
    return 0
  fi
  log "⚠ порт ${port} занят:"
  printf '%s\n' "$holders"
  if [ "$FORCE" != "1" ]; then
    log "✗ Освободите порт или поставьте FORCE=1 (остановит nginx/caddy на этом порту)"
    return 1
  fi
  if printf '%s' "$holders" | grep -q nginx; then
    log "→ FORCE: останавливаю nginx"
    run systemctl disable --now nginx 2>/dev/null || true
  fi
  if printf '%s' "$holders" | grep -q caddy; then
    log "→ FORCE: останавливаю caddy"
    run systemctl disable --now caddy 2>/dev/null || true
  fi
}

install_pkg() {
  if ! command -v apt-get >/dev/null 2>&1; then
    log "✗ Нужен apt-get (Debian/Ubuntu)"
    return 1
  fi
  if haproxy_bin >/dev/null 2>&1 && [ "$FORCE" != "1" ]; then
    log "✓ пакет haproxy уже стоит — apt пропускаю"
    return 0
  fi
  wait_apt || return 1
  run apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=3 update -y -qq || {
    wait_apt || true
    run apt-get -o DPkg::Lock::Timeout=120 update -y -qq
  }
  wait_apt || return 1
  log "→ apt-get install haproxy"
  run apt-get -o DPkg::Lock::Timeout=120 install -y haproxy
}

ensure_runtime_dir() {
  run mkdir -p /run/haproxy
  if id haproxy >/dev/null 2>&1; then
    run chown haproxy:haproxy /run/haproxy 2>/dev/null || true
  fi
}

configure_bbr() {
  log "→ BBR + fq"
  run modprobe tcp_bbr 2>/dev/null || true
  printf '%s\n' tcp_bbr | run tee /etc/modules-load.d/bbr.conf >/dev/null
  printf '%s\n' \
    "net.core.default_qdisc = fq" \
    "net.ipv4.tcp_congestion_control = bbr" \
    | run tee /etc/sysctl.d/99-haproxy-bbr.conf >/dev/null
  run sysctl -w net.core.default_qdisc=fq >/dev/null 2>&1 || true
  run sysctl -w net.ipv4.tcp_congestion_control=bbr >/dev/null 2>&1 || true
  run sysctl -p /etc/sysctl.d/99-haproxy-bbr.conf >/dev/null 2>&1 || true
  local cc qdisc
  cc=$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || echo "?")
  qdisc=$(sysctl -n net.core.default_qdisc 2>/dev/null || echo "?")
  if [ "$cc" = "bbr" ] && [ "$qdisc" = "fq" ]; then
    log "✓ BBR+fq (cc=$cc qdisc=$qdisc)"
    return 0
  fi
  log "⚠ BBR+fq не активен (cc=$cc qdisc=$qdisc) — нет tcp_bbr в ядре?"
  return 1
}

case "$ACTION" in
install)
  install_pkg
  maybe_free_ports
  ensure_runtime_dir
  if [ -n "$CFG_SRC" ]; then
    apply_cfg
  elif [ ! -f "$CFG_DST" ]; then
    log "✗ Нет $CFG_DST и нет конфига с панели"
    exit 1
  else
    log "✓ оставляю существующий $CFG_DST"
    validate_cfg
  fi
  log "→ systemctl enable --now haproxy"
  run systemctl enable haproxy
  if systemctl is-active --quiet haproxy; then
    run systemctl reload haproxy
    log "✓ haproxy reload"
  else
    run systemctl start haproxy
    log "✓ haproxy start"
  fi
  configure_bbr || true
  ;;
apply)
  [ -n "$CFG_SRC" ] || {
    log "✗ apply без конфига"
    exit 1
  }
  haproxy_bin >/dev/null || {
    log "✗ HAProxy не установлен — сначала install"
    exit 1
  }
  maybe_free_ports
  ensure_runtime_dir
  apply_cfg
  if systemctl is-active --quiet haproxy; then
    log "→ systemctl reload haproxy"
    run systemctl reload haproxy
  else
    log "→ systemctl enable --now haproxy"
    run systemctl enable --now haproxy
  fi
  configure_bbr || true
  log "✓ конфиг применён"
  ;;
reload)
  haproxy_bin >/dev/null || {
    log "✗ HAProxy не установлен"
    exit 1
  }
  validate_cfg
  run systemctl reload haproxy
  log "✓ reload"
  ;;
start)
  ensure_runtime_dir
  run systemctl enable --now haproxy
  log "✓ start"
  ;;
stop)
  run systemctl stop haproxy
  log "✓ stop"
  ;;
bbr)
  configure_bbr
  ;;
*)
  log "✗ Неизвестное ACTION=$ACTION"
  exit 1
  ;;
esac

sleep 0.4
if bin=$(haproxy_bin); then
  log "✓ $($bin -v 2>&1 | head -n1)"
fi
systemctl is-active haproxy >/dev/null 2>&1 && log "✓ service: active" || log "⚠ service: $(systemctl is-active haproxy 2>/dev/null || echo unknown)"
ss -lntp 2>/dev/null | grep -F haproxy || true
log "Готово"
