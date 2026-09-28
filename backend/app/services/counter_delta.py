"""Разница накопительных счётчиков агента между отсчётами сэмплера.

Агент отдаёт TCP OutSegs / RetransSegs и oom_kill как есть — с момента загрузки
ядра. Разницу считаем здесь, между двумя своими тиками, а не на агенте: /metrics
опрашивает ещё и живой список нод, и «разница с прошлого опроса» на агенте
раздала бы ретрансмиты разным читателям, а в историю попал бы обрывок.

Счётчики живут в ядре, так что перезапуск агента им не мешает. Перезагрузка
ноды обнуляет их — это ловим по boot_id и по отрицательной разнице и такой
интервал пропускаем, а не пишем мусор. Перезапуск самой панели теряет
предыдущий отсчёт: первый тик после него честно пустой.

С агента 0.1.22 рядом идут TcpExt TCPSynRetrans и TCPOrigDataSent. По ним доля
ретрансмитов считается как (RetransSegs − TCPSynRetrans) / TCPOrigDataSent:
на тихой ноде RetransSegs / OutSegs раздувают повторы SYN-ACK сканерам и SYN в
мёртвые соединения, а в OutSegs сидят чистые ACK. Так us-1 показывал 8% при
0,08% на данных. Без новых полей (старый агент) — прежняя формула.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CounterSnapshot:
    boot_id: str | None
    tcp_out: int | None
    tcp_retrans: int | None
    oom_kill: int | None
    # TcpExt TCPSynRetrans / TCPOrigDataSent — с агента 0.1.22.
    tcp_syn_retrans: int | None = None
    tcp_orig_data: int | None = None

    def empty(self) -> bool:
        return (
            self.tcp_out is None
            and self.tcp_retrans is None
            and self.oom_kill is None
            and self.tcp_syn_retrans is None
            and self.tcp_orig_data is None
        )


EMPTY_DELTAS: dict[str, int | None] = {
    "tcp_out_segs": None,
    "tcp_retrans_segs": None,
    "tcp_syn_retrans": None,
    "tcp_orig_data_sent": None,
    "oom_kills": None,
}

# Порог объёма по умолчанию: меньше 20 000 сегментов с данными за 5 минут — доля
# ретрансмитов уже не про сеть, а про пару случайных повторов. Цифру отдаём, но
# с пометкой «мало данных», и фронт её не красит.
RETR_MIN_DATA_SEGS_5MIN = 20_000
_RETR_MIN_WINDOW_SEC = 300.0


def _delta(prev: int | None, cur: int | None) -> int | None:
    if prev is None or cur is None:
        return None
    d = cur - prev
    # Отрицательная разница — счётчик обнулился (перезагрузка, 32-битный
    # переход на старом ядре). Сколько было до сброса, уже не узнать.
    return d if d >= 0 else None


def counter_deltas(
    prev: CounterSnapshot | None, cur: CounterSnapshot
) -> dict[str, int | None]:
    """Дельты за интервал между двумя отсчётами одной ноды."""
    if prev is None:
        return dict(EMPTY_DELTAS)
    if prev.boot_id and cur.boot_id and prev.boot_id != cur.boot_id:
        return dict(EMPTY_DELTAS)

    out = _delta(prev.tcp_out, cur.tcp_out)
    retrans = _delta(prev.tcp_retrans, cur.tcp_retrans)
    # Ретрансмиты читаются только в паре с отправленным. Ноль отправленных
    # сегментов у живой ноды не бывает — это тот же отсчёт, что и прошлый
    # (агент на миг пропал, и опрос отдал закешированный статус). Ретрансмитов
    # больше, чем отправлено, тоже не бывает. В обоих случаях интервал пустой.
    if out is None or retrans is None or out == 0 or retrans > out:
        out = retrans = None

    syn = _delta(prev.tcp_syn_retrans, cur.tcp_syn_retrans)
    orig = _delta(prev.tcp_orig_data, cur.tcp_orig_data)
    # Новая пара читается только вместе со старой: числитель — те же RetransSegs
    # за вычетом SYN. Пустой интервал по OutSegs/RetransSegs — пустой и здесь;
    # нет одного из новых счётчиков — остаёмся на старой формуле. Ноль сегментов
    # с данными при живом OutSegs бывает (нода только отвечает на SYN) — пишем.
    if out is None or syn is None or orig is None:
        syn = orig = None

    return {
        "tcp_out_segs": out,
        "tcp_retrans_segs": retrans,
        "tcp_syn_retrans": syn,
        "tcp_orig_data_sent": orig,
        "oom_kills": _delta(prev.oom_kill, cur.oom_kill),
    }


def retr_pct(retrans: float | int | None, out: float | int | None) -> float | None:
    """Доля ретрансмитов, %. Считается от сумм за шаг, а не среднее процентов:
    минута с десятком сегментов не должна весить как минута с миллионом."""
    if retrans is None or out is None:
        return None
    try:
        r, o = float(retrans), float(out)
    except (TypeError, ValueError):
        return None
    if o <= 0 or r < 0:
        return None
    return round(min(100.0, r / o * 100.0), 2)


def _num(v: float | int | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def retr_data_segs(
    retrans: float | int | None, syn_retrans: float | int | None
) -> int | None:
    """Числитель новой формулы: RetransSegs − TCPSynRetrans, не меньше нуля.

    Минус бывает из-за того, что snmp и netstat читаются не одним вызовом, и
    SYN-повтор успевает попасть в один файл раньше, чем в другой.
    """
    r, s = _num(retrans), _num(syn_retrans)
    if r is None or s is None:
        return None
    return int(max(0.0, r - s))


def retr_pct_data(
    retrans: float | int | None,
    syn_retrans: float | int | None,
    orig_data: float | int | None,
) -> float | None:
    """Доля ретрансмитов на сегмент с данными, %: (retrans − syn) / orig_data · 100."""
    num = retr_data_segs(retrans, syn_retrans)
    o = _num(orig_data)
    if num is None or o is None or o <= 0:
        return None
    return round(min(100.0, num / o * 100.0), 2)


def retr_low_volume(
    orig_data: float | int | None,
    covered_sec: float,
    min_segs_5min: int = RETR_MIN_DATA_SEGS_5MIN,
) -> bool | None:
    """Мало ли данных ушло за точку, чтобы доле ретрансмитов верить.

    Порог задан на 5 минут и пересчитывается пропорционально времени, которое
    точка реально покрывает: шаг недели — 20 минут, а последний шаг графика
    обычно заполнен лишь частично, и мерить его полным порогом значило бы
    гасить живую ноду в каждой свежей точке. None — порог не к чему применить.
    """
    o = _num(orig_data)
    if o is None or covered_sec <= 0:
        return None
    need = max(0, min_segs_5min) * covered_sec / _RETR_MIN_WINDOW_SEC
    return o < need


def retr_point(
    *,
    retrans: float | int | None,
    out: float | int | None,
    retrans_new: float | int | None,
    syn_retrans: float | int | None,
    orig_data: float | int | None,
    covered_sec: float,
    min_segs_5min: int = RETR_MIN_DATA_SEGS_5MIN,
) -> tuple[float | None, bool | None]:
    """Доля ретрансмитов за точку графика и флаг «мало данных».

    retrans_new — сумма RetransSegs только по отсчётам, где есть и новые поля:
    в шаге, где агент обновился посередине, старые отсчёты не должны попасть в
    числитель новой формулы без своих SYN и сегментов с данными.
    """
    if syn_retrans is not None and orig_data is not None:
        return (
            retr_pct_data(retrans_new, syn_retrans, orig_data),
            retr_low_volume(orig_data, covered_sec, min_segs_5min),
        )
    # Старый агент: прежняя формула, объём по ней не оцениваем.
    return retr_pct(retrans, out), None


class CounterTracker:
    """Последний отсчёт счётчиков по каждой ноде — в памяти процесса."""

    def __init__(self) -> None:
        self._last: dict[str, CounterSnapshot] = {}

    def update(self, node_id: str, cur: CounterSnapshot | None) -> dict[str, int | None]:
        if cur is None or cur.empty():
            # Агент не ответил или слишком старый — прошлый отсчёт держим:
            # следующий удачный тик покроет весь пропуск, события не потеряются.
            return dict(EMPTY_DELTAS)
        prev = self._last.get(node_id)
        self._last[node_id] = cur
        return counter_deltas(prev, cur)

    def forget(self, node_id: str) -> None:
        self._last.pop(node_id, None)
