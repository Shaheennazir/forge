"""
forge.product_compiler.messaging — NATS messaging layer stub.

Provides a Pub/Sub interface for pipeline events so multiple consumers
(e.g. a TUI, a web dashboard, an audit logger) can subscribe independently.

Usage:
    from forge.product_compiler.messaging import MessagingLayer, MessagingBackend, NATSConfig

    layer = MessagingLayer(backend=MessagingBackend.NATS, config=NATSConfig(url="nats://localhost:4222"))
    layer.connect()

    for event in layer.wrap_pipeline(pipeline):
        print(event)

    layer.disconnect()
"""

from __future__ import annotations

import json
import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Generator, Optional

log = structlog.get_logger(__name__)


class MessagingBackend(Enum):
    NATS = "nats"
    STDOUT = "stdout"  # passthrough / debug backend


@dataclass
class NATSConfig:
    url: str = "nats://localhost:4222"
    cluster: str = ""
    subject_prefix: str = "forge.pipeline"
    queue_group: str = "forge-consumers"


class MessagingChannel(Enum):
    STAGE = "stage"
    QUESTION = "question"
    OUTPUT = "output"
    GATE = "gate"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETE = "complete"
    ERROR = "error"
    THINKING = "thinking"
    AWAITING_INPUT = "awaiting_input"


@dataclass
class PipelineEvent:
    """A pipeline event ready to be serialised and published."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    channel: MessagingChannel = MessagingChannel.STAGE
    source: str = "product-compiler"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_json(self) -> str:
        return json.dumps(
            {
                "type": self.type,
                "payload": self.payload,
                "timestamp": self.timestamp,
                "channel": self.channel.value,
                "source": self.source,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> PipelineEvent:
        d = json.loads(data)
        return cls(
            type=d["type"],
            payload=d["payload"],
            timestamp=d.get("timestamp", ""),
            channel=MessagingChannel(d.get("channel", "stage")),
            source=d.get("source", "product-compiler"),
        )


class MessagingLayer:
    """
    Abstraction over message-broker backends (NATS primary; STDOUT for dev / no-dependency use).

    The layer is connectionless on STDOUT; for NATS it manages a connection lifecycle.
    """

    def __init__(
        self,
        backend: MessagingBackend = MessagingBackend.STDOUT,
        config: Optional[NATSConfig] = None,
    ):
        self.backend = backend
        self.config = config or NATSConfig()
        self._subscriptions: list[tuple[str, Callable[[PipelineEvent], None]]] = []
        self._connected = False

    # ── Connection ──────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Establish a connection to the message broker."""
        if self._connected:
            return

        if self.backend == MessagingBackend.NATS:
            self._connect_nats()
        elif self.backend == MessagingBackend.STDOUT:
            # No connection needed.
            self._connected = True
        else:
            raise ValueError(f"Unsupported messaging backend: {self.backend}")

    def _connect_nats(self) -> None:
        """Stub: replace with `from nats import NATS` client logic."""
        # TODO: Replace with real NATS client
        #   import asyncio
        #   async def _main():
        #       self._nc = await nats.connect(self.config.url)
        #   asyncio.run(_main())
        log.info("nats.connect_stub", url=self.config.url, prefix=self.config.subject_prefix)
        self._connected = True

    def disconnect(self) -> None:
        """Close the connection gracefully."""
        if not self._connected:
            return

        if self.backend == MessagingBackend.NATS:
            self._disconnect_nats()

        self._connected = False

    def _disconnect_nats(self) -> None:
        """Stub: replace with real NATS client teardown."""
        # TODO: Replace with real NATS client
        #   await self._nc.close()
        log.info("nats.disconnect_stub")

    # ── Publish ─────────────────────────────────────────────────────────────────

    def publish(self, event: PipelineEvent) -> None:
        """Publish a pipeline event to the message broker."""
        if self.backend == MessagingBackend.STDOUT:
            self._publish_stdout(event)
        elif self.backend == MessagingBackend.NATS:
            self._publish_nats(event)

    def _publish_stdout(self, event: PipelineEvent) -> None:
        """Print event to stdout (dev / no-dependency mode)."""
        print(f"[forge.messaging] {event.channel.value}: {json.dumps(event.payload)}")

    def _publish_nats(self, event: PipelineEvent) -> None:
        """Stub: publish event via NATS client."""
        subject = f"{self.config.subject_prefix}.{event.channel.value}"
        # TODO: Replace with real NATS publish
        #   await self._nc.publish(subject, event.to_json().encode())
        log.debug("nats.publish_stub", subject=subject, event_type=event.type)

    # ── Subscribe ───────────────────────────────────────────────────────────────

    def subscribe(
        self,
        channel: MessagingChannel,
        callback: Callable[[PipelineEvent], None],
    ) -> None:
        """Register a callback for a specific channel."""
        self._subscriptions.append((channel.value, callback))
        log.info("messaging.subscribe", channel=channel.value)

    # ── Pipeline adapter ────────────────────────────────────────────────────────

    def wrap_pipeline(self, pipeline) -> Generator[dict, None, None]:
        """
        Wrap a ProductCompilerPipeline so every yielded event is also published.

        Yields the same raw event dicts as the underlying pipeline so existing
        CLI / TUI consumers continue to work without modification.
        """
        for event in pipeline.run():
            channel_name = event.get("type", "stage")
            try:
                channel = MessagingChannel(channel_name)
            except ValueError:
                channel = MessagingChannel.STAGE

            pevent = PipelineEvent(
                type=event.get("type", ""),
                payload=event.get("payload", {}),
                channel=channel,
            )
            self.publish(pevent)
            yield event

    # ── Convenience ────────────────────────────────────────────────────────────

    def publish_from_emit(self, event: dict) -> None:
        """
        Publish a raw _emit dict directly from the pipeline.

        This is called inside _emit() so the pipeline doesn't need to yield
        just to publish — it publishes inline and then yields.
        """
        channel_name = event.get("type", "stage")
        try:
            channel = MessagingChannel(channel_name)
        except ValueError:
            channel = MessagingChannel.STAGE

        pevent = PipelineEvent(
            type=event.get("type", ""),
            payload=event.get("payload", {}),
            channel=channel,
        )
        self.publish(pevent)

    # ── Convenience ────────────────────────────────────────────────────────────

    @staticmethod
    def _iso_now() -> str:
        return datetime.now(timezone.utc).isoformat()
