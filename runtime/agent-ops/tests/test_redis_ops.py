from __future__ import annotations

import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(SCRIPT_DIR))

import redis_ops  # noqa: E402


class FakeRedis:
    def __init__(self, values: dict[int, int]) -> None:
        self.values = values
        self.current = 0

    def ping(self) -> bool:
        return True

    def select(self, database: int) -> bool:
        self.current = database
        return True

    def dbsize(self) -> int:
        return self.values.get(self.current, 0)

    def close(self) -> None:
        return None


class RedisOpsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_env = dict(os.environ)
        os.environ["LYSTAR_HOME"] = str(Path(self.temp.name) / ".lystar")

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)
        self.temp.cleanup()

    def settings(self) -> dict[str, object]:
        return redis_ops.resolve_settings(
            Namespace(
                profile="inline-test",
                host="redis.example",
                port=6379,
                username="default",
                password="not-output",
                tls=False,
                database_count=4,
                socket_timeout=1.0,
            )
        )

    def test_free_database_excludes_used_and_reserved_indexes(self) -> None:
        settings = self.settings()
        with patch.object(redis_ops, "database_count", return_value=(4, None)), \
            patch.object(redis_ops, "connect", return_value=FakeRedis({0: 2, 1: 0, 2: 0, 3: 1})):
            first = redis_ops.free_databases(settings)
        self.assertEqual(first["selected"], 1)
        self.assertEqual([item["database"] for item in first["available"]], [1, 2])

        with patch.object(redis_ops, "database_count", return_value=(4, None)), \
            patch.object(redis_ops, "connect", return_value=FakeRedis({0: 2, 1: 0, 2: 0, 3: 1})):
            reserved = redis_ops.reserve_database(settings, "demo")
        self.assertEqual(reserved["status"], "reserved")
        self.assertEqual(reserved["reservation"]["database"], 1)

        with patch.object(redis_ops, "database_count", return_value=(4, None)), \
            patch.object(redis_ops, "connect", return_value=FakeRedis({0: 2, 1: 0, 2: 0, 3: 1})):
            second = redis_ops.free_databases(settings)
        self.assertEqual(second["selected"], 2)

    def test_reassign_preserves_old_database_and_moves_only_reservation(self) -> None:
        settings = self.settings()
        with patch.object(redis_ops, "database_count", return_value=(4, None)), patch.object(
            redis_ops, "connect", return_value=FakeRedis({0: 0, 1: 0, 2: 0, 3: 0})
        ):
            reserved = redis_ops.reserve_database(settings, "demo", database=1)
            preview = redis_ops.reassign_database(settings, "demo", database=2, confirm=False)
            moved = redis_ops.reassign_database(settings, "demo", database=2, confirm=True)
        self.assertEqual(reserved["reservation"]["database"], 1)
        self.assertEqual(preview["status"], "dry_run")
        self.assertEqual(moved["status"], "reassigned")
        self.assertEqual(moved["released_database"], 1)
        self.assertEqual(moved["reservation"]["database"], 2)
        self.assertEqual(redis_ops.load_reservations()["reservations"]["demo"]["database"], 2)

        settings = self.settings()
        public = redis_ops.safe_public_settings(settings)
        self.assertNotIn("password", public)
        self.assertEqual(settings["password"], "not-output")


if __name__ == "__main__":
    unittest.main()
