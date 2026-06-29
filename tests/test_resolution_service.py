from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.schemas.classification import (
    AccountClassification,
    ClassificationResult,
    ClassificationThresholds,
    DecisionBasis,
    DecisionRiskLevel,
)
from app.schemas.conflicts import ConflictDetectionResult, ConflictType, DetectedConflict
from app.schemas.enums import ConflictSeverity, EvidenceDirection, MatchDecision, PlatformSource
from app.schemas.evidence import (
    EvidenceExtractionResult,
    EvidenceIndependenceGroup,
    EvidenceTargetType,
    EvidenceType,
    ExtractedEvidence,
)
from app.schemas.requests import ProfileResolveRequest
from app.schemas.scoring import ScoringResult
from app.schemas.source_account import SourceAccount
from app.services.resolution_service import ResolutionService
from app.utils.errors import ResolutionFailedError


class StaticEvidenceExtractor:
    def __init__(self, evidence: list[ExtractedEvidence]) -> None:
        self.evidence = evidence
        self.calls = 0

    def extract(self, **kwargs):
        self.calls += 1
        return EvidenceExtractionResult(evidence=self.evidence)


class StaticConflictDetector:
    def __init__(self, conflicts: list[DetectedConflict]) -> None:
        self.conflicts = conflicts
        self.calls = 0

    def detect(self, **kwargs):
        self.calls += 1
        return ConflictDetectionResult(conflicts=self.conflicts)


class StaticScorer:
    def __init__(self) -> None:
        self.calls = 0

    def score(self, **kwargs):
        self.calls += 1
        return ScoringResult()


class StaticClassifier:
    def __init__(self, classifications: list[AccountClassification], anchors: list[str] | None = None) -> None:
        self.classifications = classifications
        self.anchors = anchors or []
        self.calls = 0

    def classify(self, **kwargs):
        self.calls += 1
        return ClassificationResult(
            classifications=self.classifications,
            thresholds=ClassificationThresholds(),
            anchor_account_keys=self.anchors,
        )


class FakeSourceAccountsRepo:
    def __init__(self, rows_by_key: dict[str, dict] | None = None) -> None:
        self.rows_by_key = rows_by_key or {}
        self.get_by_key_calls: list[str] = []

    def get_by_key(self, key: str):
        self.get_by_key_calls.append(key)
        return self.rows_by_key.get(key)

    def list_by_ids(self, account_ids):
        return [{"id": str(account_id)} for account_id in account_ids]


class FakeEvidenceRepo:
    def __init__(self, fail_insert: bool = False) -> None:
        self.rows: list[dict] = []
        self.delete_calls = 0
        self.fail_insert = fail_insert

    def delete_for_profile(self, profile_id):
        self.delete_calls += 1
        before = len(self.rows)
        self.rows = []
        return before

    def insert_many_for_profile_links(self, *, evidence, source_link_by_account_id):
        if self.fail_insert:
            raise RuntimeError("raw db exploded")
        start = len(self.rows)
        for index, item in enumerate(evidence):
            source_id = str(item.source_account_id) if item.source_account_id else None
            if source_id is None or source_id not in source_link_by_account_id:
                continue
            self.rows.append(
                {
                    "id": str(uuid4()),
                    "profile_source_link_id": str(source_link_by_account_id[source_id]),
                    "source_account_a_id": source_id,
                    "signal_type": item.evidence_type.value,
                    "index": start + index,
                }
            )
        return self.rows[start:]


class FakeConflictsRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.delete_calls = 0

    def delete_for_profile(self, profile_id):
        self.delete_calls += 1
        before = len(self.rows)
        self.rows = [row for row in self.rows if row.get("profile_id") != str(profile_id)]
        return before - len(self.rows)

    def insert_many_for_profile(self, *, profile_id, conflicts):
        start = len(self.rows)
        for item in conflicts:
            self.rows.append(
                {
                    "id": str(uuid4()),
                    "profile_id": str(profile_id),
                    "field_name": item.metadata.get("conflict_basis") or item.conflict_type.value,
                }
            )
        return self.rows[start:]


