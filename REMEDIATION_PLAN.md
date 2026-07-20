# Forge Codebase Remediation Plan

## Priority A: Critical Bug Fixes & Verification

### A1. Agent Exports Verification ✅ VERIFIED
- [x] Verify `/workspace/src/forge/product_compiler/agents/__init__.py` exports all 14 agents
- Status: COMPLETE - All 14 agents exported correctly

### A2. Reviewer.py Syntax Error ✅ VERIFIED  
- [x] Verify no syntax errors from f-strings with embedded chr(10).join()
- Status: COMPLETE - No syntax errors found, imports successfully

### A3. TUI Screen Imports ⚠️ ISSUE FOUND
- [ ] Fix ModelPickerScreen import (class is named `ModelPicker` not `ModelPickerScreen`)
- [ ] Verify all 6 screens load without runtime errors

### A4. Tool Wiring Verification
- [ ] Verify 29 tools correctly wired in Chat screen (7 base + 22 code intelligence)

## Priority B: UI/UX Improvements

### B1. Loading Indicators During LLM Calls
- [ ] Add spinner/progress widgets in ChatScreen during streaming
- [ ] Show clear state for thinking, tool execution, response generation

### B2. Command Palette Keybindings
- [ ] Display shortcut hints next to commands
- [ ] Include global bindings (Ctrl+A, Ctrl+P, Ctrl+H, Ctrl+Q, Escape)

### B3. Chat Input History
- [ ] Implement ↑/↓ input history navigation
- [ ] Persist history per session

### B4. Theme Switching Improvements
- [ ] Add theme cycling
- [ ] Add theme preview/selectable list
- [ ] Persist selected theme

### B5. Command Palette Search
- [ ] Add fuzzy filtering for commands
- [ ] Show empty-state messaging

### B6. Status Messaging Improvements
- [ ] Ensure levels visually distinct (INFO, WARN, ERROR, SUCCESS)
- [ ] Add actionable messages for errors

### B7. Accessibility
- [ ] Add reduced-motion toggle
- [ ] Improve widget labels
- [ ] Document terminal font-scaling guidance

## Priority C: Performance Improvements

### C1. Parallel Hard Gates ✅ PARTIALLY IMPLEMENTED
- [x] Reviewer already uses ThreadPoolExecutor for 6 tools
- [ ] Extend parallelization to other pipeline stages where safe

### C2. Incremental Analysis Caching
- [ ] Cache code intelligence results at file/tool level
- [ ] Cache key includes: file hash, tool name, version, config
- [ ] Store in SQLite, invalidate on changes

### C3. Memory Query Caching
- [ ] Add LRU cache for frequent memory queries
- [ ] Invalidate on writes

### C4. LLM Circuit Breaker
- [ ] Detect repeated failures/timeouts/auth errors
- [ ] Support closed/open/half-open states
- [ ] Provider fallback support

### C5. Lazy-Load Code Intelligence Tools
- [ ] Use registry with metadata
- [ ] Import only when invoked

### C6. Progress Bars for Long Gates
- [ ] Show progress in TUI and CLI
- [ ] Include stage name, elapsed time, result state

### C7. Pipeline Optimization
- [ ] Remove unnecessary repeated I/O
- [ ] Reuse parsed artifacts

## Priority D: Usability & DX

### D1. Edit Pipeline Core ✅ IMPLEMENTED
- [x] _run_edit_pipeline() is fully implemented (lines 356-481 in pipeline.py)
- [ ] Add comprehensive tests

### D2. Quickstart Tutorial
- [ ] Implement `forge demo` command
- [ ] Interactive walkthrough with mock provider mode

### D3. Error Message Improvements
- [ ] Replace generic tracebacks with actionable errors
- [ ] Add `forge doctor` diagnostics command

### D4. Shell Completions
- [ ] bash/zsh/fish completion scripts
- [ ] `forge completions install --shell <shell>`

### D5. Environment Variable Configuration
- [ ] Support FORGE_* env vars
- [ ] Precedence: CLI > env > config > defaults

### D6. Project Templates
- [ ] `forge init <template>`
- [ ] Templates: python-cli, python-library, python-service

### D7. Local Dev Setup
- [ ] Docker Compose for NATS
- [ ] Dev container configuration

### D8. Optional Dependencies
- [ ] Add extras: `pip install -e .[tools]`
- [ ] `forge tools install` or `forge tools doctor`

### D9. Async Architecture
- [ ] Refactor pipeline to async/await where appropriate

## Priority E: Functional Completeness

### E1. WebSocket/Web TUI Bridge
- [ ] Live web bridge with session viewing, command triggering
- [ ] Auth/localhost-only binding by default

### E2. Plugin System
- [ ] Extensible plugin system with entry points
- [ ] Tool registration, agent extension, command registration
- [ ] Example plugin

### E3. Multi-language Support
- [ ] Language abstraction layer
- [ ] Python first-class, add TypeScript/JavaScript
- [ ] Language-specific templates, lint/test abstraction

## Priority F: Security

### F1. Encrypt API Keys
- [ ] Use OS keyring/keychain when available
- [ ] Encrypted file fallback
- [ ] Migration from plaintext

### F2. Input Validation/Sanitization
- [ ] Path validation (no directory traversal)
- [ ] Command validation for shell tools
- [ ] Length limits, unsafe pattern detection

### F3. File System Sandboxing
- [ ] Confine agent access to workspace root
- [ ] Canonicalize paths, reject escapes

### F4. NATS TLS/Auth
- [ ] Support TLS connections
- [ ] Token/user-pass/credentials auth

### F5. Audit Logging
- [ ] Log security-relevant operations
- [ ] Structured logging, redact secrets

### F6. Dependency/Secret Scanning
- [ ] Keep semgrep/pip-audit as hard gates
- [ ] Clear failure reports

## Priority G: Documentation & Quality

### G1. Update README.md
### G2. Update SPEC.md
### G3. Improve Inline Documentation
### G4. Add Tests for All New Features
### G5. Add Benchmarks

