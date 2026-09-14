#!/bin/bash
# Non-interactive RemnaNode runner for remna-agent panel.
# Env:
#   ACTION=install|reinstall|tune|update
#   NODE_PORT=2222
#   SECRET_KEY=...          (required for install/reinstall)
#   ADDITIONAL_PORTS=       comma-separated
#   MTU_DDOS=1|0
#   GAMING=1|0
#   SWAP=1|0
#   SWAP_SIZE=1G
#   CACHE_SIZE=1G           tmpfs size in compose
#   DISABLE_IPV6=1|0
#   USE_ORIGIN=1|0
#   ORIGIN_DOMAIN=
#   TUNE_MTU=on|off|skip
#   TUNE_GAMING=on|off|skip
#   TUNE_SWAP=on|off|skip
#   TUNE_PORTS=1|0
#   TUNE_IPV6=disable|enable|skip
#   SKIP_SYSTEM_UPDATE=1|0
#   SHAPER=1|0              шейпер очереди на 90% канала
#   TUNE_SHAPER=on|off|skip

set -euo pipefail

for _locale_candidate in C.UTF-8 C.utf8 en_US.UTF-8 en_US.utf8 C; do
  if LC_ALL="$_locale_candidate" LANG="$_locale_candidate" locale >/dev/null 2>&1; then
    export LC_ALL="$_locale_candidate"
    export LANG="$_locale_candidate"
    break
  fi
done
unset _locale_candidate

case "${EUID:-}" in
0) SUDO_CMD="" ;;
*) SUDO_CMD="sudo" ;;
esac

ACTION="${ACTION:-install}"
NODE_PORT="${NODE_PORT:-2222}"
SECRET_KEY="${SECRET_KEY:-}"
ADDITIONAL_PORTS="${ADDITIONAL_PORTS:-}"
MTU_DDOS="${MTU_DDOS:-1}"
GAMING="${GAMING:-1}"
SWAP="${SWAP:-1}"
SWAP_SIZE="${SWAP_SIZE:-1G}"
CACHE_SIZE="${CACHE_SIZE:-1G}"
DISABLE_IPV6="${DISABLE_IPV6:-1}"
USE_ORIGIN="${USE_ORIGIN:-0}"
ORIGIN_DOMAIN="${ORIGIN_DOMAIN:-}"
TUNE_MTU="${TUNE_MTU:-skip}"
TUNE_GAMING="${TUNE_GAMING:-skip}"
TUNE_SWAP="${TUNE_SWAP:-skip}"
TUNE_PORTS="${TUNE_PORTS:-0}"
TUNE_IPV6="${TUNE_IPV6:-skip}"
SKIP_SYSTEM_UPDATE="${SKIP_SYSTEM_UPDATE:-1}"
SHAPER="${SHAPER:-1}"
TUNE_SHAPER="${TUNE_SHAPER:-skip}"

log() { printf '%s\n' "$*"; }

detect_primary_iface() {
  local iface
  iface=$(ip -o -4 route show to default 2>/dev/null | awk '{print $5; exit}')
  if [ -z "$iface" ]; then
    iface=$(ip -o link show 2>/dev/null | awk -F': ' '$2 !~ /^(lo|docker|veth|br-)/ {print $2; exit}')
  fi
  [ -n "$iface" ] || return 1
  printf '%s\n' "$iface"
}

normalize_port_list() {
  local input="$1" out="" token
  input=${input//,/ }
  for token in $input; do
    token=$(printf '%s\n' "$token" | tr -d '[:space:]')
    [[ -z "$token" ]] && continue
    if [[ "$token" =~ ^[0-9]+$ ]] && [ "$token" -ge 1 ] && [ "$token" -le 65535 ]; then
      case " $out " in *" $token "*) ;; *) out="$out $token" ;; esac
    fi
  done
  printf '%s\n' "$out"
}

