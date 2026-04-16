"""High-level crawler tools built on top of playwright-cli."""

from __future__ import annotations

import fnmatch
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from adk_playwright_agent.adapters.credentials import load_named_credentials
from adk_playwright_agent.adapters.playwright_cli import PlaywrightCliAdapter
from adk_playwright_agent.app.context_memory import (
    CredentialReference,
    CrawlerContext,
    PageSummary,
)
from adk_playwright_agent.app.policies import DANGEROUS_UI_KEYWORDS, resolve_workspace_path

_ADAPTER = PlaywrightCliAdapter()

_DOWNLOAD_EXTENSIONS = {
    ".7z",
    ".csv",
    ".doc",
    ".docx",
    ".gz",
    ".pdf",
    ".tar",
    ".xls",
    ".xlsx",
    ".zip",
}
_SKIP_SCHEMES = {"javascript", "mailto", "tel", "data", "blob"}
_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_NAMES = {"fbclid", "gclid", "mc_cid", "mc_eid"}
_GENERIC_LABELS = {
    "導覽",
    "navigation",
    "nav",
    "menu",
}
_LOW_VALUE_LABEL_RE = re.compile(
    r"(^/)|(\d{4}年)|(\b(?:am|pm)\b)|(\b\d{1,2}:\d{2}\b)",
    re.IGNORECASE,
)

_PAGE_DATA_SCRIPT = (
    "JSON.stringify((() => {"
    "const text = el => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();"
    "const attr = (el, name) => el.getAttribute(name) || '';"
    "const safeUrl = href => { try { return new URL(href, location.href); } catch { return null; } };"
    "const links = [...document.querySelectorAll('a[href]')].map(a => {"
    "  const url = safeUrl(attr(a, 'href'));"
    "  return url ? { text: text(a), href: url.href, path: url.pathname, query: url.search.slice(1), same_origin: url.origin === location.origin } : null;"
    "}).filter(Boolean);"
    "const actions = [...document.querySelectorAll('button, [role=\"button\"], a[href]')].map(text).filter(Boolean).slice(0, 80);"
    "const headings = [...document.querySelectorAll('h1,h2,h3,[role=\"heading\"]')].map(text).filter(Boolean).slice(0, 20);"
    "const forms = [...document.querySelectorAll('input,textarea,select')].map(el => ({"
    "  tag: el.tagName.toLowerCase(),"
    "  type: attr(el, 'type'),"
    "  name: attr(el, 'name'),"
    "  placeholder: attr(el, 'placeholder'),"
    "  aria_label: attr(el, 'aria-label')"
    "})).slice(0, 40);"
    "return { url: location.href, title: document.title, headings, primary_actions: actions, links, forms };"
    "})())"
)


