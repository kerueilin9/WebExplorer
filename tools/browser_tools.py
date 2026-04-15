"""Browser tools that wrap playwright-cli."""

from __future__ import annotations

import json
import os

from adk_playwright_agent.adapters.credentials import load_named_credentials
from adk_playwright_agent.adapters.playwright_cli import PlaywrightCliAdapter

_ADAPTER = PlaywrightCliAdapter()


def open_browser(
    base_url: str,
    session_name: str = "default",
    headed: bool = True,
    persistent: bool = True,
) -> dict:
    """Open a named browser session for the target site."""

    return _ADAPTER.open_browser(
        base_url=base_url,
        session_name=session_name,
        headed=headed,
        persistent=persistent,
    ).to_tool_result()


def goto(session_name: str, url: str) -> dict:
    """Navigate the current browser session to a URL."""

    return _ADAPTER.goto(session_name=session_name, url=url).to_tool_result()


def snapshot(session_name: str, depth: int | None = None) -> dict:
    """Capture a playwright-cli snapshot for reasoning and locator discovery."""

    return _ADAPTER.snapshot(session_name=session_name, depth=depth).to_tool_result()


def click(session_name: str, target: str) -> dict:
    """Click a locator or snapshot reference."""

    return _ADAPTER.click(session_name=session_name, target=target).to_tool_result()


def fill(session_name: str, target: str, text: str, submit: bool = False) -> dict:
    """Fill a field in the current browser session."""

    return _ADAPTER.fill(
        session_name=session_name,
        target=target,
        text=text,
        submit=submit,
    ).to_tool_result()


def press_key(session_name: str, key: str) -> dict:
    """Press a keyboard key such as Enter or Escape."""

    return _ADAPTER.press_key(session_name=session_name, key=key).to_tool_result()


def eval_js(session_name: str, script: str, raw: bool = True) -> dict:
    """Evaluate JavaScript in the current page and return the result."""

    return _ADAPTER.eval_js(
        session_name=session_name,
        script=script,
        raw=raw,
    ).to_tool_result()


def collect_page_links(session_name: str, same_origin_only: bool = True) -> dict:
    """Return normalized page links from the current document."""

    filter_expression = ".filter(x => x.same_origin)" if same_origin_only else ""
    script = (
        "JSON.stringify([...document.querySelectorAll('a')]"
        ".map(a => {"
        "  const href = a.href || '';"
        "  const url = href ? new URL(href, location.href) : new URL(location.href);"
        "  return {"
        "    text: (a.textContent || '').trim(),"
        "    href,"
        "    path: url.pathname,"
        "    same_origin: url.origin === location.origin"
        "  };"
        "})"
        f"{filter_expression}, null, 2)"
    )
    result = _ADAPTER.eval_js(session_name=session_name, script=script, raw=True)
    links = []
    if result.raw_value:
        if isinstance(result.raw_value, list):
            links = result.raw_value
        elif isinstance(result.raw_value, str):
            try:
                links = json.loads(result.raw_value)
            except json.JSONDecodeError:
                links = []
    payload = result.to_tool_result()
    payload["links"] = links
    return payload


def collect_page_inputs(session_name: str) -> dict:
    """Return visible input field metadata from the current document."""

    script = (
        "JSON.stringify([...document.querySelectorAll('input,textarea,select')]"
        ".map(el => ({"
        "  tag: el.tagName.toLowerCase(),"
        "  type: el.type || '',"
        "  name: el.name || '',"
        "  placeholder: el.placeholder || ''"
        "})), null, 2)"
    )
    result = _ADAPTER.eval_js(session_name=session_name, script=script, raw=True)
    inputs = []
    if result.raw_value:
        if isinstance(result.raw_value, list):
            inputs = result.raw_value
        elif isinstance(result.raw_value, str):
            try:
                inputs = json.loads(result.raw_value)
            except json.JSONDecodeError:
                inputs = []
    payload = result.to_tool_result()
    payload["inputs"] = inputs
    return payload


def login_from_notes(
    session_name: str,
    system_name: str,
    username_target: str,
    password_target: str,
    submit_button_target: str,
    credentials_path: str | None = None,
) -> dict:
    """Read credentials from a notes file and perform a login flow."""

    source = credentials_path or os.getenv("DEFAULT_CREDENTIALS_FILE")
    if not source:
        raise ValueError("No credentials path provided and DEFAULT_CREDENTIALS_FILE is not set.")

    credentials = load_named_credentials(source, system_name)
    _ADAPTER.fill(
        session_name=session_name,
        target=username_target,
        text=credentials["username"],
        submit=False,
    )
    _ADAPTER.fill(
        session_name=session_name,
        target=password_target,
        text=credentials["password"],
        submit=False,
    )
    click_result = _ADAPTER.click(session_name=session_name, target=submit_button_target)
    payload = click_result.to_tool_result()
    payload["credential_source"] = source
    payload["username"] = credentials["username"]
    payload["system_name"] = system_name
    return payload


def save_storage_state(session_name: str, path: str) -> dict:
    """Save the browser storage state to disk."""

    return _ADAPTER.save_storage_state(
        session_name=session_name,
        path=path,
    ).to_tool_result()


def close_browser(session_name: str) -> dict:
    """Close a named browser session."""

    return _ADAPTER.close_browser(session_name=session_name).to_tool_result()
