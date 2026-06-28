from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.candidate_discovery import CandidateDiscoveryService
    from app.services.ingestion_service import IngestionService

__all__ = [
    "CandidateDiscoveryService",
    "IngestionService",
]


def __getattr__(name: str):
    if name == "CandidateDiscoveryService":
        from app.services.candidate_discovery import CandidateDiscoveryService

        return CandidateDiscoveryService

    if name == "IngestionService":
        from app.services.ingestion_service import IngestionService

        return IngestionService

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