class FakeProfilesRepo:
    def __init__(self) -> None:
        self.profiles_by_run: dict[str, dict] = {}
        self.links_by_profile: dict[str, list[dict]] = {}
        self.created_count = 0
        self.deleted_profile_ids: list[str] = []

    def get_by_resolution_run_id(self, resolution_run_id):
        return self.profiles_by_run.get(str(resolution_run_id))

    def create_resolution_shell(self, *, resolution_run_id, request, summary):
        run_id = str(resolution_run_id)
        existing = self.profiles_by_run.get(run_id)
        if existing:
            existing["profile_payload"] = {"resolution_summary": summary}
            return existing, False
        row = {
            "id": str(uuid4()),
            "resolution_run_id": run_id,
            "display_name": request.name,
            "confidence_level": summary["confidence_level"],
            "profile_payload": {"resolution_summary": summary},
        }
        self.profiles_by_run[run_id] = row
        self.created_count += 1
        return row, True

    def delete_source_links_for_profile(self, canonical_profile_id):
        profile_id = str(canonical_profile_id)
        count = len(self.links_by_profile.get(profile_id, []))
        self.links_by_profile[profile_id] = []
        return count

    def insert_source_links_for_classifications(self, *, canonical_profile_id=None, profile_id=None, classifications, review_outcome_by_key=None):
        target_profile_id = str(profile_id if profile_id is not None else canonical_profile_id)
        rows = []
        for item in classifications:
            row = {
                "id": str(uuid4()),
                "profile_id": target_profile_id,
                "source_account_id": str(item.source_account_id),
                "decision": item.decision.value,
                "relationship_type": _relationship_for(item),
                "verification_status": _verification_for(item),
                "confidence_score": item.decision_confidence_score,
                "decision_payload": {
                    "decision_basis": item.decision_basis.value,
                    "rationale": item.rationale,
                    "hn_conservative": item.hn_conservative,
                    "weak_signal_only": item.weak_signal_only,
                },
            }
            rows.append(row)
        self.links_by_profile[target_profile_id] = rows
        return rows

    def delete_by_id(self, profile_id):
        profile_id = str(profile_id)
        self.deleted_profile_ids.append(profile_id)
        for run_id, row in list(self.profiles_by_run.items()):
            if row["id"] == profile_id:
                del self.profiles_by_run[run_id]
        self.links_by_profile.pop(profile_id, None)
        return 1


class FakeResolutionRunsRepo:
    def __init__(self) -> None:
        self.finalized: list[dict] = []
        self.failed: list[dict] = []

    def finalize_resolution(self, *, resolution_run_id, status, summary):
        row = {"resolution_run_id": str(resolution_run_id), "status": status.value, "summary": summary}
        self.finalized.append(row)
        return row

    def mark_failed(self, **kwargs):
        self.failed.append(kwargs)
        return kwargs


def _relationship_for(item: AccountClassification) -> str:
    if item.decision == MatchDecision.AUTO_MATCH and item.is_anchor:
        return "primary"
    if item.decision == MatchDecision.AUTO_MATCH:
        return "secondary"
    if item.decision == MatchDecision.NEEDS_REVIEW:
        return "possible_alias"
    return "rejected"


def _verification_for(item: AccountClassification) -> str:
    if item.decision == MatchDecision.AUTO_MATCH and item.is_anchor:
        return "claimed_by_input"
    if item.decision == MatchDecision.AUTO_MATCH:
        return "evidence_matched"
    if item.decision == MatchDecision.NEEDS_REVIEW:
        return "needs_review"
    return "rejected"


def _account(source: PlatformSource, handle: str, account_id: UUID | None = None) -> SourceAccount:
    return SourceAccount(id=account_id, source=source, handle=handle, display_name=handle)


