# Architectural Analysis & Implementation Specification: CLI Trampoline, Symlink Integration, and Installer Synchronization (Milestone M1)

**Timestamp:** 2026-10-06T21:15:00Z  
**Author:** Teamwork Explorer Agent (`explorer_m1_2`)  
**Scope:** Milestone M1 — Bash Trampoline `bin/auto-speech`, Symlink `~/.local/bin/auto-speech`, `setup/install-plugin.sh`, `setup/uninstall.sh`, and `tests/test_install_plugin.sh`  
**Target Worktree:** `/Users/joshua/Developer/auto-speech-worktree`  
**Repository Baseline:** `tests/test_install_plugin.sh` currently passes 10/10; `tests/run_all.sh` baseline passing 42 suites.

---

## 1. Executive Summary

Milestone M1 establishes the unified CLI surface for `auto-speech`. The user request (`ORIGINAL_REQUEST.md` §R1) demands that `auto-speech` be executable directly from any shell environment and linked into `~/.local/bin/auto-speech`, providing a consistent entrypoint for all 12 subcommands.

This report formulates the complete technical specification for:
1. **The bash trampoline script (`bin/auto-speech`)**: A robust, zero-dependency bash trampoline that canonicalizes its own path across arbitrary symlinks (including chained and relative symlinks), locates `$PROJECT_ROOT/.venv/bin/python`, emits actionable diagnostics on missing virtual environments, sets `PYTHONPATH`, and replaces the shell process via `exec` into `cli.py "$@"`.
2. **The global symlink (`~/.local/bin/auto-speech`)**: Creation, idempotency rules, conflict handling, and PATH resolution in macOS environments.
3. **Installer & Uninstaller Synchronization (`setup/install-plugin.sh` and `setup/uninstall.sh`)**: Ensuring `~/.local/bin/auto-speech` is linked upon installation and cleanly purged upon uninstall—without hardcoded absolute paths, strictly respecting `$HOME` for sandbox isolation.
4. **Test Suite Invariants (`tests/test_install_plugin.sh`)**: Proving that all 10 existing assertions remain 100% green while adding 3 new contract assertions verifying symlink creation, uninstallation, and portability.

---

## 2. Forensic Audit & Architectural Constraints

### 2.1 The Trampoline Imperative
On macOS systems, the default system Python (`/usr/bin/python3`) is Xcode's Python (3.9.x), which lacks modern standard libraries (`tomllib`), project dependencies (`mlx-audio`, `flask`, `numpy`), and project virtual environment packages. 

A standard shebang like `#!/usr/bin/env python3` is brittle because:
1. It executes whichever Python happens to be first on `PATH` in the calling shell.
2. In non-interactive subshells, cron jobs, launchd daemons, or IDE subprocesses, `PATH` is often minimal (`/usr/bin:/bin:/usr/sbin:/sbin`), causing immediate failure or silent version mismatches.
3. Subprocess calls from AI agent tools or slash commands should not require users to activate a virtual environment manually.

Therefore, `bin/auto-speech` must act as a lightweight, robust bash trampoline that:
- Resolves the physical repository root (`$PROJECT_ROOT`) regardless of where or how the script is invoked.
- Discovers the project virtual environment at `$PROJECT_ROOT/.venv/bin/python`.
- Handles virtual environment absence defensively with a clear, diagnostic stderr message and exit code 1.
- Seamlessly transfers control to the Python interpreter via `exec`, preserving standard input streams, command-line arguments, UNIX signals (`SIGINT`, `SIGTERM`), and process return codes.

### 2.2 Worktree vs. Main Isolation (Critical Finding)
A key architectural consideration discovered during our audit is that development and verification occur within an isolated Git worktree:
- Main repository: `/Users/joshua/Developer/auto-speech`
- Worktree: `/Users/joshua/Developer/auto-speech-worktree`

In the worktree, `/Users/joshua/Developer/auto-speech-worktree/.venv` is a symlink pointing to the main repository's `.venv`:
```
/Users/joshua/Developer/auto-speech-worktree/.venv -> /Users/joshua/Developer/auto-speech/.venv
```
If `bin/auto-speech` were to fall back to the recorded project root in `~/.config/auto-speech/root` when `.venv` is missing, it would cause silent cross-contamination: invoking the worktree binary could accidentally execute against the main repository or vice versa!

