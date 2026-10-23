"""Публичные тесты. Часть из них на исходной версии приложения падает — это нормально."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import process_event


GOOD = {
    "source": "telegram",
    "external_id": "event-100",
    "customer": "Учебный клиент",
    "message": "Нужна доставка в Казань",
}


class LeadProcessorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "leads.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def count(self, table):
        with closing(sqlite3.connect(self.db_path)) as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if exists else 0

    def test_new_lead_creates_one_notification(self):
        response = process_event(dict(GOOD), self.db_path)
        self.assertEqual(response["status"], "created")
        self.assertIsInstance(response["id"], int)
        self.assertEqual(self.count("leads"), 1)
        self.assertEqual(self.count("notifications"), 1)

    def test_retry_is_duplicate_without_new_notification(self):
        first = process_event(dict(GOOD), self.db_path)
        second = process_event(dict(GOOD), self.db_path)
        self.assertEqual(second, {"status": "duplicate", "id": first["id"]})
        self.assertEqual(self.count("leads"), 1)
        self.assertEqual(self.count("notifications"), 1)

    def test_same_external_id_from_different_source_is_new(self):
        first = process_event(dict(GOOD), self.db_path)
        second = process_event({**GOOD, "source": "max"}, self.db_path)
        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "created")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.count("leads"), 2)
        self.assertEqual(self.count("notifications"), 2)

    def test_missing_customer_is_invalid_and_not_saved(self):
        result = process_event({**GOOD, "customer": "   "}, self.db_path)
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(result.get("reason"))
        self.assertEqual(self.count("leads"), 0)
        self.assertEqual(self.count("notifications"), 0)

    def test_whitespace_is_trimmed_before_comparison(self):
        first = process_event({**GOOD, "source": " telegram ", "external_id": " event-100 "}, self.db_path)
        second = process_event(dict(GOOD), self.db_path)
        self.assertEqual(first["status"], "created")
        self.assertEqual(second, {"status": "duplicate", "id": first["id"]})
        self.assertEqual(self.count("leads"), 1)

    def test_first_event_data_is_preserved_on_retry(self):
        first = process_event(dict(GOOD), self.db_path)
        response = process_event({**GOOD, "customer": "Другая подпись", "message": "Изменённый текст"}, self.db_path)
        self.assertEqual(response, {"status": "duplicate", "id": first["id"]})
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute("SELECT customer, message FROM leads WHERE id=?", (first["id"],)).fetchone()
        self.assertEqual(row, (GOOD["customer"], GOOD["message"]))

    def test_duplicate_after_reopening_database(self):
        first = process_event(dict(GOOD), self.db_path)
        # Второй вызов открывает отдельное подключение к той же БД.
        second = process_event(dict(GOOD), self.db_path)
        self.assertEqual(second, {"status": "duplicate", "id": first["id"]})

    def test_non_string_field_is_invalid(self):
        response = process_event({**GOOD, "external_id": 100}, self.db_path)
        self.assertEqual(response["status"], "invalid")
        self.assertEqual(self.count("leads"), 0)

    def test_all_required_fields_are_validated_without_changing_saved_data(self):
        first = process_event(dict(GOOD), self.db_path)
        missing = object()
        for field in GOOD:
            for value in (missing, None, 100, False, [], {}, "", " \t\n"):
                with self.subTest(field=field, value=value):
                    event = dict(GOOD)
                    if value is missing:
                        del event[field]
                    else:
                        event[field] = value
                    result = process_event(event, self.db_path)
                    self.assertEqual(result["status"], "invalid")
                    self.assertIsInstance(result.get("reason"), str)
                    self.assertTrue(result["reason"].strip())
                    self.assertEqual(self.count("leads"), 1)
                    self.assertEqual(self.count("notifications"), 1)
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT customer, message FROM leads WHERE id = ?", (first["id"],)
            ).fetchone()
        self.assertEqual(row, (GOOD["customer"], GOOD["message"]))

    def test_all_fields_are_trimmed_before_saving(self):
        event = {field: f" \t{value}\n " for field, value in GOOD.items()}
        result = process_event(event, self.db_path)
        self.assertEqual(result["status"], "created")
        self.assertGreater(result["id"], 0)
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT source, external_id, customer, message FROM leads WHERE id = ?",
                (result["id"],),
            ).fetchone()
        self.assertEqual(row, tuple(GOOD.values()))

    def test_source_case_is_significant(self):
        first = process_event(dict(GOOD), self.db_path)
        second = process_event({**GOOD, "source": "Telegram"}, self.db_path)
        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "created")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.count("leads"), 2)
        self.assertEqual(self.count("notifications"), 2)

    def test_different_external_ids_from_same_source_are_new(self):
        first = process_event(dict(GOOD), self.db_path)
        second = process_event({**GOOD, "external_id": "event-101"}, self.db_path)
        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "created")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.count("leads"), 2)
        self.assertEqual(self.count("notifications"), 2)

    def test_non_dictionary_event_is_invalid(self):
        for event in (None, [], "event", 100, False):
            with self.subTest(event=event):
                result = process_event(event, self.db_path)
                self.assertEqual(result["status"], "invalid")
                self.assertTrue(result.get("reason"))
                self.assertEqual(self.count("leads"), 0)
                self.assertEqual(self.count("notifications"), 0)

    def test_notification_failure_rolls_back_new_lead(self):
        first = process_event(dict(GOOD), self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                """
                CREATE TRIGGER reject_notification BEFORE INSERT ON notifications
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic notification failure');
                END;
                """
            )
        with self.assertRaises(sqlite3.IntegrityError):
            process_event({**GOOD, "external_id": "event-101"}, self.db_path)
        self.assertEqual(self.count("leads"), 1)
        self.assertEqual(self.count("notifications"), 1)
        self.assertEqual(
            process_event(dict(GOOD), self.db_path),
            {"status": "duplicate", "id": first["id"]},
        )


if __name__ == "__main__":
    unittest.main()
