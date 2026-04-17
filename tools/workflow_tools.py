"""High-level workflow tools for repeatable browser task generation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from adk_playwright_agent.app.policies import resolve_workspace_path
from adk_playwright_agent.tools.crawler_tools import (
    crawl_authenticated_site_to_manifest,
    crawl_site_to_manifest,
)
from adk_playwright_agent.tools.generator_tools import generate_tasks_from_manifest
from adk_playwright_agent.tools.validation_tools import validate_task_directory

_DEFAULT_LOGIN_PATH = "/login"
_LOGIN_POSITIVE_TERMS = {
    "login",
    "log in",
    "log-in",
    "sign in",
    "sign-in",
    "signin",
}
_LOGIN_NEGATIVE_TERMS = {
    "forgot",
    "forgot password",
    "forgot-password",
    "logout",
    "log out",
    "log-out",
    "password recovery",
    "password reset",
    "recover password",
    "reset password",
    "reset-password",
    "sign out",
    "sign-out",
    "signup",
    "sign up",
    "register",
}
_LOGIN_NEGATIVE_COMPACT_TERMS = {
    "forgotpassword",
    "passwordrecovery",
    "passwordreset",
    "recoverpassword",
    "resetpassword",
}


def run_manifest_first_route_workflow(
    start_url: str,
    site_name: str | None = None,
    output_root: str | None = None,
    credentials_system_name: str | None = None,
    credentials_path: str | None = None,
    storage_state_path: str | None = None,
    sut_profile: str = "generic",
    session_name_prefix: str = "workflow",
    guest_max_depth: int = 3,
    auth_max_depth: int = 3,
    max_pages: int = 100,
    auth_max_pages: int | None = None,
    max_links_per_page: int = 80,
    include_patterns: str | None = None,
    exclude_patterns: str | None = None,
    auth_include_patterns: str | None = None,
    auth_exclude_patterns: str | None = None,
    login_path: str | None = None,
    run_guest: bool = True,
    run_authenticated: bool = True,
    generate_guest_tasks: bool = True,
    generate_auth_tasks: bool = True,
    validate_outputs: bool = True,
    clean_task_dirs: bool = True,
    headed: bool = True,
    persistent: bool = True,
    same_origin_only: bool = True,
    close_on_finish: bool = True,
    include_home: bool = True,
    include_unsafe_routes: bool = False,
    skip_invalid_query_routes: bool = True,
    max_tasks: int | None = None,
    context_window_tokens: int = 128_000,
) -> dict[str, Any]:
    """Run guest/auth crawl, route-task generation, and validation as one workflow."""

    normalized_site_name = _slug(site_name or _site_name_from_url(start_url))
    normalized_profile = _slug(sut_profile or "generic")
    root = resolve_workspace_path(output_root or f"manifests/{normalized_site_name}")
    root.mkdir(parents=True, exist_ok=True)

    resolved_storage_state_path = storage_state_path or f".auth/{normalized_site_name}_state.json"
    resolved_credentials_system_name = credentials_system_name or normalized_site_name

    guest_manifest_path = root / f"route_manifest.guest.{normalized_profile}.json"
    auth_manifest_path = root / f"route_manifest.auth.{normalized_profile}.json"
    guest_task_dir = root / "generated_tasks" / "guest"
    auth_task_dir = root / "generated_tasks" / "auth"

    result: dict[str, Any] = {
        "ok": True,
        "start_url": start_url,
        "site_name": normalized_site_name,
        "sut_profile": normalized_profile,
        "output_root": str(root),
        "storage_state_path": resolved_storage_state_path,
        "phases": {},
        "summary": {
            "guest_route_count": 0,
            "auth_route_count": 0,
            "guest_generated_count": 0,
            "auth_generated_count": 0,
            "guest_valid_files": 0,
            "auth_valid_files": 0,
            "total_generated_count": 0,
            "total_skipped_count": 0,
            "validation_issue_count": 0,
            "login_path": None,
            "login_path_source": None,
        },
        "issues": [],
    }

    if run_guest:
        guest_phase = _run_guest_phase(
            start_url=start_url,
            output_path=guest_manifest_path,
            session_name=f"{session_name_prefix}-guest",
            sut_profile=normalized_profile,
            max_depth=guest_max_depth,
            max_pages=max_pages,
            max_links_per_page=max_links_per_page,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            headed=headed,
            persistent=persistent,
            same_origin_only=same_origin_only,
            close_on_finish=close_on_finish,
            context_window_tokens=context_window_tokens,
        )
        result["phases"]["guest"] = guest_phase
        _merge_phase_summary(result, "guest", guest_phase)

        if generate_guest_tasks:
            _generate_and_validate_phase(
                result=result,
                phase_name="guest",
                phase=guest_phase,
                task_dir=guest_task_dir,
                site_name=normalized_site_name,
                start_url=start_url,
                task_id_prefix=f"{normalized_site_name}_guest",
                require_login=False,
                storage_state_path=None,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                include_home=include_home,
                include_unsafe_routes=include_unsafe_routes,
                skip_invalid_query_routes=skip_invalid_query_routes,
                max_tasks=max_tasks,
                clean_task_dirs=clean_task_dirs,
                validate_outputs=validate_outputs,
            )
    else:
        result["phases"]["guest"] = {"skipped": True, "reason": "run_guest_false"}

    if run_authenticated:
        login_discovery = _resolve_workflow_login_path(
            explicit_login_path=login_path,
            guest_phase=result["phases"].get("guest", {}),
            fallback=_DEFAULT_LOGIN_PATH,
        )
        resolved_login_path = login_discovery["path"]
        result["summary"]["login_path"] = resolved_login_path
        result["summary"]["login_path_source"] = login_discovery["source"]
        result["phases"]["login_discovery"] = login_discovery

        auth_phase = _run_auth_phase(
            start_url=start_url,
            output_path=auth_manifest_path,
            session_name=f"{session_name_prefix}-auth",
            sut_profile=normalized_profile,
            max_depth=auth_max_depth,
            max_pages=auth_max_pages or max_pages,
            max_links_per_page=max_links_per_page,
            include_patterns=auth_include_patterns if auth_include_patterns is not None else include_patterns,
            exclude_patterns=auth_exclude_patterns if auth_exclude_patterns is not None else exclude_patterns,
            headed=headed,
            persistent=persistent,
            same_origin_only=same_origin_only,
            close_on_finish=close_on_finish,
            context_window_tokens=context_window_tokens,
            credentials_system_name=resolved_credentials_system_name,
            credentials_path=credentials_path,
            storage_state_path=resolved_storage_state_path,
            login_path=resolved_login_path,
        )
        result["phases"]["auth"] = auth_phase
        _merge_phase_summary(result, "auth", auth_phase)

        if generate_auth_tasks:
            _generate_and_validate_phase(
                result=result,
                phase_name="auth",
                phase=auth_phase,
                task_dir=auth_task_dir,
                site_name=normalized_site_name,
                start_url=start_url,
                task_id_prefix=f"{normalized_site_name}_auth",
                require_login=True,
                storage_state_path=resolved_storage_state_path,
                include_patterns=auth_include_patterns if auth_include_patterns is not None else include_patterns,
                exclude_patterns=auth_exclude_patterns if auth_exclude_patterns is not None else exclude_patterns,
                include_home=include_home,
                include_unsafe_routes=include_unsafe_routes,
                skip_invalid_query_routes=skip_invalid_query_routes,
                max_tasks=max_tasks,
                clean_task_dirs=clean_task_dirs,
                validate_outputs=validate_outputs,
            )
    else:
        result["phases"]["auth"] = {
            "skipped": True,
            "reason": "run_authenticated_false",
        }

    summary = result["summary"]
    summary["total_generated_count"] = (
        summary["guest_generated_count"] + summary["auth_generated_count"]
    )
    result["ok"] = not result["issues"]
    return result


def _resolve_workflow_login_path(
    *,
    explicit_login_path: str | None,
    guest_phase: dict[str, Any],
    fallback: str,
) -> dict[str, Any]:
    if explicit_login_path:
        return {
            "path": explicit_login_path,
            "source": "explicit",
            "confidence": 1.0,
            "candidates": [],
        }

    manifest_path = guest_phase.get("manifest_path") or guest_phase.get("crawl", {}).get("manifest_path")
    if not manifest_path:
        return {
            "path": fallback,
            "source": "fallback_no_guest_manifest",
            "confidence": 0.0,
            "candidates": [],
        }

    return _discover_login_path_from_manifest(str(manifest_path), fallback=fallback)


def _discover_login_path_from_manifest(manifest_path: str, fallback: str) -> dict[str, Any]:
    try:
        manifest_file = resolve_workspace_path(manifest_path)
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "path": fallback,
            "source": "fallback_manifest_unreadable",
            "confidence": 0.0,
            "error": str(exc),
            "candidates": [],
        }

    routes = manifest.get("routes", [])
    if not isinstance(routes, list):
        return {
            "path": fallback,
            "source": "fallback_manifest_has_no_routes",
            "confidence": 0.0,
            "candidates": [],
        }

    candidates = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        score, reasons = _score_login_route(route)
        if score <= 0:
            continue
        candidates.append(
            {
                "path": _route_key(route),
                "label": str(route.get("label") or ""),
                "score": score,
                "reasons": reasons,
            }
        )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    if not candidates or candidates[0]["score"] < 50:
        return {
            "path": fallback,
            "source": "fallback_no_confident_login_route",
            "confidence": 0.0,
            "candidates": candidates[:5],
        }

    best = candidates[0]
    return {
        "path": best["path"],
        "source": "guest_manifest",
        "confidence": round(min(float(best["score"]) / 100.0, 1.0), 2),
        "candidates": candidates[:5],
    }


def _score_login_route(route: dict[str, Any]) -> tuple[int, list[str]]:
    path = _route_key(route).lower()
    context = route.get("context", {}) if isinstance(route.get("context"), dict) else {}
    headings_text = " ".join(str(value) for value in context.get("headings", []) if value)
    actions_text = " ".join(str(value) for value in context.get("primary_actions", []) if value)
    identity_parts = [
        str(route.get("label") or ""),
        str(route.get("path") or ""),
        str(route.get("url") or ""),
        str(route.get("page_title") or ""),
        headings_text,
    ]
    text_parts = [
        *identity_parts,
        str(route.get("page_type") or ""),
        actions_text,
    ]
    forms = context.get("forms", []) if isinstance(context.get("forms"), list) else []
    identity_text = _normalize_text(" ".join(identity_parts))
    text = _normalize_text(" ".join(text_parts))
    compact_path = path.replace("-", "").replace("_", "")
    compact_identity_text = identity_text.replace(" ", "").replace("-", "").replace("_", "")

    if any(term in identity_text or term in path for term in _LOGIN_NEGATIVE_TERMS) or any(
        term in compact_identity_text or term in compact_path for term in _LOGIN_NEGATIVE_COMPACT_TERMS
    ):
        return 0, ["negative_login_term"]

    score = 0
    reasons: list[str] = []
    if compact_path in {"/login", "/signin"}:
        score += 90
        reasons.append("exact_login_path")
    elif compact_path.endswith("/login") or compact_path.endswith("/signin"):
        score += 80
        reasons.append("login_path_suffix")
    elif "login" in compact_path or "signin" in compact_path:
        score += 70
        reasons.append("login_path_contains_term")

    if any(term in text for term in _LOGIN_POSITIVE_TERMS):
        score += 35
        reasons.append("login_text")

    has_login_evidence = any(
        reason in reasons
        for reason in ("exact_login_path", "login_path_suffix", "login_path_contains_term", "login_text")
    )
    if not has_login_evidence:
        return 0, ["no_login_evidence"]

    if str(route.get("page_type") or "").lower() == "auth":
        score += 20
        reasons.append("auth_page_type")
    if any(str(form.get("type") or "").lower() == "password" for form in forms if isinstance(form, dict)):
        score += 30
        reasons.append("password_field")

    return score, reasons


def _run_guest_phase(
    *,
    start_url: str,
    output_path: Path,
    session_name: str,
    sut_profile: str,
    max_depth: int,
    max_pages: int,
    max_links_per_page: int,
    include_patterns: str | None,
    exclude_patterns: str | None,
    headed: bool,
    persistent: bool,
    same_origin_only: bool,
    close_on_finish: bool,
    context_window_tokens: int,
) -> dict[str, Any]:
    crawl = crawl_site_to_manifest(
        start_url=start_url,
        output_path=str(output_path),
        session_name=session_name,
        sut_profile=sut_profile,
        max_depth=max_depth,
        max_pages=max_pages,
        max_links_per_page=max_links_per_page,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        headed=headed,
        persistent=persistent,
        same_origin_only=same_origin_only,
        close_on_finish=close_on_finish,
        context_window_tokens=context_window_tokens,
    )
    return {
        "manifest_path": crawl.get("manifest_path"),
        "task_dir": str(output_path.parent / "generated_tasks" / "guest"),
        "crawl": crawl,
    }


def _run_auth_phase(
    *,
    start_url: str,
    output_path: Path,
    session_name: str,
    sut_profile: str,
    max_depth: int,
    max_pages: int,
    max_links_per_page: int,
    include_patterns: str | None,
    exclude_patterns: str | None,
    headed: bool,
    persistent: bool,
    same_origin_only: bool,
    close_on_finish: bool,
    context_window_tokens: int,
    credentials_system_name: str,
    credentials_path: str | None,
    storage_state_path: str,
    login_path: str,
) -> dict[str, Any]:
    crawl = crawl_authenticated_site_to_manifest(
        start_url=start_url,
        output_path=str(output_path),
        credentials_system_name=credentials_system_name,
        credentials_path=credentials_path,
        storage_state_path=storage_state_path,
        session_name=session_name,
        sut_profile=sut_profile,
        max_depth=max_depth,
        max_pages=max_pages,
        max_links_per_page=max_links_per_page,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        headed=headed,
        persistent=persistent,
        same_origin_only=same_origin_only,
        close_on_finish=close_on_finish,
        context_window_tokens=context_window_tokens,
        login_path=login_path,
    )
    return {
        "manifest_path": crawl.get("manifest_path"),
        "task_dir": str(output_path.parent / "generated_tasks" / "auth"),
        "crawl": crawl,
    }


def _generate_and_validate_phase(
    *,
    result: dict[str, Any],
    phase_name: str,
    phase: dict[str, Any],
    task_dir: Path,
    site_name: str,
    start_url: str,
    task_id_prefix: str,
    require_login: bool,
    storage_state_path: str | None,
    include_patterns: str | None,
    exclude_patterns: str | None,
    include_home: bool,
    include_unsafe_routes: bool,
    skip_invalid_query_routes: bool,
    max_tasks: int | None,
    clean_task_dirs: bool,
    validate_outputs: bool,
) -> None:
    crawl = phase.get("crawl", {})
    if not _is_stable_crawl(crawl):
        issue = {
            "phase": phase_name,
            "type": "unstable_manifest",
            "message": "Task generation skipped because crawl did not finish cleanly.",
            "crawl_ok": crawl.get("ok"),
            "pending_count": crawl.get("pending_count"),
            "error_count": crawl.get("error_count"),
            "manifest_path": crawl.get("manifest_path"),
        }
        result["issues"].append(issue)
        phase["generation"] = {"skipped": True, "reason": "unstable_manifest"}
        return

    if clean_task_dirs:
        _remove_generated_task_files(task_dir)

    generation = generate_tasks_from_manifest(
        manifest_path=str(crawl["manifest_path"]),
        output_dir=str(task_dir),
        site_name=site_name,
        storage_state_path=storage_state_path,
        require_login=require_login,
        start_url=start_url,
        task_id_prefix=task_id_prefix,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        include_home=include_home,
        include_unsafe_routes=include_unsafe_routes,
        skip_invalid_query_routes=skip_invalid_query_routes,
        max_tasks=max_tasks,
    )
    phase["generation"] = generation
    result["summary"][f"{phase_name}_generated_count"] = generation["generated_count"]
    result["summary"]["total_skipped_count"] += generation["skipped_count"]

    if not validate_outputs:
        return

    validation = validate_task_directory(
        directory=str(task_dir),
        expected_start_url=start_url,
    )
    phase["validation"] = validation
    result["summary"][f"{phase_name}_valid_files"] = validation["valid_files"]
    result["summary"]["validation_issue_count"] += len(validation.get("issues", []))
    for issue in validation.get("issues", []):
        result["issues"].append(
            {
                "phase": phase_name,
                "type": "validation_issue",
                **issue,
            }
        )


def _merge_phase_summary(result: dict[str, Any], phase_name: str, phase: dict[str, Any]) -> None:
    crawl = phase.get("crawl", {})
    result["summary"][f"{phase_name}_route_count"] = int(crawl.get("route_count") or 0)
    if crawl and not _is_stable_crawl(crawl):
        result["issues"].append(
            {
                "phase": phase_name,
                "type": "crawl_issue",
                "message": "Crawl did not finish cleanly.",
                "crawl_ok": crawl.get("ok"),
                "pending_count": crawl.get("pending_count"),
                "error_count": crawl.get("error_count"),
                "manifest_path": crawl.get("manifest_path"),
            }
        )


def _is_stable_crawl(crawl: dict[str, Any]) -> bool:
    return (
        bool(crawl.get("ok"))
        and int(crawl.get("pending_count") or 0) == 0
        and int(crawl.get("error_count") or 0) == 0
        and bool(crawl.get("manifest_path"))
    )


def _remove_generated_task_files(task_dir: Path) -> None:
    resolved_dir = resolve_workspace_path(str(task_dir))
    if not resolved_dir.exists():
        return
    if not resolved_dir.is_dir():
        raise ValueError(f"Task output path is not a directory: {resolved_dir}")
    for path in resolved_dir.glob("task_*.json"):
        if path.is_file():
            path.unlink()


def _site_name_from_url(url: str) -> str:
    host = re.sub(r"^https?://", "", url.strip(), flags=re.IGNORECASE).split("/")[0]
    host = host.split(":")[0]
    return host or "webapp"


def _route_key(route: dict[str, Any]) -> str:
    path = str(route.get("path") or "/")
    query = str(route.get("query") or "")
    return f"{path}?{query}" if query else path


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "webapp"
