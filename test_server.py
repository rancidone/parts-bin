"""Smoke tests for server-level logic (no live server needed)."""

from server import _is_ingestion


class TestRoutingHeuristic:
    def test_photo_always_ingestion(self):
        assert _is_ingestion("what is this?", has_photo=True)

    def test_add_keyword(self):
        assert _is_ingestion("add 10 resistors", has_photo=False)

    def test_i_have_keyword(self):
        assert _is_ingestion("I have 5 2N7002", has_photo=False)

    def test_query_goes_to_query_path(self):
        assert not _is_ingestion("do I have any 10k resistors?", has_photo=False)

    def test_how_many_is_query(self):
        assert not _is_ingestion("how many 100nF caps do I have?", has_photo=False)

    def test_stock_keyword(self):
        assert _is_ingestion("stock 20 0402 caps", has_photo=False)
