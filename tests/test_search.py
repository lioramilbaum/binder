import os
import unittest
from unittest.mock import MagicMock, Mock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from binder import folder_repository
from binder.database import Base
from binder.embeddings import (
    SEMANTIC_SCHEMA_SQL,
    EmbeddingUnavailable,
    embed_document,
    embed_pending,
    pending_document_ids,
)
from binder.large_objects import RELEASE_FUNCTION_SQL, RELEASE_TRIGGER_SQL
from binder.search import (
    MAX_QUERY_TERMS,
    SEARCH_SCHEMA_SQL,
    build_tsquery,
    reciprocal_rank_fusion,
)


_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
_POSTGRES_REASON = (
    "search needs PostgreSQL (tsvector, pgvector); set TEST_DATABASE_URL to a disposable "
    "Postgres database to run these tests (its tables are dropped and recreated for every test)"
)


class BuildTsqueryTests(unittest.TestCase):
    def test_words_become_prefix_terms(self):
        self.assertEqual(build_tsquery("  Lease  contract!"), "Lease:* & contract:*")

    def test_hebrew_words_are_kept(self):
        self.assertEqual(build_tsquery("חוזה שכירות"), "חוזה:* & שכירות:*")

    def test_operators_are_stripped(self):
        self.assertEqual(build_tsquery("a' | b & !(c"), "a:* & b:* & c:*")

    def test_input_without_words_gives_none(self):
        self.assertIsNone(build_tsquery("!!! ???"))
        self.assertIsNone(build_tsquery(""))

    def test_terms_are_capped(self):
        raw = " ".join(f"word{index}" for index in range(MAX_QUERY_TERMS + 4))

        query = build_tsquery(raw)

        self.assertEqual(query.count(":*"), MAX_QUERY_TERMS)
        self.assertNotIn(f"word{MAX_QUERY_TERMS}", query)


class ReciprocalRankFusionTests(unittest.TestCase):
    def test_document_in_both_lists_beats_document_in_one(self):
        fused = reciprocal_rank_fusion([["a", "b"], ["c", "a"]])

        self.assertEqual(fused[0][0], "a")

    def test_ties_are_deterministic(self):
        first = reciprocal_rank_fusion([["x"], ["y"]])
        second = reciprocal_rank_fusion([["x"], ["y"]])

        self.assertEqual(first, second)
        self.assertEqual([item for item, _ in first], ["x", "y"])

    def test_empty_inputs(self):
        self.assertEqual(reciprocal_rank_fusion([]), [])
        self.assertEqual(reciprocal_rank_fusion([[], []]), [])
        self.assertEqual([item for item, _ in reciprocal_rank_fusion([["only"], []])], ["only"])


class FakeEmbedder:
    """Maps texts containing a known word onto one axis of a 1024-dim unit vector."""

    model = "bge-m3"
    dimensions = 1024

    def __init__(self, concepts):
        self.concepts = concepts
        self.error = None
        self.on_embed = None

    def embed(self, texts, *, kind="doc", timeout=0):
        if self.on_embed is not None:
            self.on_embed()
        if self.error is not None:
            raise self.error
        vectors = []
        for item in texts:
            lowered = item.lower()
            axis = next((index for word, index in self.concepts.items() if word in lowered), 1000)
            vector = [0.0] * self.dimensions
            vector[axis] = 1.0
            vectors.append(vector)
        return vectors


class SearchModeTests(unittest.TestCase):
    """Mode and fallback logic with the database stubbed out."""

    def setUp(self):
        keyword = patch("binder.folder_repository._keyword_candidates", return_value=[])
        pending = patch("binder.folder_repository.pending_document_ids", return_value=[])
        self.keyword = keyword.start()
        self.pending = pending.start()
        self.addCleanup(keyword.stop)
        self.addCleanup(pending.stop)
        self.factory = MagicMock()
        self.embedder = Mock(model="bge-m3")
        self.embedder.embed.return_value = [[1.0, 0.0]]

    def test_keyword_mode_never_calls_embedder(self):
        result = folder_repository.search_documents(self.factory, "lease contract", mode="keyword", embedder=self.embedder)

        self.embedder.embed.assert_not_called()
        self.assertEqual(result["semantic"], "skipped")
        self.assertEqual(self.keyword.call_count, 1)

    def test_embedding_failure_yields_keyword_only_results(self):
        self.embedder.embed.side_effect = EmbeddingUnavailable("down")

        result = folder_repository.search_documents(self.factory, "lease contract", embedder=self.embedder)

        self.assertEqual(result["semantic"], "unavailable")
        self.assertEqual(result["results"], [])
        self.assertEqual(self.keyword.call_count, 1)

    def test_short_query_skips_semantic_search(self):
        result = folder_repository.search_documents(self.factory, "ab", embedder=self.embedder)

        self.embedder.embed.assert_not_called()
        self.assertEqual(result["semantic"], "skipped")

    def test_no_embedder_reports_semantic_disabled(self):
        result = folder_repository.search_documents(self.factory, "lease contract")

        self.assertEqual(result["semantic"], "disabled")