ensure_port_open() {
  local port="$1" proto ufw_status
  if command -v ufw >/dev/null 2>&1; then
    ufw_status=$($SUDO_CMD ufw status 2>/dev/null || true)
    if printf '%s\n' "$ufw_status" | grep -qi "Status: active"; then
      for proto in tcp udp; do
        if printf '%s\n' "$ufw_status" | grep -Eq "^[[:space:]]*${port}/${proto}[[:space:]]+ALLOW"; then
          log "→ Порт уже открыт: ${port}/${proto}"
        else
          $SUDO_CMD ufw allow "${port}/${proto}"
          log "✓ Порт открыт: ${port}/${proto}"
        fi
      done
      return
    fi
    log "→ Firewall не активен — пропускаем ufw"
    return
  fi
  if command -v firewall-cmd >/dev/null 2>&1 && $SUDO_CMD firewall-cmd --state >/dev/null 2>&1; then
    for proto in tcp udp; do
      if $SUDO_CMD firewall-cmd --query-port="${port}/${proto}" >/dev/null 2>&1; then
        log "→ Порт уже открыт: ${port}/${proto}"
      else
        $SUDO_CMD firewall-cmd --permanent --add-port="${port}/${proto}" >/dev/null
        log "✓ Порт открыт: ${port}/${proto}"
      fi
    done
    $SUDO_CMD firewall-cmd --reload >/dev/null
    return
  fi
  if command -v iptables >/dev/null 2>&1; then
    for proto in tcp udp; do
      if $SUDO_CMD iptables -C INPUT -p "$proto" --dport "$port" -j ACCEPT >/dev/null 2>&1; then
        log "→ Порт уже открыт: ${port}/${proto}"
      else
        $SUDO_CMD iptables -I INPUT -p "$proto" --dport "$port" -j ACCEPT
        log "✓ Порт открыт: ${port}/${proto}"
      fi
    done
    return
  fi
  log "→ Firewall tool не найден — порты не меняем"
}

open_node_ports() {
  local ports port
  ports=$(normalize_port_list "$1 $2")
  [ -n "${ports// /}" ] || { log "→ Нет валидных портов"; return; }
  log "→ Открываем порты…"
  for port in $ports; do ensure_port_open "$port"; done
  log "✓ Порты готовы"
}

configure_mtu_1450() {
  local iface mtu_service="/etc/systemd/system/remnanode-mtu.service"
  log "→ MTU 1450 (DDoS)…"
  iface=$(detect_primary_iface) || { log "✗ Интерфейс не найден"; return 1; }
  $SUDO_CMD ip link set dev "$iface" mtu 1450 || { log "✗ Не удалось выставить MTU"; return 1; }
  $SUDO_CMD tee "$mtu_service" > /dev/null <<EOF
[Unit]
Description=RemnaNode set MTU 1450 on $iface
After=network-pre.target
Before=network.target
DefaultDependencies=no
[Service]
Type=oneshot
ExecStart=/sbin/ip link set dev $iface mtu 1450
RemainAfterExit=yes
TimeoutStartSec=10
[Install]
WantedBy=multi-user.target
EOF
  $SUDO_CMD systemctl daemon-reload >/dev/null 2>&1 || true
  $SUDO_CMD systemctl enable remnanode-mtu.service >/dev/null 2>&1 || true
  log "✓ MTU 1450 на $iface"
}

configure_mtu_1500() {
  local iface mtu_service="/etc/systemd/system/remnanode-mtu.service"
  log "→ MTU 1500 (default)…"
  iface=$(detect_primary_iface) || { log "✗ Интерфейс не найден"; return 1; }
  if [ -f "$mtu_service" ]; then
    $SUDO_CMD systemctl disable remnanode-mtu.service >/dev/null 2>&1 || true
    $SUDO_CMD rm -f "$mtu_service"
    $SUDO_CMD systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  $SUDO_CMD ip link set dev "$iface" mtu 1500 || true
  log "✓ MTU 1500 на $iface"
}

configure_bbr_basic() {
  log "→ BBR…"
  $SUDO_CMD modprobe tcp_bbr 2>/dev/null || true
  echo tcp_bbr | $SUDO_CMD tee /etc/modules-load.d/bbr.conf > /dev/null 2>&1 || true
  $SUDO_CMD sysctl -w net.core.default_qdisc=fq >/dev/null 2>&1 || true
  $SUDO_CMD sysctl -w net.ipv4.tcp_congestion_control=bbr >/dev/null 2>&1 || true
  log "✓ BBR"
}