def crawl_site_to_manifest(
    start_url: str,
    output_path: str,
    session_name: str = "crawler",
    sut_profile: str = "generic",
    max_depth: int = 3,
    max_pages: int = 100,
    max_links_per_page: int = 80,
    include_patterns: str | None = None,
    exclude_patterns: str | None = None,
    headed: bool = True,
    persistent: bool = True,
    same_origin_only: bool = True,
    close_on_finish: bool = False,
    context_window_tokens: int = 128_000,
    crawl_authenticated: bool = False,
    credentials_system_name: str | None = None,
    credentials_path: str | None = None,
    storage_state_path: str | None = None,
    login_path: str = "/login",
) -> dict[str, Any]:
    """Explore same-origin routes with bounded BFS and write a manifest."""

    context = CrawlerContext()
    context.long_term_memory.final_goal = f"Explore {start_url} and write a route manifest."
    context.long_term_memory.target_app = _site_name_from_url(start_url)
    context.task_state.start_url = start_url
    phase = "authenticated" if crawl_authenticated else "guest"
    context.task_state.phase = phase

    include = _split_patterns(include_patterns)
    exclude = _split_patterns(exclude_patterns)
    start_parts = urlsplit(start_url)
    base_origin = f"{start_parts.scheme}://{start_parts.netloc}"
    start_path = _path_with_query(start_parts)
    context.task_state.add_pending(start_path)
    context.task_state.record_route_parent(start_path, source_path=None, label="Home")
    normalized_sut_profile = _normalize_sut_profile(sut_profile)
    credential_system = credentials_system_name or context.long_term_memory.target_app or "webapp"
    if crawl_authenticated and storage_state_path is None:
        storage_state_path = f".auth/{_slug(credential_system)}_state.json"

    routes: list[dict[str, Any]] = []
    depth_by_path = {start_path: 0}
    seen_route_ids: set[str] = set()
    open_result = _ADAPTER.open_browser(
        base_url=start_url,
        session_name=session_name,
        headed=headed,
        persistent=persistent,
    )
    context.record_operation_feedback(
        action="open",
        target=start_url,
        ok=open_result.ok,
        url_after=open_result.url,
        message=open_result.stderr or open_result.stdout,
        error_type=None if open_result.ok else "navigation_failed",
    )

    if not open_result.ok:
        context.task_state.errors.append(_command_error("entry_open_failed", start_path, open_result))
        return _write_manifest(
            output_path=output_path,
            start_url=start_url,
            base_origin=base_origin,
            session_name=session_name,
            max_depth=max_depth,
            max_pages=max_pages,
            routes=routes,
            context=context,
            context_window_tokens=context_window_tokens,
            ok=False,
            same_origin_only=same_origin_only,
            phase=phase,
            sut_profile=normalized_sut_profile,
        )

    try:
        if crawl_authenticated:
            login_result = _perform_login(
                session_name=session_name,
                base_origin=base_origin,
                login_path=login_path,
                credentials_system_name=credentials_system_name
                or credential_system,
                credentials_path=credentials_path,
                storage_state_path=storage_state_path,
                context=context,
            )
            if not login_result["ok"]:
                context.task_state.errors.append(
                    {
                        "path": _path_with_query_from_values(login_path, ""),
                        "error_type": "login_failed",
                        "message": login_result.get("message") or login_result.get("reason"),
                    }
                )
                return _write_manifest(
                    output_path=output_path,
                    start_url=start_url,
                    base_origin=base_origin,
                    session_name=session_name,
                    max_depth=max_depth,
                    max_pages=max_pages,
                    routes=routes,
                    context=context,
                    context_window_tokens=context_window_tokens,
                    ok=False,
                    same_origin_only=same_origin_only,
                    phase=phase,
                    sut_profile=normalized_sut_profile,
                )

        while context.task_state.pending_paths and len(routes) < max_pages:
            current_path = context.task_state.pending_paths.pop(0)
            current_depth = depth_by_path.get(current_path, 0)
            if current_path in context.task_state.visited_paths:
                continue
            if current_depth > max_depth:
                context.task_state.skipped_routes.append(
                    {
                        "path": current_path,
                        "reason": "max_depth_exceeded",
                        "depth": current_depth,
                    }
                )
                continue

            current_url = _full_url(base_origin, current_path)
            goto_result = _ADAPTER.goto(session_name=session_name, url=current_url)
            context.record_operation_feedback(
                action="goto",
                target=current_url,
                ok=goto_result.ok,
                url_before=current_url,
                url_after=goto_result.url,
                message=goto_result.stderr or goto_result.stdout,
                error_type=None if goto_result.ok else "navigation_failed",
            )
            if not goto_result.ok:
                context.task_state.errors.append(
                    _command_error("navigation_failed", current_path, goto_result)
                )
                context.task_state.skipped_routes.append(
                    {
                        "path": current_path,
                        "reason": "navigation_failed",
                        "depth": current_depth,
                    }
                )
                continue

            page_data, page_error = _collect_page_data(session_name)
            if page_error:
                context.task_state.errors.append(
                    {
                        "path": current_path,
                        "error_type": "page_data_failed",
                        "message": page_error,
                    }
                )

            page_summary = _page_summary_from_data(
                page_data=page_data,
                fallback_url=goto_result.url or current_url,
                fallback_title=goto_result.title,
                max_links=max_links_per_page,
            )
            context.set_current_page(page_summary)

            discovered_links = _accepted_links(
                page_data=page_data,
                current_url=page_summary.url,
                base_origin=base_origin,
                same_origin_only=same_origin_only,
                include_patterns=include,
                exclude_patterns=exclude,
                max_links=max_links_per_page,
            )
            for link in discovered_links:
                candidate_path = link["path"]
                if candidate_path not in context.task_state.route_parents:
                    context.task_state.record_route_parent(
                        candidate_path,
                        source_path=current_path,
                        label=link.get("text") or candidate_path,
                    )
                if (
                    candidate_path not in context.task_state.visited_paths
                    and candidate_path not in context.task_state.pending_paths
                    and current_depth + 1 <= max_depth
                ):
                    context.task_state.add_pending(candidate_path)
                    depth_by_path[candidate_path] = current_depth + 1

            context.task_state.add_visited(current_path)
            route = _route_record(
                site_name=context.long_term_memory.target_app or "webapp",
                base_origin=base_origin,
                path=current_path,
                depth=current_depth,
                page=page_summary,
                discovered_links=discovered_links,
                parents=context.task_state.route_parents,
                seen_route_ids=seen_route_ids,
                phase=phase,
                require_login=crawl_authenticated,
            )
            routes.append(route)
            context.task_state.discovered_routes.append(route)
    finally:
        if close_on_finish:
            _ADAPTER.close_browser(session_name=session_name)

    return _write_manifest(
        output_path=output_path,
        start_url=start_url,
        base_origin=base_origin,
        session_name=session_name,
        max_depth=max_depth,
        max_pages=max_pages,
        routes=routes,
        context=context,
        context_window_tokens=context_window_tokens,
        ok=True,
        same_origin_only=same_origin_only,
        phase=phase,
        sut_profile=normalized_sut_profile,
    )


