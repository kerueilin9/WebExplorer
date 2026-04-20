"""Context memory primitives for crawler-style web agents."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

DEFAULT_RECENT_FEEDBACK_LIMIT = 3
DEFAULT_LOOP_BLOCK_AFTER = 2
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
DEFAULT_COMPACTION_RATIO = 0.70


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class PageSummary:
    """Compact page state that can safely be shown to an LLM."""

    url: str
    title: str | None = None
    headings: list[str] = field(default_factory=list)
    primary_actions: list[str] = field(default_factory=list)
    links_sample: list[dict[str, str]] = field(default_factory=list)
    forms: list[dict[str, str]] = field(default_factory=list)
    snapshot_artifact: str | None = None

    def to_context_dict(self, compact_level: int = 0) -> dict[str, Any]:
        link_limit = _limit_for_level(compact_level, full=None, compact=25, minimal=8)
        heading_limit = _limit_for_level(compact_level, full=None, compact=12, minimal=5)
        action_limit = _limit_for_level(compact_level, full=None, compact=20, minimal=8)
        form_limit = _limit_for_level(compact_level, full=None, compact=10, minimal=3)

        return {
            "url": self.url,
            "title": self.title,
            "headings": _limited_text(self.headings, heading_limit, keep_newest=False),
            "primary_actions": _limited_text(
                self.primary_actions,
                action_limit,
                keep_newest=False,
            ),
            "links_sample": _limited_dicts(
                self.links_sample,
                link_limit,
                keep_newest=False,
            ),
            "forms": _limited_dicts(self.forms, form_limit, keep_newest=False),
            "snapshot_artifact": self.snapshot_artifact,
        }


@dataclass(slots=True)
class OperationFeedback:
    """Result of a browser operation kept in short-term working memory."""

    action: str
    target: str
    ok: bool
    url_before: str | None = None
    url_after: str | None = None
    message: str | None = None
    error_type: str | None = None
    created_at: str = field(default_factory=_utc_now)

    def to_context_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ErrorAttempt:
    """Aggregated failure record used to prevent repeated bad actions."""

    url: str
    action: str
    target: str
    error_type: str
    message: str | None = None
    attempt_count: int = 1
    blocked: bool = False
    last_seen_at: str = field(default_factory=_utc_now)

    @property
    def key(self) -> str:
        return action_key(self.url, self.action, self.target)

    def bump(self, message: str | None = None, block_after: int = DEFAULT_LOOP_BLOCK_AFTER) -> None:
        self.attempt_count += 1
        self.message = message or self.message
        self.last_seen_at = _utc_now()
        if self.attempt_count >= block_after:
            self.blocked = True

    def to_context_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CredentialReference:
    """Long-lived credential metadata without raw passwords."""

    system_name: str
    username: str | None = None
    credentials_source: str | None = None
    storage_state_path: str | None = None
    verified_at: str | None = None

    def to_context_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WorkingMemory:
    """Short-lived state for the current browser page and recent feedback."""

    current_page: PageSummary | None = None
    recent_feedback: list[OperationFeedback] = field(default_factory=list)

    def record_feedback(
        self,
        feedback: OperationFeedback,
        limit: int = DEFAULT_RECENT_FEEDBACK_LIMIT,
    ) -> None:
        self.recent_feedback.append(feedback)
        if len(self.recent_feedback) > limit:
            del self.recent_feedback[: len(self.recent_feedback) - limit]

    def to_context_dict(self, compact_level: int = 0) -> dict[str, Any]:
        return {
            "current_page": (
                self.current_page.to_context_dict(compact_level)
                if self.current_page
                else None
            ),
            "recent_feedback": [
                feedback.to_context_dict() for feedback in self.recent_feedback
            ],
        }


@dataclass(slots=True)
class TaskState:
    """Deterministic crawler progress that should not depend on LLM memory."""

    start_url: str | None = None
    phase: str = "guest"
    _visited_paths: dict[str, None] = field(default_factory=dict, repr=False)
    _pending_paths: dict[str, None] = field(default_factory=dict, repr=False)
    route_parents: dict[str, dict[str, str | None]] = field(default_factory=dict)
    discovered_routes: list[dict[str, Any]] = field(default_factory=list)
    skipped_routes: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def visited_paths(self) -> list[str]:
        return list(self._visited_paths.keys())

    @visited_paths.setter
    def visited_paths(self, paths: list[str]) -> None:
        self._visited_paths = dict.fromkeys(path for path in paths if path)

    @property
    def pending_paths(self) -> list[str]:
        return list(self._pending_paths.keys())

    @pending_paths.setter
    def pending_paths(self, paths: list[str]) -> None:
        self._pending_paths = {
            path: None
            for path in paths
            if path and path not in self._visited_paths
        }

    @property
    def visited_count(self) -> int:
        return len(self._visited_paths)

    @property
    def pending_count(self) -> int:
        return len(self._pending_paths)

    def has_visited(self, path: str) -> bool:
        return path in self._visited_paths

    def has_pending(self, path: str) -> bool:
        return path in self._pending_paths

    def add_visited(self, path: str) -> None:
        if not path:
            return
        self._visited_paths[path] = None
        self._pending_paths.pop(path, None)

    def add_pending(self, path: str) -> None:
        if path and path not in self._visited_paths and path not in self._pending_paths:
            self._pending_paths[path] = None

    def pop_next_pending(self) -> str | None:
        if not self._pending_paths:
            return None
        path = next(iter(self._pending_paths))
        self._pending_paths.pop(path, None)
        return path

    def record_route_parent(
        self,
        path: str,
        source_path: str | None,
        label: str | None = None,
    ) -> None:
        self.route_parents[path] = {
            "source_path": source_path,
            "label": label,
        }

    def to_context_dict(self, compact_level: int = 0) -> dict[str, Any]:
        sample_limit = _limit_for_level(compact_level, full=None, compact=25, minimal=8)
        error_limit = _limit_for_level(compact_level, full=None, compact=20, minimal=6)

        return {
            "start_url": self.start_url,
            "phase": self.phase,
            "visited_count": self.visited_count,
            "pending_count": self.pending_count,
            "discovered_route_count": len(self.discovered_routes),
            "skipped_route_count": len(self.skipped_routes),
            "error_count": len(self.errors),
            "visited_paths": _limited_text(
                self.visited_paths,
                sample_limit,
                keep_newest=True,
            ),
            "next_candidates": _limited_text(
                self.pending_paths,
                sample_limit,
                keep_newest=False,
            ),
            "skipped_routes": _limited_dicts(
                self.skipped_routes,
                error_limit,
                keep_newest=True,
            ),
            "errors": _limited_dicts(self.errors, error_limit, keep_newest=True),
        }


@dataclass(slots=True)
class LongTermMemory:
    """Stable facts that can be reused across crawl runs."""

    final_goal: str | None = None
    target_app: str | None = None
    credential_refs: dict[str, CredentialReference] = field(default_factory=dict)
    storage_state_paths: dict[str, str] = field(default_factory=dict)
    known_safe_exclusions: list[str] = field(default_factory=list)
    blocked_actions: dict[str, ErrorAttempt] = field(default_factory=dict)
    avoided_error_paths: dict[str, str] = field(default_factory=dict)

    def remember_credentials(self, reference: CredentialReference) -> None:
        self.credential_refs[reference.system_name] = reference
        if reference.storage_state_path:
            self.storage_state_paths[reference.system_name] = reference.storage_state_path

    def remember_blocked_action(self, attempt: ErrorAttempt) -> None:
        self.blocked_actions.pop(attempt.key, None)
        self.blocked_actions[attempt.key] = attempt
        self.avoided_error_paths.pop(attempt.url, None)
        self.avoided_error_paths[attempt.url] = attempt.error_type

    def to_context_dict(self, compact_level: int = 0) -> dict[str, Any]:
        blocked_limit = _limit_for_level(compact_level, full=None, compact=25, minimal=8)
        blocked_items = list(self.blocked_actions.values())

        return {
            "final_goal": self.final_goal,
            "target_app": self.target_app,
            "credentials": {
                name: reference.to_context_dict()
                for name, reference in self.credential_refs.items()
            },
            "storage_state_paths": dict(self.storage_state_paths),
            "known_safe_exclusions": _limited_text(
                self.known_safe_exclusions,
                _limit_for_level(compact_level, full=None, compact=25, minimal=8),
                keep_newest=True,
            ),
            "blocked_actions": [
                item.to_context_dict()
                for item in _limited_items(
                    blocked_items,
                    blocked_limit,
                    keep_newest=True,
                )
            ],
            "avoided_error_paths": dict(
                _limited_items(
                    list(self.avoided_error_paths.items()),
                    blocked_limit,
                    keep_newest=True,
                )
            ),
        }


@dataclass(slots=True)
class CrawlerContext:
    """Aggregates working memory, deterministic task state, and long-term memory."""

    working_memory: WorkingMemory = field(default_factory=WorkingMemory)
    task_state: TaskState = field(default_factory=TaskState)
    long_term_memory: LongTermMemory = field(default_factory=LongTermMemory)
    error_attempts: dict[str, ErrorAttempt] = field(default_factory=dict)
    recent_feedback_limit: int = DEFAULT_RECENT_FEEDBACK_LIMIT
    loop_block_after: int = DEFAULT_LOOP_BLOCK_AFTER

    def set_current_page(self, page: PageSummary) -> None:
        self.working_memory.current_page = page

    def record_operation_feedback(
        self,
        action: str,
        target: str,
        ok: bool,
        url_before: str | None = None,
        url_after: str | None = None,
        message: str | None = None,
        error_type: str | None = None,
    ) -> OperationFeedback:
        feedback = OperationFeedback(
            action=action,
            target=target,
            ok=ok,
            url_before=url_before,
            url_after=url_after,
            message=message,
            error_type=error_type,
        )
        self.working_memory.record_feedback(feedback, self.recent_feedback_limit)
        if not ok and error_type:
            self.record_error_attempt(
                url=url_before or url_after or "",
                action=action,
                target=target,
                error_type=error_type,
                message=message,
            )
        return feedback

    def record_error_attempt(
        self,
        url: str,
        action: str,
        target: str,
        error_type: str,
        message: str | None = None,
    ) -> ErrorAttempt:
        key = action_key(url, action, target)
        attempt = self.error_attempts.get(key)
        if attempt is None:
            attempt = ErrorAttempt(
                url=url,
                action=action,
                target=target,
                error_type=error_type,
                message=message,
                blocked=self.loop_block_after <= 1,
            )
            self.error_attempts[key] = attempt
        else:
            attempt.bump(message=message, block_after=self.loop_block_after)
            self.error_attempts.pop(key, None)
            self.error_attempts[key] = attempt

        if attempt.blocked:
            self.long_term_memory.remember_blocked_action(attempt)
        return attempt

    def is_action_blocked(self, url: str, action: str, target: str) -> bool:
        attempt = self.error_attempts.get(action_key(url, action, target))
        return bool(attempt and attempt.blocked)

    def build_context_pack(
        self,
        max_context_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
        threshold_ratio: float = DEFAULT_COMPACTION_RATIO,
    ) -> dict[str, Any]:
        """Return an LLM-visible context pack, compacting when it exceeds budget."""

        threshold_tokens = max(1, int(max_context_tokens * threshold_ratio))
        for compact_level in (0, 1, 2):
            payload = self._payload(compact_level)
            estimated_tokens = estimate_tokens(payload)
            if estimated_tokens <= threshold_tokens or compact_level == 2:
                payload["context_budget"] = {
                    "estimated_tokens": estimated_tokens,
                    "max_context_tokens": max_context_tokens,
                    "threshold_ratio": threshold_ratio,
                    "threshold_tokens": threshold_tokens,
                    "compacted": compact_level > 0,
                    "compact_level": compact_level,
                }
                return payload

        raise RuntimeError("Unreachable context compaction state.")

    def _payload(self, compact_level: int) -> dict[str, Any]:
        return {
            "goal": self.long_term_memory.final_goal,
            "target_app": self.long_term_memory.target_app,
            "working_memory": self.working_memory.to_context_dict(compact_level),
            "task_state": self.task_state.to_context_dict(compact_level),
            "long_term_memory": self.long_term_memory.to_context_dict(compact_level),
            "error_attempts": [
                attempt.to_context_dict()
                for attempt in _limited_items(
                    list(self.error_attempts.values()),
                    _limit_for_level(compact_level, full=None, compact=30, minimal=10),
                    keep_newest=True,
                )
            ],
        }


def action_key(url: str, action: str, target: str) -> str:
    """Build a stable key for loop-prevention records."""

    return "|".join(
        [
            _normalize_key_part(url),
            _normalize_key_part(action),
            _normalize_key_part(target),
        ]
    )


def estimate_tokens(payload: Any) -> int:
    """Estimate token count from serialized JSON with lightweight multilingual weighting."""

    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    ascii_count = sum(1 for char in serialized if ord(char) < 128)
    non_ascii_count = len(serialized) - ascii_count
    return max(1, int((ascii_count / 4) + (non_ascii_count * 1.5)))


def _normalize_key_part(value: str | None) -> str:
    return (value or "").strip().lower()


def _limit_for_level(
    compact_level: int,
    full: int | None,
    compact: int,
    minimal: int,
) -> int | None:
    if compact_level <= 0:
        return full
    if compact_level == 1:
        return compact
    return minimal


def _limited_items(
    items: list[Any],
    limit: int | None,
    *,
    keep_newest: bool = True,
) -> list[Any]:
    if limit is None:
        return list(items)
    if limit <= 0:
        return []
    return list(items[-limit:] if keep_newest else items[:limit])


def _limited_text(
    values: list[str],
    limit: int | None,
    *,
    keep_newest: bool = True,
) -> list[str]:
    return [
        _truncate_text(value)
        for value in _limited_items(values, limit, keep_newest=keep_newest)
    ]


def _limited_dicts(
    values: list[dict[str, Any]],
    limit: int | None,
    *,
    keep_newest: bool = True,
) -> list[dict[str, Any]]:
    return [
        {str(key): _truncate_text(value) for key, value in item.items()}
        for item in _limited_items(values, limit, keep_newest=keep_newest)
    ]


def _truncate_text(value: Any, max_chars: int = 220) -> str:
    text = "" if value is None else str(value)
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return "." * max(0, max_chars)
    head_chars = (max_chars - 3) // 2
    tail_chars = max_chars - 3 - head_chars
    return f"{text[:head_chars]}...{text[-tail_chars:]}"


__all__ = [
    "CredentialReference",
    "CrawlerContext",
    "ErrorAttempt",
    "LongTermMemory",
    "OperationFeedback",
    "PageSummary",
    "TaskState",
    "WorkingMemory",
    "action_key",
    "estimate_tokens",
]
