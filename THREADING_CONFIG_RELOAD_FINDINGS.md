# Thread-Safe Config Reload Research Findings

**Date**: 2026-04-23  
**Scope**: Python daemon hot-reload locking architecture  
**Issue**: `reload_lock` guards registry mutations but `Resolver`/`LLMRouter` instances lack synchronization on reads

---

## 1. Python Reader-Writer Lock (RWLock) Patterns

### The Problem
Python's stdlib has **no built-in RWLock**. This has been a feature request since 2010 (Python tracker issue #8800, GitHub issue #53046) but remains unimplemented. RWLock allows multiple concurrent readers while serializing a single writer.

### Solutions

#### Option A: Third-Party Library
- **[readerwriterlock](https://pypi.org/project/readerwriterlock/)** - production-ready, compliant with Python lock interface, supports timeout
- **[reader-writer-locks](https://pypi.org/project/reader-writer-locks/)** - multiprocessing-aware, uses RLock + 2 Condition variables

#### Option B: Custom Implementation (Simple Pattern)
From [GitHub Gist](https://gist.github.com/tylerneylon/a7ff6017b7a1f9a506cf75aa23eacfd6) and Python Cookbook:

```python
from threading import Lock, Condition

class RWLock:
    """Reader-write lock: many readers, one writer."""
    def __init__(self):
        self._readers = 0
        self._writers = 0
        self._read_ready = Condition(Lock())
    
    def acquire_read(self):
        self._read_ready.acquire()
        try:
            while self._writers > 0:
                self._read_ready.wait()
            self._readers += 1
        finally:
            self._read_ready.release()
    
    def release_read(self):
        self._read_ready.acquire()
        try:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()
        finally:
            self._read_ready.release()
    
    def acquire_write(self):
        self._read_ready.acquire()
        try:
            while self._writers > 0 or self._readers > 0:
                self._read_ready.wait()
            self._writers += 1
        finally:
            self._read_ready.release()
    
    def release_write(self):
        self._read_ready.acquire()
        try:
            self._writers -= 1
            self._read_ready.notify_all()
        finally:
            self._read_ready.release()

# Usage with context managers
with rwlock.r_locked():  # acquire_read()
    result = resolver.resolve_window(target)

with rwlock.w_locked():  # acquire_write()
    registry = reload_registry_from_disk()
```

**Note**: This pattern prioritizes reader throughput over writer fairness (readers can starve writers). For occasional reloads, this is acceptable.

---

## 2. Thread-Safe Config Reload: Atomic Swap Pattern

### The Key Insight: Simple Assignment IS Atomic (With Caveats)

From Python docs on [Thread Safety Guarantees](https://docs.python.org/3/library/threadsafety.html) and [GIL semantics](https://opensource.com/article/17/4/grok-gil):

**Single variable assignment is atomic under the GIL:**
```python
self.registry = new_registry  # Single STORE_FAST bytecode → atomic
```

**But swaps are NOT atomic:**
```python
# NOT atomic: involves LOAD_ATTR, LOAD_GLOBAL, STORE_ATTR, etc.
old, self.registry = self.registry, new_registry  # RACE CONDITION!
```

**Why?** Operations compile to multiple bytecode instructions. A thread context switch can occur between LOAD and STORE.

### Recommended Pattern for Config Reload

```python
class Resolver:
    def __init__(self):
        self._registry = {}
        self._registry_lock = Lock()
    
    def resolve_window(self, target):
        # Readers acquire lock briefly, then release
        with self._registry_lock:
            registry_snapshot = self._registry
        
        # Do work outside lock
        return registry_snapshot.get(target)
    
    def reload_registry(self, new_registry):
        # Writers hold lock for minimal duration
        with self._registry_lock:
            self._registry = new_registry  # Atomic assignment

class HotReloadEndpoint:
    def reload(self):
        new_registry = load_from_disk()
        resolver.reload_registry(new_registry)  # Atomic swap
        llm_router.reload_registry(new_registry)
```

**Why this works:**
1. **Atomic assignment** (`self._registry = X`) is a single bytecode instruction under GIL
2. **Minimal lock hold time** reduces contention
3. **Snapshot pattern** avoids holding lock during work

### Anti-Pattern: Long-Held Locks During I/O

```python
# BAD: Lock held while loading from disk
with lock:
    self.registry = load_from_disk()  # I/O takes ms, blocks all readers
```

---

## 3. Multiple Locks: Ordering and Deadlock Prevention

### The Deadlock Problem

From [GeeksforGeeks](https://www.geeksforgeeks.org/python-locking-without-deadlocks/) and [RealPython](https://realpython.com/python-thread-lock/):

**Deadlock occurs when threads acquire locks in inconsistent order:**
```python
# Thread 1                    # Thread 2
with lock_a:                 with lock_b:
    with lock_b:                 with lock_a:
        ...                          ...
# DEADLOCK: T1 holds A, waits for B
#           T2 holds B, waits for A
```

### Prevention Strategy: Enforce Lock Ordering

```python
# GLOBAL LOCK HIERARCHY (document this!)
# 1. reload_lock (guards registry mutations)
# 2. resolver_lock (guards resolver state)
# 3. llm_router_lock (guards router state)

class Resolver:
    def __init__(self, reload_lock):
        self._registry = {}
        self._resolver_lock = Lock()
        self._reload_lock = reload_lock  # Reference to daemon's lock
    
    def resolve_window(self, target):
        # Always acquire in same order: reload_lock first
        with self._reload_lock:
            with self._resolver_lock:
                return self._registry.get(target)
    
    def reload_registry(self, new_registry):
        with self._reload_lock:
            with self._resolver_lock:
                self._registry = new_registry

class LLMRouter:
    def __init__(self, reload_lock):
        self._prompts = {}
        self._router_lock = Lock()
        self._reload_lock = reload_lock
    
    def route(self, text):
        with self._reload_lock:
            with self._router_lock:
                prompt = self._prompts["default"]
        # Do work outside lock
        return llm.call(prompt, text)
    
    def reload_prompts(self, new_prompts):
        with self._reload_lock:
            with self._router_lock:
                self._prompts = new_prompts
```

### RLock Alternative: When the Same Thread Recurses

From [Python docs](https://docs.python.org/3/library/threading.html):

**Lock** vs **RLock**:
- **Lock**: Non-reentrant. Same thread acquiring twice = deadlock.
- **RLock**: Reentrant. Same thread can acquire multiple times, must release equally.

```python
from threading import RLock

# Use RLock only if same thread calls acquire multiple times
lock = RLock()
lock.acquire()
lock.acquire()  # OK with RLock, DEADLOCK with Lock
lock.release()
lock.release()

# Common use case: nested function calls with shared lock
class Component:
    def __init__(self):
        self.lock = RLock()  # Prefer for nested protect sections
    
    def public_method(self):
        with self.lock:
            self._private_method()
    
    def _private_method(self):
        # Same thread already holds lock, no deadlock with RLock
        with self.lock:
            ...
```

**Verdict for your daemon:** Use RLock for `reload_lock` if `Resolver.resolve_window()` may be called during a reload operation. Otherwise, simple `Lock` is sufficient with documented ordering.

---

## 4. GIL Guarantees: Object Swap Atomicity

### What's Atomic Under GIL

From [Python documentation](https://docs.python.org/3/library/threadsafety.html) and [A.M. Kuchling's blog](https://blog.qqrs.us/blog/2016/05/01/which-python-operations-are-atomic/):

**Atomic (single bytecode instruction):**
- Simple assignment: `x = value`
- List append: `lst.append(item)`
- Dict key access: `d[key]`

**NOT atomic (multiple bytecode instructions):**
- Swap: `a, b = b, a`
- Increment: `x = x + 1`
- Read-modify-write: `d[key] = d[key] + 1`

### Example Bytecode

```python
# Assignment (atomic)
self.registry = new_registry
# Compiles to:
#   LOAD_FAST new_registry
#   LOAD_FAST self
#   STORE_ATTR registry
# Can context-switch only between instructions, but once STORE_ATTR starts, 
# the attribute is updated atomically.

# Swap (NOT atomic)
old, self.registry = self.registry, new_registry
# Compiles to ~8 bytecode instructions with multiple load/store pairs
# Context switch can occur between LOAD_ATTR and STORE_ATTR
```

### Implication for Config Reload

**DO NOT rely on swap atomicity:**
```python
# BAD: Not atomic, reader may see partial state
old_registry, self.registry = self.registry, new_registry

# GOOD: Atomic assignment + lock
with lock:
    self.registry = new_registry  # Atomic
```

### Python 3.14+ No-GIL Consideration

From [PEP 703](https://peps.python.org/pep-0703/) and [DZone article](https://dzone.com/articles/breaking-the-chains-of-the-gil-in-python):

Python 3.14+ introduces optional no-GIL mode. **Even if you remove the GIL, assignment remains atomic within object's internal lock**, but inter-object coordination requires explicit synchronization. Always use locks; don't assume GIL coverage.

---

## 5. Actionable Recommendations for Your Daemon

### Recommendation: Shared RWLock (One Lock, Two Access Modes)

**Why:** Your daemon has one mutable resource (registry) with two access patterns:
- **Reads**: `Resolver.resolve_window()`, `LLMRouter.route()` — frequent, short
- **Writes**: Hot-reload endpoint — infrequent, brief

**Pattern:**
```python
from threading import Lock, Condition

class StreamingDaemon:
    def __init__(self):
        self._registry_rwlock = RWLock()  # Shared, not per-component
        self.resolver = Resolver(self._registry_rwlock)
        self.llm_router = LLMRouter(self._registry_rwlock)
    
    def on_reload_request(self):
        with self._registry_rwlock.w_locked():
            new_registry = load_from_disk()
            self.resolver._registry = new_registry
            self.llm_router._registry = new_registry

class Resolver:
    def __init__(self, registry_rwlock):
        self._registry = {}
        self._rwlock = registry_rwlock
    
    def resolve_window(self, target):
        with self._rwlock.r_locked():
            registry = self._registry
        return registry.get(target)

class LLMRouter:
    def __init__(self, registry_rwlock):
        self._registry = {}
        self._rwlock = registry_rwlock
    
    def route(self, text):
        with self._rwlock.r_locked():
            registry = self._registry
        # I/O outside lock
        return self._plan_from_llm(registry, text)
```

**Trade-offs:**
- ✅ Multiple readers (resolve_window, route) run concurrently
- ✅ Single writer (reload) blocks all readers briefly
- ✅ Single lock to order; no deadlock risk
- ❌ Adds ~50 lines if implementing custom RWLock; consider `readerwriterlock` PyPI package

### Alternative: Separate Locks (If You Want Finer Granularity)

Use only if resolver and router have independent state:
```python
class StreamingDaemon:
    def __init__(self):
        self._reload_lock = Lock()  # Master lock, acquired during reload
        self.resolver = Resolver(self._reload_lock)
        self.llm_router = LLMRouter(self._reload_lock)
    
    def on_reload_request(self):
        with self._reload_lock:  # Exclusive access during reload
            new_registry = load_from_disk()
            self.resolver._registry = new_registry
            self.llm_router._registry = new_registry
```

**Trade-offs:**
- ✅ Simpler (just a Lock)
- ❌ Reload blocks ALL reads (resolver + router)
- ⚠️ Only viable if reload is very infrequent

### Lock Ordering Documentation (If Using Multiple Locks)

Add to codebase if you decide on finer-grained locking:
```python
"""
LOCK ORDERING POLICY (Deadlock Avoidance)

All threads must acquire locks in this order:
  1. daemon.reload_lock (outermost)
  2. resolver.resolver_lock (if needed)
  3. llm_router.router_lock (innermost)

Violation results in deadlock. Document any exception.

Example:
  with daemon.reload_lock:
      with resolver.resolver_lock:
          # Safe to do both resolver and reload coordination
          pass
"""
```

---

## 6. Implementation Checklist

- [ ] **Choose lock strategy:**
  - [ ] RWLock (shared, fine-grained): Use `readerwriterlock` PyPI or copy custom impl
  - [ ] Single reload_lock: Simple but blocks all reads during reload
  
- [ ] **Audit current code:**
  - [ ] Find all places `self.registry` is read (Resolver, LLMRouter, others)
  - [ ] Find all places `self.registry` is written (reload endpoint, startup)
  - [ ] Document lock assumption for each
  
- [ ] **Wire locks into Resolver, LLMRouter:**
  - [ ] Pass lock reference to `__init__`
  - [ ] Wrap reads with `with lock`
  - [ ] Minimize lock hold time (snapshot pattern)
  
- [ ] **Test hot-reload race conditions:**
  - [ ] Spin 1000 background resolve_window() calls
  - [ ] Trigger reload from main thread
  - [ ] Assert no crashes, stale data reads, or deadlocks
  
- [ ] **Document lock policy:**
  - [ ] Add LOCK_ORDERING comment block to daemon startup
  - [ ] Update ADR if locking pattern is non-obvious

---

## 7. Quick Reference: Code Patterns

### Pattern: Snapshot + Release
```python
def read_data(self):
    with self.lock.r_locked():
        data = self.registry  # Snapshot
    # Lock released, do work
    return process(data)
```

### Pattern: Atomic Swap During Write
```python
def reload_data(self, new_data):
    with self.lock.w_locked():
        self.registry = new_data  # Atomic assignment
    # Lock released
```

### Pattern: Context Manager Ensures Release
```python
with lock:
    # Code here
try:
    # Code here
finally:
    lock.release()  # Equivalent, but with is cleaner
```

---

## Sources

- [Python threading documentation](https://docs.python.org/3/library/threading.html)
- [Python thread safety guarantees](https://docs.python.org/3/library/threadsafety.html)
- [RealPython: Thread-safe locks](https://realpython.com/python-thread-lock/)
- [GitHub Gist: Simple RWLock](https://gist.github.com/tylerneylon/a7ff6017b7a1f9a506cf75aa23eacfd6)
- [PyPI: readerwriterlock](https://pypi.org/project/readerwriterlock/)
- [SuperFastPython: Atomic operations](https://superfastpython.com/thread-atomic-operations/)
- [GIL explained](https://opensource.com/article/17/4/grok-gil/)
- [GeeksforGeeks: Deadlock prevention](https://www.geeksforgeeks.org/python-locking-without-deadlocks/)
- [PEP 703: No-GIL Python](https://peps.python.org/pep-0703/)