@unittest.skipUnless(_TEST_DATABASE_URL, _POSTGRES_REASON)
class SearchRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(_TEST_DATABASE_URL)
        reset_database(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.folder = folder_repository.create_folder(self.factory, "Case")

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def create(self, name, folder_id=None, **fields):
        return folder_repository.create_document(
            self.factory, folder_id or self.folder["id"], "2026-10-01", name, **fields
        )

    def found(self, query, folder_id=None):
        result = folder_repository.search_documents(self.factory, query, folder_id)
        return [item["id"] for item in result["results"]]

    def test_matches_title_summary_tag_content_and_attachment_text(self):
        title = self.create("Lease renewal")
        summary = self.create("Other", summary="Quarterly report wording")
        tagged = self.create("Tagged", tags=["Zebra"])
        content = self.create("Body", content="Plumbing repair notes")
        attachment = self.create("Scan")
        folder_repository.add_files(
            self.factory,
            self.folder["id"],
            attachment["id"],
            [("minutes.txt", "text/plain", "Parking allocation minutes".encode("utf-8"))],
        )

        self.assertEqual(self.found("renewal"), [title["id"]])
        self.assertEqual(self.found("quarterly"), [summary["id"]])
        self.assertEqual(self.found("zebra"), [tagged["id"]])
        self.assertEqual(self.found("plumbing"), [content["id"]])
        self.assertEqual(self.found("allocation"), [attachment["id"]])

    def test_terms_must_all_match_across_fields(self):
        doc = self.create("Lease", summary="Parking")

        self.assertEqual(self.found("lease parking"), [doc["id"]])
        self.assertEqual(self.found("lease plumbing"), [])

    def test_search_is_scoped_to_the_folder(self):
        other = folder_repository.create_folder(self.factory, "Other")
        here = self.create("Invoice")
        self.create("Invoice", folder_id=other["id"])

        self.assertEqual(self.found("invoice", self.folder["id"]), [here["id"]])

    def test_tag_changes_update_search(self):
        doc = self.create("Note")
        self.assertEqual(self.found("zebra"), [])

        folder_repository.update_document(self.factory, self.folder["id"], doc["id"], {"tags": ["zebra"]})
        self.assertEqual(self.found("zebra"), [doc["id"]])

        folder_repository.update_document(self.factory, self.folder["id"], doc["id"], {"tags": []})
        self.assertEqual(self.found("zebra"), [])

    def test_hebrew_prefixes_and_inflections_are_found(self):
        doc = self.create("חוזה השכירות")

        self.assertEqual(self.found("שכירות"), [doc["id"]])
        self.assertEqual(self.found("שכיר"), [doc["id"]])

    def test_operator_input_matches_nothing_without_sql_error(self):
        self.create("Lease", content="rent")

        result = folder_repository.search_documents(self.factory, "a' | b & !(c")

        self.assertEqual(result["results"], [])
        self.assertEqual(result["semantic"], "disabled")


@unittest.skipUnless(_TEST_DATABASE_URL, _POSTGRES_REASON)
class SemanticSearchPostgresTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(_TEST_DATABASE_URL)
        with self.engine.connect() as connection:
            available = connection.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
            ).first()
        if available is None:
            self.engine.dispose()
            self.skipTest("PostgreSQL lacks pgvector")
        reset_database(self.engine, semantic=True)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.folder = folder_repository.create_folder(self.factory, "Case")
        self.other_folder = folder_repository.create_folder(self.factory, "Elsewhere")
        self.fake = FakeEmbedder({"lease": 5, "שכירות": 5, "invoice": 9})

        self.lease_doc = self.create("Lease renewal", self.folder["id"])
        self.hebrew_doc = self.create("חוזה השכירות", self.folder["id"], content="שכירות")
        self.unrelated_doc = self.create("Gardening", self.folder["id"])

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def create(self, name, folder_id, **fields):
        document = folder_repository.create_document(self.factory, folder_id, "2026-10-01", name, **fields)
        return document["id"]

    def embed_all(self):
        return embed_pending(self.factory, self.fake)

    def search(self, query="lease", folder_id=None, embedder=None):
        return folder_repository.search_documents(
            self.factory, query, folder_id, embedder=embedder or self.fake
        )

    def test_embed_pending_embeds_everything_then_nothing(self):
        self.assertEqual(self.embed_all(), (3, 0))
        self.assertEqual(self.embed_all(), (0, 0))

    def test_updating_a_document_makes_it_pending_again(self):
        self.embed_all()
        folder_repository.update_document(
            self.factory, self.folder["id"], self.lease_doc, {"summary": "Renewed"}
        )

        with self.factory() as session:
            self.assertEqual(pending_document_ids(session, "bge-m3"), [self.lease_doc])
        self.assertEqual(self.embed_all(), (1, 0))

    def test_stale_write_is_discarded(self):
        self.fake.on_embed = lambda: folder_repository.update_document(
            self.factory, self.folder["id"], self.lease_doc, {"summary": "changed mid-flight"}
        )

        self.assertFalse(embed_document(self.factory, self.fake, self.lease_doc))

        with self.factory() as session:
            self.assertIn(self.lease_doc, pending_document_ids(session, "bge-m3"))

    def test_semantic_only_match_is_returned(self):
        self.embed_all()

        result = self.search("lease")

        self.assertEqual(result["semantic"], "ok")
        by_id = {item["id"]: item for item in result["results"]}
        self.assertEqual(by_id[self.hebrew_doc]["matched_by"], ["semantic"])
        self.assertNotIn(self.unrelated_doc, by_id)

    def test_document_matching_both_ways_ranks_first(self):
        self.embed_all()

        results = self.search("lease")["results"]

        self.assertEqual(results[0]["id"], self.lease_doc)
        self.assertEqual(results[0]["matched_by"], ["keyword", "semantic"])

    def test_folder_scope_excludes_other_folders(self):
        elsewhere = self.create("חוזה נוסף", self.other_folder["id"], content="שכירות")
        self.embed_all()

        ids = [item["id"] for item in self.search("lease", self.folder["id"])["results"]]

        self.assertIn(self.hebrew_doc, ids)
        self.assertNotIn(elsewhere, ids)

    def test_min_similarity_floor_drops_weak_matches(self):
        self.embed_all()

        with patch("binder.folder_repository.SEMANTIC_MIN_SIMILARITY", 1.5):
            ids = [item["id"] for item in self.search("lease")["results"]]

        self.assertEqual(ids, [self.lease_doc])

    def test_vectors_from_other_models_or_dimensions_are_ignored(self):
        self.embed_all()
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE documents SET search_embedding_model = 'other-model' WHERE id = :id"),
                {"id": self.hebrew_doc},
            )
            connection.execute(
                text(
                    "UPDATE documents SET search_embedding = CAST('[1,0,0]' AS vector), "
                    "search_embedding_model = 'bge-m3', "
                    "search_embedding_source_updated_at = updated_at WHERE id = :id"
                ),
                {"id": self.unrelated_doc},
            )

        result = self.search("lease")

        self.assertEqual(result["semantic"], "ok")
        self.assertEqual([item["id"] for item in result["results"]], [self.lease_doc])

    def test_ollama_unavailable_falls_back_to_keyword(self):
        self.fake.error = EmbeddingUnavailable("down")

        result = self.search("lease")

        self.assertEqual(result["semantic"], "unavailable")
        self.assertEqual(
            [(item["id"], item["matched_by"]) for item in result["results"]],
            [(self.lease_doc, ["keyword"])],
        )


def reset_database(engine, *, semantic=False):
    """Give each test an empty schema with the trigger and search DDL in place."""

    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS lo"))
        # Dropping tables leaves their large objects behind; start clean.
        connection.execute(text("SELECT lo_unlink(oid) FROM pg_largeobject_metadata"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(RELEASE_FUNCTION_SQL))
        connection.execute(text(RELEASE_TRIGGER_SQL))
        for statement in SEARCH_SCHEMA_SQL:
            connection.execute(text(statement))
        if semantic:
            for statement in SEMANTIC_SCHEMA_SQL:
                connection.execute(text(statement))


if __name__ == "__main__":
    unittest.main()
