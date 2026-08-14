#!/bin/bash
 
# Скрипт установщика для Ubuntu
# Настройки интерфейса, установка Docker и дополнительного софта

# Pick an installed locale to avoid setlocale / perl warnings on minimal VPS images.
for _locale_candidate in C.UTF-8 C.utf8 en_US.UTF-8 en_US.utf8 C; do
    if LC_ALL="$_locale_candidate" LANG="$_locale_candidate" locale >/dev/null 2>&1; then
        export LC_ALL="$_locale_candidate"
        export LANG="$_locale_candidate"
        break
    fi
done
unset _locale_candidate

set -e  # Остановить скрипт при ошибке

INSTALLER_VERSION="20260810-locale-menu"

# Определение команды для sudo
case "$EUID" in
0)
   SUDO_CMD=""
;;
*)
   SUDO_CMD="sudo"
;;
esac

echo "Choose language / Выберите язык:"
echo "1. English"
echo "2. Русский"
read -r lang

# Инициализация переменных
skip_update=false
skip_docker=false
skip_mtu=false
additional_ports_input=""
install_mode="1"
clean_install=false
reconfigure_only=false
gaming_node=false
swap_configured=false
mtu_configured=false
mtu_default_restored=false
ipv6_disable=true

case "$lang" in
1)
    MSG_NODE_PORT="Enter NODE_PORT (default 2222):"
    MSG_SECRET="Enter SECRET_KEY for RemnaNode:"
    MSG_SECRET_EMPTY="SECRET_KEY not entered or empty. Enter again? (y/n)"
    MSG_CANCEL="Installation canceled."
    MSG_UPDATE="Updating system..."
    MSG_GUI_COMMENT="# Interface settings (example: install and configure Xfce for GUI, if needed)"
    MSG_GUI_UNCOMMENT="# If you need GUI, uncomment the following lines:"
    MSG_GUI_COMPLETE="# Interface setup completed."
    MSG_DOCKER_CHECK="Docker is already installed. Perform a clean reinstall? (y/n)"
    MSG_DOCKER_CLEAN="Removing Docker for clean reinstall..."
    MSG_SKIP_DOCKER="Skipping Docker installation."
    MSG_INSTALL_DOCKER="Installing Docker..."
    MSG_REMOVE_OLD="Removing old Docker versions, if any"
    MSG_START_DOCKER="Starting Docker..."
    MSG_DOCKER_UNCHANGED="Docker left unchanged."
    MSG_DOCKER_CONTAINERS_FOUND="The following Docker containers were found and may conflict with the new installation:"
    MSG_DOCKER_CONTAINERS_REMOVE="It is recommended to remove them before continuing. Remove all containers and their data (volumes, images)? (y/n)"
    MSG_DOCKER_CONTAINERS_REMOVED="All Docker containers, volumes and images removed."
    MSG_DOCKER_CONTAINERS_UNCHANGED="Docker containers left unchanged."
    MSG_DOCKER_NO_CONTAINERS="No Docker containers found."
    MSG_NGINX_FOUND="Nginx is already installed. Remove it for a clean install? (y/n)"
    MSG_NGINX_REMOVED="Nginx removed."
    MSG_NGINX_UNCHANGED="Nginx left unchanged."
    MSG_CADDY_FOUND="Caddy is already installed. Remove it for a clean install? (y/n)"
    MSG_CADDY_REMOVED="Caddy removed."
    MSG_CADDY_UNCHANGED="Caddy left unchanged."
    MSG_IPV6="Disabling IPv6..."
    MSG_IPV6_ENABLE="Restoring default IPv6 (enable)..."
    MSG_IPV6_CONFIRM="Disable IPv6? (y = disable / n = restore default/enable)"
    MSG_IPV6_ONLY_START="Running in IPv6 configuration mode."
    MSG_IPV6_ENABLE_DONE="IPv6 restored to default (enabled)."
    MSG_IPV6_DISABLE_DONE="IPv6 fully disabled."
    MSG_IPV6_MENU="IPv6 configuration:"
    MSG_IPV6_MENU_1="1) Fully disable IPv6"
    MSG_IPV6_MENU_2="2) Restore default (enable IPv6)"
    MSG_IPV6_MENU_INVALID="Invalid choice. Exiting."
    MSG_BBR="Configuring BBR for network optimization..."
    MSG_PORTS="Checking required ports..."
    MSG_PORT_ALREADY_OPEN="Port already open"
    MSG_PORT_OPENED="Port opened"
    MSG_FIREWALL_NOT_FOUND="No supported firewall tool found. Skipping port configuration."
    MSG_FIREWALL_INACTIVE="Firewall is not active. Skipping port configuration."
    MSG_PORTS_DONE="Port check completed."
    MSG_INSTALLER_VERSION="Installer version:"
    MSG_MODE_SELECT="Select mode:"
    MSG_MODE_0="0) Exit"
    MSG_MODE_1="1) Full install"
    MSG_MODE_2="2) Open ports only (no reinstall)"
    MSG_MODE_3="3) Clean reinstall (remove old data)"
    MSG_MODE_4="4) Update parameters only"
    MSG_MODE_5="5) Create or update swap (default 2G)"
    MSG_MODE_6="6) IPv6: fully disable or restore default"
    MSG_MODE_7="7) Enable or disable gaming node tuning"
    MSG_MODE_8="8) Repair host parameters (iface/MTU/IPv6/gaming)"
    MSG_MODE_INVALID="Invalid mode selected. Full install will be used."
    MSG_ADDITIONAL_PORTS="Enter additional node ports (comma-separated), or leave empty:"
    MSG_PORTS_ONLY_START="Running in ports-only mode."
    MSG_SWAP_ONLY_START="Running in swap-only mode."
    MSG_GAMING_ONLY_START="Running in gaming-tuning mode."
    MSG_REPAIR_ONLY_START="Running host parameters repair mode."
    MSG_REPAIR_DONE="Host parameters repair completed."
    MSG_CHECK_IFACE="Network interface"
    MSG_CHECK_MSS="MSS clamp 1360"
    MSG_GAMING_MENU="Gaming node tuning:"
    MSG_GAMING_MENU_1="1) Enable gaming tuning"
    MSG_GAMING_MENU_2="2) Disable gaming tuning"
    MSG_GAMING_MENU_INVALID="Invalid choice. Exiting."
    MSG_GAMING_DISABLE="Disabling gaming node tuning..."
    MSG_GAMING_DISABLE_DONE="Gaming node tuning disabled. Basic BBR left enabled."
    MSG_SAVED_NODE_PORT_FOUND="Found NODE_PORT from existing installation"
    MSG_SAVED_NODE_PORT_NOT_FOUND="NODE_PORT from existing installation was not found. Using default 2222."
    MSG_NO_VALID_PORTS="No valid ports provided. Skipping port changes."
    MSG_PORT_22_UNCHANGED="SSH port 22 is already open. Leaving it unchanged"
    MSG_CLEAN_REINSTALL="Running clean reinstall: removing old RemnaNode data..."
    MSG_RECONFIGURE_ONLY="Running parameter update mode (without clean reinstall)."
    MSG_REMNANODE="Installing RemnaNode..."
    MSG_COMPLETE="Installation completed!"
    MSG_UPDATE_CONFIRM="Update and upgrade system packages? (y/n)"
    MSG_SKIP_UPDATE="Skipping system update."
    MSG_REBOOT="If the system was updated, a reboot may be required (check with sudo reboot if needed)."
    MSG_MTU_CONFIRM="Set MTU to 1450 for hosts using DDoS protection? (y/n)"
    MSG_SKIP_MTU="Restoring default MTU 1500..."
    MSG_MTU_CONFIG="Setting MTU 1450..."
    MSG_MTU_DONE="MTU 1450 applied on interface"
    MSG_MTU_FAIL="Failed to set MTU 1450."
    MSG_MTU_DEFAULT_CONFIG="Setting default MTU 1500..."
    MSG_MTU_DEFAULT_DONE="Default MTU 1500 applied on interface"
    MSG_MTU_DEFAULT_FAIL="Failed to set default MTU 1500."
    MSG_MTU_IFACE_NOT_FOUND="Could not detect network interface. Skipping MTU configuration."
    MSG_SWAP_CONFIRM="Install or reconfigure swap? (default 2G) (y/n)"
    MSG_SWAP_SIZE="Enter swap size (default 2G, examples: 2G, 2048M):"
    MSG_SWAP_SKIP="Skipping swap configuration."
    MSG_SWAP_INVALID_SIZE="Invalid swap size format. Try again."
    MSG_SWAP_NOT_FOUND="No active swap found. Creating new swap..."
    MSG_SWAP_FOUND="Existing swap found. It will be recreated with the new size..."
    MSG_SWAP_CONFIG="Configuring swap..."
    MSG_SWAP_DONE="Swap configured successfully."
    MSG_GAMING_CONFIRM="Install gaming node tuning (low latency: BBR+, CAKE, MSS clamp, THP off)? (y/n)"
    MSG_GAMING_SKIP="Skipping gaming node tuning. Basic BBR will be used."
    MSG_GAMING_CONFIG="Configuring gaming node tuning..."
    MSG_GAMING_DONE="Gaming node tuning configured successfully."
    MSG_GAMING_IFACE_FOUND="Detected network interface"
    MSG_GAMING_IFACE_NOT_FOUND="Could not detect network interface. Skipping CAKE queue setup."
    MSG_GAMING_CAKE_SKIP="CAKE setup skipped."
    MSG_GAMING_STEP_SYSCTL="Applying gaming sysctl..."
    MSG_GAMING_STEP_CAKE="Configuring CAKE queue..."
    MSG_GAMING_STEP_MSS="Configuring MSS clamp..."
    MSG_GAMING_STEP_THP="Disabling Transparent Huge Pages..."
    MSG_GAMING_STEP_APT="Installing package (may take a minute)"
    MSG_CHECKLIST_TITLE="Installation checklist:"
    MSG_CHECKLIST_OK="[OK]"
    MSG_CHECKLIST_NO="[NO]"
    MSG_CHECKLIST_SKIP="[SKIP]"
    MSG_CHECK_SWAP="Swap"
    MSG_CHECK_MTU="MTU 1450"
    MSG_CHECK_MTU_DEFAULT="MTU 1500 (default)"
    MSG_CHECK_UPDATE="System update"
    MSG_CHECK_DOCKER="Docker"
    MSG_CHECK_REMNANODE="RemnaNode container"
    MSG_CHECK_IPV6="IPv6 disabled"
    MSG_CHECK_IPV6_ENABLED="IPv6 enabled"
    MSG_CHECK_BBR="BBR congestion control"
    MSG_CHECK_GAMING="Gaming node tuning"
    MSG_CHECK_GAMING_SYSCTL="Gaming sysctl profile"
    MSG_CHECK_GAMING_CAKE="CAKE queue"
    MSG_CHECK_GAMING_MSS="MSS clamp"
    MSG_CHECK_GAMING_THP="Transparent Huge Pages disabled"
    MSG_CHECK_GAMING_SWAPINESS="vm.swappiness=10"
    MSG_CHECK_PORTS="Node ports configured"
