"""
forge.code_intelligence.messaging — NATS pub/sub for forge agent event streaming.

NATS provides:
  - Durable queues (JetStream) for cross-session event persistence
  - Hierarchical subjects (forge.pipeline.stage, forge.pipeline.gate, etc.)
  - Request/reply for agent coordination
  - Horizontal scale via clustering

This module wires nats-py into the product compiler pipeline and the graph runner,
replacing the STDOUT-only stub in product_compiler/messaging.py.

Usage:
    nats = NATSMessaging(url="nats://localhost:4222")
    await nats.connect()
    await nats.publish("forge.pipeline.stage", {"stage": "executor", "status": "done"})
    await nats.subscribe("forge.pipeline.*", my_handler)
    await nats.disconnect()
"""

from __future__ import annotations

import asyncio
import json
import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

log = structlog.get_logger(__name__)

# Lazy import
_nats = None


def _get_nats():
    global _nats
    if _nats is None:
        try:
            import nats
            _nats = nats
        except ImportError as e:
            log.warning("nats.not_installed", error=str(e))
            _nats = None
    return _nats


@dataclass
class MessagingConfig:
    """Configuration for NATS messaging."""

    url: str = "nats://localhost:4222"
    cluster: str = ""
    subject_prefix: str = "forge"
    queue_group: str = "forge-workers"
    user: str = ""
    password: str = ""
    token: str = ""
    tls: bool = False

    @property
    def subjects(self) -> "SubjectNamespace":
        return SubjectNamespace(self.subject_prefix)


@dataclass
class SubjectNamespace:
    """Hierarchical subject namespace for forge pipeline events."""

    prefix: str

    @property
    def pipeline(self) -> str:
        return f"{self.prefix}.pipeline"

    def stage(self, name: str) -> str:
        return f"{self.prefix}.pipeline.stage.{name}"

    def gate(self, gate_num: int) -> str:
        return f"{self.prefix}.pipeline.gate.{gate_num}"

    def agent(self, agent_id: str, event: str) -> str:
        return f"{self.prefix}.agent.{agent_id}.{event}"

    def executor(self, event: str) -> str:
        return f"{self.prefix}.executor.{event}"

    def log(self) -> str:
        return f"{self.prefix}.log"