# Обязательная базовая оптимизация — применяется на ЛЮБОЙ ноде, независимо от
# gaming-режима. Раньше эти ключи жили только в "игровом" профиле, из-за чего
# половина парка работала на дефолтах ядра: мелкие TCP-буферы режут скорость
# на длинных плечах, а slow-start-after-idle роняет её после каждой паузы.
# Файл 99-remnanode-tuning.conf применяется ПОСЛЕ 99-remnanode-gaming.conf
# (алфавит), значения совпадают — конфликта нет.
# Шейпер очереди на 90% канала.
#
# Зачем: когда нода упирается в свой канал, очередь копится не у нас, а в
# коммутаторе хостера, где мы ей не управляем — задержка разъезжается, и клиент
# видит это как «скорость есть, а видео буферит». Если поставить cake чуть ниже
# реальной полосы, очередь переезжает к нам и остаётся короткой.
#
# Полосу НЕ угадываем: берём заявленную скорость порта, а если драйвер её не
# отдаёт (virtio почти всегда пишет -1) — меряем несколькими потоками сразу.
# Один поток упирается в маршрут до зеркала, а не в наш канал, и занижает ответ
# в разы: на живой ноде одиночная загрузка показала 72 Мбит там, где нода
# реально отдаёт 763.
measure_link_mbit() {
    local tmp i total=0 v
    tmp=$(mktemp -d)
    for i in 1 2 3 4; do
        (curl -sk -o /dev/null -w '%{speed_download}' --max-time 8 \
            https://cachefly.cachefly.net/100mb.test > "$tmp/$i" 2>/dev/null) &
    done
    wait
    for i in 1 2 3 4; do
        v=$(cat "$tmp/$i" 2>/dev/null)
        v=${v%%.*}
        [ -n "$v" ] && total=$((total + v))
    done
    rm -rf "$tmp"
    echo $((total * 8 / 1000000))
}

configure_shaper() {
    local iface port measured ceiling rate unit="/etc/systemd/system/remnanode-shaper.service"

    log "→ Шейпер очереди (90% канала)…"
    if ! iface=$(detect_primary_iface); then
        log "  интерфейс не найден — пропускаю"
        return 0
    fi

    port=$(cat "/sys/class/net/$iface/speed" 2>/dev/null || echo -1)
    case "$port" in ''|*[!0-9-]*) port=-1 ;; esac

    log "  меряю канал…"
    measured=$(measure_link_mbit)

    ceiling=0
    [ "$measured" -gt 0 ] 2>/dev/null && ceiling=$measured
    if [ "$port" -gt 0 ] 2>/dev/null; then
        # порт — верхняя граница: быстрее него нода не отдаст при всём желании
        [ "$ceiling" -eq 0 ] && ceiling=$port
        [ "$ceiling" -gt "$port" ] && ceiling=$port
    fi

    if [ "$ceiling" -lt 1 ]; then
        log "  замерить не удалось и порт не известен — шейпер НЕ ставлю"
        return 0
    fi

    rate=$((ceiling * 90 / 100))
    [ "$rate" -lt 100 ] && rate=100

    $SUDO_CMD modprobe sch_cake 2>/dev/null || true
    if ! $SUDO_CMD tc qdisc replace dev "$iface" root cake bandwidth "${rate}mbit" 2>/dev/null; then
        log "  cake недоступен в ядре — шейпер НЕ поставлен"
        return 0
    fi

    # Чтобы пережило перезагрузку. Если стоит игровой профиль, он ставит очередь
    # своим юнитом — дописываем полосу туда, иначе после ребута он вернёт
    # безлимит и настройка молча пропадёт.
    local gaming_unit="/etc/systemd/system/remnanode-gaming-qos.service"
    if [ -f "$gaming_unit" ]; then
        $SUDO_CMD sed -i "s|root cake.*|root cake bandwidth ${rate}mbit|" "$gaming_unit"
    else
        $SUDO_CMD tee "$unit" > /dev/null <<UNIT
[Unit]
Description=RemnaNode queue shaper (cake at 90% of link)
After=network-pre.target
DefaultDependencies=no

[Service]
Type=oneshot
ExecStart=/sbin/tc qdisc replace dev $iface root cake bandwidth ${rate}mbit
RemainAfterExit=yes
TimeoutStartSec=10

[Install]
WantedBy=multi-user.target
UNIT
        $SUDO_CMD systemctl enable remnanode-shaper.service >/dev/null 2>&1 || true
    fi
    $SUDO_CMD systemctl daemon-reload >/dev/null 2>&1 || true

    log "  замер ${measured} Мбит, порт ${port} Мбит → cake ${rate} Мбит"
    log "✓ Шейпер"
}

disable_shaper() {
    local iface
    log "→ Снимаю шейпер…"
    iface=$(detect_primary_iface) || return 0
    $SUDO_CMD tc qdisc change dev "$iface" root cake unlimited 2>/dev/null \
        || $SUDO_CMD tc qdisc replace dev "$iface" root cake unlimited 2>/dev/null || true
    if [ -f /etc/systemd/system/remnanode-shaper.service ]; then
        $SUDO_CMD systemctl disable remnanode-shaper.service >/dev/null 2>&1 || true
        $SUDO_CMD rm -f /etc/systemd/system/remnanode-shaper.service
    fi
    local gaming_unit="/etc/systemd/system/remnanode-gaming-qos.service"
    [ -f "$gaming_unit" ] && $SUDO_CMD sed -i "s|root cake.*|root cake|" "$gaming_unit"
    $SUDO_CMD systemctl daemon-reload >/dev/null 2>&1 || true
    log "✓ Шейпер снят"
}

