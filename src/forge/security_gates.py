"""Parallel security and code quality gates."""

import asyncio
import subprocess
from typing import Dict, List
from dataclasses import dataclass


@dataclass
class GateResult:
    tool_name: str
    passed: bool
    output: str
    errors: List[str]
    warnings: List[str]


class BanditGate:
    async def run_analysis(self, workspace_path: str) -> GateResult:
        try:
            result = subprocess.run(
                ['python', '-m', 'bandit', '-r', workspace_path, '-f', 'json'],
                capture_output=True, text=True, timeout=300
            )
            
            if result.returncode == 0:
                return GateResult("bandit", True, result.stdout, [], [])
            elif result.returncode == 1:
                import json
                try:
                    data = json.loads(result.stdout)
                    errors = [f"{i.get('filename', 'unknown')}:{i.get('line_number', 0)} - {i.get('issue_text', '')}" 
                             for i in data.get('results', [])]
                    return GateResult("bandit", False, result.stdout, errors, [])
                except:
                    return GateResult("bandit", False, "", [result.stdout], [])
            else:
                return GateResult("bandit", False, "", [f"Bandit failed: {result.stderr}"], [])
        except subprocess.TimeoutExpired:
            return GateResult("bandit", False, "", ["Timeout"], [])
        except FileNotFoundError:
            return GateResult("bandit", True, "", [], ["Bandit not installed"])
        except Exception as e:
            return GateResult("bandit", False, "", [str(e)], [])


class SemgrepGate:
    async def run_analysis(self, workspace_path: str) -> GateResult:
        try:
            result = subprocess.run(
                ['semgrep', '--config=auto', '--json', workspace_path],
                capture_output=True, text=True, timeout=300
            )
            
            if result.returncode in [0, 1]:
                import json
                try:
                    data = json.loads(result.stdout)
                    errors = [f"{r.get('path', '')}:{r.get('start', {}).get('line', 0)} - {r.get('check_id', '')}" 
                             for r in data.get('results', [])]
                    return GateResult("semgrep", len(errors) == 0, result.stdout, errors, [])
                except:
                    return GateResult("semgrep", False, "", ["Invalid JSON output"], [])
            else:
                return GateResult("semgrep", False, "", [f"Semgrep failed: {result.stderr}"], [])
        except subprocess.TimeoutExpired:
            return GateResult("semgrep", False, "", ["Timeout"], [])
        except FileNotFoundError:
            return GateResult("semgrep", True, "", [], ["Semgrep not installed"])
        except Exception as e:
            return GateResult("semgrep", False, "", [str(e)], [])


class RadonGate:
    async def run_analysis(self, workspace_path: str) -> GateResult:
        try:
            result = subprocess.run(
                ['python', '-m', 'radon', 'cc', '-s', '--total-average', workspace_path],
                capture_output=True, text=True, timeout=300
            )
            
            if result.returncode == 0:
                lines = result.stdout.split('\n')
                for line in lines:
                    if 'Total average:' in line:
                        parts = line.split()
                        for part in parts:
                            try:
                                avg = float(part)
                                if avg <= 5.0:
                                    return GateResult("radon", True, result.stdout, [], [])
                                else:
                                    return GateResult("radon", False, result.stdout, 
                                                     [f"High complexity: {avg}"], [])
                            except ValueError:
                                continue
                return GateResult("radon", True, result.stdout, [], [])
            else:
                return GateResult("radon", False, "", [f"Radon failed: {result.stderr}"], [])
        except subprocess.TimeoutExpired:
            return GateResult("radon", False, "", ["Timeout"], [])
        except FileNotFoundError:
            return GateResult("radon", True, "", [], ["Radon not installed"])
        except Exception as e:
            return GateResult("radon", False, "", [str(e)], [])


class VultureGate:
    async def run_analysis(self, workspace_path: str) -> GateResult:
        try:
            result = subprocess.run(
                ['python', '-m', 'vulture', workspace_path, '--min-confidence', '80'],
                capture_output=True, text=True, timeout=300
            )
            
            if result.returncode == 0:
                if result.stdout.strip() or result.stderr.strip():
                    errors = [line.strip() for line in (result.stdout + result.stderr).split('\n') if line.strip()]
                    return GateResult("vulture", False, result.stdout, errors, [])
                return GateResult("vulture", True, result.stdout, [], [])
            else:
                return GateResult("vulture", False, "", [f"Vulture failed: {result.stderr}"], [])
        except subprocess.TimeoutExpired:
            return GateResult("vulture", False, "", ["Timeout"], [])
        except FileNotFoundError:
            return GateResult("vulture", True, "", [], ["Vulture not installed"])
        except Exception as e:
            return GateResult("vulture", False, "", [str(e)], [])


class DeptryGate:
    async def run_analysis(self, workspace_path: str) -> GateResult:
        try:
            result = subprocess.run(['deptry', workspace_path], capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                return GateResult("deptry", True, result.stdout, [], [])
            elif result.returncode == 1:
                errors = [line.strip() for line in result.stdout.split('\n') if 'DETR' in line or 'missing' in line.lower()]
                return GateResult("deptry", False, result.stdout, errors, [])
            else:
                return GateResult("deptry", False, "", [f"Deptry failed: {result.stderr}"], [])
        except subprocess.TimeoutExpired:
            return GateResult("deptry", False, "", ["Timeout"], [])
        except FileNotFoundError:
            return GateResult("deptry", True, "", [], ["Deptry not installed"])
        except Exception as e:
            return GateResult("deptry", False, "", [str(e)], [])


class ParallelGateExecutor:
    def __init__(self, workspace_path: str, max_concurrent: int = 5):
        self.workspace_path = workspace_path
        self.max_concurrent = max_concurrent
    
    async def run_all_gates(self) -> Dict[str, GateResult]:
        gates = [
            BanditGate(),
            SemgrepGate(),
            RadonGate(),
            VultureGate(),
            DeptryGate(),
        ]
        
        results = await asyncio.gather(
            *[gate.run_analysis(self.workspace_path) for gate in gates],
            return_exceptions=True
        )
        
        final_results = {}
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                name = gates[i].__class__.__name__.replace('Gate', '').lower()
                final_results[name] = GateResult(name, False, "", [str(result)], [])
            else:
                final_results[result.tool_name] = result
        
        return final_results
    
    def run_sequentially(self) -> Dict[str, GateResult]:
        import asyncio
        gates = [BanditGate(), SemgrepGate(), RadonGate(), VultureGate(), DeptryGate()]
        
        results = {}
        for gate in gates:
            try:
                result = asyncio.run(gate.run_analysis(self.workspace_path))
                results[result.tool_name] = result
            except Exception as e:
                name = gate.__class__.__name__.replace('Gate', '').lower()
                results[name] = GateResult(name, False, "", [str(e)], [])
        
        return results