def crawl_authenticated_site_to_manifest(
    start_url: str,
    output_path: str,
    credentials_system_name: str | None = None,
    credentials_path: str | None = None,
    storage_state_path: str | None = None,
    session_name: str = "crawler-auth",
    sut_profile: str = "generic",
    max_depth: int = 3,
    max_pages: int = 100,
    max_links_per_page: int = 80,
    include_patterns: str | None = None,
    exclude_patterns: str | None = None,
    headed: bool = True,
    persistent: bool = True,
    same_origin_only: bool = True,
    close_on_finish: bool = False,
    context_window_tokens: int = 128_000,
    login_path: str = "/login",
) -> dict[str, Any]:
    """Login with notes-file credentials, then crawl authenticated routes."""

    inferred_system_name = credentials_system_name or _site_name_from_url(start_url)
    inferred_storage_state_path = storage_state_path or f".auth/{_slug(inferred_system_name)}_state.json"

    return crawl_site_to_manifest(
        start_url=start_url,
        output_path=output_path,
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
        crawl_authenticated=True,
        credentials_system_name=inferred_system_name,
        credentials_path=credentials_path,
        storage_state_path=inferred_storage_state_path,
        login_path=login_path,
    )


def _collect_page_data(session_name: str) -> tuple[dict[str, Any], str | None]:
    result = _ADAPTER.eval_js(session_name=session_name, script=_PAGE_DATA_SCRIPT, raw=True)
    if not result.ok:
        return {}, result.stderr or result.stdout or "playwright-cli eval failed"
    raw = result.raw_value
    if isinstance(raw, dict):
        return raw, None
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}, "page data JSON decode failed"
        if isinstance(decoded, dict):
            return decoded, None
    return {}, "page data was not an object"