class NATSMessaging:
    """
    NATS messaging layer for forge agent event streaming.

    Supports:
      - Connect / disconnect with auto-reconnect
      - Publish to hierarchical subjects
      - Subscribe with queue groups for load balancing
      - Request/reply for agent coordination

    Usage:
        nats = NATSMessaging()
        await nats.connect()
        await nats.publish(nats.config.subjects.stage("executor"), {"status": "done"})
        await nats.subscribe("forge.pipeline.*", handler)
    """

    def __init__(self, config: Optional[MessagingConfig] = None):
        self.config = config or MessagingConfig()
        self._nc = None
        self._js = None  # JetStream context
        self._subscriptions: list = []
        self._connected = False
        self._reconnect_task: Optional[asyncio.Task] = None

    # ── Connection ──────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Connect to the NATS server with auto-reconnect on failure.
        """
        nats_cls = _get_nats()
        if nats_cls is None:
            raise RuntimeError(
                "nats-py not installed. Run: pip install nats-py"
            )

        opts: dict = {
            "servers": [self.config.url],
            "connect_timeout": 10,
            "max_reconnect_attempts": -1,
            "reconnect_time_wait": 2,
        }

        if self.config.user and self.config.password:
            opts["user"] = self.config.user
            opts["password"] = self.config.password
        elif self.config.token:
            opts["token"] = self.config.token

        if self.config.tls:
            opts["tls"] = True

        try:
            self._nc = await nats_cls.connect(**opts)
            self._connected = True
            log.info("nats.connected", url=self.config.url)

            # Try to get JetStream context for durable streams
            try:
                self._js = self._nc.jetstream()
            except Exception as e:
                log.warning("nats.js_unavailable", error=str(e))
                self._js = None

        except Exception as e:
            log.error("nats.connect_failed", url=self.config.url, error=str(e))
            raise

    async def disconnect(self) -> None:
        """Close the NATS connection gracefully."""
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None

        for sub in self._subscriptions:
            try:
                await sub.unsubscribe()
            except Exception:
                pass
        self._subscriptions.clear()

        if self._nc:
            await self._nc.close()
            self._nc = None
            self._js = None
            self._connected = False
        log.info("nats.disconnected")

    async def is_connected(self) -> bool:
        """Return True if connected to NATS."""
        return self._connected and self._nc is not None and not self._nc.is_closed

    # ── Publish ───────────────────────────────────────────────────────────

    async def publish(
        self,
        subject: str,
        payload: dict,
        *,
        stream: bool = False,
    ) -> None:
        """
        Publish a message to a NATS subject.

        Args:
            subject: The subject to publish to (e.g. "forge.pipeline.stage.executor")
            payload: Dict payload — will be JSON-serialized.
            stream: If True and JetStream is available, persist the message.
        """
        if not await self.is_connected():
            log.warning("nats.publish_disconnected", subject=subject)
            return

        data = json.dumps(payload, default=str).encode("utf-8")

        try:
            if stream and self._js:
                await self._js.publish(subject, data)
            else:
                await self._nc.publish(subject, data)
            log.debug("nats.published", subject=subject)
        except Exception as e:
            log.error("nats.publish_failed", subject=subject, error=str(e))

    async def publish_stage(
        self,
        stage: str,
        status: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """Convenience: publish a stage event."""
        await self.publish(
            self.config.subjects.stage(stage),
            {"stage": stage, "status": status, **(metadata or {})},
        )

    async def publish_log(
        self,
        level: str,
        message: str,
        *,
        agent: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Convenience: publish a structured log event."""
        await self.publish(
            self.config.subjects.log(),
            {
                "level": level,
                "message": message,
                "agent": agent,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **(metadata or {}),
            },
        )

    # ── Subscribe ──────────────────────────────────────────────────────────

    async def subscribe(
        self,
        subject: str,
        handler: Callable[[dict], None],
        *,
        queue: Optional[str] = None,
    ) -> any:
        """
        Subscribe to a subject (or wildcard pattern).

        Args:
            subject: Subject or wildcard pattern (e.g. "forge.pipeline.stage.*")
            handler: Async function called with the deserialized payload dict.
            queue: Queue group for load-balancing across multiple workers.

        Returns:
            The subscription object (for unsubscribe).
        """
        if not await self.is_connected():
            log.warning("nats.subscribe_disconnected", subject=subject)
            return None

        async def _dispatch(msg):
            try:
                payload = json.loads(msg.data.decode("utf-8"))
                await handler(payload)
            except Exception as e:
                log.error("nats.handler_error", subject=subject, error=str(e))

        sub = await self._nc.subscribe(
            subject,
            queue=queue or self.config.queue_group,
            cb=_dispatch,
        )
        self._subscriptions.append(sub)
        log.info("nats.subscribed", subject=subject, queue=queue)
        return sub

    async def subscribe_pipeline(
        self,
        handler: Callable[[dict], None],
        stages: Optional[list[str]] = None,
    ) -> None:
        """
        Subscribe to all pipeline events.

        Args:
            handler: Called with each pipeline event dict.
            stages: Optional list of specific stages to subscribe to.
        """
        if stages:
            for stage in stages:
                pattern = self.config.subjects.stage(stage)
                await self.subscribe(pattern, handler)
        else:
            # Subscribe to all pipeline events
            await self.subscribe(f"{self.config.subjects.pipeline}.*", handler)

    # ── Request / Reply ─────────────────────────────────────────────────────

    async def request(
        self,
        subject: str,
        payload: Optional[dict] = None,
        timeout: float = 5.0,
    ) -> Optional[dict]:
        """
        Send a request and wait for a single reply.

        Args:
            subject: Subject to request on.
            payload: Request payload dict.
            timeout: Max seconds to wait for a reply.

        Returns:
            Reply payload dict, or None if no reply received.
        """
        if not await self.is_connected():
            return None

        data = json.dumps(payload or {}, default=str).encode("utf-8")

        try:
            msg = await self._nc.timed_request(subject, data, timeout=timeout)
            return json.loads(msg.data.decode("utf-8"))
        except Exception as e:
            log.debug("nats.request_timeout", subject=subject, error=str(e))
            return None

    # ── Stream helpers ──────────────────────────────────────────────────────

    async def ensure_stream(
        self,
        stream_name: str,
        subjects: list[str],
        *,
        retention: str = "limits",
        max_bytes: int = 1024 * 1024 * 1024,  # 1GB
        max_age_secs: int = 7 * 24 * 3600,  # 7 days
    ) -> bool:
        """
        Ensure a JetStream stream exists with the given configuration.

        Args:
            stream_name: Name of the stream (e.g. "forge_events").
            subjects: List of subjects to bind to this stream (with wildcard support).
            retention: "limits", "interest", or "workqueue".
            max_bytes: Max stream size in bytes.
            max_age_secs: Max message age before expiration.

        Returns:
            True if stream was created or already exists.
        """
        if not self._js:
            log.warning("nats.js_unavailable")
            return False

        try:
            await self._js.add_stream(
                name=stream_name,
                subjects=subjects,
                retention=retention,
                max_bytes=max_bytes,
                max_age=max_age_secs,
            )
            log.info("nats.stream_created", stream=stream_name)
            return True
        except Exception as e:
            # Stream likely already exists
            if "stream name already in use" in str(e).lower():
                return True
            log.error("nats.stream_create_failed", stream=stream_name, error=str(e))
            return False

    # ── Convenience ─────────────────────────────────────────────────────────

    async def publish_agent_heartbeat(
        self,
        agent_id: str,
        status: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """Publish an agent heartbeat event."""
        await self.publish(
            self.config.subjects.agent(agent_id, "heartbeat"),
            {
                "agent_id": agent_id,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **(metadata or {}),
            },
        )
