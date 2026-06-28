from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.dependencies import (
    get_devto_client,
    get_github_client,
    get_hackernews_client,
    get_stackoverflow_client,
)
from app.utils.errors import PlatformNotFoundError


async def test_github() -> None:
    github = get_github_client()
    bundle = await github.fetch_profile_bundle("octocat")

    print("GitHub:")
    print("  login:", bundle["profile"].get("login"))
    print("  repos:", len(bundle["repos"]))


async def test_devto() -> None:
    devto = get_devto_client()
    bundle = await devto.fetch_profile_bundle("ben")

    print("dev.to:")
    print("  username:", bundle["user"].get("username"))
    print("  articles:", len(bundle["articles"]))


async def test_hackernews() -> None:
    hn = get_hackernews_client()
    bundle = await hn.fetch_profile_bundle("pg")

    print("Hacker News:")
    print("  username:", bundle["user"].get("username"))
    print("  activity:", len(bundle["activity"].get("hits", [])))


async def test_stackoverflow() -> None:
    stackoverflow = get_stackoverflow_client()

    # 22656 is a known public Stack Overflow user id.
    bundle = await stackoverflow.fetch_profile_bundle("22656")

    user_items = bundle["user"].get("items", [])
    user = user_items[0] if user_items else {}

    print("Stack Overflow:")
    print("  display_name:", user.get("display_name"))
    print("  answers:", len(bundle["answers"].get("items", [])))
    print("  questions:", len(bundle["questions"].get("items", [])))


async def test_github_404() -> None:
    github = get_github_client()

    try:
        await github.fetch_profile_bundle("this-user-should-not-exist-123456789")
    except PlatformNotFoundError:
        print("GitHub 404 handling: passed")
    else:
        raise AssertionError("Expected PlatformNotFoundError for missing GitHub user")


async def main() -> None:
    await test_github()
    await test_devto()
    await test_hackernews()
    await test_stackoverflow()
    await test_github_404()

    print("\nPhase 5 integration smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())