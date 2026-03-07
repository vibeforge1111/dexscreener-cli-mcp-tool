"""Tests for the state store (presets, tasks, export/import)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dexscreener_cli.config import ScanFilters
from dexscreener_cli.state import ScanPreset, ScanTask, StateStore


@pytest.fixture()
def store(tmp_path: Path) -> StateStore:
    return StateStore(base_dir=tmp_path)


class TestPresets:
    def test_save_and_list(self, store: StateStore) -> None:
        preset = ScanPreset.from_filters("degen", ScanFilters(chains=("solana",), limit=10))
        store.save_preset(preset)
        presets = store.list_presets()
        assert len(presets) == 1
        assert presets[0].name == "degen"

    def test_get_preset(self, store: StateStore) -> None:
        preset = ScanPreset.from_filters("test", ScanFilters(chains=("solana",)))
        store.save_preset(preset)
        found = store.get_preset("TEST")  # Case-insensitive.
        assert found is not None
        assert found.name == "test"

    def test_get_missing_returns_none(self, store: StateStore) -> None:
        assert store.get_preset("nope") is None

    def test_delete_preset(self, store: StateStore) -> None:
        preset = ScanPreset.from_filters("rm-me", ScanFilters(chains=("solana",)))
        store.save_preset(preset)
        assert store.delete_preset("rm-me") is True
        assert store.get_preset("rm-me") is None

    def test_delete_missing_returns_false(self, store: StateStore) -> None:
        assert store.delete_preset("ghost") is False

    def test_save_overwrites_existing(self, store: StateStore) -> None:
        p1 = ScanPreset.from_filters("x", ScanFilters(chains=("solana",), limit=5))
        store.save_preset(p1)
        p2 = ScanPreset.from_filters("x", ScanFilters(chains=("solana",), limit=99))
        store.save_preset(p2)
        assert len(store.list_presets()) == 1
        assert store.get_preset("x").limit == 99  # type: ignore[union-attr]


class TestTasks:
    def test_create_and_list(self, store: StateStore) -> None:
        task = store.create_task(name="scout", interval_seconds=60)
        assert task.name == "scout"
        assert len(store.list_tasks()) == 1

    def test_create_duplicate_raises(self, store: StateStore) -> None:
        store.create_task(name="dup")
        with pytest.raises(ValueError, match="already exists"):
            store.create_task(name="dup")

    def test_get_task_by_name(self, store: StateStore) -> None:
        store.create_task(name="finder")
        assert store.get_task("FINDER") is not None  # Case-insensitive.

    def test_get_task_by_id(self, store: StateStore) -> None:
        task = store.create_task(name="by-id")
        assert store.get_task(task.id) is not None

    def test_delete_task(self, store: StateStore) -> None:
        store.create_task(name="gone")
        assert store.delete_task("gone") is True
        assert store.get_task("gone") is None

    def test_update_status(self, store: StateStore) -> None:
        task = store.create_task(name="status-test")
        updated = store.update_task_status(task.id, status="running")
        assert updated.status == "running"


class TestExportRedaction:
    def test_export_redacts_secrets(self, store: StateStore) -> None:
        store.create_task(
            name="secret-task",
            alerts={
                "webhook_url": "https://evil.com/hook",
                "discord_webhook_url": "https://discord.com/api/webhooks/123/abc",
                "telegram_bot_token": "123:ABC",
                "telegram_chat_id": "456",
                "min_score": 70,
            },
        )
        bundle = store.export_bundle()
        task_data = bundle["tasks"][0]
        alerts = task_data["alerts"]
        assert "webhook_url" not in alerts
        assert "discord_webhook_url" not in alerts
        assert "telegram_bot_token" not in alerts
        assert "telegram_chat_id" not in alerts
        assert alerts["_redacted"] is True
        # Non-secret fields preserved.
        assert alerts["min_score"] == 70

    def test_export_no_alerts_unchanged(self, store: StateStore) -> None:
        store.create_task(name="no-alerts")
        bundle = store.export_bundle()
        task_data = bundle["tasks"][0]
        assert task_data["alerts"] is None


class TestImportBundle:
    def test_import_merge(self, store: StateStore) -> None:
        store.create_task(name="existing")
        bundle = {
            "presets": [{"name": "new-preset", "chains": ["solana"], "limit": 10,
                         "min_liquidity_usd": 1000, "min_volume_h24_usd": 1000,
                         "min_txns_h1": 5, "min_price_change_h1": -10}],
            "tasks": [{"id": "imp1", "name": "imported", "status": "todo", "notes": ""}],
            "runs": [],
        }
        counts = store.import_bundle(bundle, mode="merge")
        assert counts["presets"] == 1
        assert counts["tasks"] == 2  # existing + imported

    def test_import_replace(self, store: StateStore) -> None:
        store.create_task(name="will-be-gone")
        bundle = {
            "presets": [],
            "tasks": [{"id": "rep1", "name": "only-one", "status": "todo", "notes": ""}],
            "runs": [],
        }
        counts = store.import_bundle(bundle, mode="replace")
        assert counts["tasks"] == 1
        assert store.get_task("will-be-gone") is None
        assert store.get_task("only-one") is not None
