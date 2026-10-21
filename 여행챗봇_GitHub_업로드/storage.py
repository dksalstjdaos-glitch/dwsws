"""로컬 SQLite에 프로젝트와 대화를 저장합니다. API 키는 저장하지 않습니다."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

DB_PATH = Path(__file__).resolve().parent / ".data" / "conversations.sqlite3"


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    with connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
            messages TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS project_plans (
            project_id TEXT PRIMARY KEY, conditions TEXT NOT NULL DEFAULT '{}',
            selected_places TEXT NOT NULL DEFAULT '[]')""")
        db.execute("""CREATE TABLE IF NOT EXISTS execution_runs (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, question TEXT NOT NULL,
            answer TEXT NOT NULL, execution TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS saved_itineraries (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_run_id TEXT NOT NULL,
            title TEXT NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(project_id, source_run_id))""")


def list_projects():
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM projects ORDER BY rowid")]


def create_project(name):
    name = name.strip()
    if not name:
        raise ValueError("프로젝트 이름을 입력해 주세요.")
    project_id = uuid4().hex
    with connect() as db:
        db.execute("INSERT INTO projects VALUES (?, ?)", (project_id, name[:80]))
    return project_id


def list_conversations(project_id):
    with connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT id, title FROM conversations WHERE project_id=? ORDER BY updated_at DESC, rowid DESC",
            (project_id,),
        )]


def create_conversation(project_id, messages=None):
    messages = messages or []
    title = messages[0]["content"][:40] if messages else "새 대화"
    conversation_id = uuid4().hex
    with connect() as db:
        db.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
            (conversation_id, project_id, title, json.dumps(messages, ensure_ascii=False)),
        )
    return conversation_id


