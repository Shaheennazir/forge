"""Tests for newly implemented features."""

import pytest
import tempfile
import os
import asyncio
from pathlib import Path


def test_circuit_breaker_import():
    """Test circuit breaker can be imported."""
    from forge.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
    assert CircuitBreaker is not None
    assert CircuitState.CLOSED is not None


def test_circuit_breaker_initial_state():
    """Test circuit breaker starts closed."""
    from forge.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker()
    assert cb.state.value == "closed"


def test_circuit_breaker_success():
    """Test successful calls keep circuit closed."""
    from forge.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker()
    
    def success_func():
        return "ok"
    
    result = cb.call(success_func)
    assert result == "ok"
    assert cb.state.value == "closed"


def test_circuit_breaker_failure():
    """Test failures trigger open state."""
    from forge.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
    
    cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2))
    
    def fail_func():
        raise ValueError("fail")
    
    for _ in range(2):
        try:
            cb.call(fail_func)
        except ValueError:
            pass
    
    assert cb.state.value == "open"


@pytest.mark.asyncio
async def test_circuit_breaker_async():
    """Test async circuit breaker."""
    from forge.circuit_breaker import CircuitBreaker
    
    cb = CircuitBreaker()
    
    async def async_success():
        await asyncio.sleep(0.01)
        return "async_ok"
    
    result = await cb.acall(async_success)
    assert result == "async_ok"


def test_cache_import():
    """Test cache module can be imported."""
    from forge.cache import AnalysisCache
    assert AnalysisCache is not None


def test_cache_creation():
    """Test cache can be created."""
    from forge.cache import AnalysisCache
    
    with tempfile.TemporaryDirectory() as td:
        cache_path = os.path.join(td, "test.db")
        cache = AnalysisCache(cache_path)
        assert os.path.exists(cache_path)


def test_cache_store_retrieve():
    """Test storing and retrieving from cache."""
    from forge.cache import AnalysisCache
    
    with tempfile.TemporaryDirectory() as td:
        cache_path = os.path.join(td, "test.db")
        cache = AnalysisCache(cache_path)
        
        # Create test file
        test_file = os.path.join(td, "test.py")
        with open(test_file, 'w') as f:
            f.write("print('hello')")
        
        # Store result
        result = {"issues": [], "complexity": 1.0}
        cache.cache_result("test_tool", test_file, result)
        
        # Retrieve result
        retrieved = cache.get_cached_result("test_tool", test_file)
        assert retrieved == result


def test_cache_invalidation():
    """Test cache invalidation on file change."""
    from forge.cache import AnalysisCache
    
    with tempfile.TemporaryDirectory() as td:
        cache_path = os.path.join(td, "test.db")
        cache = AnalysisCache(cache_path)
        
        test_file = os.path.join(td, "test.py")
        with open(test_file, 'w') as f:
            f.write("original")
        
        cache.cache_result("tool", test_file, {"result": "old"})
        
        # Change file content
        with open(test_file, 'w') as f:
            f.write("changed")
        
        # Should not find cached result (hash changed)
        retrieved = cache.get_cached_result("tool", test_file)
        assert retrieved is None


def test_security_gates_import():
    """Test security gates can be imported."""
    from forge.security_gates import (
        BanditGate, SemgrepGate, RadonGate, VultureGate, DeptryGate,
        ParallelGateExecutor, GateResult
    )
    assert BanditGate is not None
    assert ParallelGateExecutor is not None


@pytest.mark.asyncio
async def test_bandit_gate_basic():
    """Test bandit gate runs."""
    from forge.security_gates import BanditGate
    
    with tempfile.TemporaryDirectory() as td:
        test_file = os.path.join(td, "test.py")
        with open(test_file, 'w') as f:
            f.write("# safe code\nx = 1\n")
        
        gate = BanditGate()
        result = await gate.run_analysis(td)
        
        assert result.tool_name == "bandit"
        assert isinstance(result.passed, bool)
        assert isinstance(result.errors, list)


@pytest.mark.asyncio
async def test_parallel_executor():
    """Test parallel gate executor."""
    from forge.security_gates import ParallelGateExecutor
    
    with tempfile.TemporaryDirectory() as td:
        test_file = os.path.join(td, "test.py")
        with open(test_file, 'w') as f:
            f.write("# test\n")
        
        executor = ParallelGateExecutor(td)
        results = await executor.run_all_gates()
        
        # Should have results for all gates (even if tools not installed)
        assert len(results) == 5
        assert "bandit" in results or "semgrep" in results


def test_sequential_executor():
    """Test sequential gate execution."""
    from forge.security_gates import ParallelGateExecutor
    
    with tempfile.TemporaryDirectory() as td:
        test_file = os.path.join(td, "test.py")
        with open(test_file, 'w') as f:
            f.write("# test\n")
        
        executor = ParallelGateExecutor(td)
        results = executor.run_sequentially()
        
        assert len(results) == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