configure_base_tuning() {
  local sysctl_file="/etc/sysctl.d/99-remnanode-tuning.conf"
  log "→ Базовая оптимизация ядра…"
  $SUDO_CMD modprobe tcp_bbr 2>/dev/null || true
  echo tcp_bbr | $SUDO_CMD tee /etc/modules-load.d/bbr.conf > /dev/null 2>&1 || true
  $SUDO_CMD tee "$sysctl_file" > /dev/null <<'EOF'
# Managed by remna-agent: базовая оптимизация ноды (не трогать руками)
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
# скорость не падает после пауз в трафике
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.tcp_no_metrics_save = 1
# ищем реальный MTU сами — важно за фронтами и туннелями
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_notsent_lowat = 16384
# автотюнинг буферов: это то, что реально видит VLESS/Reality/XHTTP
net.ipv4.tcp_rmem = 4096 1048576 16777216
net.ipv4.tcp_wmem = 4096 1048576 16777216
# потолки для приложений, которые просят буфер сами (QUIC/UDP)
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
# очереди и порты: сотни одновременных сессий на ноду
net.core.netdev_max_backlog = 16384
net.core.somaxconn = 8192
net.ipv4.ip_local_port_range = 10240 65535
EOF
  $SUDO_CMD sysctl -p "$sysctl_file" >/dev/null 2>&1 || true
  log "✓ Базовая оптимизация ядра"
}

