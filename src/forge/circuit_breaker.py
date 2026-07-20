"""Circuit breaker for LLM API calls."""

import time
import threading
from enum import Enum
from typing import Callable, Any, Optional
from dataclasses import dataclass


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    timeout_seconds: int = 60
    success_threshold: int = 3


class CircuitBreakerError(Exception):
    pass


class CircuitBreaker:
    def __init__(self, config: CircuitBreakerConfig = None):
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0
        self._success_count = 0
        self._lock = threading.Lock()
    
    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.config.timeout_seconds:
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
            return self._state
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        if not self._should_allow_call():
            raise CircuitBreakerError(
                f"Circuit breaker is {self.state.value}, call blocked"
            )
        
        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure()
            raise
    
    async def acall(self, func, *args, **kwargs) -> Any:
        if not self._should_allow_call():
            raise CircuitBreakerError(
                f"Circuit breaker is {self.state.value}, call blocked"
            )
        
        try:
            result = await func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure()
            raise
    
    def _should_allow_call(self) -> bool:
        current_state = self.state
        if current_state == CircuitState.CLOSED:
            return True
        elif current_state == CircuitState.HALF_OPEN:
            now = time.time()
            if now - self._last_failure_time > 1:
                return True
            return False
        return False
    
    def _record_success(self):
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._close_circuit()
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0
    
    def _record_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if (self._state == CircuitState.CLOSED and 
                self._failure_count >= self.config.failure_threshold):
                self._open_circuit()
    
    def _open_circuit(self):
        self._state = CircuitState.OPEN
        self._failure_count = 0
    
    def _close_circuit(self):
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
    
    def get_status(self) -> dict:
        return {
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
        }
    
    def reset(self):
        with self._lock:
            self._close_circuit()
