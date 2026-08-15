"""Seeing and forgetting what Ultron knows about you."""

import pytest

SEED = [
    ("contact", "Email", "hello@rateup.app", 9),
    ("contact", "Phone", "+91 555 0100", 8),
    ("test", "Favorite Animal", "Elephant", 10),
    ("work", "Favorite Editor", "VS Code", 4),
]


@pytest.fixture
def stocked(brain):
    for category, key, value, importance in SEED:
        brain.db.save_memory(category, key, value, importance)
    return brain


class TestListing:
    def test_lists_everything_saved(self, stocked):
        listing = stocked._invoke_tool("list_memories", {})
        for _, key, value, _ in SEED:
            assert key in listing and value in listing

    def test_is_numbered_from_one(self, stocked):
        assert "1. [contact] Email" in stocked._invoke_tool("list_memories", {})

    def test_order_is_stable_between_calls(self, stocked):
        """The numbers a user is shown must still mean the same thing later."""
        assert stocked._invoke_tool("list_memories", {}) == \
            stocked._invoke_tool("list_memories", {})

    def test_says_so_when_empty(self, brain):
        assert "not saved anything" in brain._invoke_tool("list_memories", {})


class TestResolution:
    def test_by_number(self, stocked):
        memory, problem = stocked._resolve_memory("3")
        assert problem is None and memory["key"] == "Favorite Animal"

    def test_by_words(self, stocked):
        memory, problem = stocked._resolve_memory("elephant")
        assert problem is None and memory["key"] == "Favorite Animal"

    def test_is_case_insensitive(self, stocked):
        memory, _ = stocked._resolve_memory("ELEPHANT")
        assert memory["key"] == "Favorite Animal"

    def test_ambiguity_refuses_rather_than_guessing(self, stocked):
        memory, problem = stocked._resolve_memory("favorite")
        assert memory is None
        assert "matches 2 memories" in problem

    def test_no_match_is_reported(self, stocked):
        memory, problem = stocked._resolve_memory("my inside leg measurement")
        assert memory is None and problem.startswith("Error")

    def test_out_of_range_number(self, stocked):
        memory, problem = stocked._resolve_memory("99")
        assert memory is None and "no memory 99" in problem

    def test_empty_input(self, stocked):
        memory, problem = stocked._resolve_memory("")
        assert memory is None and problem.startswith("Error")


class TestDeletion:
    def test_ambiguous_deletes_nothing(self, stocked, approve_all):
        result = stocked._invoke_tool("delete_memory", {"which": "favorite"})
        assert result.startswith("Error")
        assert len(stocked.db.list_memories()) == len(SEED)

    def test_refusal_deletes_nothing(self, stocked, refuse_all):
        result = stocked._invoke_tool("delete_memory", {"which": "3"})
        assert result.startswith("Error")
        assert len(stocked.db.list_memories()) == len(SEED)

    def test_approval_removes_exactly_one(self, stocked, approve_all):
        stocked._invoke_tool("list_memories", {})
        result = stocked._invoke_tool("delete_memory", {"which": "3"})
        assert "Forgotten" in result
        remaining = stocked.db.list_memories()
        assert len(remaining) == len(SEED) - 1
        assert not any(m["key"] == "Favorite Animal" for m in remaining)

    def test_the_card_names_the_memory_not_the_search_term(self, stocked, approve_all):
        """Approving 'the animal one' should show what will actually go."""
        stocked._invoke_tool("delete_memory", {"which": "elephant"})
        assert approve_all[-1] == "forget what it knows: 'Favorite Animal: Elephant'"

    def test_stale_numbers_are_not_reused_after_a_delete(self, stocked, approve_all):
        stocked._invoke_tool("list_memories", {})
        stocked._invoke_tool("delete_memory", {"which": "1"})
        # 4 was valid a moment ago; there are only three memories now.
        assert stocked._invoke_tool("delete_memory", {"which": "4"}).startswith("Error")

    def test_deleting_from_an_empty_store_is_graceful(self, brain, approve_all):
        assert "nothing saved" in brain._invoke_tool("delete_memory", {"which": "1"}).lower()


class TestStorage:
    def test_value_and_timestamp_are_recorded(self, brain):
        brain.db.save_memory("c", "k", "v", 5)
        memory = brain.db.list_memories()[0]
        assert memory["value"] == "v"
        assert memory["saved_at"], "needed to keep listings in a stable order"

    def test_older_entries_without_a_stored_value_still_read(self, brain):
        """Memories saved before 'value' was stored separately must still show."""
        import uuid

        brain.db.memories_col.add(
            documents=["[legacy] Old Key: the old value"],
            metadatas=[{"category": "legacy", "key": "Old Key", "importance": 3}],
            ids=[str(uuid.uuid4())],
        )
        memory = brain.db.list_memories()[0]
        assert memory["value"] == "the old value"

    def test_deleting_a_missing_id_reports_false(self, brain):
        assert brain.db.delete_memory("no-such-id") is False
