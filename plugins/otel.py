"""otel_exporter (span hook). OTEL_EXPORTER=console (default) or otlp (not implemented in PoC)."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from plugins.hookspecs import hookimpl


class SpanCollector:
    def __init__(self, sink: TextIO | None = None) -> None:
        self.spans: list[dict[str, Any]] = []
        self.sink = sink

    @hookimpl
    def span(self, event: dict[str, Any]) -> None:
        self.spans.append(dict(event))
        if self.sink is not None:
            self.sink.write(json.dumps(event, sort_keys=True) + "\n")
            self.sink.flush()


def default_console() -> SpanCollector:
    return SpanCollector(sys.stdout)
