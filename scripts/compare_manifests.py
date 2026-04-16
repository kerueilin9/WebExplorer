"""Compare two route manifests, usually guest and authenticated phases."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    args = _parse_args()
    guest_path = Path(args.guest_manifest).resolve()
    auth_path = Path(args.authenticated_manifest).resolve()

    guest = _load_manifest(guest_path)
    auth = _load_manifest(auth_path)
    guest_routes = guest.get("routes", [])
    auth_routes = auth.get("routes", [])
    guest_keys = {_route_key(route) for route in guest_routes}
    auth_keys = {_route_key(route) for route in auth_routes}
    auth_only = [route for route in auth_routes if _route_key(route) not in guest_keys]
    guest_only = [route for route in guest_routes if _route_key(route) not in auth_keys]

    result = {
        "guest": _summary(guest),
        "authenticated": _summary(auth),
        "guest_manifest": str(guest_path),
        "authenticated_manifest": str(auth_path),
        "common_route_count": len(guest_keys & auth_keys),
        "auth_only_route_count": len(auth_only),
        "guest_only_route_count": len(guest_only),
        "auth_only_by_page_type": dict(Counter(route.get("page_type", "unknown") for route in auth_only)),
        "auth_only_sample": [
            _route_view(route)
            for route in auth_only[:20]
        ],
        "guest_only_sample": [
            _route_view(route)
            for route in guest_only[:20]
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare two crawler route manifests by path and query."
    )
    parser.add_argument("guest_manifest", help="Path to the guest/public route manifest.")
    parser.add_argument(
        "authenticated_manifest",
        help="Path to the authenticated or second-phase route manifest.",
    )
    return parser.parse_args()


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _summary(manifest: dict) -> dict:
    summary = manifest.get("summary", {})
    return {
        "path_count": len({_route_key(route) for route in manifest.get("routes", [])}),
        "route_count": summary.get("route_count", len(manifest.get("routes", []))),
        "visited_count": summary.get("visited_count"),
        "pending_count": summary.get("pending_count"),
        "skipped_count": summary.get("skipped_count"),
        "error_count": summary.get("error_count"),
        "phase": manifest.get("crawl_options", {}).get("phase"),
    }


def _route_key(route: dict) -> str:
    path = route.get("path") or "/"
    query = route.get("query") or ""
    return f"{path}?{query}"


def _route_view(route: dict) -> dict:
    return {
        "label": route.get("label"),
        "path": route.get("path"),
        "query": route.get("query"),
        "page_type": route.get("page_type"),
        "depth": route.get("depth"),
        "source_path": route.get("source_path"),
    }


if __name__ == "__main__":
    main()