def _perform_login(
    session_name: str,
    base_origin: str,
    login_path: str,
    credentials_system_name: str,
    credentials_path: str | None,
    storage_state_path: str | None,
    context: CrawlerContext,
) -> dict[str, Any]:
    source = credentials_path or os.getenv("DEFAULT_CREDENTIALS_FILE")
    if not source:
        return {
            "ok": False,
            "reason": "missing_credentials_path",
            "message": "No credentials path provided and DEFAULT_CREDENTIALS_FILE is not set.",
        }

    credentials = load_named_credentials(source, credentials_system_name)
    context.long_term_memory.remember_credentials(
        CredentialReference(
            system_name=credentials_system_name,
            username=credentials.get("username"),
            credentials_source=source,
            storage_state_path=storage_state_path,
            verified_at=_utc_now(),
        )
    )

    login_url = _full_url(base_origin, _path_with_query_from_values(login_path, ""))
    goto_result = _ADAPTER.goto(session_name=session_name, url=login_url)
    context.record_operation_feedback(
        action="goto",
        target=login_url,
        ok=goto_result.ok,
        url_after=goto_result.url,
        message=goto_result.stderr or goto_result.stdout,
        error_type=None if goto_result.ok else "navigation_failed",
    )
    if not goto_result.ok:
        return {
            "ok": False,
            "reason": "login_navigation_failed",
            "message": goto_result.stderr or goto_result.stdout,
        }

    fill_script = _login_script(
        username=credentials["username"],
        password=credentials["password"],
    )
    fill_result = _ADAPTER.eval_js(session_name=session_name, script=fill_script, raw=True)
    login_payload = _coerce_login_payload(fill_result.raw_value)
    ok = fill_result.ok and bool(login_payload.get("ok"))
    context.record_operation_feedback(
        action="login-fill",
        target=credentials_system_name,
        ok=ok,
        url_before=login_url,
        url_after=str(login_payload.get("url") or ""),
        message=str(login_payload.get("reason") or fill_result.stderr or fill_result.stdout or ""),
        error_type=None if ok else "login_failed",
    )
    if not ok:
        return {
            "ok": False,
            "reason": login_payload.get("reason") or "login_failed",
            "message": fill_result.stderr or fill_result.stdout,
            "url": login_payload.get("url"),
        }

    if not login_payload.get("authenticated"):
        submit_selector = str(login_payload.get("submit_selector") or "")
        if submit_selector:
            submit_result = _ADAPTER.click(
                session_name=session_name,
                target=submit_selector,
            )
            submit_action = "login-submit-click"
        else:
            submit_result = _ADAPTER.press_key(session_name=session_name, key="Enter")
            submit_action = "login-submit-enter"

        verify_payload: dict[str, Any] = {}
        verify_result = None
        if submit_result.ok:
            verify_result = _ADAPTER.eval_js(
                session_name=session_name,
                script=_login_verify_script(),
                raw=True,
            )
            verify_payload = _coerce_login_payload(verify_result.raw_value)

        ok = submit_result.ok and bool(verify_payload.get("ok"))
        context.record_operation_feedback(
            action=submit_action,
            target=credentials_system_name,
            ok=ok,
            url_before=login_url,
            url_after=str(
                verify_payload.get("url")
                or submit_result.url
                or login_payload.get("url")
                or ""
            ),
            message=str(
                verify_payload.get("reason")
                or (verify_result.stderr if verify_result else "")
                or submit_result.stderr
                or submit_result.stdout
                or ""
            ),
            error_type=None if ok else "login_failed",
        )
        if not ok:
            return {
                "ok": False,
                "reason": verify_payload.get("reason") or "login_submit_failed",
                "message": (
                    (verify_result.stderr or verify_result.stdout)
                    if verify_result
                    else (submit_result.stderr or submit_result.stdout)
                ),
                "url": verify_payload.get("url") or submit_result.url,
            }
        login_payload = verify_payload

    if storage_state_path:
        destination = resolve_workspace_path(storage_state_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        state_result = _ADAPTER.save_storage_state(
            session_name=session_name,
            path=str(destination),
        )
        context.record_operation_feedback(
            action="state-save",
            target=str(destination),
            ok=state_result.ok,
            url_after=state_result.url,
            message=state_result.stderr or state_result.stdout,
            error_type=None if state_result.ok else "state_save_failed",
        )
        if state_result.ok:
            context.long_term_memory.storage_state_paths[credentials_system_name] = str(destination)

    return {
        "ok": True,
        "url": login_payload.get("url"),
        "username": credentials.get("username"),
        "storage_state_path": storage_state_path,
    }


def _page_summary_from_data(
    page_data: dict[str, Any],
    fallback_url: str,
    fallback_title: str | None,
    max_links: int,
) -> PageSummary:
    links = [
        {
            "text": _clean_text(str(link.get("text") or link.get("path") or "")),
            "path": _path_with_query_from_values(
                str(link.get("path") or "/"),
                str(link.get("query") or ""),
            ),
        }
        for link in page_data.get("links", [])
        if isinstance(link, dict)
    ][:max_links]

    return PageSummary(
        url=str(page_data.get("url") or fallback_url),
        title=str(page_data.get("title") or fallback_title or ""),
        headings=_clean_list(page_data.get("headings", []), limit=20),
        primary_actions=_clean_list(page_data.get("primary_actions", []), limit=30),
        links_sample=links,
        forms=_clean_forms(page_data.get("forms", []), limit=20),
        snapshot_artifact=None,
    )


def _accepted_links(
    page_data: dict[str, Any],
    current_url: str,
    base_origin: str,
    same_origin_only: bool,
    include_patterns: list[str],
    exclude_patterns: list[str],
    max_links: int,
) -> list[dict[str, str]]:
    accepted: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_link in page_data.get("links", []):
        if not isinstance(raw_link, dict):
            continue
        normalized = _normalize_candidate_link(
            raw_link=raw_link,
            current_url=current_url,
            base_origin=base_origin,
            same_origin_only=same_origin_only,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
        )
        if normalized is None or normalized["path"] in seen:
            continue
        seen.add(normalized["path"])
        accepted.append(normalized)
        if len(accepted) >= max_links:
            break
    return accepted


def _normalize_candidate_link(
    raw_link: dict[str, Any],
    current_url: str,
    base_origin: str,
    same_origin_only: bool,
    include_patterns: list[str],
    exclude_patterns: list[str],
) -> dict[str, str] | None:
    href = str(raw_link.get("href") or "")
    text = _clean_text(str(raw_link.get("text") or ""))
    if not href or href.startswith("#"):
        return None

    parsed = urlsplit(urljoin(current_url, href))
    if parsed.scheme in _SKIP_SCHEMES:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None

    origin = f"{parsed.scheme}://{parsed.netloc}"
    if same_origin_only and origin != base_origin:
        return None

    path = parsed.path or "/"
    suffix = Path(path).suffix.lower()
    if suffix in _DOWNLOAD_EXTENSIONS:
        return None
    if path.lower().endswith(".rss") or "/rss" in path.lower():
        return None

    query = _strip_tracking_query(parsed.query)
    path_with_query = _path_with_query_from_values(path, query)
    lowered_route = f"{path_with_query} {text}".lower()
    if "logout" in lowered_route:
        return None
    if any(keyword in lowered_route for keyword in DANGEROUS_UI_KEYWORDS):
        return None
    if include_patterns and not _matches_any(path_with_query, include_patterns):
        return None
    if exclude_patterns and _matches_any(path_with_query, exclude_patterns):
        return None

    return {
        "text": text or path_with_query,
        "url": _full_url(base_origin, path_with_query),
        "path": path_with_query,
    }


def _route_record(
    site_name: str,
    base_origin: str,
    path: str,
    depth: int,
    page: PageSummary,
    discovered_links: list[dict[str, str]],
    parents: dict[str, dict[str, str | None]],
    seen_route_ids: set[str],
    phase: str,
    require_login: bool,
) -> dict[str, Any]:
    split_path = urlsplit(path)
    page_type = _classify_page_type(split_path.path)
    label = _route_label(path, page, parents)
    route_id = _unique_route_id(_route_id(site_name, page_type, path, phase), seen_route_ids)
    source_path = parents.get(path, {}).get("source_path") if path in parents else None

    return {
        "id": route_id,
        "label": label,
        "url": _full_url(base_origin, path),
        "path": split_path.path or "/",
        "query": split_path.query,
        "page_title": page.title,
        "page_type": page_type,
        "phase": phase,
        "require_login": require_login,
        "depth": depth,
        "source_path": source_path,
        "navigation_steps": _build_navigation_steps(path, parents),
        "assertions": _route_assertions(path, label),
        "validation_mode": "url",
        "discovered_links": [link["path"] for link in discovered_links],
        "context": {
            "headings": page.headings[:10],
            "primary_actions": page.primary_actions[:10],
            "snapshot_artifact": page.snapshot_artifact,
        },
    }


def _write_manifest(
    output_path: str,
    start_url: str,
    base_origin: str,
    session_name: str,
    max_depth: int,
    max_pages: int,
    routes: list[dict[str, Any]],
    context: CrawlerContext,
    context_window_tokens: int,
    ok: bool,
    same_origin_only: bool,
    phase: str,
    sut_profile: str,
) -> dict[str, Any]:
    destination = resolve_workspace_path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    context_pack = context.build_context_pack(max_context_tokens=context_window_tokens)
    manifest = {
        "schema_version": "1.0",
        "generated_at": _utc_now(),
        "start_url": start_url,
        "base_origin": base_origin,
        "crawl_options": {
            "session_name": session_name,
            "phase": phase,
            "sut_profile": sut_profile,
            "max_depth": max_depth,
            "max_pages": max_pages,
            "same_origin_only": same_origin_only,
            "crawl_authenticated": phase == "authenticated",
        },
        "summary": {
            "route_count": len(routes),
            "visited_count": len(context.task_state.visited_paths),
            "pending_count": len(context.task_state.pending_paths),
            "skipped_count": len(context.task_state.skipped_routes),
            "error_count": len(context.task_state.errors),
        },
        "routes": routes,
        "skipped_routes": context.task_state.skipped_routes,
        "errors": context.task_state.errors,
        "context_summary": {
            "budget": context_pack["context_budget"],
            "goal": context_pack["goal"],
            "target_app": context_pack["target_app"],
            "blocked_actions": context_pack["long_term_memory"]["blocked_actions"],
        },
    }
    write_destination = _manifest_write_destination(destination, ok=ok)
    write_destination.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    preserved_existing_manifest = str(destination) if write_destination != destination else None
    return {
        "ok": ok,
        "manifest_path": str(write_destination),
        "preserved_existing_manifest": preserved_existing_manifest,
        "route_count": len(routes),
        "visited_count": len(context.task_state.visited_paths),
        "pending_count": len(context.task_state.pending_paths),
        "skipped_count": len(context.task_state.skipped_routes),
        "error_count": len(context.task_state.errors),
        "context_budget": context_pack["context_budget"],
    }


def _manifest_write_destination(destination: Path, ok: bool) -> Path:
    if ok or not destination.exists():
        return destination

    try:
        existing = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return destination

    summary = existing.get("summary", {}) if isinstance(existing, dict) else {}
    route_count = summary.get("route_count", existing.get("route_count", 0))
    try:
        existing_route_count = int(route_count or 0)
    except (TypeError, ValueError):
        existing_route_count = 0
    if existing_route_count <= 0:
        return destination

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = destination.with_name(
        f"{destination.stem}.failed.{timestamp}{destination.suffix}"
    )
    index = 2
    while candidate.exists():
        candidate = destination.with_name(
            f"{destination.stem}.failed.{timestamp}.{index}{destination.suffix}"
        )
        index += 1
    return candidate


def _build_navigation_steps(
    path: str,
    parents: dict[str, dict[str, str | None]],
) -> list[str]:
    steps = ["I open the configured home page"]
    chain: list[tuple[str, str]] = []
    current = path
    seen: set[str] = set()
    while current in parents and current not in seen:
        seen.add(current)
        parent = parents[current]
        source_path = parent.get("source_path")
        if not source_path:
            break
        chain.append((parent.get("label") or current, current))
        current = source_path
    for label, target_path in reversed(chain):
        steps.append(f'I click the "{_clean_text(label)}" link to reach "{target_path}"')
    return steps


def _route_assertions(path: str, label: str) -> list[str]:
    split_path = urlsplit(path)
    assertions = [f'The browser URL should include "{split_path.path or "/"}"']
    if label:
        assertions.append(f'The page title or primary heading should show "{label}"')
    return assertions


def _route_label(
    path: str,
    page: PageSummary,
    parents: dict[str, dict[str, str | None]],
) -> str:
    parent_label = parents.get(path, {}).get("label") if path in parents else None
    if parent_label:
        cleaned_parent = _clean_text(parent_label)
        if cleaned_parent and not _is_low_value_label(cleaned_parent):
            return cleaned_parent
    if page.headings:
        heading = _first_meaningful_label(page.headings)
        if heading:
            return heading
    if page.title:
        title_label = page.title.split("|", 1)[0].strip()
        if not _is_low_value_label(title_label):
            return title_label
    return _clean_text(parent_label or _label_from_path(path) or "Home")


def _classify_page_type(path: str) -> str:
    normalized = path.lower().rstrip("/") or "/"
    if normalized == "/":
        return "home"

    return _classify_generic_page_type(normalized)


def _classify_generic_page_type(normalized: str) -> str:
    segments = [segment for segment in normalized.split("/") if segment]
    segment_set = set(segments)
    first = segments[0] if segments else ""
    last = segments[-1] if segments else ""

    if first in {"login", "register", "signin", "sign-in", "signup", "sign-up"}:
        return "auth"
    if any(segment in segment_set for segment in {"reset", "password-reset", "forgot-password"}):
        return "auth"
    if first in {"admin", "administrator", "manage", "management"}:
        if segment_set & {"settings", "config", "configuration"}:
            return "admin_settings"
        if segment_set & {"users", "roles", "groups", "members"}:
            return "admin_manage"
        return "admin"
    if segment_set & {"settings", "preferences"}:
        return "settings"
    if segment_set & {"account", "profile", "me"} or first in {"user", "users", "member", "members"}:
        return "account"
    if segment_set & {"search", "find"}:
        return "search"
    if segment_set & {"dashboard", "overview"}:
        return "dashboard"
    if last in {"new", "create", "add"}:
        return "create"
    if last in {"edit", "update"}:
        return "edit"
    if len(segments) <= 1:
        return "section"
    if any(_looks_like_identifier(segment) for segment in segments[1:]):
        return "detail"
    return "page"


def _normalize_sut_profile(value: str | None) -> str:
    profile = _slug(value or "generic")
    return profile or "generic"


def _looks_like_identifier(segment: str) -> bool:
    return bool(re.search(r"\d", segment)) or len(segment) >= 24


def _route_id(site_name: str, page_type: str, path: str, phase: str = "guest") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", path.strip("/") or "home").strip("_").lower()
    return f"{_slug(site_name)}_{_slug(phase)}_{page_type}_{slug or 'home'}"


def _unique_route_id(route_id: str, seen: set[str]) -> str:
    candidate = route_id
    index = 2
    while candidate in seen:
        candidate = f"{route_id}_{index}"
        index += 1
    seen.add(candidate)
    return candidate


def _site_name_from_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or "webapp"
    if host == "localhost" and parts.port:
        return f"localhost_{parts.port}"
    return host


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return slug or "webapp"


def _full_url(base_origin: str, path: str) -> str:
    split_path = urlsplit(path)
    return urlunsplit(
        (
            urlsplit(base_origin).scheme,
            urlsplit(base_origin).netloc,
            split_path.path or "/",
            split_path.query,
            "",
        )
    )


def _path_with_query(parts) -> str:
    return _path_with_query_from_values(parts.path or "/", _strip_tracking_query(parts.query))


def _path_with_query_from_values(path: str, query: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    if normalized_path != "/":
        normalized_path = normalized_path.rstrip("/")
    return f"{normalized_path}?{query}" if query else normalized_path


def _strip_tracking_query(query: str) -> str:
    if not query:
        return ""
    kept = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key not in _TRACKING_QUERY_NAMES
        and not any(key.startswith(prefix) for prefix in _TRACKING_QUERY_PREFIXES)
    ]
    return urlencode(kept, doseq=True)


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)