# Логи на нодах — реальная причина забитых дисков: journald по умолчанию ест
# до 10% раздела, nginx-заглушка пишет строку на каждый коннект REALITY,
# а btmp растёт от переборов ssh. Аргумент: 1 — можно перезапустить docker
# (установка/переустановка), 0 — нельзя (tune на живой ноде).
configure_log_limits() {
  local allow_docker_restart="${1:-0}"
  log "→ Лимиты логов…"

  if [ -f /etc/systemd/journald.conf ]; then
    if grep -q '^[[:space:]]*SystemMaxUse=' /etc/systemd/journald.conf; then
      $SUDO_CMD sed -i 's/^[[:space:]]*SystemMaxUse=.*/SystemMaxUse=200M/' /etc/systemd/journald.conf
    else
      echo 'SystemMaxUse=200M' | $SUDO_CMD tee -a /etc/systemd/journald.conf > /dev/null
    fi
    $SUDO_CMD journalctl --vacuum-size=200M >/dev/null 2>&1 || true
    $SUDO_CMD systemctl restart systemd-journald >/dev/null 2>&1 || true
  fi

  # btmp: неудачные логины. Файл не ротируется сам и вырастает до сотен МБ.
  $SUDO_CMD truncate -s 0 /var/log/btmp 2>/dev/null || true
  $SUDO_CMD rm -f /var/log/btmp.1 2>/dev/null || true

  # nginx-заглушка: не выключаем логи совсем, но не даём им расти безгранично.
  # На части образов logrotate не стоит вообще — без него правило мёртвое.
  if [ -d /var/log/nginx ] && ! command -v logrotate >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive $SUDO_CMD apt-get install -y -qq logrotate >/dev/null 2>&1 || true
  fi
  if [ -d /etc/logrotate.d ] && [ -d /var/log/nginx ] && command -v logrotate >/dev/null 2>&1; then
    $SUDO_CMD tee /etc/logrotate.d/remnanode-nginx > /dev/null <<'EOF'
# Managed by remna-agent: заглушка пишет строку на каждый коннект
/var/log/nginx/*.log {
    size 20M
    rotate 3
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
}
EOF
  fi

  # docker: json-логи контейнера без лимита пишутся до заполнения диска
  $SUDO_CMD mkdir -p /etc/docker
  if [ ! -f /etc/docker/daemon.json ]; then
    $SUDO_CMD tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "3" }
}
EOF
    log "  /etc/docker/daemon.json: лимит логов 50m x3"
  elif ! grep -q 'max-size' /etc/docker/daemon.json; then
    # чужой daemon.json не перезаписываем — аккуратно дописываем свои ключи
    if command -v python3 >/dev/null 2>&1; then
      $SUDO_CMD python3 - <<'PY' || log "  daemon.json: не смог дописать лимит, оставил как есть"
import json
p = "/etc/docker/daemon.json"
try:
    cfg = json.load(open(p))
except Exception:
    raise SystemExit(1)
cfg.setdefault("log-driver", "json-file")
cfg.setdefault("log-opts", {}).update({"max-size": "50m", "max-file": "3"})
json.dump(cfg, open(p, "w"), indent=2)
PY
      log "  /etc/docker/daemon.json: дописан лимит логов 50m x3"
    fi
  fi

  # LimitNOFILE для самого демона: контейнеру мы даём 1048576 в compose,
  # но упереться можно и в лимит dockerd.
  $SUDO_CMD mkdir -p /etc/systemd/system/docker.service.d
  $SUDO_CMD tee /etc/systemd/system/docker.service.d/limits.conf > /dev/null <<'EOF'
[Service]
LimitNOFILE=1048576
LimitNPROC=infinity
LimitCORE=infinity
EOF
  $SUDO_CMD systemctl daemon-reload >/dev/null 2>&1 || true

  if [ "$allow_docker_restart" = "1" ] && command -v docker >/dev/null 2>&1; then
    $SUDO_CMD systemctl restart docker >/dev/null 2>&1 || true
  else
    log "  лимит docker-логов вступит в силу после перезапуска docker"
  fi
  log "✓ Лимиты логов"
}

configure_gaming_node() {
  local iface sysctl_file="/etc/sysctl.d/99-remnanode-gaming.conf"
  local cake_service="/etc/systemd/system/remnanode-gaming-qos.service"
  local thp_service="/etc/systemd/system/remnanode-disable-thp.service"
  log "→ Gaming tuning…"
  $SUDO_CMD modprobe tcp_bbr 2>/dev/null || true
  echo tcp_bbr | $SUDO_CMD tee /etc/modules-load.d/bbr.conf > /dev/null
  $SUDO_CMD tee "$sysctl_file" > /dev/null <<'EOF'
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_notsent_lowat = 16384
net.ipv4.tcp_no_metrics_save = 1
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 1048576 16777216
net.ipv4.tcp_wmem = 4096 1048576 16777216
net.core.netdev_max_backlog = 16384
net.core.somaxconn = 8192
net.ipv4.ip_forward = 1
vm.swappiness = 10
EOF
  $SUDO_CMD sysctl -p "$sysctl_file" >/dev/null 2>&1 || true
  if iface=$(detect_primary_iface); then
    command -v tc >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive $SUDO_CMD apt-get install -y iproute2 >/dev/null 2>&1 || true
    if command -v tc >/dev/null 2>&1; then
      $SUDO_CMD tc qdisc replace dev "$iface" root cake >/dev/null 2>&1 || true
      $SUDO_CMD tee "$cake_service" > /dev/null <<EOF
[Unit]
Description=RemnaNode gaming CAKE queue
After=network-pre.target
DefaultDependencies=no
[Service]
Type=oneshot
ExecStart=/sbin/tc qdisc replace dev $iface root cake
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
      $SUDO_CMD systemctl daemon-reload >/dev/null 2>&1 || true
      $SUDO_CMD systemctl enable remnanode-gaming-qos.service >/dev/null 2>&1 || true
    fi
  fi
  if command -v iptables >/dev/null 2>&1; then
    $SUDO_CMD iptables -t mangle -C OUTPUT -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360 >/dev/null 2>&1 \
      || $SUDO_CMD iptables -t mangle -A OUTPUT -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360 2>/dev/null || true
  fi
  if [ -e /sys/kernel/mm/transparent_hugepage/enabled ]; then
    echo never | $SUDO_CMD tee /sys/kernel/mm/transparent_hugepage/enabled > /dev/null 2>&1 || true
  fi
  $SUDO_CMD tee "$thp_service" > /dev/null <<'EOF'
[Unit]
Description=Disable Transparent Huge Pages for RemnaNode gaming
After=local-fs.target
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'echo never > /sys/kernel/mm/transparent_hugepage/enabled'
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
  $SUDO_CMD systemctl daemon-reload >/dev/null 2>&1 || true
  $SUDO_CMD systemctl enable remnanode-disable-thp.service >/dev/null 2>&1 || true
  log "✓ Gaming tuning"
}

disable_gaming_node() {
  local iface sysctl_file="/etc/sysctl.d/99-remnanode-gaming.conf"
  local cake_service="/etc/systemd/system/remnanode-gaming-qos.service"
  local thp_service="/etc/systemd/system/remnanode-disable-thp.service"
  log "→ Отключаем gaming…"
  $SUDO_CMD rm -f "$sysctl_file"
  $SUDO_CMD sysctl -w vm.swappiness=60 >/dev/null 2>&1 || true
  if iface=$(detect_primary_iface) && command -v tc >/dev/null 2>&1; then
    $SUDO_CMD tc qdisc del dev "$iface" root >/dev/null 2>&1 || true
    $SUDO_CMD tc qdisc replace dev "$iface" root fq >/dev/null 2>&1 || true
  fi
  if [ -f "$cake_service" ]; then
    $SUDO_CMD systemctl disable remnanode-gaming-qos.service >/dev/null 2>&1 || true
    $SUDO_CMD rm -f "$cake_service"
  fi
  if [ -f "$thp_service" ]; then
    $SUDO_CMD systemctl disable remnanode-disable-thp.service >/dev/null 2>&1 || true
    $SUDO_CMD rm -f "$thp_service"
  fi
  $SUDO_CMD systemctl daemon-reload >/dev/null 2>&1 || true
  configure_bbr_basic
  log "✓ Gaming выключен"
}

configure_swap() {
  local size="$1" file="/swapfile"
  log "→ Swap $size…"
  if ! [[ "$size" =~ ^[0-9]+[MmGg]$ ]]; then
    log "✗ Неверный размер swap: $size"
    return 1
  fi
  $SUDO_CMD swapoff "$file" 2>/dev/null || true
  $SUDO_CMD rm -f "$file"
  $SUDO_CMD fallocate -l "$size" "$file" 2>/dev/null || $SUDO_CMD dd if=/dev/zero of="$file" bs=1M count="$(echo "$size" | sed -E 's/[Gg]$/*1024/;s/[Mm]$//' | bc 2>/dev/null || echo 1024)" 2>/dev/null || true
  if [ ! -f "$file" ]; then
    # fallback create via dd for 1G
    local mb=1024
    case "$size" in
      *[Gg]|*[Gg]) mb=$(( ${size%[GgGg]} * 1024 )) ;;
      *[Mm]|*[Mm]) mb=${size%[MmMm]} ;;
    esac
    $SUDO_CMD dd if=/dev/zero of="$file" bs=1M count="$mb" status=none
  fi
  $SUDO_CMD chmod 600 "$file"
  $SUDO_CMD mkswap "$file" >/dev/null
  $SUDO_CMD swapon "$file"
  if ! grep -q "^$file " /etc/fstab 2>/dev/null; then
    echo "$file none swap sw 0 0" | $SUDO_CMD tee -a /etc/fstab >/dev/null
  fi
  log "✓ Swap $size"
}

disable_ipv6() {
  local dropin="/etc/sysctl.d/99-remnanode-ipv6.conf"
  log "→ Отключаем IPv6…"
  $SUDO_CMD tee "$dropin" > /dev/null <<'EOF'
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
EOF
  $SUDO_CMD sysctl -p "$dropin" >/dev/null 2>&1 || true
  log "✓ IPv6 отключён"
}

enable_ipv6() {
  local dropin="/etc/sysctl.d/99-remnanode-ipv6.conf"
  log "→ Включаем IPv6 (default)…"
  $SUDO_CMD rm -f "$dropin" /etc/sysctl.d/99-remnanode-disable-ipv6.conf
  $SUDO_CMD sysctl -w net.ipv6.conf.all.disable_ipv6=0 >/dev/null 2>&1 || true
  $SUDO_CMD sysctl -w net.ipv6.conf.default.disable_ipv6=0 >/dev/null 2>&1 || true
  log "✓ IPv6 включён"
}

configure_conntrack_limits() {
  # Cap nf_conntrack so small VPS don't OOM; persist across reboot.
  local sysctl_file="/etc/sysctl.d/99-remnanode-conntrack.conf"
  local modprobe_file="/etc/modprobe.d/nf_conntrack.conf"
  local unit="/etc/systemd/system/remnanode-conntrack.service"
  log "→ Лимиты nf_conntrack…"
  $SUDO_CMD modprobe nf_conntrack >/dev/null 2>&1 || true
  $SUDO_CMD sysctl -w net.netfilter.nf_conntrack_max=65536 >/dev/null 2>&1 || true
  $SUDO_CMD sysctl -w net.netfilter.nf_conntrack_tcp_timeout_established=7200 >/dev/null 2>&1 || true
  $SUDO_CMD sysctl -w net.netfilter.nf_conntrack_tcp_timeout_time_wait=30 >/dev/null 2>&1 || true
  if [ -e /sys/module/nf_conntrack/parameters/hashsize ]; then
    echo 16384 | $SUDO_CMD tee /sys/module/nf_conntrack/parameters/hashsize >/dev/null 2>&1 || true
  fi
  $SUDO_CMD tee "$sysctl_file" >/dev/null <<'EOF'
net.netfilter.nf_conntrack_max = 65536
net.netfilter.nf_conntrack_tcp_timeout_established = 7200
net.netfilter.nf_conntrack_tcp_timeout_time_wait = 30
EOF
  $SUDO_CMD sysctl -p "$sysctl_file" >/dev/null 2>&1 || true
  echo "options nf_conntrack hashsize=16384" | $SUDO_CMD tee "$modprobe_file" >/dev/null
  $SUDO_CMD tee "$unit" >/dev/null <<'EOF'
[Unit]
Description=RemnaNode nf_conntrack hashsize
After=network-pre.target
DefaultDependencies=no
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'modprobe nf_conntrack 2>/dev/null || true; if [ -e /sys/module/nf_conntrack/parameters/hashsize ]; then echo 16384 > /sys/module/nf_conntrack/parameters/hashsize; fi'
RemainAfterExit=yes
TimeoutStartSec=10
[Install]
WantedBy=multi-user.target
EOF
  $SUDO_CMD systemctl daemon-reload >/dev/null 2>&1 || true
  $SUDO_CMD systemctl enable remnanode-conntrack.service >/dev/null 2>&1 || true
  $SUDO_CMD systemctl start remnanode-conntrack.service >/dev/null 2>&1 || true
  log "✓ nf_conntrack max=65536 hashsize=16384 timeouts established=7200 time_wait=30"
}

ensure_docker() {
  if command -v docker >/dev/null 2>&1; then
    log "→ Docker уже установлен"
    $SUDO_CMD systemctl start docker 2>/dev/null || true
    $SUDO_CMD systemctl enable docker 2>/dev/null || true
    return
  fi
  log "→ Установка Docker…"
  command -v curl >/dev/null 2>&1 || { $SUDO_CMD apt-get update -y && $SUDO_CMD apt-get install -y curl; }
  curl -fsSL https://get.docker.com | $SUDO_CMD sh
  $SUDO_CMD systemctl start docker
  $SUDO_CMD systemctl enable docker
  log "✓ Docker установлен"
}

write_compose() {
  local origin_env=""
  if [ "$USE_ORIGIN" = "1" ] && [ -n "$ORIGIN_DOMAIN" ]; then
    origin_env=$(printf '\n      - ORIGIN_DOMAIN=%s' "$ORIGIN_DOMAIN")
  fi
  $SUDO_CMD mkdir -p /opt/remnanode
  $SUDO_CMD tee /opt/remnanode/docker-compose.yml > /dev/null <<EOF
version: '3.8'
services:
  remnanode:
    container_name: remnanode
    hostname: remnanode
    image: remnawave/node:latest
    network_mode: host
    restart: always
    cap_add:
      - NET_ADMIN
    ulimits:
      nofile:
        soft: 1048576
        hard: 1048576
    tmpfs:
      - /var/tmp/xray-cache:size=${CACHE_SIZE},mode=1777
    environment:
      - NODE_PORT=${NODE_PORT}
      - SECRET_KEY=${SECRET_KEY}${origin_env}
EOF
  $SUDO_CMD chmod 644 /opt/remnanode/docker-compose.yml
  log "✓ docker-compose.yml (port=${NODE_PORT}, cache=${CACHE_SIZE})"
}

start_remnanode() {
  log "→ Запуск RemnaNode…"
  cd /opt/remnanode && $SUDO_CMD docker compose up -d --force-recreate
  sleep 2
  if $SUDO_CMD docker ps --format '{{.Names}}' | grep -qx remnanode; then
    log "✓ Контейнер remnanode запущен"
  else
    log "✗ Контейнер remnanode не найден в docker ps"
    exit 1
  fi
}

clean_remnanode() {
  log "→ Чистая переустановка: удаляем старые данные…"
  if [ -f /opt/remnanode/docker-compose.yml ]; then
    (cd /opt/remnanode && $SUDO_CMD docker compose down -v --remove-orphans) 2>/dev/null || true
  fi
  $SUDO_CMD docker rm -f remnanode 2>/dev/null || true
  $SUDO_CMD rm -rf /opt/remnanode
  log "✓ Старые данные удалены"
}

do_host_tuning_for_install() {
  configure_conntrack_limits || true
  configure_base_tuning || true
  configure_log_limits 1 || true
  if [ "$SHAPER" = "1" ]; then configure_shaper || true; fi
  if [ "$SWAP" = "1" ]; then configure_swap "$SWAP_SIZE" || true; fi
  if [ "$MTU_DDOS" = "1" ]; then configure_mtu_1450 || true; else configure_mtu_1500 || true; fi
  if [ "$DISABLE_IPV6" = "1" ]; then disable_ipv6 || true; else enable_ipv6 || true; fi
  if [ "$GAMING" = "1" ]; then configure_gaming_node || true; else configure_bbr_basic || true; fi
  open_node_ports "$NODE_PORT" "$ADDITIONAL_PORTS"
}

do_install() {
  if [ -z "$SECRET_KEY" ]; then
    log "✗ SECRET_KEY пустой"
    exit 1
  fi
  log "=== Установка RemnaNode (ACTION=$ACTION) ==="
  if [ "$SKIP_SYSTEM_UPDATE" != "1" ]; then
    log "→ apt update/upgrade…"
    $SUDO_CMD apt-get update -y && DEBIAN_FRONTEND=noninteractive $SUDO_CMD apt-get upgrade -y || true
  else
    log "→ Пропуск apt upgrade"
  fi
  ensure_docker
  do_host_tuning_for_install
  if [ "$ACTION" = "reinstall" ]; then
    clean_remnanode
  fi
  write_compose
  start_remnanode
  log "=== Готово ==="
}

do_update() {
  # https://docs.rw/install/upgrading — Remnawave Node
  log "=== Обновление RemnaNode ==="
  if [ ! -d /opt/remnanode ]; then
    log "✗ Каталог /opt/remnanode не найден — сначала установите RemnaNode"
    exit 1
  fi
  if [ ! -f /opt/remnanode/docker-compose.yml ]; then
    log "✗ Нет /opt/remnanode/docker-compose.yml"
    exit 1
  fi
  ensure_docker
  configure_base_tuning || true
  configure_log_limits 1 || true
  cd /opt/remnanode
  log "→ docker compose pull…"
  $SUDO_CMD docker compose pull
  log "→ docker compose down…"
  $SUDO_CMD docker compose down
  log "→ docker compose up -d…"
  $SUDO_CMD docker compose up -d
  log "→ Статус контейнера:"
  $SUDO_CMD docker compose ps || true
  log "=== Готово ==="
}

do_tune() {
  log "=== Настройка параметров хоста ==="
  configure_conntrack_limits || true
  configure_base_tuning || true
  configure_log_limits 0 || true
  case "$TUNE_MTU" in
    on) configure_mtu_1450 || true ;;
    off) configure_mtu_1500 || true ;;
    *) log "→ MTU: skip" ;;
  esac
  case "$TUNE_GAMING" in
    on) configure_gaming_node || true ;;
    off) disable_gaming_node || true ;;
    *) log "→ Gaming: skip" ;;
  esac
  case "$TUNE_SWAP" in
    on) configure_swap "$SWAP_SIZE" || true ;;
    off) log "→ Swap off: оставляем как есть (безопасный skip)" ;;
    *) log "→ Swap: skip" ;;
  esac
  case "$TUNE_SHAPER" in
    on) configure_shaper || true ;;
    off) disable_shaper || true ;;
    *) log "→ Шейпер: skip" ;;
  esac
  case "$TUNE_IPV6" in
    disable) disable_ipv6 || true ;;
    enable) enable_ipv6 || true ;;
    *) log "→ IPv6: skip" ;;
  esac
  if [ "$TUNE_PORTS" = "1" ]; then
    local port="$NODE_PORT"
    if [ -z "$port" ] || [ "$port" = "2222" ]; then
      if [ -f /opt/remnanode/docker-compose.yml ]; then
        port=$(awk -F= '/NODE_PORT=/{gsub(/[[:space:]]/,"",$2); print $2; exit}' /opt/remnanode/docker-compose.yml)
        port=${port:-2222}
      fi
    fi
    open_node_ports "$port" "$ADDITIONAL_PORTS"
  else
    log "→ Ports: skip"
  fi
  log "=== Готово ==="
}

case "$ACTION" in
  install|reinstall) do_install ;;
  update) do_update ;;
  tune) do_tune ;;
  *) log "✗ Неизвестный ACTION=$ACTION"; exit 1 ;;
esac
