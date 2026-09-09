"""Тесты хранения бэкапов. Сеть и SSH не трогаются — только отбор файлов."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from app.services import panel_backup


class RetentionTests(unittest.TestCase):
    """Свежие держим подряд, дальше по одному в день, остальное убираем."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(panel_backup, "BACKUP_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, when: datetime, size: int = 200_000) -> str:
        name = f"panel-{when.strftime('%Y%m%d-%H%M')}.rwbk"
        (self.dir / name).write_bytes(b"x" * size)
        return name

    def test_listing_is_newest_first_and_ignores_strangers(self) -> None:
        base = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        self.make(base - timedelta(hours=6))
        newest = self.make(base)
        (self.dir / "заметка.txt").write_text("не бэкап")
        (self.dir / ".panel-20260909-1200.rwbk.part").write_bytes(b"incomplete")   # недокачанный кусок

        files = panel_backup.listing()
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0].name, newest)

    def test_recent_are_kept_whole(self) -> None:
        base = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        for i in range(10):
            self.make(base - timedelta(hours=6 * i))
        removed = panel_backup.prune(keep_recent=10, keep_daily=0)
        self.assertEqual(removed, [])
        self.assertEqual(len(panel_backup.listing()), 10)

    def test_older_collapse_to_one_per_day(self) -> None:
        base = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)
        for day in range(5):                      # пять суток по четыре снимка
            for hour in (0, 6, 12, 18):
                self.make(base - timedelta(days=day) - timedelta(hours=18 - hour))
        panel_backup.prune(keep_recent=4, keep_daily=30)
        left = panel_backup.listing()
        # четыре свежих + по одному на каждый оставшийся день
        days = {b.created.strftime("%Y%m%d") for b in left[4:]}
        self.assertEqual(len(left[4:]), len(days))
        self.assertGreaterEqual(len(left), 5)

    def test_daily_window_limits_depth(self) -> None:
        base = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        for day in range(20):
            self.make(base - timedelta(days=day))
        panel_backup.prune(keep_recent=2, keep_daily=5)
        self.assertEqual(len(panel_backup.listing()), 2 + 5)

    def test_nothing_to_prune_on_empty_dir(self) -> None:
        self.assertEqual(panel_backup.prune(28, 30), [])


class FetchGuardTests(unittest.TestCase):
    """До сети дело доходить не должно, если настройки неполные."""

    def test_missing_host_refuses(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            panel_backup.fetch({"host": "  "}, "ключ")
        self.assertIn("адрес", ctx.exception.args[0])

    def test_missing_key_refuses(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            panel_backup.fetch({"host": "panel"}, "   ")
        self.assertIn("ключ", ctx.exception.args[0])

    def test_unparsable_key_refuses(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            panel_backup.fetch({"host": "panel"}, "это не ключ")
        self.assertIn("разобрать", ctx.exception.args[0])


if __name__ == "__main__":
    unittest.main()