;;
*)
    MSG_NODE_PORT="Введите NODE_PORT (по умолчанию 2222):"
    MSG_SECRET="Введите SECRET_KEY для RemnaNode:"
    MSG_SECRET_EMPTY="SECRET_KEY не введен или пустой. Ввести еще раз? (y/n)"
    MSG_CANCEL="Установка отменена."
    MSG_UPDATE="Обновление системы..."
    MSG_GUI_COMMENT="# Настройки интерфейса (пример: установка и настройка Xfce для GUI, если нужно)"
    MSG_GUI_UNCOMMENT="# Если вам нужен GUI, раскомментируйте следующие строки:"
    MSG_GUI_COMPLETE="# Настройка интерфейса завершена."
    MSG_DOCKER_CHECK="Docker уже установлен. Выполнить чистую переустановку? (y/n)"
    MSG_DOCKER_CLEAN="Удаление Docker для чистой переустановки..."
    MSG_SKIP_DOCKER="Пропускаем установку Docker."
    MSG_INSTALL_DOCKER="Установка Docker..."
    MSG_REMOVE_OLD="Удаление старых версий Docker, если есть"
    MSG_START_DOCKER="Запуск Docker..."
    MSG_DOCKER_UNCHANGED="Docker оставлен без изменений."
    MSG_DOCKER_CONTAINERS_FOUND="Обнаружены следующие Docker-контейнеры, которые могут помешать новой установке:"
    MSG_DOCKER_CONTAINERS_REMOVE="Рекомендуется удалить их перед продолжением. Удалить все контейнеры и их данные (тома, образы)? (y/n)"
    MSG_DOCKER_CONTAINERS_REMOVED="Все Docker-контейнеры, тома и образы удалены."
    MSG_DOCKER_CONTAINERS_UNCHANGED="Docker-контейнеры оставлены без изменений."
    MSG_DOCKER_NO_CONTAINERS="Docker-контейнеры не найдены."
    MSG_NGINX_FOUND="Nginx уже установлен. Удалить для чистой установки? (y/n)"
    MSG_NGINX_REMOVED="Nginx удалён."
    MSG_NGINX_UNCHANGED="Nginx оставлен без изменений."
    MSG_CADDY_FOUND="Caddy уже установлен. Удалить для чистой установки? (y/n)"
    MSG_CADDY_REMOVED="Caddy удалён."
    MSG_CADDY_UNCHANGED="Caddy оставлен без изменений."
    MSG_IPV6="Отключение IPv6..."
    MSG_IPV6_ENABLE="Восстановление IPv6 по умолчанию (включение)..."
    MSG_IPV6_CONFIRM="Отключить IPv6? (y = отключить / n = вернуть дефолт/включить)"
    MSG_IPV6_ONLY_START="Запуск в режиме настройки IPv6."
    MSG_IPV6_ENABLE_DONE="IPv6 возвращён к дефолту (включён)."
    MSG_IPV6_DISABLE_DONE="IPv6 полностью отключён."
    MSG_IPV6_MENU="Настройка IPv6:"
    MSG_IPV6_MENU_1="1) Полностью отключить IPv6"
    MSG_IPV6_MENU_2="2) Вернуть дефолт (включить IPv6)"
    MSG_IPV6_MENU_INVALID="Неверный выбор. Выход."
    MSG_BBR="Настройка BBR для оптимизации сети..."
    MSG_PORTS="Проверка необходимых портов..."
    MSG_PORT_ALREADY_OPEN="Порт уже открыт"
    MSG_PORT_OPENED="Порт открыт"
    MSG_FIREWALL_NOT_FOUND="Поддерживаемый фаервол не найден. Пропускаем настройку портов."
    MSG_FIREWALL_INACTIVE="Фаервол не активен. Пропускаем настройку портов."
    MSG_PORTS_DONE="Проверка портов завершена."
    MSG_INSTALLER_VERSION="Версия установщика:"
    MSG_MODE_SELECT="Выберите режим:"
    MSG_MODE_0="0) Выход"
    MSG_MODE_1="1) Полная установка"
    MSG_MODE_2="2) Только открыть порты (без переустановки)"
    MSG_MODE_3="3) Чистая переустановка (удалить старые данные)"
    MSG_MODE_4="4) Только изменение параметров"
    MSG_MODE_5="5) Создание/обновление swap (по умолчанию 2G)"
    MSG_MODE_6="6) IPv6: полное отключение или возврат к дефолту"
    MSG_MODE_7="7) Включить или отключить тюнинг игровой ноды"
    MSG_MODE_8="8) Починить параметры хоста (интерфейс/MTU/IPv6/gaming)"
    MSG_MODE_INVALID="Выбран неверный режим. Будет использована полная установка."
    MSG_ADDITIONAL_PORTS="Введите дополнительные порты ноды (через запятую) или оставьте пустым:"
    MSG_PORTS_ONLY_START="Запуск в режиме только открытия портов."
    MSG_SWAP_ONLY_START="Запуск в режиме только настройки swap."
    MSG_GAMING_ONLY_START="Запуск в режиме настройки игровой ноды."
    MSG_REPAIR_ONLY_START="Запуск режима починки параметров хоста."
    MSG_REPAIR_DONE="Починка параметров хоста завершена."
    MSG_CHECK_IFACE="Сетевой интерфейс"
    MSG_CHECK_MSS="MSS-clamp 1360"
    MSG_GAMING_MENU="Тюнинг игровой ноды:"
    MSG_GAMING_MENU_1="1) Включить игровой тюнинг"
    MSG_GAMING_MENU_2="2) Отключить игровой тюнинг"
    MSG_GAMING_MENU_INVALID="Неверный выбор. Выход."
    MSG_GAMING_DISABLE="Отключаем тюнинг игровой ноды..."
    MSG_GAMING_DISABLE_DONE="Тюнинг игровой ноды отключён. Базовый BBR оставлен включённым."
    MSG_SAVED_NODE_PORT_FOUND="Найден NODE_PORT из существующей установки"
    MSG_SAVED_NODE_PORT_NOT_FOUND="NODE_PORT из существующей установки не найден. Используется 2222 по умолчанию."
    MSG_NO_VALID_PORTS="Валидные порты не указаны. Пропускаем изменение портов."
    MSG_PORT_22_UNCHANGED="SSH порт 22 уже открыт. Оставляем без изменений"
    MSG_CLEAN_REINSTALL="Запуск чистой переустановки: удаляем старые данные RemnaNode..."
    MSG_RECONFIGURE_ONLY="Запуск режима изменения параметров (без чистой переустановки)."
    MSG_REMNANODE="Установка RemnaNode..."
    MSG_COMPLETE="Установка завершена!"
    MSG_UPDATE_CONFIRM="Обновить и апгрейдить пакеты системы? (y/n)"
    MSG_SKIP_UPDATE="Пропускаем обновление системы."
    MSG_REBOOT="Если система была обновлена, возможно, потребуется перезагрузка (проверьте с sudo reboot если нужно)."
    MSG_MTU_CONFIRM="Установить MTU в 1450 для хостов, использующих защиту от DDoS-атак? (y/n)"
    MSG_SKIP_MTU="Восстанавливаем MTU по умолчанию 1500..."
    MSG_MTU_CONFIG="Устанавливаем MTU 1450..."
    MSG_MTU_DONE="MTU 1450 применён на интерфейсе"
    MSG_MTU_FAIL="Не удалось установить MTU 1450."
    MSG_MTU_DEFAULT_CONFIG="Устанавливаем MTU по умолчанию 1500..."
    MSG_MTU_DEFAULT_DONE="MTU по умолчанию 1500 применён на интерфейсе"
    MSG_MTU_DEFAULT_FAIL="Не удалось установить MTU по умолчанию 1500."
    MSG_MTU_IFACE_NOT_FOUND="Не удалось определить сетевой интерфейс. Пропускаем настройку MTU."
    MSG_SWAP_CONFIRM="Установить или перенастроить swap? (по умолчанию 2G) (y/n)"
    MSG_SWAP_SIZE="Введите размер swap (по умолчанию 2G, примеры: 2G, 2048M):"
    MSG_SWAP_SKIP="Пропускаем настройку swap."
    MSG_SWAP_INVALID_SIZE="Неверный формат размера swap. Попробуйте снова."
    MSG_SWAP_NOT_FOUND="Активный swap не найден. Создаем новый swap..."
    MSG_SWAP_FOUND="Обнаружен существующий swap. Он будет пересоздан с новым размером..."
    MSG_SWAP_CONFIG="Настраиваем swap..."
    MSG_SWAP_DONE="Swap успешно настроен."
    MSG_GAMING_CONFIRM="Установить игровую ноду (низкий пинг: BBR+, CAKE, MSS-clamp, THP off)? (y/n)"
    MSG_GAMING_SKIP="Пропускаем тюнинг игровой ноды. Будет использован базовый BBR."
    MSG_GAMING_CONFIG="Настраиваем тюнинг игровой ноды..."
    MSG_GAMING_DONE="Тюнинг игровой ноды успешно применён."
    MSG_GAMING_IFACE_FOUND="Обнаружен сетевой интерфейс"
    MSG_GAMING_IFACE_NOT_FOUND="Не удалось определить сетевой интерфейс. Пропускаем настройку CAKE."
    MSG_GAMING_CAKE_SKIP="Настройка CAKE пропущена."
    MSG_GAMING_STEP_SYSCTL="Применяем gaming sysctl..."
    MSG_GAMING_STEP_CAKE="Настраиваем очередь CAKE..."
    MSG_GAMING_STEP_MSS="Настраиваем MSS-clamp..."
    MSG_GAMING_STEP_THP="Отключаем Transparent Huge Pages..."
    MSG_GAMING_STEP_APT="Установка пакета (может занять минуту)"
    MSG_CHECKLIST_TITLE="Чеклист установки:"
    MSG_CHECKLIST_OK="[OK]"
    MSG_CHECKLIST_NO="[НЕТ]"
    MSG_CHECKLIST_SKIP="[ПРОПУСК]"
    MSG_CHECK_SWAP="Swap"
    MSG_CHECK_MTU="MTU 1450"
    MSG_CHECK_MTU_DEFAULT="MTU 1500 (по умолчанию)"
    MSG_CHECK_UPDATE="Обновление системы"
    MSG_CHECK_DOCKER="Docker"
    MSG_CHECK_REMNANODE="Контейнер RemnaNode"
    MSG_CHECK_IPV6="IPv6 отключён"
    MSG_CHECK_IPV6_ENABLED="IPv6 включён"
    MSG_CHECK_BBR="BBR congestion control"
    MSG_CHECK_GAMING="Тюнинг игровой ноды"
    MSG_CHECK_GAMING_SYSCTL="Gaming sysctl профиль"
    MSG_CHECK_GAMING_CAKE="Очередь CAKE"
    MSG_CHECK_GAMING_MSS="MSS-clamp"
    MSG_CHECK_GAMING_THP="Transparent Huge Pages выключен"
    MSG_CHECK_GAMING_SWAPINESS="vm.swappiness=10"
    MSG_CHECK_PORTS="Порты ноды настроены"
