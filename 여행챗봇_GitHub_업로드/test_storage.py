"""임시 데이터베이스로 대화 영구 저장과 프로젝트 분리를 검증합니다."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        db_patch = patch("storage.DB_PATH", Path(directory.name) / "chats.sqlite3")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        storage.initialize()
        with storage.connect() as db:
            db.execute("INSERT INTO projects VALUES ('default', '테스트 여행')")

    def test_persist_reopen_rename_and_project_separation(self):
        project = storage.create_project("제주 여행")
        first = storage.create_conversation("default")
        second = storage.create_conversation(project)
        storage.append_turn(first, "부산 2박 3일", "부산 일정")
        storage.append_turn(first, "예산 줄여줘", "수정 일정")
        storage.initialize()
        self.assertEqual([m["content"] for m in storage.load_messages(first)],
                         ["부산 2박 3일", "부산 일정", "예산 줄여줘", "수정 일정"])
        self.assertEqual(storage.load_messages(second), [])
        self.assertEqual([c["id"] for c in storage.list_conversations(project)], [second])
        self.assertEqual(storage.list_conversations("default")[0]["title"], "부산 2박 3일")
        storage.rename_conversation(first, "나의 부산 여행")
        self.assertEqual(storage.list_conversations("default")[0]["title"], "나의 부산 여행")

    def test_import_preserves_current_conversation(self):
        messages = [{"role": "user", "content": "이전 질문"},
                    {"role": "assistant", "content": "이전 답변"}]
        chat = storage.create_conversation("default", messages)
        self.assertEqual(storage.load_messages(chat), messages)

    def test_delete_removes_only_selected_project_and_does_not_recreate_default(self):
        other = storage.create_project("보존할 프로젝트")
        deleted_chat = storage.create_conversation("default")
        kept_chat = storage.create_conversation(other)
        storage.append_turn(kept_chat, "제주 여행", "제주 답변")
        self.assertEqual(storage.delete_project("default"), other)
        storage.initialize()
        self.assertEqual([p["id"] for p in storage.list_projects()], [other])
        self.assertEqual(storage.list_conversations("default"), [])
        with self.assertRaises(ValueError):
            storage.load_messages(deleted_chat)
        self.assertEqual(storage.load_messages(kept_chat)[0]["content"], "제주 여행")

    def test_delete_last_project_stays_empty_after_reinitialization(self):
        deleted_chat = storage.create_conversation("default")
        next_project = storage.delete_project("default")
        self.assertIsNone(next_project)
        storage.initialize()
        self.assertEqual(storage.list_projects(), [])
        with self.assertRaises(ValueError):
            storage.load_messages(deleted_chat)
        self.assertTrue(storage.create_project("다시 시작"))


if __name__ == "__main__":
    unittest.main()
