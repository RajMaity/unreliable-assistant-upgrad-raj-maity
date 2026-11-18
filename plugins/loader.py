"""Builds the pluggy manager from mcp_config.json (design §1.2 plugin list)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pluggy

from contracts.errors import ErrorCode, ToolError
from plugins.breaker import CircuitBreaker
from plugins.guards import InjectionShield, PiiRedactor
from plugins.hookspecs import make_manager
from plugins.otel import SpanCollector
from plugins.ragas import RagasAuditor

CONFIG_PATH = Path(__file__).resolve().parent.parent / "mcp_config.json"


def build_manager(config_path: Path = CONFIG_PATH, otel_exporter: str | None = None) -> pluggy.PluginManager:
    cfg: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
    pm = make_manager()
    exporter = otel_exporter or os.environ.get("OTEL_EXPORTER", "console")
    entries = sorted(cfg.get("plugins", []), key=lambda p: -int(p.get("priority", 0)))
    # pluggy calls the most recently registered impl first, so register in descending priority
    # so that the lowest priority number runs first (pii_redactor 10 before injection_shield 20).
    for entry in entries:
        name = entry["name"]
        if name == "pii_redactor":
            pm.register(PiiRedactor(), name=name)
        elif name == "injection_shield":
            pm.register(InjectionShield(), name=name)
        elif name == "circuit_breaker":
            pm.register(CircuitBreaker(int(entry.get("max_failures", 2))), name=name)
        elif name == "ragas_auditor":
            pm.register(RagasAuditor(), name=name)
        elif name == "otel_exporter":
            if exporter == "otlp":
                raise ToolError(ErrorCode.MANIFEST_INVALID, "OTEL_EXPORTER=otlp is not implemented in this PoC")
            pm.register(SpanCollector(sys.stderr if exporter == "console" else None), name=name)
        else:
            raise ToolError(ErrorCode.MANIFEST_INVALID, f"unknown plugin {name}")
    return pm