def _classification(account: SourceAccount, decision: MatchDecision, *, score: float, anchor: bool = False) -> AccountClassification:
    return AccountClassification(
        source_account_id=account.id,
        source_account_key=account.expected_source_account_key(),
        source=account.source,
        decision=decision,
        decision_basis=DecisionBasis.ANCHOR_INPUT if anchor else DecisionBasis.STRONG_ANCHOR_PAIR,
        risk_level=DecisionRiskLevel.LOW if decision == MatchDecision.AUTO_MATCH else DecisionRiskLevel.MEDIUM,
        evidence_confidence_score=score,
        decision_confidence_score=max(score, 0.85) if anchor and decision == MatchDecision.AUTO_MATCH else score,
        account_score=score,
        is_anchor=anchor,
        accepted_as_anchor=anchor,
        independent_positive_groups=["input_identifier"] if anchor else ["website", "profile_link"],
        strong_positive_groups=["input_identifier"] if anchor else ["website", "profile_link"],
        rationale=["evidence-based decision"],
    )


def _evidence(account: SourceAccount, target: SourceAccount | None = None) -> ExtractedEvidence:
    return ExtractedEvidence(
        evidence_type=EvidenceType.INPUT_HANDLE_MATCH if target is None else EvidenceType.SAME_WEBSITE,
        direction=EvidenceDirection.POSITIVE,
        target_type=EvidenceTargetType.REQUEST if target is None else EvidenceTargetType.ACCOUNT_PAIR,
        source_account_id=account.id,
        source_account_key=account.expected_source_account_key(),
        source=account.source,
        target_account_id=target.id if target else None,
        target_account_key=target.expected_source_account_key() if target else None,
        target_source=target.source if target else None,
        weight=0.25 if target is None else 0.30,
        independence_group=EvidenceIndependenceGroup.INPUT_IDENTIFIER if target is None else EvidenceIndependenceGroup.WEBSITE,
        reason="confidence-scored match evidence",
    )


def _conflict(left: SourceAccount, right: SourceAccount) -> DetectedConflict:
    return DetectedConflict(
        conflict_type=ConflictType.LOCATION_CONFLICT,
        severity=ConflictSeverity.LOW,
        penalty=-0.1,
        source_account_id=left.id,
        source_account_key=left.expected_source_account_key(),
        source=left.source,
        target_account_id=right.id,
        target_account_key=right.expected_source_account_key(),
        target_source=right.source,
        description="needs review conflict",
    )


def _service(*, accounts, classifications, evidence=None, conflicts=None, evidence_repo=None, source_repo=None):
    evidence = evidence if evidence is not None else [_evidence(accounts[0])]
    conflicts = conflicts if conflicts is not None else []
    profiles_repo = FakeProfilesRepo()
    runs_repo = FakeResolutionRunsRepo()
    service = ResolutionService(
        evidence_extractor=StaticEvidenceExtractor(evidence),
        conflict_detector=StaticConflictDetector(conflicts),
        scorer=StaticScorer(),
        classifier=StaticClassifier(classifications, anchors=[accounts[0].expected_source_account_key()]),
        evidence_repo=evidence_repo or FakeEvidenceRepo(),
        conflicts_repo=FakeConflictsRepo(),
        profiles_repo=profiles_repo,
        source_accounts_repo=source_repo or FakeSourceAccountsRepo(),
        resolution_runs_repo=runs_repo,
    )
    return service, profiles_repo, runs_repo


def test_resolution_service_successful_persistence():
    github = _account(PlatformSource.GITHUB, "amil", uuid4())
    devto = _account(PlatformSource.DEVTO, "amil", uuid4())
    hn = _account(PlatformSource.HACKERNEWS, "amil", uuid4())
    classifications = [
        _classification(github, MatchDecision.AUTO_MATCH, score=0.25, anchor=True),
        _classification(devto, MatchDecision.AUTO_MATCH, score=0.90),
        _classification(hn, MatchDecision.NEEDS_REVIEW, score=0.62),
    ]
    service, profiles_repo, runs_repo = _service(
        accounts=[github, devto, hn],
        classifications=classifications,
        evidence=[_evidence(github), _evidence(devto, github)],
        conflicts=[_conflict(devto, hn)],
    )

    result = service.resolve(
        resolution_run_id=uuid4(),
        request=ProfileResolveRequest(name="Amil", github="amil"),
        accounts=[github, devto, hn],
    )

    assert result.persistence.match_evidence_rows == 2
    assert result.persistence.profile_conflict_rows == 1
    assert result.persistence.profile_source_link_rows == 3
    assert result.persistence.canonical_profile_created is True
    assert result.summary["canonical_profile_pending"] is True
    assert result.summary["max_evidence_confidence_score"] == 0.9
    assert result.summary["max_decision_confidence_score"] == 0.9
    assert runs_repo.finalized[-1]["summary"]["canonical_profile_id"] == str(result.canonical_profile_id)
    links = profiles_repo.links_by_profile[str(result.canonical_profile_id)]
    assert {row["relationship_type"] for row in links} == {"primary", "secondary", "possible_alias"}
    assert all(row["decision_payload"]["decision_basis"] for row in links)