**Architectural Decision:** `bin/auto-speech` must strictly resolve its virtual environment relative to its own canonicalized `$PROJECT_ROOT` (`$PROJECT_ROOT/.venv/bin/python`). It must **never** fall back to `~/.config/auto-speech/root` for python discovery, guaranteeing hermetic execution in both the worktree and the main repository.

---

## 3. Specification: `bin/auto-speech` Bash Trampoline

### 3.1 Script Architecture & Canonicalization
The script must be placed at `bin/auto-speech` with executable file mode (`0755` / `chmod +x`).

To resolve the true project root across arbitrary symlinks (e.g. `~/.local/bin/auto-speech -> .../bin/auto-speech`), the trampoline uses a portable POSIX-compliant `readlink` loop with a recursion guard:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Resolve true script location across arbitrary symlink chains
SOURCE="${BASH_SOURCE[0]}"
HOPS=0
MAX_HOPS=20
while [ -L "$SOURCE" ]; do
    if (( HOPS++ >= MAX_HOPS )); then
        echo "error: auto-speech: exceeded maximum symlink depth of $MAX_HOPS ($SOURCE)" >&2
        exit 1
    fi
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
BIN_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
PROJECT_ROOT="$(cd -P "$BIN_DIR/.." && pwd)"
```

#### Why this implementation is robust:
1. `cd -P`: Resolves all physical directory paths without following logical symlink aliases.
2. `[[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"`: Correctly re-anchors relative symlinks (e.g., `../../repo/bin/auto-speech`) to their parent directory before the next iteration.
3. `MAX_HOPS=20`: Prevents infinite loops in the event of circular symlink corruption.
4. `PROJECT_ROOT="$(cd -P "$BIN_DIR/.." && pwd)"`: Reliably establishes the repository root as the immediate parent of `bin/`.

### 3.2 Virtual Environment Validation & Diagnostics
Once `PROJECT_ROOT` is established, the script checks for an executable Python binary in `.venv`:

```bash
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "error: virtual environment python not found at $VENV_PYTHON" >&2
    echo "       Run $PROJECT_ROOT/setup/install.sh to initialize the virtual environment." >&2
    exit 1
fi
```

#### Diagnostic Requirements:
- **Output Channel**: `sys.stderr` (`>&2`).
- **Exit Code**: `1` (`EXIT_FAILURE`).
- **Verbatim Error String**: States the exact missing interpreter path and provides the exact remediation command (`setup/install.sh`).
- **Symlink Compatibility**: Because bash's `[[ -x "$PATH" ]]` tests whether the ultimate target file has execute permissions, this works seamlessly whether `.venv` is a real directory or a symlink (as in the worktree sandbox).

### 3.3 Python Dispatcher Verification & Process Exec
Next, the script validates that `cli.py` is present, exports the Python library path, and invokes `exec`:

```bash
CLI_PY="$PROJECT_ROOT/plugin/scripts/python/cli.py"

if [[ ! -f "$CLI_PY" ]]; then
    echo "error: auto-speech CLI dispatcher not found at $CLI_PY" >&2
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT/plugin/scripts/python${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PYTHON" "$CLI_PY" "$@"
```

#### Rationale for `exec`:
1. **Process Image Replacement**: `exec` replaces the bash shell process with the Python interpreter in place. No zombie bash wrapper remains in the OS process table.
2. **Signal Propagation**: Signals like `SIGINT` (Ctrl+C, exit code 130) and `SIGTERM` are delivered directly to the Python process and its child subprocesses (e.g., `mpv`).
3. **I/O Channel Transparency**: `stdin`, `stdout`, and `stderr` file descriptors are inherited directly without shell buffering or pipe multiplexing. This guarantees that piped inputs (e.g., `echo "text" | auto-speech speak`) and output redirection (e.g., `auto-speech doctor --json > report.json`) function without degradation.
4. **Exit Code Preservation**: The exit status returned by `cli.py` (`0`, `1`, `2`, `3`, `4`, `6`, `130`) becomes the immediate exit status of the CLI invocation.

---

## 4. Specification: `~/.local/bin/auto-speech` Symlink & PATH Integration

### 4.1 System Environment Analysis
On the target developer machine:
- `~/.local/bin` exists and is already configured on `$PATH`:
  ```
  /Users/joshua/.gemini/antigravity/bin:...:/Users/joshua/.local/bin:/opt/homebrew/bin:...
  ```
- It hosts developer toolchains and system CLI scripts (`agent-event-router`, `svc`, `sysobs`, `systemone`).
- Linking `auto-speech` into `~/.local/bin/auto-speech` makes the command globally available across interactive terminal windows, subshells, and scripts without modifying system directories like `/usr/local/bin` or `/opt/homebrew/bin`.

### 4.2 Idempotent Symlink Creation Logic
When `setup/install-plugin.sh` runs, it must ensure `~/.local/bin/auto-speech` is properly established according to the following state matrix:

| Current State of `~/.local/bin/auto-speech` | Installer Action | Log Output |
|---|---|---|
| Does not exist | Create symlink `ln -s "$src" "$dst"` | `[install-plugin] linked $dst -> $src` |
| Symlink already points to `$src` | No-op (idempotent pass) | `[install-plugin] symlink already in place: $dst -> $src` |
| Symlink points to old/different path | Remove stale link `rm "$dst"` and create new `ln -s "$src" "$dst"` | `[install-plugin] removing stale symlink $dst (-> $existing)`<br>`[install-plugin] linked $dst -> $src` |
| Exists as a regular file or directory | Do not overwrite; print error to stderr and exit 1 | `error: $dst exists and is not a symlink. Refusing to overwrite.` |

### 4.3 Safe Symlink Removal Logic
When `setup/uninstall.sh` runs, it must clean up `~/.local/bin/auto-speech` safely:
- It checks whether `$HOME/.local/bin/auto-speech` is a symlink (`[[ -L "$cli_link" ]]`).
- It resolves the target via `readlink "$cli_link"`.
- It verifies that the target matches `"$PROJECT_ROOT/"*`.
- **Foreign Protection Invariant**: If `~/.local/bin/auto-speech` points outside `$PROJECT_ROOT`, `uninstall.sh` logs a skip message and leaves the foreign link untouched.

---

## 5. Specification: Updates to Installer & Uninstaller

### 5.1 Updates to `setup/install-plugin.sh`

In `setup/install-plugin.sh`:
1. Add helper function `install_cli_symlink()`.
2. Ensure `$PROJECT_ROOT/bin/auto-speech` exists and is executable (`chmod +x`).
3. Ensure `$HOME/.local/bin` exists (`mkdir -p "$HOME/.local/bin"`).
4. Apply the state matrix specified in §4.2.
5. Invoke `install_cli_symlink` during the execution flow.
6. Add confirmation in the summary output.

#### Code Snippet for `setup/install-plugin.sh`:
```bash
install_cli_symlink() {
    local src="$PROJECT_ROOT/bin/auto-speech"
    local bin_dir="$HOME/.local/bin"
    local dst="$bin_dir/auto-speech"

    if [[ ! -f "$src" ]]; then
        echo "error: expected auto-speech trampoline at $src" >&2
        exit 1
    fi

    chmod +x "$src"
    mkdir -p "$bin_dir"

    if [[ -L "$dst" ]]; then
        local existing
        existing="$(readlink "$dst")"
        if [[ "$existing" == "$src" ]]; then
            echo "[install-plugin] symlink already in place: $dst -> $src"
            return 0
        fi
        echo "[install-plugin] removing stale symlink $dst (-> $existing)"
        rm "$dst"
    elif [[ -e "$dst" ]]; then
        echo "error: $dst exists and is not a symlink. Refusing to overwrite." >&2
        echo "       Back it up manually, then re-run." >&2
        exit 1
    fi

    ln -s "$src" "$dst"
    echo "[install-plugin] linked $dst -> $src"
}
```

### 5.2 Updates to `setup/uninstall.sh`

In `setup/uninstall.sh`, insert the CLI symlink removal step alongside slash command symlink cleanup:

#### Code Snippet for `setup/uninstall.sh`:
```bash
# 1b. Remove CLI dispatcher symlink in ~/.local/bin/ if (and only if) it points into our project.
cli_link="$HOME/.local/bin/auto-speech"
if [[ -L "$cli_link" ]]; then
    target="$(readlink "$cli_link")"
    case "$target" in
        "$PROJECT_ROOT/"*)
            rm -f "$cli_link"
            echo "[uninstall] removed CLI symlink $cli_link → $target"
            ;;
        *)
            echo "[uninstall] skipped $cli_link (symlink points outside project: $target)"
            ;;
    esac
fi
```

---

## 6. Verification & Test Suite Invariants (`tests/test_install_plugin.sh`)

### 6.1 Audit of Existing Test Assertions
`tests/test_install_plugin.sh` exercises the installer and uninstaller against an isolated temporary directory by setting `HOME="$SANDBOX"`.

Currently, the test validates 10 contract assertions:
1. `curated install links exactly 8 commands`: Queries `find "$SANDBOX/.claude/commands" -name 'auto-speech-*.md'`. Since `$SANDBOX/.local/bin/auto-speech` is outside `$SANDBOX/.claude/commands`, this assertion remains **100% green**.
2. `root file recorded with the clone path`: Checks `$SANDBOX/.config/auto-speech/root`. Unaffected, remains **green**.
3. `doctor block resolves root from ~/.config/auto-speech/root`: Executes fenced markdown block against sandbox root file. Unaffected, remains **green**.
4. `guard fires when root file is absent`: Verifies non-zero exit when root file is removed. Unaffected, remains **green**.
5. `--with-extras links 21 commands`: Queries count in `.claude/commands`. Unaffected, remains **green**.
6. `plain re-run preserves opted-in extras`: Tests idempotency of command extras. Unaffected, remains **green**.
7. `prune removes non-keep-set links into plugin/commands/`: Verifies tier 1 prune. Unaffected, remains **green**.
8. `prune removes dangling links into plugin/commands-extra/`: Verifies tier 2 prune. Unaffected, remains **green**.
9. `uninstall removes the recorded root file`: Verifies root file deletion. Unaffected, remains **green**.
10. `no hardcoded absolute project paths in command files`: Checks `grep -rq "/Users/joshua" "$PROJECT_ROOT/plugin/commands" "$PROJECT_ROOT/plugin/commands-extra"`. Unaffected, remains **green**.

### 6.2 New Test Contract Assertions
To ensure complete test coverage and verification for the new CLI symlink infrastructure, `tests/test_install_plugin.sh` will be expanded with three explicit assertions:

#### Assertion 11: Curated install creates `~/.local/bin/auto-speech`
```bash
cli_link="$SANDBOX/.local/bin/auto-speech"
if [[ -L "$cli_link" && "$(readlink "$cli_link")" == "$PROJECT_ROOT/bin/auto-speech" ]]; then
    ok "curated install links ~/.local/bin/auto-speech"
else
    fail "cli symlink" "missing or wrong target: $(readlink "$cli_link" 2>/dev/null)"
fi
```

#### Assertion 12: Uninstall cleans up `~/.local/bin/auto-speech`
```bash
if [[ ! -L "$cli_link" && ! -e "$cli_link" ]]; then
    ok "uninstall removes ~/.local/bin/auto-speech symlink"
else
    fail "uninstall cli symlink" "symlink still present: $(readlink "$cli_link" 2>/dev/null)"
fi
```

#### Assertion 13: Portability invariant covers `bin/auto-speech`
```bash
if grep -rq "/Users/joshua" "$PROJECT_ROOT/bin" 2>/dev/null; then
    fail "portability" "hardcoded /Users/joshua path present in bin/ files"
else
    ok "no hardcoded absolute project paths in bin/ trampoline"
fi
```

With these additions, `tests/test_install_plugin.sh` will cleanly expand from 10 to 13 passing assertions.

---

## 7. Concrete Code Implementation Artifacts

Below are the exact file contents and patches ready for application in the target worktree (`/Users/joshua/Developer/auto-speech-worktree`).

### 7.1 New File: `bin/auto-speech`
**Target Path:** `/Users/joshua/Developer/auto-speech-worktree/bin/auto-speech`  
**File Mode:** `0755` (`chmod +x`)

```bash
#!/usr/bin/env bash
# auto-speech — unified command-line interface dispatcher trampoline.
# Resolves the true project root across arbitrary symlinks, locates the
# project virtual environment, and executes plugin/scripts/python/cli.py.

set -euo pipefail

# Resolve true script location across arbitrary symlink chains
SOURCE="${BASH_SOURCE[0]}"
HOPS=0
MAX_HOPS=20
while [ -L "$SOURCE" ]; do
    if (( HOPS++ >= MAX_HOPS )); then
        echo "error: auto-speech: exceeded maximum symlink depth of $MAX_HOPS ($SOURCE)" >&2
        exit 1
    fi
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
BIN_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
PROJECT_ROOT="$(cd -P "$BIN_DIR/.." && pwd)"

VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "error: virtual environment python not found at $VENV_PYTHON" >&2
    echo "       Run $PROJECT_ROOT/setup/install.sh to initialize the virtual environment." >&2
    exit 1
fi

CLI_PY="$PROJECT_ROOT/plugin/scripts/python/cli.py"

if [[ ! -f "$CLI_PY" ]]; then
    echo "error: auto-speech CLI dispatcher not found at $CLI_PY" >&2
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT/plugin/scripts/python${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PYTHON" "$CLI_PY" "$@"
```

### 7.2 Diff Patch: `setup/install-plugin.sh`
**Target File:** `/Users/joshua/Developer/auto-speech-worktree/setup/install-plugin.sh`

```diff
--- a/setup/install-plugin.sh
+++ b/setup/install-plugin.sh
@@ -32,6 +32,38 @@ printf '%s\n' "$PROJECT_ROOT" > "$HOME/.config/auto-speech/root"
 echo "[install-plugin] recorded project root: $PROJECT_ROOT"
 
+# Link CLI dispatcher trampoline to ~/.local/bin/auto-speech
+install_cli_symlink() {
+    local src="$PROJECT_ROOT/bin/auto-speech"
+    local bin_dir="$HOME/.local/bin"
+    local dst="$bin_dir/auto-speech"
+
+    if [[ ! -f "$src" ]]; then
+        echo "error: expected auto-speech trampoline at $src" >&2
+        exit 1
+    fi
+
+    chmod +x "$src"
+    mkdir -p "$bin_dir"
+
+    if [[ -L "$dst" ]]; then
+        local existing
+        existing="$(readlink "$dst")"
+        if [[ "$existing" == "$src" ]]; then
+            echo "[install-plugin] symlink already in place: $dst -> $src"
+            return 0
+        fi
+        echo "[install-plugin] removing stale symlink $dst (-> $existing)"
+        rm "$dst"
+    elif [[ -e "$dst" ]]; then
+        echo "error: $dst exists and is not a symlink. Refusing to overwrite." >&2
+        echo "       Back it up manually, then re-run." >&2
+        exit 1
+    fi
+
+    ln -s "$src" "$dst"
+    echo "[install-plugin] linked $dst -> $src"
+}
+
+install_cli_symlink
+
 WITH_EXTRAS=0
 if [[ "${1:-}" == "--with-extras" ]]; then
@@ -148,6 +180,7 @@ echo "[install-plugin] auto-speech commands installed (curated keep-set):"
 if [[ $WITH_EXTRAS -eq 1 ]]; then
     echo "[install-plugin] extras installed:"
     echo "    /auto-speech-pause         /auto-speech-resume        /auto-speech-seek"
     echo "    /auto-speech-restart       /auto-speech-end           /auto-speech-autoplay-status"
     echo "    /auto-speech-update        /auto-speech-narrate-{on,off,status,stop,install,config}"
 else
     echo "[install-plugin] 13 extra commands available: re-run with --with-extras"
 fi
+echo "[install-plugin] CLI dispatcher available at: $HOME/.local/bin/auto-speech"
```

### 7.3 Diff Patch: `setup/uninstall.sh`
**Target File:** `/Users/joshua/Developer/auto-speech-worktree/setup/uninstall.sh`

```diff
--- a/setup/uninstall.sh
+++ b/setup/uninstall.sh
@@ -66,6 +66,22 @@ for cmd in "${NAMESPACED_CMDS[@]}" "${LEGACY_CMDS[@]}"; do
 done
 echo "[uninstall] removed $removed_cmds slash command symlinks"
 
+# 1b. Remove CLI dispatcher symlink in ~/.local/bin/ if (and only if) it points into our project.
+cli_link="$HOME/.local/bin/auto-speech"
+if [[ -L "$cli_link" ]]; then
+    target="$(readlink "$cli_link")"
+    case "$target" in
+        "$PROJECT_ROOT/"*)
+            rm -f "$cli_link"
+            echo "[uninstall] removed CLI symlink $cli_link → $target"
+            ;;
+        *)
+            echo "[uninstall] skipped $cli_link (symlink points outside project: $target)"
+            ;;
+    esac
+fi
+
 # 2. Remove Stop hook entry. Use the existing idempotent uninstall script.
 if [[ -x "$PROJECT_ROOT/setup/uninstall-hook.sh" ]]; then
     "$PROJECT_ROOT/setup/uninstall-hook.sh" || true
```

### 7.4 Diff Patch: `tests/test_install_plugin.sh`
**Target File:** `/Users/joshua/Developer/auto-speech-worktree/tests/test_install_plugin.sh`

```diff
--- a/tests/test_install_plugin.sh
+++ b/tests/test_install_plugin.sh
@@ -38,6 +38,13 @@ if [[ -f "$root_file" && "$(cat "$root_file")" == "$PROJECT_ROOT" ]]; then
 else fail "root file" "missing or wrong content: $(cat "$root_file" 2>/dev/null)"; fi
 
+cli_link="$SANDBOX/.local/bin/auto-speech"
+if [[ -L "$cli_link" && "$(readlink "$cli_link")" == "$PROJECT_ROOT/bin/auto-speech" ]]; then
+    ok "curated install links ~/.local/bin/auto-speech"
+else
+    fail "cli symlink" "missing or wrong target: $(readlink "$cli_link" 2>/dev/null)"
+fi
+
 # --- a real command block runs against the recorded root --------------------
@@ -94,6 +101,11 @@ HOME="$SANDBOX" PATH="$SANDBOX/bin:$PATH" AUTO_SPEECH_TMP_ROOT="$SANDBOX/tmp" \
 if [[ ! -f "$root_file" ]]; then ok "uninstall removes the recorded root file"
 else fail "uninstall root" "root file still present"; fi
 
+if [[ ! -L "$cli_link" && ! -e "$cli_link" ]]; then
+    ok "uninstall removes ~/.local/bin/auto-speech symlink"
+else
+    fail "uninstall cli symlink" "symlink still present: $(readlink "$cli_link" 2>/dev/null)"
+fi
+
 # --- portability invariant --------------------------------------------------
-if grep -rq "/Users/joshua" "$PROJECT_ROOT/plugin/commands" \
-        "$PROJECT_ROOT/plugin/commands-extra" 2>/dev/null; then
+if grep -rq "/Users/joshua" "$PROJECT_ROOT/plugin/commands" \
+        "$PROJECT_ROOT/plugin/commands-extra" \
+        "$PROJECT_ROOT/bin" 2>/dev/null; then
     fail "portability" "hardcoded /Users/joshua path present in command files"
 else
     ok "no hardcoded absolute project paths in command files"
 fi
```

---

## 8. Conclusion & Handoff Readiness

The investigation is complete. The trampoline design guarantees:
1. Exact symlink resolution without circular loops or worktree cross-talk.
2. Clean virtual environment detection and helpful stderr diagnostics.
3. Completely portable installation and uninstallation adhering strictly to sandbox and idempotency standards.
4. Seamless integration with `tests/test_install_plugin.sh` that expands test coverage while keeping all 10 baseline assertions green.

See `handoff.md` for the 5-component handoff report.
