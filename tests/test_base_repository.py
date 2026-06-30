from __future__ import annotations

from uuid import uuid4

import pytest

from app.storage.base import BaseRepository
from app.utils.errors import StorageError


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, *, table_name: str, client: "FakeClient") -> None:
        self.table_name = table_name
        self.client = client
        self.operation = "table"
        self.payload = None
        self.filters: list[tuple[str, str, str]] = []
        self.limit_value = None

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def select(self, payload):
        self.operation = "select"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters.append(("eq", key, str(value)))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        return self.client.execute(self)


class FakeClient:
    def __init__(self, *, update_data, fetched_row=None) -> None:
        self.update_data = update_data
        self.fetched_row = fetched_row
        self.calls: list[tuple[str, str, object]] = []

    def table(self, table_name):
        return FakeQuery(table_name=table_name, client=self)

    def execute(self, query: FakeQuery):
        self.calls.append((query.table_name, query.operation, query.payload))
        if query.operation == "update":
            return FakeResponse(self.update_data)
        if query.operation == "select":
            return FakeResponse([self.fetched_row] if self.fetched_row else [])
        return FakeResponse([])


class ExampleRepo(BaseRepository):
    table_name = "example_table"


def test_update_by_id_returns_update_representation_when_present():
    row_id = uuid4()
    repo = ExampleRepo(FakeClient(update_data=[{"id": str(row_id), "status": "resolved"}]))

    row = repo._update_by_id(row_id, {"status": "resolved"})

    assert row == {"id": str(row_id), "status": "resolved"}
    assert repo.client.calls == [("example_table", "update", {"status": "resolved"})]


def test_update_by_id_reads_back_row_when_update_response_is_empty():
    row_id = uuid4()
    repo = ExampleRepo(
        FakeClient(
            update_data=[],
            fetched_row={"id": str(row_id), "status": "partial"},
        )
    )

    row = repo._update_by_id(row_id, {"status": "partial"})

    assert row == {"id": str(row_id), "status": "partial"}
    assert repo.client.calls == [
        ("example_table", "update", {"status": "partial"}),
        ("example_table", "select", "*"),
    ]


def test_update_by_id_raises_when_update_and_readback_find_no_row():
    repo = ExampleRepo(FakeClient(update_data=[], fetched_row=None))

    with pytest.raises(StorageError) as exc_info:
        repo._update_by_id(uuid4(), {"status": "missing"})

    assert "update_by_id" in str(exc_info.value)