def test_resolution_service_idempotent_rerun_reuses_profile_and_counts():
    github = _account(PlatformSource.GITHUB, "amil", uuid4())
    devto = _account(PlatformSource.DEVTO, "amil", uuid4())
    classifications = [
        _classification(github, MatchDecision.AUTO_MATCH, score=0.25, anchor=True),
        _classification(devto, MatchDecision.AUTO_MATCH, score=0.90),
    ]
    service, _, _ = _service(
        accounts=[github, devto],
        classifications=classifications,
        evidence=[_evidence(github), _evidence(devto, github)],
    )
    run_id = uuid4()
    request = ProfileResolveRequest(name="Amil", github="amil")

    first = service.resolve(resolution_run_id=run_id, request=request, accounts=[github, devto], replace_existing=True)
    second = service.resolve(resolution_run_id=run_id, request=request, accounts=[github, devto], replace_existing=True)

    assert second.canonical_profile_id == first.canonical_profile_id
    assert second.persistence.canonical_profile_created is False
    assert second.persistence.canonical_profile_reused is True
    assert second.persistence.match_evidence_rows == first.persistence.match_evidence_rows == 2
    assert second.persistence.profile_source_link_rows == first.persistence.profile_source_link_rows == 2
    assert len(service.evidence_repo.rows) == 2


def test_resolution_service_hn_weak_decision_payload_preserves_safety_flags():
    github = _account(PlatformSource.GITHUB, "amil", uuid4())
    hn = _account(PlatformSource.HACKERNEWS, "amil", uuid4())
    hn_classification = _classification(hn, MatchDecision.REJECT, score=0.12).model_copy(
        update={
            "decision_basis": DecisionBasis.REJECTED_WEAK_ONLY,
            "weak_signal_only": True,
            "hn_conservative": True,
            "hn_requires_strong_evidence": True,
            "rationale": ["HN handle-only evidence is too weak."],
        }
    )
    classifications = [
        _classification(github, MatchDecision.AUTO_MATCH, score=0.25, anchor=True),
        hn_classification,
    ]
    service, profiles_repo, _ = _service(accounts=[github, hn], classifications=classifications)

    result = service.resolve(
        resolution_run_id=uuid4(),
        request=ProfileResolveRequest(name="Amil", github="amil"),
        accounts=[github, hn],
    )

    links = profiles_repo.links_by_profile[str(result.canonical_profile_id)]
    hn_link = next(row for row in links if row["source_account_id"] == str(hn.id))
    assert hn_link["decision"] == "reject"
    assert hn_link["decision_payload"]["hn_conservative"] is True
    assert hn_link["decision_payload"]["weak_signal_only"] is True



def test_resolution_service_all_rejected_creates_no_profile_or_links():
    github = _account(PlatformSource.GITHUB, "amil", uuid4())
    hn = _account(PlatformSource.HACKERNEWS, "other", uuid4())
    classifications = [
        _classification(github, MatchDecision.REJECT, score=0.0),
        _classification(hn, MatchDecision.REJECT, score=0.0),
    ]
    service, profiles_repo, runs_repo = _service(
        accounts=[github, hn],
        classifications=classifications,
        evidence=[_evidence(github)],
        conflicts=[_conflict(github, hn)],
    )

    result = service.resolve(
        resolution_run_id=uuid4(),
        request=ProfileResolveRequest(name="Amil"),
        accounts=[github, hn],
    )

    assert result.canonical_profile_id is None
    assert result.persistence.profile_source_link_rows == 0
    assert result.persistence.canonical_profile_created is False
    assert profiles_repo.profiles_by_run == {}
    assert runs_repo.finalized[-1]["summary"]["no_profile_created_reason"] == "all_accounts_rejected"
    assert runs_repo.finalized[-1]["summary"]["canonical_profile_pending"] is False