;;
esac

normalize_swap_size() {
    local raw="$1"

    raw=$(printf '%s' "$raw" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
    if [ -z "$raw" ]; then
        printf '2G\n'
        return 0
    fi

    case "$raw" in
    *B)
        raw=${raw%B}
    ;;
    esac

    if [[ "$raw" =~ ^[0-9]+$ ]]; then
        printf '%sG\n' "$raw"
        return 0
    fi

    if [[ "$raw" =~ ^[0-9]+[KMG]$ ]]; then
        printf '%s\n' "$raw"
        return 0
    fi

    return 1
}

configure_swap() {
    local swap_size="$1"
    local swap_file="/swapfile"
    local active_swaps
    local swap_device

    echo "$MSG_SWAP_CONFIG"

    active_swaps=$($SUDO_CMD swapon --show=NAME --noheadings 2>/dev/null || true)
    if [ -n "${active_swaps// /}" ]; then
        echo "$MSG_SWAP_FOUND"
        while IFS= read -r swap_device; do
            if [ -n "$swap_device" ]; then
                $SUDO_CMD swapoff "$swap_device" 2>/dev/null || true
            fi
        done <<EOF
$active_swaps
EOF
    else
        echo "$MSG_SWAP_NOT_FOUND"
    fi

    # Remove existing swap entries to avoid duplicate or stale records.
    $SUDO_CMD sed -i '/^[[:space:]]*#/!{/[[:space:]]swap[[:space:]]/d;}' /etc/fstab

    if [ -f "$swap_file" ]; then
        $SUDO_CMD rm -f "$swap_file"
    fi

    if ! $SUDO_CMD fallocate -l "$swap_size" "$swap_file" 2>/dev/null; then
        $SUDO_CMD dd if=/dev/zero of="$swap_file" bs="$swap_size" count=1 status=none
    fi

    $SUDO_CMD chmod 600 "$swap_file"
    $SUDO_CMD mkswap "$swap_file" >/dev/null
    $SUDO_CMD swapon "$swap_file"

    if ! $SUDO_CMD grep -Eq '^[[:space:]]*/swapfile[[:space:]]+none[[:space:]]+swap[[:space:]]+sw[[:space:]]+0[[:space:]]+0[[:space:]]*$' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' | $SUDO_CMD tee -a /etc/fstab > /dev/null
    fi

    echo "$MSG_SWAP_DONE"
}

run_swap_size_prompt() {
    while true; do
        echo "$MSG_SWAP_SIZE"
        read -r swap_size_input

        if normalized_swap_size=$(normalize_swap_size "$swap_size_input"); then
            configure_swap "$normalized_swap_size"
            break
        fi

        echo "$MSG_SWAP_INVALID_SIZE"
    done
}

prompt_configure_swap() {
    echo "$MSG_SWAP_CONFIRM"
    read -r response
    case "$response" in
    [yY])
        run_swap_size_prompt
        swap_configured=true
    ;;
    *)
        echo "$MSG_SWAP_SKIP"
        swap_configured=false
    ;;
    esac
}

