# Contributing — Style & Commits

Guidelines for the **Ape** platform (ape-skeleton workspace and its checkouts).

---

## 1. C# coding style

### General principles

- **Consistency** — follow established patterns in the area you touch.
- **Readability** — code is written for humans first.
- **IDE-agnostic** — rules should not depend on a specific formatter.

### Naming

| Element | Convention | Example |
|---------|------------|---------|
| Classes, types | PascalCase | `NetworkManager` |
| Interfaces | `I` + PascalCase | `IEventManager` |
| Methods, properties | PascalCase | `StartServer`, `LocalPeerId` |
| Private fields | `_camelCase` or `camelCase` (pick one per file) | `_logger` |
| Locals, parameters | camelCase | `configPath` |
| Constants | PascalCase or UPPER_SNAKE | `DefaultPort` |

### Files and namespaces

- **File-scoped namespaces** preferred (.NET 6+).
- **One public type per file**; filename matches type name.
- Namespace matches folder: `Ape.Core.Network`, `Ape.Module.Example.Services`.
- Module layout: see [ARCHITECTURE.md](ARCHITECTURE.md) § Module folder layout.

### Formatting

- **4 spaces**, no tabs.
- **Allman braces** (opening brace on its own line).
- ~100–120 characters per line (flexible).
- `var` when the type is obvious from the right-hand side.

### Nullable reference types

- Project uses nullable reference types — annotate with `?` consistently.

### XML documentation

Required on **public** APIs (interfaces, public classes, public methods). Use `<summary>`, `<param>`, `<returns>`, `<exception>` where helpful.

### Async

- Suffix async methods with `Async`.
- `CancellationToken` as the last parameter with a default.

### Dependency injection

- Constructor injection preferred.
- `GetRequiredService<T>()` when the service is mandatory; `GetService<T>()` when optional.

---

## 2. Thread safety and concurrency

Ape plugins run on **separate threads**. Scene graph objects inherit locking via `ApeReplica.SyncRoot`.

**Rules:**

1. **Document** when a type is thread-safe.
2. **Keep critical sections short** — do not hold locks during I/O or logging.
3. **Avoid nested locks** on different nodes (deadlock risk).
4. Use `ConcurrentDictionary` / `ConcurrentQueue` for shared collections where appropriate.
5. Prefer **RAII-style** scoped locks (`WithLock`, `LockNode`) for multi-step mutations.

```csharp
// Good: short critical section
node?.WithLock(n => n.Position = newPos);

// Bad: lock held during slow work
node?.WithLock(n =>
{
    n.Position = newPos;
    _logger?.LogInfo($"Updated to {newPos}"); // slow — do outside lock
});
```

**Direction:** authoritative scene writes should move to the **commit pipeline** ([DETERMINISM.md](../src/Ape.Core/docs/DETERMINISM.md)); direct `ISceneManager` mutation from plugin threads is legacy.

---

## 3. Git commit messages

### Format

```
<type>: <short description>

[optional body — wrap at ~72 chars]

[optional footer]
```

- **Short description:** imperative mood, ≤50–72 characters, no trailing period.
- **Language:** English.

### Types

| Type | Use |
|------|-----|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Behaviour-preserving restructure |
| `perf` | Performance improvement |
| `test` | Tests |
| `build` | Build, CI, dependencies |
| `style` | Formatting, no logic change |
| `chore` | Miscellaneous |

### Examples

```
feat: add QUIC transport support
fix: load audio UI defaults from config
docs: consolidate platform documentation
refactor: extract transport factory from NetworkManager
```

### With body

```
fix: handle role vs mode config key in network setup

Read both "role" and "mode" from network config, preferring
"role" for backward compatibility.
```

### Optional scope

```
feat(network): add GetPreferredLocalIPAddress for discovery
fix(replica): ownership validation on delete
```

### Breaking changes

```
fix: change INetworkManager.StartServer to return bool

Breaking change: callers must check return value.
```

### Rules

1. One logical change per commit.
2. Imperative mood: "Add feature", not "Added feature".
3. No `WIP` / meaningless messages.

---

*Apply style gradually; no big-bang reformat required.*
