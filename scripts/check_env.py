from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings


def main() -> int:
    settings = get_settings()
    missing = settings.missing_required_settings()
    recommended_missing = settings.missing_recommended_settings()
    integrations = settings.configured_integrations()

    print(f"Service: {settings.app_name} v{settings.app_version}")
    print(f"Environment: {settings.app_env}")
    print("")

    print("Configured integrations:")
    for name, configured in integrations.items():
        status = "OK" if configured else "MISSING"
        print(f"  - {name}: {status}")

    print("")

    if missing:
        print("Missing required production settings:")
        for item in missing:
            print(f"  - {item}")
    else:
        print("All required production settings are configured.")

    print("")

    if recommended_missing:
        print("Missing recommended settings:")
        for item in recommended_missing:
            print(f"  - {item}")
    else:
        print("All recommended settings are configured.")

    strict = "--strict" in sys.argv

    if strict and missing:
        print("")
        print("Strict mode failed.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