normalize_port_list() {
    local input="$1"
    local out=""
    local token

    input=${input//,/ }

    for token in $input; do
        token=$(printf '%s\n' "$token" | tr -d '[:space:]')
        if [[ -z "$token" ]]; then
            continue
        fi

        if [[ "$token" =~ ^[0-9]+$ ]] && [ "$token" -ge 1 ] && [ "$token" -le 65535 ]; then
            case " $out " in
            *" $token "*)
                ;;
            *)
                out="$out $token"
                ;;
            esac
        fi
    done

    printf '%s\n' "$out"
}

get_installed_node_port() {
    local compose_file="/opt/remnanode/docker-compose.yml"

    if [ ! -f "$compose_file" ]; then
        return
    fi

    awk -F= '/NODE_PORT=/{
        port=$2
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", port)
        if (port != "") {
            print port
            exit
        }
    }' "$compose_file"
}

ensure_port_open() {
    local port="$1"
    local proto
    local ufw_status

    if command -v ufw >/dev/null 2>&1; then
        ufw_status=$($SUDO_CMD ufw status 2>/dev/null || true)
        if printf '%s\n' "$ufw_status" | grep -qi "Status: active"; then
            for proto in tcp udp; do
                if printf '%s\n' "$ufw_status" | grep -Eq "^[[:space:]]*${port}/${proto}[[:space:]]+ALLOW"; then
                    if [ "$port" = "22" ]; then
                        echo "$MSG_PORT_22_UNCHANGED: ${port}/${proto}"
                    else
                        echo "$MSG_PORT_ALREADY_OPEN: ${port}/${proto}"
                    fi
                else
                    $SUDO_CMD ufw allow "${port}/${proto}"
                    echo "$MSG_PORT_OPENED: ${port}/${proto}"
                fi
            done
            return
        fi
        echo "$MSG_FIREWALL_INACTIVE"
        return
    fi

    if command -v firewall-cmd >/dev/null 2>&1; then
        if $SUDO_CMD firewall-cmd --state >/dev/null 2>&1; then
            for proto in tcp udp; do
                if $SUDO_CMD firewall-cmd --query-port="${port}/${proto}" >/dev/null 2>&1; then
                    if [ "$port" = "22" ]; then
                        echo "$MSG_PORT_22_UNCHANGED: ${port}/${proto}"
                    else
                        echo "$MSG_PORT_ALREADY_OPEN: ${port}/${proto}"
                    fi
                else
                    $SUDO_CMD firewall-cmd --permanent --add-port="${port}/${proto}" >/dev/null
                    echo "$MSG_PORT_OPENED: ${port}/${proto}"
                fi
            done
            $SUDO_CMD firewall-cmd --reload >/dev/null
            return
        fi
        echo "$MSG_FIREWALL_INACTIVE"
        return
    fi

    if command -v iptables >/dev/null 2>&1; then
        for proto in tcp udp; do
            if $SUDO_CMD iptables -C INPUT -p "$proto" --dport "$port" -j ACCEPT >/dev/null 2>&1; then
                if [ "$port" = "22" ]; then
                    echo "$MSG_PORT_22_UNCHANGED: ${port}/${proto}"
                else
                    echo "$MSG_PORT_ALREADY_OPEN: ${port}/${proto}"
                fi
            else
                $SUDO_CMD iptables -I INPUT -p "$proto" --dport "$port" -j ACCEPT
                echo "$MSG_PORT_OPENED: ${port}/${proto}"
            fi
        done
        return
    fi

    echo "$MSG_FIREWALL_NOT_FOUND"
}

open_node_ports() {
    local base_port="$1"
    local extra_ports="$2"
    local ports
    local port

    ports=$(normalize_port_list "$base_port $extra_ports")
    if [ -z "${ports// /}" ]; then
        echo "$MSG_NO_VALID_PORTS"
        return
    fi

    echo "$MSG_PORTS"
    for port in $ports; do
        ensure_port_open "$port"
    done
    echo "$MSG_PORTS_DONE"
}

ensure_sysctl_setting() {
    local key="$1"
    local value="$2"
    local file="/etc/sysctl.conf"
    local duplicates
    local key_escaped

    echo "${key}=${value}" | $SUDO_CMD tee -a "$file" > /dev/null

    duplicates=$($SUDO_CMD awk -F= -v target_key="$key" '
    {
        current_key=$1
        gsub(/[[:space:]]/, "", current_key)
        if (current_key ~ /^#/) {
            next
        }
        if (current_key == target_key) {
            count++
        }
    }
    END {
        print count+0
    }
    ' "$file")

    if [ "$duplicates" -gt 1 ]; then
        key_escaped=$(printf '%s\n' "$key" | sed 's/[.[\*^$()+?{|]/\\&/g')
        $SUDO_CMD sed -i "/^[[:space:]]*${key_escaped}[[:space:]]*=/d" "$file"
        echo "${key}=${value}" | $SUDO_CMD tee -a "$file" > /dev/null
    fi
}

disable_ipv6() {
    local ipv6_dropin="/etc/sysctl.d/99-remnanode-ipv6.conf"

    echo "$MSG_IPV6"
    $SUDO_CMD sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true
    $SUDO_CMD sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1 || true
    $SUDO_CMD sysctl -w net.ipv6.conf.lo.disable_ipv6=1 >/dev/null 2>&1 || true

    $SUDO_CMD tee "$ipv6_dropin" > /dev/null <<EOF
net.ipv6.conf.all.disable_ipv6=1
net.ipv6.conf.default.disable_ipv6=1
net.ipv6.conf.lo.disable_ipv6=1
EOF

    # Keep legacy keys in sync / dedupe in sysctl.conf
    ensure_sysctl_setting "net.ipv6.conf.all.disable_ipv6" "1"
    ensure_sysctl_setting "net.ipv6.conf.default.disable_ipv6" "1"
    ensure_sysctl_setting "net.ipv6.conf.lo.disable_ipv6" "1"
    # Remove old one-off filename if present
    $SUDO_CMD rm -f /etc/sysctl.d/99-remnanode-disable-ipv6.conf
    $SUDO_CMD sysctl --system >/dev/null 2>&1 || $SUDO_CMD sysctl -p >/dev/null 2>&1 || true
    echo "$MSG_IPV6_DISABLE_DONE"
}

enable_ipv6() {
    local ipv6_dropin="/etc/sysctl.d/99-remnanode-ipv6.conf"
    local key
    local key_escaped

    echo "$MSG_IPV6_ENABLE"
    $SUDO_CMD sysctl -w net.ipv6.conf.all.disable_ipv6=0 >/dev/null 2>&1 || true
    $SUDO_CMD sysctl -w net.ipv6.conf.default.disable_ipv6=0 >/dev/null 2>&1 || true
    $SUDO_CMD sysctl -w net.ipv6.conf.lo.disable_ipv6=0 >/dev/null 2>&1 || true

    $SUDO_CMD rm -f "$ipv6_dropin" /etc/sysctl.d/99-remnanode-disable-ipv6.conf

    # Remove remnanode IPv6 overrides from sysctl.conf so OS default can apply.
    for key in \
        net.ipv6.conf.all.disable_ipv6 \
        net.ipv6.conf.default.disable_ipv6 \
        net.ipv6.conf.lo.disable_ipv6
    do
        key_escaped=$(printf '%s\n' "$key" | sed 's/[.[\*^$()+?{|]/\\&/g')
        $SUDO_CMD sed -i "/^[[:space:]]*${key_escaped}[[:space:]]*=/d" /etc/sysctl.conf 2>/dev/null || true
    done

    $SUDO_CMD sysctl --system >/dev/null 2>&1 || $SUDO_CMD sysctl -p >/dev/null 2>&1 || true
    # Explicitly leave enabled after cleanup
    $SUDO_CMD sysctl -w net.ipv6.conf.all.disable_ipv6=0 >/dev/null 2>&1 || true
    $SUDO_CMD sysctl -w net.ipv6.conf.default.disable_ipv6=0 >/dev/null 2>&1 || true
    $SUDO_CMD sysctl -w net.ipv6.conf.lo.disable_ipv6=0 >/dev/null 2>&1 || true
    echo "$MSG_IPV6_ENABLE_DONE"
}

run_ipv6_only_mode() {
    local choice

    echo "$MSG_IPV6_ONLY_START"
    echo "$MSG_IPV6_MENU"
    echo "$MSG_IPV6_MENU_1"
    echo "$MSG_IPV6_MENU_2"
    read -r choice
    case "$choice" in
    1)
        disable_ipv6
    ;;
    2)
        enable_ipv6
    ;;
    *)
        echo "$MSG_IPV6_MENU_INVALID"
        exit 1
    ;;
    esac
}

