"""Hook specifications: pre_execution, tool_dispatch, post_execution, span (design §1.2)."""

from __future__ import annotations

from typing import Any

import pluggy

HOOK_NAMESPACE = "unreliable"
hookspec = pluggy.HookspecMarker(HOOK_NAMESPACE)
hookimpl = pluggy.HookimplMarker(HOOK_NAMESPACE)


class HookSpecs:
    @hookspec
    def pre_execution(self, request: dict[str, Any]) -> None:
        """Ingress. Plugins may rewrite request['text']; raise ToolError to block."""

    @hookspec
    def tool_dispatch(self, call: dict[str, Any]) -> None:
        """Just before dispatch. Raise ToolError to block the call."""

    @hookspec
    def post_execution(self, response: dict[str, Any]) -> None:
        """After dispatch or answer. Plugins may annotate response in place."""

    @hookspec
    def span(self, event: dict[str, Any]) -> None:
        """Every stage emits a span event."""


def make_manager() -> pluggy.PluginManager:
    pm = pluggy.PluginManager(HOOK_NAMESPACE)
    pm.add_hookspecs(HookSpecs)
    return pm