def _split_patterns(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _clean_list(values: Any, limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_clean_text(str(value)) for value in values if _clean_text(str(value))][:limit]


def _clean_forms(values: Any, limit: int) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    forms: list[dict[str, str]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        forms.append({str(key): _clean_text(str(item)) for key, item in value.items()})
        if len(forms) >= limit:
            break
    return forms


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _login_script(username: str, password: str) -> str:
    return (
        "(async () => {"
        "const usernameValue = "
        + json.dumps(username)
        + ";"
        "const passwordValue = "
        + json.dumps(password)
        + ";"
        "const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));"
        "const pick = selectors => selectors.map(selector => document.querySelector(selector)).find(Boolean);"
        "const byText = pattern => [...document.querySelectorAll('button,a,input[type=\"submit\"]')]"
        ".find(el => pattern.test((el.innerText || el.value || el.textContent || '').trim()));"
        "const pathLooksLikeLogin = () => /\\/(login|log-in|signin|sign-in|auth)\\/?$/i.test(location.pathname);"
        "const hasLoggedInEvidence = () => !!document.querySelector('a[href*=\"logout\"], .logged-in, [aria-label*=\"account\" i], [aria-label*=\"profile\" i], [data-testid*=\"user\" i]');"
        "const selectorFor = el => {"
        "if (!el) return '';"
        "if (el.id) return '#' + (window.CSS && CSS.escape ? CSS.escape(el.id) : el.id);"
        "if (el.name) return el.tagName.toLowerCase() + '[name=\"' + el.name.replace(/\"/g, '\\\\\"') + '\"]';"
        "return el.tagName.toLowerCase() + '[type=\"submit\"]';"
        "};"
        "const usernameInput = pick(["
        "'input[name=\"username\"]',"
        "'input[name=\"email\"]',"
        "'input[type=\"email\"]',"
        "'input[autocomplete=\"username\"]',"
        "'#username',"
        "'#email',"
        "'input[type=\"text\"]'"
        "]);"
        "const passwordInput = pick(["
        "'input[name=\"password\"]',"
        "'input[type=\"password\"]',"
        "'input[autocomplete=\"current-password\"]',"
        "'#password'"
        "]);"
        "if (!usernameInput || !passwordInput) {"
        "if (!pathLooksLikeLogin() || hasLoggedInEvidence()) {"
        "return {ok:true, authenticated:true, reason: hasLoggedInEvidence() ? 'already_authenticated_evidence' : 'already_authenticated_redirect', url:location.href, title:document.title};"
        "}"
        "return {ok:false, reason:'missing_login_fields', url:location.href, title:document.title};"
        "}"
        "const setValue = (el, value) => {"
        "el.focus();"
        "el.value = value;"
        "el.dispatchEvent(new Event('input', {bubbles:true}));"
        "el.dispatchEvent(new Event('change', {bubbles:true}));"
        "};"
        "setValue(usernameInput, usernameValue);"
        "setValue(passwordInput, passwordValue);"
        "const submit = pick(['button[type=\"submit\"]','input[type=\"submit\"]','.btn-primary','#login','#submit_login']) || byText(/^(login|log in|sign in|登入)$/i);"
        "return {ok:true, authenticated:false, reason:'login_fields_filled', submit_selector: selectorFor(submit), url:location.href, title:document.title};"
        "})()"
    )


def _login_verify_script() -> str:
    return (
        "(() => {"
        "const pathLooksLikeLogin = () => /\\/(login|log-in|signin|sign-in|auth)\\/?$/i.test(location.pathname);"
        "const hasLoggedInEvidence = () => !!document.querySelector('a[href*=\"logout\"], .logged-in, [aria-label*=\"account\" i], [aria-label*=\"profile\" i], [data-testid*=\"user\" i]');"
        "const evidence = hasLoggedInEvidence();"
        "const stillLogin = pathLooksLikeLogin();"
        "return {ok: evidence || !stillLogin, authenticated: evidence || !stillLogin, reason: evidence ? 'logged_in_evidence' : (stillLogin ? 'still_on_login' : 'left_login_page'), url: location.href, title: document.title};"
        "})()"
    )


def _coerce_login_payload(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    if isinstance(raw_value, str):
        try:
            decoded = json.loads(raw_value)
        except json.JSONDecodeError:
            return {"ok": False, "reason": raw_value}
        if isinstance(decoded, dict):
            return decoded
    return {"ok": False, "reason": "login_payload_not_object"}


def _first_meaningful_label(values: list[str]) -> str | None:
    for value in values:
        cleaned = _clean_text(value)
        if cleaned and not _is_low_value_label(cleaned):
            return cleaned
    return None


def _is_low_value_label(value: str) -> bool:
    cleaned = _clean_text(value)
    return (
        not cleaned
        or cleaned.lower() in _GENERIC_LABELS
        or bool(_LOW_VALUE_LABEL_RE.search(cleaned))
    )


def _label_from_path(path: str) -> str:
    split_path = urlsplit(path)
    path_parts = [part for part in split_path.path.strip("/").split("/") if part]
    if not path_parts:
        return "Home"
    label = path_parts[-1].replace("-", " ").replace("_", " ")
    if split_path.query:
        query_pairs = dict(parse_qsl(split_path.query, keep_blank_values=True))
        if query_pairs.get("filter"):
            label = f"{label} {query_pairs['filter']}"
        elif query_pairs.get("term"):
            label = f"{label} {query_pairs['term']}"
        elif query_pairs.get("section"):
            label = f"{label} {query_pairs['section']}"
    return label.title()


def _command_error(error_type: str, path: str, result) -> dict[str, Any]:
    return {
        "path": path,
        "error_type": error_type,
        "returncode": result.returncode,
        "message": result.stderr or result.stdout,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = ["crawl_authenticated_site_to_manifest", "crawl_site_to_manifest"]