prompt_configure_ipv6() {
    echo "$MSG_IPV6_CONFIRM"
    read -r response
    case "$response" in
    [yY])
        ipv6_disable=true
    ;;
    *)
        ipv6_disable=false
    ;;
    esac
}

apply_ipv6_choice() {
    if $ipv6_disable; then
        disable_ipv6
    else
        enable_ipv6
    fi
}

detect_primary_iface() {
    local iface

    iface=$(ip -4 route show default 2>/dev/null | awk '{print $5; exit}')
    if [ -n "$iface" ] && [ -d "/sys/class/net/$iface" ]; then
        printf '%s\n' "$iface"
        return 0
    fi

    iface=$(ip -br link show up 2>/dev/null | awk '$1 != "lo" && $1 != "docker0" && $1 !~ /^br-/ && $1 !~ /^veth/ && $1 !~ /^ifb/ {print $1; exit}' | cut -d'@' -f1)
    if [ -n "$iface" ] && [ -d "/sys/class/net/$iface" ]; then
        printf '%s\n' "$iface"
        return 0
    fi

    return 1
}

configure_mtu_1450() {
    local iface
    local mtu_service="/etc/systemd/system/remnanode-mtu.service"
    local current_mtu

    echo "$MSG_MTU_CONFIG"

    if ! iface=$(detect_primary_iface); then
        echo "$MSG_MTU_IFACE_NOT_FOUND"
        return 1
    fi

    echo "$MSG_GAMING_IFACE_FOUND: $iface"

    if ! $SUDO_CMD ip link set dev "$iface" mtu 1450; then
        echo "$MSG_MTU_FAIL"
        return 1
    fi

    current_mtu=$(cat "/sys/class/net/$iface/mtu" 2>/dev/null || true)
    if [ "$current_mtu" != "1450" ]; then
        echo "$MSG_MTU_FAIL"
        return 1
    fi

    # Persist across reboot without netplan set (netplan set is unsafe / iface-specific).
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

    echo "$MSG_MTU_DONE: $iface"
    return 0
}

configure_mtu_default_1500() {
    local iface
    local mtu_service="/etc/systemd/system/remnanode-mtu.service"
    local current_mtu

    echo "$MSG_MTU_DEFAULT_CONFIG"

    if ! iface=$(detect_primary_iface); then
        echo "$MSG_MTU_IFACE_NOT_FOUND"
        return 1
    fi

    echo "$MSG_GAMING_IFACE_FOUND: $iface"

    # Remove persistence that forced MTU 1450 from previous runs.
    if [ -f "$mtu_service" ]; then
        $SUDO_CMD systemctl disable remnanode-mtu.service >/dev/null 2>&1 || true
        $SUDO_CMD rm -f "$mtu_service"
        $SUDO_CMD systemctl daemon-reload >/dev/null 2>&1 || true
    fi

    if ! $SUDO_CMD ip link set dev "$iface" mtu 1500; then
        echo "$MSG_MTU_DEFAULT_FAIL"
        return 1
    fi

    current_mtu=$(cat "/sys/class/net/$iface/mtu" 2>/dev/null || true)
    if [ "$current_mtu" != "1500" ]; then
        echo "$MSG_MTU_DEFAULT_FAIL"
        return 1
    fi

    echo "$MSG_MTU_DEFAULT_DONE: $iface"
    return 0
}

ensure_mss_1360() {
    echo "$MSG_GAMING_STEP_MSS"

    if ! command -v iptables >/dev/null 2>&1; then
        return 1
    fi

    # Remove wrong/old MSS values, then set gaming default 1360.
    while $SUDO_CMD iptables -t mangle -C OUTPUT -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1200 >/dev/null 2>&1; do
        $SUDO_CMD iptables -t mangle -D OUTPUT -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1200 2>/dev/null || break
    done
    while $SUDO_CMD iptables -t mangle -C POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1200 >/dev/null 2>&1; do
        $SUDO_CMD iptables -t mangle -D POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1200 2>/dev/null || break
    done
    while $SUDO_CMD iptables -t mangle -C OUTPUT -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360 >/dev/null 2>&1; do
        $SUDO_CMD iptables -t mangle -D OUTPUT -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360 2>/dev/null || break
    done

    $SUDO_CMD iptables -t mangle -A OUTPUT -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360 2>/dev/null || true

    if ! $SUDO_CMD iptables -t mangle -C FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu >/dev/null 2>&1; then
        $SUDO_CMD iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true
    fi

    if ! dpkg -s iptables-persistent >/dev/null 2>&1; then
        echo "$MSG_GAMING_STEP_APT: iptables-persistent"
        echo 'iptables-persistent iptables-persistent/autosave_v4 boolean true' | $SUDO_CMD debconf-set-selections
        echo 'iptables-persistent iptables-persistent/autosave_v6 boolean true' | $SUDO_CMD debconf-set-selections
        DEBIAN_FRONTEND=noninteractive $SUDO_CMD apt-get install -y iptables-persistent >/dev/null 2>&1 || true
    fi
    if command -v netfilter-persistent >/dev/null 2>&1; then
        $SUDO_CMD netfilter-persistent save >/dev/null 2>&1 || true
    fi

    return 0
}

configure_bbr_basic() {
    echo "$MSG_BBR"
    $SUDO_CMD modprobe tcp_bbr 2>/dev/null || true
    echo tcp_bbr | $SUDO_CMD tee /etc/modules-load.d/bbr.conf > /dev/null 2>&1 || true
    $SUDO_CMD sysctl -w net.core.default_qdisc=fq >/dev/null 2>&1 || true
    $SUDO_CMD sysctl -w net.ipv4.tcp_congestion_control=bbr >/dev/null 2>&1 || true
    ensure_sysctl_setting "net.core.default_qdisc" "fq"
    ensure_sysctl_setting "net.ipv4.tcp_congestion_control" "bbr"
    $SUDO_CMD sysctl -p >/dev/null 2>&1 || true
}

configure_gaming_node() {
    local iface
    local sysctl_file="/etc/sysctl.d/99-remnanode-gaming.conf"
    local cake_service="/etc/systemd/system/remnanode-gaming-qos.service"
    local thp_service="/etc/systemd/system/remnanode-disable-thp.service"

    echo "$MSG_GAMING_CONFIG"

    echo "$MSG_GAMING_STEP_SYSCTL"
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

    # Prefer applying only our file — sysctl --system can hang on broken drop-ins.
    $SUDO_CMD sysctl -p "$sysctl_file" >/dev/null 2>&1 || true

    echo "$MSG_GAMING_STEP_CAKE"
    if iface=$(detect_primary_iface); then
        echo "$MSG_GAMING_IFACE_FOUND: $iface"
        if ! command -v tc >/dev/null 2>&1; then
            echo "$MSG_GAMING_STEP_APT: iproute2"
            $SUDO_CMD apt-get update -y >/dev/null 2>&1 || true
            DEBIAN_FRONTEND=noninteractive $SUDO_CMD apt-get install -y iproute2 >/dev/null 2>&1 || true
        fi

        if command -v tc >/dev/null 2>&1; then
            # Apply immediately; do not block on systemctl start/network targets.
            timeout 10 $SUDO_CMD tc qdisc replace dev "$iface" root cake >/dev/null 2>&1 \
                || $SUDO_CMD tc qdisc replace dev "$iface" root cake >/dev/null 2>&1 \
                || true
            $SUDO_CMD tee "$cake_service" > /dev/null <<EOF
[Unit]
Description=RemnaNode gaming CAKE queue
After=network-pre.target
DefaultDependencies=no

[Service]
Type=oneshot
ExecStart=/sbin/tc qdisc replace dev $iface root cake
RemainAfterExit=yes
TimeoutStartSec=10

[Install]
WantedBy=multi-user.target
EOF
            $SUDO_CMD systemctl daemon-reload >/dev/null 2>&1 || true
            # Enable for reboot only — starting now can hang on some VPS.
            $SUDO_CMD systemctl enable remnanode-gaming-qos.service >/dev/null 2>&1 || true
        else
            echo "$MSG_GAMING_CAKE_SKIP"
        fi
    else
        echo "$MSG_GAMING_IFACE_NOT_FOUND"
        echo "$MSG_GAMING_CAKE_SKIP"
    fi

    ensure_mss_1360

    echo "$MSG_GAMING_STEP_THP"
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
TimeoutStartSec=10

[Install]
WantedBy=multi-user.target
EOF
    $SUDO_CMD systemctl daemon-reload >/dev/null 2>&1 || true
    # Enable for reboot only; THP already applied above.
    $SUDO_CMD systemctl enable remnanode-disable-thp.service >/dev/null 2>&1 || true

    echo "$MSG_GAMING_DONE"
}

