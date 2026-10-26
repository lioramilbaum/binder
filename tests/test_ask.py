import unittest
from unittest.mock import Mock, patch

from binder import ask
from binder.ask import AskDisabled, answer_question, build_context, build_messages
from binder.config import ASK_DEFAULT_DOCUMENTS, CHAT_MODEL, CHAT_NUM_CTX, CHAT_TIMEOUT
from binder.embeddings import ChatUnavailable
from binder.folders import Folders


def document(doc_id, name, content, **extra):
    return {
        "id": doc_id,
        "folder_id": "folder-1",
        "folder_name": "Case",
        "name": name,
        "date": "2026-01-01",
        "time": "",
        "type": "regular",
        "description": "",
        "summary": "",
        "tags": ["rent"],
        "content": content,
        "attachments": [],
        **extra,
    }


class AnswerQuestionTests(unittest.TestCase):
    def setUp(self):
        self.factory = Mock()
        self.chat = Mock()
        self.chat.chat.return_value = "The rent is 5000 ILS [1]."

    def test_answer_uses_retrieved_document_text_and_returns_sources(self):
        with patch("binder.ask.folder_repository.search_documents") as search, patch(
            "binder.ask.folder_repository.documents_for_context"
        ) as load:
            search.return_value = {"results": [{"id": "doc-1"}], "semantic": "ok"}
            load.return_value = [document("doc-1", "Lease", "The rent is 5000 ILS.")]

            result = answer_question(
                self.factory, self.chat, "What is the rent?", embedder=None
            )

        load.assert_called_once_with(self.factory, ["doc-1"])
        messages = self.chat.chat.call_args.args[0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("The rent is 5000 ILS.", messages[1]["content"])
        self.assertIn("[1]", messages[1]["content"])
        self.assertIn("What is the rent?", messages[1]["content"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["answer"], "The rent is 5000 ILS [1].")
        self.assertEqual(result["sources"][0]["id"], "doc-1")
        self.assertEqual(result["semantic"], "ok")
        self.assertEqual(result["model"], CHAT_MODEL)

    def test_no_matches_skips_generation(self):
        with patch("binder.ask.folder_repository.search_documents") as search:
            search.return_value = {"results": [], "semantic": "ok"}

            result = answer_question(self.factory, self.chat, "unknown", embedder=None)

        self.chat.chat.assert_not_called()
        self.assertEqual(result["status"], "no_matches")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["sources"], [])

    def test_build_context_empty_result_skips_generation(self):
        with patch("binder.ask.folder_repository.search_documents") as search, patch(
            "binder.ask.folder_repository.documents_for_context"
        ) as load, patch("binder.ask.build_context", return_value=("", [])):
            search.return_value = {"results": [{"id": "doc-1"}], "semantic": "ok"}
            load.return_value = [document("doc-1", "Lease", "text")]

            result = answer_question(self.factory, self.chat, "rent", embedder=None)

        self.chat.chat.assert_not_called()
        self.assertEqual(result["status"], "no_matches")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["sources"], [])

    def test_search_is_hybrid_scoped_and_limited(self):
        embedder = Mock()
        with patch("binder.ask.folder_repository.search_documents") as search, patch(
            "binder.ask.folder_repository.documents_for_context", return_value=[]
        ):
            search.return_value = {"results": [], "semantic": "ok"}

            answer_question(
                self.factory, self.chat, "rent", embedder=embedder, folder_id="f1", limit=3
            )

        search.assert_called_once_with(
            self.factory, "rent", "f1", embedder=embedder, mode="hybrid", limit=3
        )

    def test_default_limit_is_used_when_not_given(self):
        with patch("binder.ask.folder_repository.search_documents") as search:
            search.return_value = {"results": [], "semantic": "ok"}

            answer_question(self.factory, self.chat, "rent", embedder=None)

        self.assertEqual(search.call_args.kwargs["limit"], ASK_DEFAULT_DOCUMENTS)

    def test_chat_options_set_num_ctx_and_model(self):
        with patch("binder.ask.folder_repository.search_documents") as search, patch(
            "binder.ask.folder_repository.documents_for_context"
        ) as load:
            search.return_value = {"results": [{"id": "doc-1"}], "semantic": "ok"}
            load.return_value = [document("doc-1", "Lease", "text")]

            answer_question(self.factory, self.chat, "rent", embedder=None)

        kwargs = self.chat.chat.call_args.kwargs
        self.assertEqual(kwargs["model"], CHAT_MODEL)
        self.assertEqual(kwargs["timeout"], CHAT_TIMEOUT)
        self.assertEqual(kwargs["options"], {"temperature": 0.2, "num_ctx": CHAT_NUM_CTX})

    def test_chat_unavailable_propagates(self):
        self.chat.chat.side_effect = ChatUnavailable("down")
        with patch("binder.ask.folder_repository.search_documents") as search, patch(
            "binder.ask.folder_repository.documents_for_context"
        ) as load:
            search.return_value = {"results": [{"id": "doc-1"}], "semantic": "ok"}
            load.return_value = [document("doc-1", "Lease", "text")]

            with self.assertRaises(ChatUnavailable):
                answer_question(self.factory, self.chat, "rent", embedder=None)

    def test_build_context_caps_each_document_and_total(self):
        long_doc = document("doc-1", "Lease", "x" * 5000)
        second_doc = document("doc-2", "Parking", "y" * 3000)

        context, sources = build_context(
            [long_doc, second_doc], total_chars=4500, per_document_chars=4000
        )

        self.assertIn("[1] Lease (Case, 2026-01-01, rent)", context)
        self.assertLessEqual(len(context), 4500)
        self.assertNotIn("[2]", context)
        self.assertNotIn("Parking", context)
        self.assertEqual([source["id"] for source in sources], ["doc-1"])
        self.assertEqual(sources[0]["index"], 1)
        self.assertEqual(sources[0]["folder_name"], "Case")

    def test_build_context_keeps_metadata_when_document_is_cut(self):
        context, _ = build_context(
            [document("doc-1", "Lease", "z" * 500)], per_document_chars=60
        )

        self.assertIn("[1] Lease (Case, 2026-01-01, rent)", context)
        self.assertLessEqual(len(context), 60)

    def test_build_messages_wraps_documents_before_question(self):
        messages = build_messages("Why?", "[1] Lease")

        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        user = messages[1]["content"]
        self.assertLess(user.index("[1] Lease"), user.index("Why?"))
        self.assertIn(ask.SYSTEM_PROMPT, messages[0]["content"])


class FoldersAskTests(unittest.TestCase):
    def test_folders_ask_without_chat_client_raises_ask_disabled(self):
        folders = Folders(factory=Mock())

        with self.assertRaises(AskDisabled):
            folders.ask("What is the rent?")


if __name__ == "__main__":
    unittest.main()
