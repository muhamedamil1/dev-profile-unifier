from __future__ import annotations

from uuid import uuid4

from app.schemas.classification import AccountClassification, DecisionBasis, DecisionRiskLevel
from app.schemas.enums import MatchDecision, PlatformSource
from app.storage.profiles_repo import ProfilesRepo
from app.storage.resolution_runs_repo import ResolutionRunsRepo
from app.utils.errors import StorageError


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, *, action: str, table_name: str, client):
        self.action = action
        self.table_name = table_name
        self.client = client
        self.payload = None
        self.filters: list[tuple[str, str, str]] = []

    def select(self, payload):
        self.payload = payload
        return self

    def limit(self, value):
        return self

    def insert(self, payload):
        self.payload = payload
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters.append(("eq", key, value))
        return self

    def execute(self):
        return self.client.execute(self)


class FakeClient:
    def __init__(self):
        self.profile_link_insert_payloads = []
        self.profile_link_select_rows = []
        self.profile_link_insert_disconnects = 0
        self.run_update_payloads = []

    def table(self, table_name):
        return FakeQuery(action="table", table_name=table_name, client=self)

    def execute(self, query: FakeQuery):
        if query.table_name == "profile_source_links" and query.payload == "*":
            return FakeResponse(self.profile_link_select_rows)

        if query.table_name == "profile_source_links" and query.payload is not None:
            self.profile_link_insert_payloads.append(query.payload)
            if self.profile_link_insert_disconnects > 0:
                self.profile_link_insert_disconnects -= 1
                raise RuntimeError("Server disconnected")
            first_payload = query.payload[0] if isinstance(query.payload, list) else query.payload
            if "decision_payload" in first_payload:
                raise RuntimeError("Could not find the 'decision_payload' column of 'profile_source_links' in the schema cache")
            return FakeResponse(query.payload)

        if query.table_name == "resolution_runs" and query.payload == "*":
            row_id = query.filters[0][2] if query.filters else str(uuid4())
            return FakeResponse([{"id": row_id, "started_at": "2026-06-30T00:00:00+00:00", "source_errors": [], "sources_failed": [], "sources_attempted": []}])
        if query.table_name == "resolution_runs" and query.payload is not None:
            self.run_update_payloads.append(query.payload)
            if "result_summary" in query.payload:
                raise RuntimeError('column "result_summary" of relation "resolution_runs" does not exist')
            payload = {"id": query.filters[0][2], **query.payload}
            return FakeResponse([payload])

        return FakeResponse([])


def test_profiles_repo_retries_profile_source_link_insert_without_unknown_columns():
    repo = ProfilesRepo(FakeClient())

    rows = repo.insert_source_links_for_classifications(
        canonical_profile_id=uuid4(),
        classifications=[],
    )

    assert rows == []

    payload = {
        "profile_id": str(uuid4()),
        "source_account_id": str(uuid4()),
        "confidence_score": 0.91,
        "decision": "auto_match",
        "relationship_type": "primary",
        "verification_status": "claimed_by_input",
        "positive_signal_count": 2,
        "negative_signal_count": 0,
        "has_high_conflict": False,
        "decision_payload": {"decision_basis": "strong_match"},
    }

    result = repo._insert_profile_source_links_with_fallback([payload])

    assert len(repo.client.profile_link_insert_payloads) == 2
    assert "decision_payload" in repo.client.profile_link_insert_payloads[0][0]
    assert "decision_payload" not in repo.client.profile_link_insert_payloads[1][0]
    assert result[0]["verification_status"] == "claimed_by_input"


def test_resolution_runs_repo_retries_update_without_result_summary_column():
    repo = ResolutionRunsRepo(FakeClient())

    row = repo.finalize_resolution(
        resolution_run_id=uuid4(),
        status=type("Status", (), {"value": "resolved"})(),
        summary={"phase": "7E"},
    )

    assert len(repo.client.run_update_payloads) == 2
    assert "result_summary" in repo.client.run_update_payloads[0]
    assert "result_summary" not in repo.client.run_update_payloads[1]
    assert row["status"] == "resolved"


def test_profile_source_link_anchor_uses_decision_confidence_for_db_contract():
    repo = ProfilesRepo(FakeClient())
    source_account_id = uuid4()
    classification = AccountClassification(
        source_account_id=source_account_id,
        source_account_key="github:583231",
        source=PlatformSource.GITHUB,
        decision=MatchDecision.AUTO_MATCH,
        decision_basis=DecisionBasis.ANCHOR_INPUT,
        risk_level=DecisionRiskLevel.LOW,
        evidence_confidence_score=0.25,
        decision_confidence_score=0.85,
        account_score=0.25,
        best_pair_score=None,
        is_anchor=True,
        accepted_as_anchor=True,
        independent_positive_groups=["input_identifier"],
        strong_positive_groups=["input_identifier"],
        rationale=["direct input anchor"],
    )

    payload = repo._source_link_payload(
        target_profile_id=uuid4(),
        item=classification,
        review_outcome_by_key={},
    )

    assert payload["decision"] == "auto_match"
    assert payload["relationship_type"] == "primary"
    assert payload["verification_status"] == "claimed_by_input"
    assert payload["confidence_score"] == 0.85
    assert payload["positive_signal_count"] == 1
    assert payload["decision_payload"]["evidence_confidence_score"] == 0.25
    assert payload["decision_payload"]["decision_confidence_score"] == 0.85
    assert payload["decision_payload"]["account_score"] == 0.25
def test_profile_source_link_insert_disconnect_reads_back_inserted_rows():
    client = FakeClient()
    client.profile_link_insert_disconnects = 1
    repo = ProfilesRepo(client)
    profile_id = str(uuid4())
    source_account_id = str(uuid4())
    payload = {
        "profile_id": profile_id,
        "source_account_id": source_account_id,
        "confidence_score": 0.85,
        "decision": "auto_match",
        "relationship_type": "primary",
        "verification_status": "claimed_by_input",
        "positive_signal_count": 1,
        "negative_signal_count": 0,
        "has_high_conflict": False,
    }
    client.profile_link_select_rows = [{"id": str(uuid4()), **payload}]

    result = repo._insert_profile_source_links_with_fallback([payload])

    assert result == client.profile_link_select_rows
    assert len(client.profile_link_insert_payloads) == 1


def test_profile_source_link_insert_disconnect_retries_after_empty_readback():
    client = FakeClient()
    client.profile_link_insert_disconnects = 1
    repo = ProfilesRepo(client)
    payload = {
        "profile_id": str(uuid4()),
        "source_account_id": str(uuid4()),
        "confidence_score": 0.85,
        "decision": "auto_match",
        "relationship_type": "primary",
        "verification_status": "claimed_by_input",
        "positive_signal_count": 1,
        "negative_signal_count": 0,
        "has_high_conflict": False,
    }

    result = repo._insert_profile_source_links_with_fallback([payload])

    assert result == [payload]
    assert len(client.profile_link_insert_payloads) == 2