disable_gaming_node() {
    local iface
    local sysctl_file="/etc/sysctl.d/99-remnanode-gaming.conf"
    local cake_service="/etc/systemd/system/remnanode-gaming-qos.service"
    local thp_service="/etc/systemd/system/remnanode-disable-thp.service"

    echo "$MSG_GAMING_DISABLE"

    if [ -f "$sysctl_file" ]; then
        $SUDO_CMD rm -f "$sysctl_file"
    fi

    $SUDO_CMD sysctl -w vm.swappiness=60 >/dev/null 2>&1 || true

    if iface=$(detect_primary_iface); then
        if command -v tc >/dev/null 2>&1; then
            timeout 10 $SUDO_CMD tc qdisc del dev "$iface" root >/dev/null 2>&1 \
                || $SUDO_CMD tc qdisc del dev "$iface" root 2>/dev/null \
                || true
            timeout 10 $SUDO_CMD tc qdisc replace dev "$iface" root fq >/dev/null 2>&1 \
                || $SUDO_CMD tc qdisc replace dev "$iface" root fq 2>/dev/null \
                || true
        fi
    fi

    if [ -f "$cake_service" ]; then
        $SUDO_CMD systemctl disable remnanode-gaming-qos.service >/dev/null 2>&1 || true
        $SUDO_CMD systemctl stop remnanode-gaming-qos.service >/dev/null 2>&1 || true
        $SUDO_CMD rm -f "$cake_service"
    fi

    if command -v iptables >/dev/null 2>&1; then
        while $SUDO_CMD iptables -t mangle -C OUTPUT -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360 >/dev/null 2>&1; do
            $SUDO_CMD iptables -t mangle -D OUTPUT -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360 2>/dev/null || break
        done
        while $SUDO_CMD iptables -t mangle -C FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu >/dev/null 2>&1; do
            $SUDO_CMD iptables -t mangle -D FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || break
        done
        if command -v netfilter-persistent >/dev/null 2>&1; then
            $SUDO_CMD netfilter-persistent save >/dev/null 2>&1 || true
        fi
    fi

    if [ -f "$thp_service" ]; then
        $SUDO_CMD systemctl disable remnanode-disable-thp.service >/dev/null 2>&1 || true
        $SUDO_CMD systemctl stop remnanode-disable-thp.service >/dev/null 2>&1 || true
        $SUDO_CMD rm -f "$thp_service"
    fi
    $SUDO_CMD systemctl daemon-reload >/dev/null 2>&1 || true

    if [ -e /sys/kernel/mm/transparent_hugepage/enabled ]; then
        echo madvise | $SUDO_CMD tee /sys/kernel/mm/transparent_hugepage/enabled > /dev/null 2>&1 || true
    fi

    configure_bbr_basic

    echo "$MSG_GAMING_DISABLE_DONE"
}

print_gaming_status_checklist() {
    echo ""
    echo "$MSG_CHECKLIST_TITLE"

    if is_gaming_sysctl_present; then
        checklist_status "$MSG_CHECK_GAMING_SYSCTL" ok
    else
        checklist_status "$MSG_CHECK_GAMING_SYSCTL" no
    fi

    if is_cake_active; then
        checklist_status "$MSG_CHECK_GAMING_CAKE" ok
    else
        checklist_status "$MSG_CHECK_GAMING_CAKE" no
    fi

    if is_mss_clamp_active; then
        checklist_status "$MSG_CHECK_GAMING_MSS" ok
    else
        checklist_status "$MSG_CHECK_GAMING_MSS" no
    fi

    if is_thp_disabled; then
        checklist_status "$MSG_CHECK_GAMING_THP" ok
    else
        checklist_status "$MSG_CHECK_GAMING_THP" no
    fi

    if is_swappiness_10; then
        checklist_status "$MSG_CHECK_GAMING_SWAPINESS" ok
    else
        checklist_status "$MSG_CHECK_GAMING_SWAPINESS" no
    fi

    if is_bbr_active; then
        checklist_status "$MSG_CHECK_BBR" ok
    else
        checklist_status "$MSG_CHECK_BBR" no
    fi

    echo ""
}

run_gaming_only_mode() {
    local gaming_choice

    echo "$MSG_GAMING_ONLY_START"
    echo "$MSG_GAMING_MENU"
    echo "$MSG_GAMING_MENU_1"
    echo "$MSG_GAMING_MENU_2"
    read -r gaming_choice

    case "$gaming_choice" in
    1)
        configure_gaming_node
        gaming_node=true
        print_gaming_status_checklist
    ;;
    2)
        disable_gaming_node
        gaming_node=false
        print_gaming_status_checklist
    ;;
    *)
        echo "$MSG_GAMING_MENU_INVALID"
        exit 1
    ;;
    esac
}

print_repair_checklist() {
    local iface

    echo ""
    echo "$MSG_CHECKLIST_TITLE"

    if iface=$(detect_primary_iface); then
        checklist_status "$MSG_CHECK_IFACE: $iface" ok
    else
        checklist_status "$MSG_CHECK_IFACE" no
    fi

    if $mtu_configured; then
        if is_mtu_1450_active; then
            checklist_status "$MSG_CHECK_MTU" ok
        else
            checklist_status "$MSG_CHECK_MTU" no
        fi
    elif $mtu_default_restored; then
        if is_mtu_1500_active; then
            checklist_status "$MSG_CHECK_MTU_DEFAULT" ok
        else
            checklist_status "$MSG_CHECK_MTU_DEFAULT" no
        fi
    else
        checklist_status "$MSG_CHECK_MTU" skip
    fi

    if $ipv6_disable; then
        if is_ipv6_disabled; then
            checklist_status "$MSG_CHECK_IPV6" ok
        else
            checklist_status "$MSG_CHECK_IPV6" no
        fi
    else
        if is_ipv6_disabled; then
            checklist_status "$MSG_CHECK_IPV6_ENABLED" no
        else
            checklist_status "$MSG_CHECK_IPV6_ENABLED" ok
        fi
    fi

    if is_bbr_active; then
        checklist_status "$MSG_CHECK_BBR" ok
    else
        checklist_status "$MSG_CHECK_BBR" no
    fi

    if is_mss_clamp_active; then
        checklist_status "$MSG_CHECK_MSS" ok
    else
        if $mtu_configured || $gaming_node; then
            checklist_status "$MSG_CHECK_MSS" no
        else
            checklist_status "$MSG_CHECK_MSS" skip
        fi
    fi

    if $gaming_node; then
        checklist_status "$MSG_CHECK_GAMING" ok
        if is_gaming_sysctl_present; then
            checklist_status "  - $MSG_CHECK_GAMING_SYSCTL" ok
        else
            checklist_status "  - $MSG_CHECK_GAMING_SYSCTL" no
        fi
        if is_cake_active; then
            checklist_status "  - $MSG_CHECK_GAMING_CAKE" ok
        else
            checklist_status "  - $MSG_CHECK_GAMING_CAKE" no
        fi
        if is_thp_disabled; then
            checklist_status "  - $MSG_CHECK_GAMING_THP" ok
        else
            checklist_status "  - $MSG_CHECK_GAMING_THP" no
        fi
        if is_swappiness_10; then
            checklist_status "  - $MSG_CHECK_GAMING_SWAPINESS" ok
        else
            checklist_status "  - $MSG_CHECK_GAMING_SWAPINESS" no
        fi
    else
        checklist_status "$MSG_CHECK_GAMING" skip
    fi

    echo ""
}