def load_messages(conversation_id):
    with connect() as db:
        row = db.execute("SELECT messages FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    if row is None:
        raise ValueError("저장된 대화를 찾을 수 없습니다.")
    return json.loads(row["messages"])


def append_turn(conversation_id, question, answer, lookups=None, execution=None):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT messages FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if row is None:
            raise ValueError("저장된 대화를 찾을 수 없습니다.")
        messages = json.loads(row["messages"])
        first_turn = not messages
        messages.extend([
            {"role": "user", "content": question.strip()},
            {"role": "assistant", "content": answer, **({"lookups": lookups} if lookups is not None else {}),
             **({"execution": execution} if execution else {})},
        ])
        db.execute(
            "UPDATE conversations SET messages=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
            (json.dumps(messages, ensure_ascii=False), conversation_id),
        )
        if first_turn:
            db.execute("UPDATE conversations SET title=? WHERE id=? AND title='새 대화'",
                       (question.strip()[:40], conversation_id))


def rename_conversation(conversation_id, title):
    title = title.strip()
    if not title:
        raise ValueError("대화 이름을 입력해 주세요.")
    with connect() as db:
        db.execute("UPDATE conversations SET title=? WHERE id=?", (title[:80], conversation_id))


def delete_project(project_id):
    """프로젝트와 소속 대화를 한 트랜잭션으로 삭제하고 다음 프로젝트를 반환합니다."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise ValueError("프로젝트를 찾을 수 없습니다.")
        db.execute("DELETE FROM execution_runs WHERE conversation_id IN (SELECT id FROM conversations WHERE project_id=?)", (project_id,))
        db.execute("DELETE FROM conversations WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM project_plans WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM saved_itineraries WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM projects WHERE id=?", (project_id,))
        remaining = db.execute("SELECT id FROM projects ORDER BY rowid LIMIT 1").fetchone()
        if remaining is not None:
            return remaining["id"]
        return None


def load_plan(project_id):
    with connect() as db:
        row = db.execute("SELECT * FROM project_plans WHERE project_id=?", (project_id,)).fetchone()
    return {"conditions": json.loads(row["conditions"]), "selected_places": json.loads(row["selected_places"])} if row else {
        "conditions": {}, "selected_places": [],
    }


def save_plan(project_id, *, conditions=None, selected_places=None):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise ValueError("프로젝트가 없습니다.")
        db.execute("INSERT OR IGNORE INTO project_plans (project_id) VALUES (?)", (project_id,))
        if conditions is not None:
            db.execute("UPDATE project_plans SET conditions=? WHERE project_id=?",
                       (json.dumps(conditions, ensure_ascii=False), project_id))
        if selected_places is not None:
            db.execute("UPDATE project_plans SET selected_places=? WHERE project_id=?",
                       (json.dumps(selected_places, ensure_ascii=False), project_id))


def project_places(project_id):
    with connect() as db:
        rows = db.execute("SELECT messages FROM conversations WHERE project_id=? ORDER BY updated_at DESC", (project_id,)).fetchall()
    places = {}
    for row in rows:
        for message in reversed(json.loads(row["messages"])):
            for lookup in message.get("lookups", []):
                if lookup.get("kind") == "places" and lookup.get("status") == "success":
                    for place in lookup.get("places", []):
                        key = str(place.get("id") or place.get("url") or (place.get("name", "") + place.get("address", "")))
                        if key not in places:
                            places[key] = {**place, "key": key}
    return list(places.values())


def project_place_records(project_id):
    """계획 검증용으로 실패·날짜 범위도 포함한 원본 조회 기록을 반환합니다."""
    with connect() as db:
        rows = db.execute("SELECT messages FROM conversations WHERE project_id=? ORDER BY updated_at DESC", (project_id,)).fetchall()
    records = []
    for row in rows:
        for message in reversed(json.loads(row["messages"])):
            records.extend(r for r in message.get("lookups", []) if r.get("kind") == "places")
    return records[:100]


def save_execution(conversation_id, question, answer, execution):
    if not execution or not execution.get("run_id"):
        return
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO execution_runs VALUES (?, ?, ?, ?, ?)",
                   (execution["run_id"], conversation_id, question.strip(), answer,
                    json.dumps(execution, ensure_ascii=False)))


def list_executions(project_id):
    with connect() as db:
        rows = db.execute("""SELECT r.* FROM execution_runs r JOIN conversations c ON c.id=r.conversation_id
                             WHERE c.project_id=? ORDER BY r.rowid DESC LIMIT 100""", (project_id,)).fetchall()
    return [{**dict(row), "execution": json.loads(row["execution"])} for row in rows]


def save_itinerary(project_id, title, message):
    """생성 시점의 계획·예보·검증 기록을 변경 없는 별도 사본으로 보관합니다."""
    title = title.strip()
    execution = message.get("execution") or {}
    run_id = execution.get("run_id")
    if not title or not run_id or not execution.get("trip_schedule"):
        raise ValueError("저장할 계획과 이름을 확인해 주세요.")
    snapshot = {"role": "assistant", "content": message["content"],
                "lookups": message.get("lookups", []), "execution": execution}
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise ValueError("프로젝트가 없습니다.")
        db.execute("""INSERT OR IGNORE INTO saved_itineraries
            VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))""",
            (uuid4().hex, project_id, run_id, title[:80], json.dumps(snapshot, ensure_ascii=False)))
        return db.execute("SELECT id FROM saved_itineraries WHERE project_id=? AND source_run_id=?",
                          (project_id, run_id)).fetchone()["id"]


def list_itineraries(project_id):
    with connect() as db:
        rows = db.execute("""SELECT id, title, source_run_id, created_at FROM saved_itineraries
            WHERE project_id=? ORDER BY rowid DESC""", (project_id,)).fetchall()
    return [dict(row) for row in rows]


def load_itinerary(project_id, itinerary_id):
    with connect() as db:
        row = db.execute("SELECT * FROM saved_itineraries WHERE project_id=? AND id=?",
                         (project_id, itinerary_id)).fetchone()
    if row is None:
        raise ValueError("저장된 계획을 찾을 수 없습니다.")
    return {**dict(row), "snapshot": json.loads(row["snapshot"])}


def delete_itinerary(project_id, itinerary_id):
    """지정한 프로젝트의 보관 사본만 삭제합니다. 원본 대화는 유지합니다."""
    with connect() as db:
        result = db.execute("DELETE FROM saved_itineraries WHERE project_id=? AND id=?",
                            (project_id, itinerary_id))
        if result.rowcount != 1:
            raise ValueError("저장된 계획을 찾을 수 없습니다.")
