"""
forge.product_compiler.messaging — NATS messaging layer.

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

import asyncio
import json
import structlog
import threading
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


# ── Real NATS implementation ─────────────────────────────────────────────────────

_NATS = None


def _get_nats():
    global _NATS
    if _NATS is None:
        try:
            import nats as _nats_mod
            _NATS = _nats_mod
        except ImportError:
            _NATS = None
    return _NATS


class _NATSConnection:
    """Thread-safe async NATS connection manager with a dedicated event loop."""

    def __init__(self, config: NATSConfig):
        self.config = config
        self._nc = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._lock = threading.Lock()

    def _run_loop(self):
        """Run the asyncio event loop in a dedicated thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def connect(self) -> None:
        nats_mod = _get_nats()
        if nats_mod is None:
            raise RuntimeError("nats-py not installed. Run: pip install nats-py")

        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="nats-loop")
        self._thread.start()

        async def _do_connect():
            self._nc = await nats_mod.connect(
                self.config.url,
                connect_timeout=10,
                max_reconnect_attempts=-1,
                reconnect_time_wait=2,
            )
            self._connected = True
            log.info("nats.connected", url=self.config.url)

        def _start():
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(_do_connect())

        t = threading.Thread(target=_start, daemon=True)
        t.start()
        t.join(timeout=15)

        if not self._connected:
            raise RuntimeError(f"NATS connection timed out: {self.config.url}")

    def publish(self, subject: str, payload: bytes) -> None:
        if not self._connected or self._loop is None:
            return

        def _pub():
            async def _do():
                await self._nc.publish(subject, payload)
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(_do())

        t = threading.Thread(target=_pub, daemon=True)
        t.start()
        t.join(timeout=5)

    def disconnect(self) -> None:
        if self._loop is not None:
            def _stop():
                async def _do():
                    if self._nc:
                        await self._nc.close()
                asyncio.set_event_loop(self._loop)
                self._loop.run_until_complete(_do())
                self._loop.call_soon_threadsafe(self._loop.stop)

            t = threading.Thread(target=_stop, daemon=True)
            t.start()
            t.join(timeout=5)
        self._connected = False


# ── MessagingLayer (updated) ────────────────────────────────────────────────────


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
        self._nats: Optional[_NATSConnection] = None

    # ── Connection ──────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Establish a connection to the message broker."""
        if self._connected:
            return

        if self.backend == MessagingBackend.NATS:
            self._connect_nats()
        elif self.backend == MessagingBackend.STDOUT:
            self._connected = True
        else:
            raise ValueError(f"Unsupported messaging backend: {self.backend}")

    def _connect_nats(self) -> None:
        try:
            self._nats = _NATSConnection(self.config)
            self._nats.connect()
            self._connected = True
        except Exception as e:
            log.warning("nats.connect_failed", error=str(e))
            self._connected = False

    def disconnect(self) -> None:
        """Close the connection gracefully."""
        if not self._connected:
            return

        if self.backend == MessagingBackend.NATS and self._nats:
            self._nats.disconnect()

        self._connected = False

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
        """Publish event via real NATS connection."""
        subject = f"{self.config.subject_prefix}.{event.channel.value}"
        self._nats.publish(subject, event.to_json().encode("utf-8"))
        log.debug("nats.published", subject=subject)

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