def test_resolution_service_persist_false_performs_zero_writes():
    account = _account(PlatformSource.GITHUB, "amil")
    classifications = [_classification(account, MatchDecision.AUTO_MATCH, score=0.25, anchor=True)]
    source_repo = FakeSourceAccountsRepo()
    evidence_repo = FakeEvidenceRepo()
    service, profiles_repo, runs_repo = _service(
        accounts=[account],
        classifications=classifications,
        evidence=[_evidence(account)],
        evidence_repo=evidence_repo,
        source_repo=source_repo,
    )

    result = service.resolve(
        resolution_run_id=uuid4(),
        request=ProfileResolveRequest(name="Amil", github="amil"),
        accounts=[account],
        persist=False,
    )

    assert result.persisted is False
    assert result.canonical_profile_id is None
    assert result.evidence.count == 1
    assert evidence_repo.rows == []
    assert profiles_repo.profiles_by_run == {}
    assert runs_repo.finalized == []
    assert source_repo.get_by_key_calls == []


def test_resolution_service_missing_persisted_account_marks_failed():
    account = _account(PlatformSource.GITHUB, "amil")
    classifications = [_classification(account, MatchDecision.AUTO_MATCH, score=0.25, anchor=True)]
    service, profiles_repo, runs_repo = _service(
        accounts=[account],
        classifications=classifications,
        source_repo=FakeSourceAccountsRepo(rows_by_key={}),
    )

    with pytest.raises(ResolutionFailedError):
        service.resolve(
            resolution_run_id=uuid4(),
            request=ProfileResolveRequest(name="Amil", github="amil"),
            accounts=[account],
            persist=True,
        )

    assert profiles_repo.profiles_by_run == {}
    assert runs_repo.failed


def test_resolution_service_rejected_link_policy_when_profile_exists():
    github = _account(PlatformSource.GITHUB, "amil", uuid4())
    other = _account(PlatformSource.DEVTO, "other", uuid4())
    classifications = [
        _classification(github, MatchDecision.AUTO_MATCH, score=0.25, anchor=True),
        _classification(other, MatchDecision.REJECT, score=0.1),
    ]
    service, profiles_repo, _ = _service(accounts=[github, other], classifications=classifications)

    result = service.resolve(
        resolution_run_id=uuid4(),
        request=ProfileResolveRequest(name="Amil", github="amil"),
        accounts=[github, other],
    )

    links = profiles_repo.links_by_profile[str(result.canonical_profile_id)]
    rejected = [row for row in links if row["decision"] == "reject"]
    assert rejected
    assert rejected[0]["relationship_type"] == "rejected"
    assert rejected[0]["verification_status"] == "rejected"


def test_resolution_service_persistence_failure_marks_failed_and_raises_safe_error():
    account = _account(PlatformSource.GITHUB, "amil", uuid4())
    classifications = [_classification(account, MatchDecision.AUTO_MATCH, score=0.25, anchor=True)]
    service, _, runs_repo = _service(
        accounts=[account],
        classifications=classifications,
        evidence=[_evidence(account)],
        evidence_repo=FakeEvidenceRepo(fail_insert=True),
    )

    with pytest.raises(ResolutionFailedError) as exc_info:
        service.resolve(
            resolution_run_id=uuid4(),
            request=ProfileResolveRequest(name="Amil", github="amil"),
            accounts=[account],
        )

    assert str(exc_info.value) == "The resolution result could not be saved safely."
    assert "raw db exploded" not in str(exc_info.value.details)
    assert runs_repo.failed