run_repair_host_mode() {
    local iface
    local response

    echo "$MSG_REPAIR_ONLY_START"

    if iface=$(detect_primary_iface); then
        echo "$MSG_GAMING_IFACE_FOUND: $iface"
    else
        echo "$MSG_MTU_IFACE_NOT_FOUND"
        exit 1
    fi

    echo "$MSG_MTU_CONFIRM"
    read -r response
    case "$response" in
    [yY])
        if configure_mtu_1450; then
            mtu_configured=true
            mtu_default_restored=false
        else
            mtu_configured=false
        fi
    ;;
    *)
        echo "$MSG_SKIP_MTU"
        if configure_mtu_default_1500; then
            mtu_default_restored=true
            mtu_configured=false
        else
            mtu_default_restored=false
            mtu_configured=false
        fi
    ;;
    esac

    prompt_configure_gaming
    prompt_configure_ipv6

    if $gaming_node; then
        configure_gaming_node
    else
        disable_gaming_node
        # DDoS/MTU nodes still need correct MSS even without full gaming profile.
        if $mtu_configured; then
            ensure_mss_1360
        fi
    fi

    apply_ipv6_choice

    print_repair_checklist
    echo "$MSG_REPAIR_DONE"
}

prompt_configure_gaming() {
    echo "$MSG_GAMING_CONFIRM"
    read -r response
    case "$response" in
    [yY])
        gaming_node=true
    ;;
    *)
        echo "$MSG_GAMING_SKIP"
        gaming_node=false
    ;;
    esac
}

checklist_status() {
    local label="$1"
    local status="$2"

    case "$status" in
    ok)
        echo "$MSG_CHECKLIST_OK $label"
    ;;
    skip)
        echo "$MSG_CHECKLIST_SKIP $label"
    ;;
    *)
        echo "$MSG_CHECKLIST_NO $label"
    ;;
    esac
}

is_swap_active() {
    local swap_out
    swap_out=$($SUDO_CMD swapon --show=NAME --noheadings 2>/dev/null || true)
    if [ -n "${swap_out// /}" ]; then
        return 0
    fi
    return 1
}

is_ipv6_disabled() {
    local all_v default_v
    all_v=$(sysctl -n net.ipv6.conf.all.disable_ipv6 2>/dev/null || echo 0)
    default_v=$(sysctl -n net.ipv6.conf.default.disable_ipv6 2>/dev/null || echo 0)
    if [ "$all_v" = "1" ] && [ "$default_v" = "1" ]; then
        return 0
    fi
    return 1
}

is_bbr_active() {
    local cc
    cc=$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || true)
    if [ "$cc" = "bbr" ]; then
        return 0
    fi
    return 1
}

is_remnanode_running() {
    local names
    command -v docker >/dev/null 2>&1 || return 1
    names=$($SUDO_CMD docker ps --format '{{.Names}}' 2>/dev/null || true)
    if printf '%s\n' "$names" | grep -qx 'remnanode'; then
        return 0
    fi
    return 1
}

is_cake_active() {
    local iface
    local qdisc_line
    iface=$(detect_primary_iface 2>/dev/null || true)
    if [ -z "$iface" ]; then
        return 1
    fi
    qdisc_line=$($SUDO_CMD tc qdisc show dev "$iface" 2>/dev/null | head -1 || true)
    if printf '%s\n' "$qdisc_line" | grep -q 'qdisc cake'; then
        return 0
    fi
    return 1
}

is_mss_clamp_active() {
    command -v iptables >/dev/null 2>&1 || return 1
    if $SUDO_CMD iptables -t mangle -C OUTPUT -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360 >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

is_thp_disabled() {
    local thp
    [ -r /sys/kernel/mm/transparent_hugepage/enabled ] || return 1
    thp=$(cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true)
    if printf '%s' "$thp" | grep -q '\[never\]'; then
        return 0
    fi
    return 1
}

is_swappiness_10() {
    local val
    val=$(sysctl -n vm.swappiness 2>/dev/null || true)
    if [ "$val" = "10" ]; then
        return 0
    fi
    return 1
}

is_gaming_sysctl_present() {
    if [ -f /etc/sysctl.d/99-remnanode-gaming.conf ]; then
        return 0
    fi
    return 1
}

is_mtu_1450_active() {
    local iface
    local current_mtu

    iface=$(detect_primary_iface 2>/dev/null || true)
    if [ -z "$iface" ]; then
        return 1
    fi
    current_mtu=$(cat "/sys/class/net/$iface/mtu" 2>/dev/null || true)
    if [ "$current_mtu" = "1450" ]; then
        return 0
    fi
    return 1
}

is_mtu_1500_active() {
    local iface
    local current_mtu

    iface=$(detect_primary_iface 2>/dev/null || true)
    if [ -z "$iface" ]; then
        return 1
    fi
    current_mtu=$(cat "/sys/class/net/$iface/mtu" 2>/dev/null || true)
    if [ "$current_mtu" = "1500" ]; then
        return 0
    fi
    return 1
}

print_install_checklist() {
    echo ""
    echo "$MSG_CHECKLIST_TITLE"

    if $swap_configured && is_swap_active; then
        checklist_status "$MSG_CHECK_SWAP" ok
    elif $swap_configured; then
        checklist_status "$MSG_CHECK_SWAP" no
    elif is_swap_active; then
        checklist_status "$MSG_CHECK_SWAP" ok
    else
        checklist_status "$MSG_CHECK_SWAP" skip
    fi

    if $mtu_configured && is_mtu_1450_active; then
        checklist_status "$MSG_CHECK_MTU" ok
    elif $mtu_configured; then
        checklist_status "$MSG_CHECK_MTU" no
    elif $mtu_default_restored && is_mtu_1500_active; then
        checklist_status "$MSG_CHECK_MTU_DEFAULT" ok
    elif $mtu_default_restored; then
        checklist_status "$MSG_CHECK_MTU_DEFAULT" no
    else
        checklist_status "$MSG_CHECK_MTU" skip
    fi

    if $skip_update; then
        checklist_status "$MSG_CHECK_UPDATE" skip
    else
        checklist_status "$MSG_CHECK_UPDATE" ok
    fi

    if command -v docker >/dev/null 2>&1; then
        checklist_status "$MSG_CHECK_DOCKER" ok
    else
        checklist_status "$MSG_CHECK_DOCKER" no
    fi

    if is_remnanode_running; then
        checklist_status "$MSG_CHECK_REMNANODE" ok
    else
        checklist_status "$MSG_CHECK_REMNANODE" no
    fi

    if $ipv6_disable; then
        if is_ipv6_disabled; then
            checklist_status "$MSG_CHECK_IPV6" ok
        else
            checklist_status "$MSG_CHECK_IPV6" no
        fi
    else
        if is_ipv6_disabled; then
            checklist_status "$MSG_CHECK_IPV6_ENABLED" no
        else
            checklist_status "$MSG_CHECK_IPV6_ENABLED" ok
        fi
    fi

    if is_bbr_active; then
        checklist_status "$MSG_CHECK_BBR" ok
    else
        checklist_status "$MSG_CHECK_BBR" no
    fi

    checklist_status "$MSG_CHECK_PORTS" ok

    if $gaming_node; then
        checklist_status "$MSG_CHECK_GAMING" ok

        if is_gaming_sysctl_present; then
            checklist_status "  - $MSG_CHECK_GAMING_SYSCTL" ok
        else
            checklist_status "  - $MSG_CHECK_GAMING_SYSCTL" no
        fi

        if is_cake_active; then
            checklist_status "  - $MSG_CHECK_GAMING_CAKE" ok
        else
            checklist_status "  - $MSG_CHECK_GAMING_CAKE" no
        fi

        if is_mss_clamp_active; then
            checklist_status "  - $MSG_CHECK_GAMING_MSS" ok
        else
            checklist_status "  - $MSG_CHECK_GAMING_MSS" no
        fi

        if is_thp_disabled; then
            checklist_status "  - $MSG_CHECK_GAMING_THP" ok
        else
            checklist_status "  - $MSG_CHECK_GAMING_THP" no
        fi

        if is_swappiness_10; then
            checklist_status "  - $MSG_CHECK_GAMING_SWAPINESS" ok
        else
            checklist_status "  - $MSG_CHECK_GAMING_SWAPINESS" no
        fi
    else
        checklist_status "$MSG_CHECK_GAMING" skip
    fi

    echo ""
}

show_install_mode_menu() {
    local mode_id

    echo "$MSG_INSTALLER_VERSION $INSTALLER_VERSION"
    echo "$MSG_MODE_SELECT"
    for mode_id in 0 1 2 3 4 5 6 7 8; do
        eval "echo \"\$MSG_MODE_${mode_id}\""
    done
}

show_install_mode_menu
read -r install_mode
case "$install_mode" in
0)
    echo "$MSG_CANCEL"
    exit 0
    ;;
