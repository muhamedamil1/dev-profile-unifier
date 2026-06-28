from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.storage.supabase_client import get_supabase_client


def main() -> int:
    print("Checking Supabase connection...")

    try:
        client = get_supabase_client()

        health_response = (
            client.table("health_profile_metrics")
            .select("*")
            .limit(1)
            .execute()
        )

        print("Connected to Supabase successfully.")
        print("health_profile_metrics response:")
        print(health_response.data)

        tables_to_check = [
            "resolution_runs",
            "raw_source_records",
            "source_accounts",
            "canonical_profiles",
            "profile_source_links",
            "match_evidence",
            "profile_conflicts",
            "profile_facts",
            "llm_summaries",
            "api_call_metrics",
        ]

        for table_name in tables_to_check:
            response = (
                client.table(table_name)
                .select("id")
                .limit(1)
                .execute()
            )
            row_count = len(response.data or [])
            print(f"OK: {table_name} reachable, sample_rows={row_count}")

        print("Supabase connection check passed.")
        return 0

    except Exception as exc:
        print("Supabase connection check failed.")
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())