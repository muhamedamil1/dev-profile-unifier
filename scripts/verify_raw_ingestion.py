from __future__ import annotations

import argparse
import asyncio
from typing import Any

from _bootstrap import bootstrap_project_root

bootstrap_project_root()

from app.dependencies import (
    get_ingestion_service,
    get_resolution_runs_repo,
    get_supabase_client,
)
from app.schemas.requests import ProfileResolveRequest


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _fetch_raw_records_for_run(run_id: str) -> list[dict[str, Any]]:
    response = (
        get_supabase_client()
        .table("raw_source_records")
        .select(
            "id,resolution_run_id,source,source_record_type,source_user_id,handle,profile_url,http_status"
        )
        .eq("resolution_run_id", run_id)
        .execute()
    )

    return response.data or []


def _fetch_api_metrics_for_run(run_id: str) -> list[dict[str, Any]]:
    response = (
        get_supabase_client()
        .table("api_call_metrics")
        .select(
            "id,resolution_run_id,source,endpoint,status_code,duration_ms,error_message"
        )
        .eq("resolution_run_id", run_id)
        .execute()
    )

    return response.data or []


async def verify_successful_ingestion(args: argparse.Namespace) -> None:
    req = ProfileResolveRequest(
        name=args.name,
        github=args.github,
        devto=args.devto,
        hackernews=args.hackernews,
        stackoverflow_user_id=args.stackoverflow_user_id,
    )

    sources_attempted = [source.value for source in req.provided_sources]

    runs_repo = get_resolution_runs_repo()
    run = runs_repo.create_run(
        input_name=req.name,
        input_payload=req.safe_input_payload(),
        sources_attempted=sources_attempted,
    )

    run_id = str(run["id"])

    ingestion = get_ingestion_service()
    result = await ingestion.ingest(
        request=req,
        resolution_run_id=run_id,
    )

    print(f"Created resolution_run: {run_id}")
    print(f"Candidates: {len(result.discovery.candidates)}")
    print(f"Succeeded: {len(result.succeeded)}")
    print(f"Failed: {len(result.failed)}")

    for item in result.results:
        print(
            f"- {item.candidate.source.value}:{item.candidate.identifier} "
            f"{item.status.value}"
        )

        if item.error_message:
            print(f"  error: {item.error_code} — {item.error_message}")

        for raw_record in item.raw_records:
            print(f"  raw: {raw_record.source_record_type} {raw_record.id}")

    _assert(
        result.discovery.candidates,
        "Expected at least one discovered candidate.",
    )

    _assert(
        result.succeeded,
        "Expected at least one successful ingestion result.",
    )

    raw_records = _fetch_raw_records_for_run(run_id)
    api_metrics = _fetch_api_metrics_for_run(run_id)

    _assert(
        raw_records,
        "Expected raw_source_records rows for successful ingestion.",
    )

    _assert(
        api_metrics,
        "Expected api_call_metrics rows for ingestion API calls.",
    )

    raw_record_types = {row["source_record_type"] for row in raw_records}

    if args.github:
        _assert(
            "github/profile" in raw_record_types,
            "Expected github/profile raw record.",
        )
        _assert(
            "github/repos" in raw_record_types,
            "Expected github/repos raw record.",
        )

    if args.devto:
        _assert(
            "devto/user" in raw_record_types,
            "Expected devto/user raw record.",
        )
        _assert(
            "devto/articles" in raw_record_types,
            "Expected devto/articles raw record.",
        )

    if args.hackernews:
        _assert(
            "hackernews/user" in raw_record_types,
            "Expected hackernews/user raw record.",
        )
        _assert(
            "hackernews/activity" in raw_record_types,
            "Expected hackernews/activity raw record.",
        )

    if args.stackoverflow_user_id:
        _assert(
            "stackoverflow/user" in raw_record_types,
            "Expected stackoverflow/user raw record.",
        )
        _assert(
            "stackoverflow/answers" in raw_record_types,
            "Expected stackoverflow/answers raw record.",
        )
        _assert(
            "stackoverflow/questions" in raw_record_types,
            "Expected stackoverflow/questions raw record.",
        )

    print("\nSuccessful ingestion verification passed.")


async def verify_not_found_handling() -> None:
    req = ProfileResolveRequest(
        name="Fake User",
        github="this-user-should-not-exist-123456789",
    )

    runs_repo = get_resolution_runs_repo()
    run = runs_repo.create_run(
        input_name=req.name,
        input_payload=req.safe_input_payload(),
        sources_attempted=["github"],
    )

    run_id = str(run["id"])

    ingestion = get_ingestion_service()
    result = await ingestion.ingest(
        request=req,
        resolution_run_id=run_id,
    )

    print(f"\nCreated not-found resolution_run: {run_id}")
    print(f"Succeeded: {len(result.succeeded)}")
    print(f"Failed: {len(result.failed)}")

    _assert(
        len(result.succeeded) == 0,
        "Not-found check should not produce successful ingestion results.",
    )

    _assert(
        len(result.failed) == 1,
        "Not-found check should produce one failed ingestion result.",
    )

    failed = result.failed[0]

    _assert(
        failed.status.value == "not_found",
        "Expected failed candidate status to be not_found.",
    )

    raw_records = _fetch_raw_records_for_run(run_id)
    api_metrics = _fetch_api_metrics_for_run(run_id)

    _assert(
        not raw_records,
        "Not-found candidate should not create raw_source_records.",
    )

    _assert(
        api_metrics,
        "Not-found API call should still create api_call_metrics.",
    )

    print("Not-found ingestion verification passed.")


async def main_async() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify raw ingestion by creating a resolution_run, fetching platform "
            "data, storing raw_source_records, and checking api_call_metrics."
        )
    )

    parser.add_argument("--name", default="Verification User")
    parser.add_argument("--github", default="octocat")
    parser.add_argument("--devto", default=None)
    parser.add_argument("--hackernews", default=None)
    parser.add_argument("--stackoverflow-user-id", default=None)
    parser.add_argument(
        "--include-not-found-check",
        action="store_true",
        help="Also verify structured not_found handling using a fake GitHub user.",
    )

    args = parser.parse_args()

    _assert(
        any(
            [
                args.github,
                args.devto,
                args.hackernews,
                args.stackoverflow_user_id,
            ]
        ),
        "At least one platform identifier must be provided.",
    )

    await verify_successful_ingestion(args)

    if args.include_not_found_check:
        await verify_not_found_handling()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
