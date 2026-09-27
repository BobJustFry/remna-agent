"""Разница накопительных счётчиков агента между отсчётами сэмплера.

Агент отдаёт TCP OutSegs / RetransSegs и oom_kill как есть — с момента загрузки
ядра. Разницу считаем здесь, между двумя своими тиками, а не на агенте: /metrics
опрашивает ещё и живой список нод, и «разница с прошлого опроса» на агенте
раздала бы ретрансмиты разным читателям, а в историю попал бы обрывок.

Счётчики живут в ядре, так что перезапуск агента им не мешает. Перезагрузка
ноды обнуляет их — это ловим по boot_id и по отрицательной разнице и такой
интервал пропускаем, а не пишем мусор. Перезапуск самой панели теряет
предыдущий отсчёт: первый тик после него честно пустой.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CounterSnapshot:
    boot_id: str | None
    tcp_out: int | None
    tcp_retrans: int | None
    oom_kill: int | None

    def empty(self) -> bool:
        return self.tcp_out is None and self.tcp_retrans is None and self.oom_kill is None


EMPTY_DELTAS: dict[str, int | None] = {
    "tcp_out_segs": None,
    "tcp_retrans_segs": None,
    "oom_kills": None,
}


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

    return {
        "tcp_out_segs": out,
        "tcp_retrans_segs": retrans,
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