1|2|3|4|5|6|7|8)
    ;;
*)
    echo "$MSG_MODE_INVALID"
    install_mode="1"
    ;;
esac

if [ "$install_mode" = "2" ]; then
    saved_node_port=$(get_installed_node_port)
    if [ -z "$saved_node_port" ]; then
        saved_node_port=2222
        echo "$MSG_SAVED_NODE_PORT_NOT_FOUND"
    else
        echo "$MSG_SAVED_NODE_PORT_FOUND: $saved_node_port"
    fi

    echo "$MSG_ADDITIONAL_PORTS"
    read -r additional_ports_input

    echo "$MSG_PORTS_ONLY_START"
    open_node_ports "$saved_node_port" "$additional_ports_input"
    exit 0
fi

if [ "$install_mode" = "5" ]; then
    echo "$MSG_SWAP_ONLY_START"
    run_swap_size_prompt
    exit 0
fi

if [ "$install_mode" = "6" ]; then
    run_ipv6_only_mode
    exit 0
fi

if [ "$install_mode" = "7" ]; then
    run_gaming_only_mode
    exit 0
fi

if [ "$install_mode" = "8" ]; then
    run_repair_host_mode
    exit 0
fi

if [ "$install_mode" = "3" ]; then
    clean_install=true
fi

if [ "$install_mode" = "4" ]; then
    reconfigure_only=true
    echo "$MSG_RECONFIGURE_ONLY"
fi

prompt_configure_swap

echo "$MSG_MTU_CONFIRM"
read -r response
case "$response" in
[yY])
    if configure_mtu_1450; then
        mtu_configured=true
        mtu_default_restored=false
        skip_mtu=false
    else
        mtu_configured=false
        skip_mtu=true
    fi
;;
*)
    echo "$MSG_SKIP_MTU"
    if configure_mtu_default_1500; then
        mtu_default_restored=true
        mtu_configured=false
        skip_mtu=false
    else
        mtu_default_restored=false
        mtu_configured=false
        skip_mtu=true
    fi
;;
esac

prompt_configure_gaming

prompt_configure_ipv6

echo "$MSG_NODE_PORT"
read -r NODE_PORT
case "$NODE_PORT" in
"")
    NODE_PORT=2222
;;
esac

echo "$MSG_ADDITIONAL_PORTS"
read -r additional_ports_input

echo "$MSG_SECRET"
read -r SECRET_KEY

while [[ -z "$SECRET_KEY" ]]; do
    echo "$MSG_SECRET_EMPTY"
    read -r response
    case "$response" in
    [yY])
        echo "$MSG_SECRET"
        read -r SECRET_KEY
    ;;
    *)
        echo "$MSG_CANCEL"
        exit 1
    ;;
    esac
done

echo "$MSG_UPDATE_CONFIRM"
read -r response
case "$response" in
[yY])
    $SUDO_CMD apt update && $SUDO_CMD apt upgrade -y
;;
*)
    echo "$MSG_SKIP_UPDATE"
    skip_update=true
;;
esac

# Настройки интерфейса (пример: установка и настройка Xfce для GUI, если нужно)
# Если вам нужен GUI, раскомментируйте следующие строки:
# sudo apt install -y xfce4 xfce4-goodies
# echo "Настройка интерфейса завершена."

if ! $reconfigure_only; then
    # Проверка Nginx
    if command -v nginx >/dev/null 2>&1; then
        echo "$MSG_NGINX_FOUND"
        read -r response
        case "$response" in
        [yY])
            $SUDO_CMD systemctl stop nginx 2>/dev/null || true
            $SUDO_CMD apt-get remove --purge -y nginx nginx-common nginx-full nginx-core 2>/dev/null || true
            $SUDO_CMD apt-get autoremove -y
            echo "$MSG_NGINX_REMOVED"
        ;;
        *)
            echo "$MSG_NGINX_UNCHANGED"
        ;;
        esac
    fi

    # Проверка Caddy
    if command -v caddy >/dev/null 2>&1; then
        echo "$MSG_CADDY_FOUND"
        read -r response
        case "$response" in
        [yY])
            $SUDO_CMD systemctl stop caddy 2>/dev/null || true
            $SUDO_CMD apt-get remove --purge -y caddy 2>/dev/null || true
            $SUDO_CMD apt-get autoremove -y
            echo "$MSG_CADDY_REMOVED"
        ;;
        *)
            echo "$MSG_CADDY_UNCHANGED"
        ;;
        esac
    fi

    # Проверка Docker
    if command -v docker >/dev/null 2>&1; then
        # Проверка наличия контейнеров
        CONTAINERS=$($SUDO_CMD docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null)
        if [[ -n "$CONTAINERS" ]]; then
            echo "$MSG_DOCKER_CONTAINERS_FOUND"
            echo "$CONTAINERS"
            echo "$MSG_DOCKER_CONTAINERS_REMOVE"
            read -r response
            case "$response" in
            [yY])
                $SUDO_CMD docker stop $($SUDO_CMD docker ps -q) 2>/dev/null || true
                $SUDO_CMD docker rm -f $($SUDO_CMD docker ps -aq) 2>/dev/null || true
                $SUDO_CMD docker volume rm $($SUDO_CMD docker volume ls -q) 2>/dev/null || true
                $SUDO_CMD docker rmi -f $($SUDO_CMD docker images -q) 2>/dev/null || true
                echo "$MSG_DOCKER_CONTAINERS_REMOVED"
            ;;
            *)
                echo "$MSG_DOCKER_CONTAINERS_UNCHANGED"
            ;;
            esac
        else
            echo "$MSG_DOCKER_NO_CONTAINERS"
        fi

        echo "$MSG_DOCKER_CHECK"
        read -r response
        case "$response" in
        [yY])
            echo "$MSG_DOCKER_CLEAN"
            $SUDO_CMD systemctl stop docker 2>/dev/null || true
            $SUDO_CMD apt-get remove --purge -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker.io 2>/dev/null || true
            $SUDO_CMD apt-get autoremove -y
            skip_docker=false
        ;;
        *)
            echo "$MSG_DOCKER_UNCHANGED"
            skip_docker=true
        ;;
        esac
    fi
else
    if command -v docker >/dev/null 2>&1; then
        skip_docker=true
    else
        skip_docker=false
    fi
fi

case "$skip_docker" in
true)
    : # Docker left as-is
;;
*)
    echo "$MSG_INSTALL_DOCKER"
    if ! command -v curl >/dev/null 2>&1; then
        $SUDO_CMD apt update
        $SUDO_CMD apt install -y curl
    fi
    curl -fsSL https://get.docker.com | $SUDO_CMD sh

    echo "$MSG_START_DOCKER"
    $SUDO_CMD systemctl start docker
    $SUDO_CMD systemctl enable docker
;;
esac

if $reconfigure_only; then
    $SUDO_CMD systemctl restart docker 2>/dev/null || $SUDO_CMD systemctl start docker
    $SUDO_CMD systemctl enable docker
fi

apply_ipv6_choice

if $gaming_node; then
    configure_gaming_node
else
    configure_bbr_basic
fi

open_node_ports "$NODE_PORT" "$additional_ports_input"

if $clean_install; then
    echo "$MSG_CLEAN_REINSTALL"
    if [ -f /opt/remnanode/docker-compose.yml ]; then
        cd /opt/remnanode && $SUDO_CMD docker compose down -v --remove-orphans 2>/dev/null || true
    fi
    $SUDO_CMD docker rm -f remnanode 2>/dev/null || true
    $SUDO_CMD rm -rf /opt/remnanode
fi

echo "$MSG_REMNANODE"
$SUDO_CMD mkdir -p /opt/remnanode
$SUDO_CMD chmod 755 /opt/remnanode
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
    environment:
      - NODE_PORT=$NODE_PORT
      - SECRET_KEY=$SECRET_KEY
EOF

$SUDO_CMD chmod 644 /opt/remnanode/docker-compose.yml

cd /opt/remnanode && $SUDO_CMD docker compose up -d --force-recreate

# Give the container a moment to appear in docker ps before checklist.
sleep 2

print_install_checklist

echo "$MSG_COMPLETE"
echo "$MSG_REBOOT"