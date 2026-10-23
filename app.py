"""Небольшой учебный обработчик заявок.

Тестовое задание САП КОД. Используются только синтетические данные.
"""

import sqlite3
from contextlib import closing


def _prepare_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            customer TEXT NOT NULL,
            message TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS leads_source_external_id
        ON leads (source, external_id);
        """
    )


def process_event(event: dict, db_path: str) -> dict:
    """Принимает событие и возвращает dict со статусом и, при успехе, id заявки.

    Контракт статусов и бизнес-правила описаны в README.md.
    """
    if not isinstance(event, dict):
        return {"status": "invalid", "reason": "Событие должно быть словарём"}

    values = []
    for field in ("source", "external_id", "customer", "message"):
        value = event.get(field)
        if not isinstance(value, str) or not value.strip():
            return {
                "status": "invalid",
                "reason": f"Поле {field} должно быть непустой строкой",
            }
        values.append(value.strip())
    source, external_id, customer, message = values

    with closing(sqlite3.connect(db_path)) as connection:
        _prepare_db(connection)
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO leads (source, external_id, customer, message)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (source, external_id) DO NOTHING
                """,
                (source, external_id, customer, message),
            )
            if cursor.rowcount == 0:
                lead_id = connection.execute(
                    "SELECT id FROM leads WHERE source = ? AND external_id = ?",
                    (source, external_id),
                ).fetchone()[0]
                return {"status": "duplicate", "id": lead_id}

            lead_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO notifications (lead_id) VALUES (?)", (lead_id,)
            )
            return {"status": "created", "id": lead_id}
