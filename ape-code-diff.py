#!/usr/bin/env python3
"""Print staged, unstaged, and untracked diffs for selected APE checkouts.

Ape.Core and each Ape.Module.* directory is its own git repository.
The skeleton repo ignores those trees, so this walks the checkouts directly.
The text on stdout is meant to be pasted to an LLM.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Untracked files under these directory names are build or editor noise.
SKIP_DIR_NAMES = {
    "bin",
    "obj",
    ".vs",
    ".idea",
    "node_modules",
    "__pycache__",
    "TestResults",
    "artifacts",
}
SKIP_FILE_NAMES = {".DS_Store", "Thumbs.db"}
SKIP_SUFFIXES = {".pyc", ".user", ".suo"}

LEGEND = (
    "# status columns match git status: first character is the index, "
    "second is the worktree. ?? is an untracked file.\n"
    "# staged = git diff --cached. unstaged = git diff. "
    "untracked files are not in git; their full text follows as a new-file diff.\n"
    "# paths are relative to that checkout, not to the skeleton root."
)


@dataclass(frozen=True)
class Repo:
    name: str
    path: Path

    @property
    def short(self) -> str:
        if self.name.startswith("Ape.Module."):
            return self.name[len("Ape.Module.") :]
        if self.name.startswith("Ape."):
            return self.name[len("Ape.") :]
        return self.name


@dataclass(frozen=True)
class StatusEntry:
    xy: str
    path: str
    orig: str | None = None

    @property
    def untracked(self) -> bool:
        return self.xy == "??"

    @property
    def staged(self) -> bool:
        return self.xy[0] not in " ?"

    @property
    def unstaged(self) -> bool:
        return self.xy[1] not in " ?"

    def label(self) -> str:
        if self.orig and ("R" in self.xy or "C" in self.xy):
            return f"{self.xy} {self.orig} -> {self.path}"
        return f"{self.xy} {self.path}"


def discover() -> list[Repo]:
    repos: list[Repo] = []
    for name in ("Ape.Core", "Ape.Launcher"):
        path = ROOT / "src" / name
        if path.is_dir():
            repos.append(Repo(name, path))
    modules = ROOT / "src" / "Ape.Modules"
    if modules.is_dir():
        for path in sorted(modules.iterdir()):
            if path.is_dir() and path.name.startswith("Ape.Module."):
                repos.append(Repo(path.name, path))
    return repos


def normalize_spec(spec: str) -> str:
    name = spec.strip().rstrip("/")
    leaf = Path(name).name
    if leaf.startswith("Ape.Module.") or leaf in ("Ape.Core", "Ape.Launcher"):
        return leaf
    return name


def resolve_specs(specs: list[str], repos: list[Repo]) -> list[Repo]:
    by_name = {repo.name: repo for repo in repos}
    chosen: list[Repo] = []
    seen: set[str] = set()
    for raw in specs:
        spec = normalize_spec(raw)
        folded = spec.casefold()
        exact = [
            repo
            for repo in repos
            if repo.name.casefold() == folded or repo.short.casefold() == folded
        ]
        if len(exact) == 1:
            repo = exact[0]
        elif len(exact) > 1:
            names = ", ".join(repo.name for repo in exact)
            raise SystemExit(f"'{raw}' matches more than one checkout: {names}")
        else:
            prefixed = [
                repo
                for repo in repos
                if repo.name.casefold().startswith(folded)
                or repo.short.casefold().startswith(folded)
            ]
            if len(prefixed) == 1:
                repo = prefixed[0]
            elif not prefixed:
                known = ", ".join(repo.name for repo in repos)
                raise SystemExit(f"unknown checkout '{raw}'. known: {known}")
            else:
                names = ", ".join(repo.name for repo in prefixed)
                raise SystemExit(f"'{raw}' is ambiguous: {names}")
        if repo.name not in seen:
            seen.add(repo.name)
            chosen.append(by_name[repo.name])
    return chosen


def git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_PAGER"] = "cat"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def git_bytes(repo: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    cmd = [
        "git",
        "-c",
        "core.quotepath=false",
        "-c",
        "color.ui=false",
        "-c",
        "diff.mnemonicPrefix=false",
        "-c",
        "diff.noprefix=false",
        "--no-pager",
        *args,
    ]
    return subprocess.run(
        cmd,
        cwd=repo,
        capture_output=True,
        env=git_env(),
    )


def git_text(repo: Path, args: list[str]) -> str:
    result = git_bytes(repo, args)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(err or f"git {' '.join(args)} failed")
    return result.stdout.decode("utf-8", "replace")


def parse_status(raw: bytes) -> list[StatusEntry]:
    parts = raw.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    entries: list[StatusEntry] = []
    index = 0
    while index < len(parts):
        rec = parts[index]
        if len(rec) < 4 or rec[2:3] != b" ":
            index += 1
            continue
        xy = rec[:2].decode("ascii", "replace")
        path = rec[3:].decode("utf-8", "surrogateescape")
        orig = None
        if "R" in xy or "C" in xy:
            index += 1
            if index >= len(parts):
                break
            orig = parts[index].decode("utf-8", "surrogateescape")
        entries.append(StatusEntry(xy, path, orig))
        index += 1
    return entries


def is_editor_noise(rel: str) -> bool:
    path = Path(rel)
    return path.name in SKIP_FILE_NAMES or path.suffix in SKIP_SUFFIXES


def is_build_output(rel: str) -> bool:
    return any(part in SKIP_DIR_NAMES for part in Path(rel).parts)


def skip_untracked(rel: str, include_build: bool) -> bool:
    if is_editor_noise(rel):
        return True
    return (not include_build) and is_build_output(rel)


def expand_untracked(repo: Path, rel: str) -> list[str]:
    target = repo / rel
    if target.is_file():
        return [rel]
    if not target.is_dir():
        return [rel]
    files: list[str] = []
    for path in sorted(target.rglob("*")):
        if path.is_file():
            files.append(path.relative_to(repo).as_posix())
    return files


def skipped_note(paths: list[str]) -> str:
    preview = ", ".join(paths[:8])
    extra = f" (+{len(paths) - 8} more)" if len(paths) > 8 else ""
    return f"# skipped untracked noise: {preview}{extra}"


def quote_path(path: str) -> str:
    if any(ch in path for ch in ' \t"\\') or any(ord(ch) < 32 for ch in path):
        escaped = path.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return path


def looks_binary(data: bytes) -> bool:
    sample = data[:8192]
    return b"\0" in sample


def cap_bytes(data: bytes, max_bytes: int) -> tuple[bytes, bool]:
    if max_bytes <= 0 or len(data) <= max_bytes:
        return data, False
    cut = data[:max_bytes]
    newline = cut.rfind(b"\n")
    if newline > max_bytes // 2:
        cut = cut[: newline + 1]
    return cut, True


def new_file_diff(rel: str, data: bytes, max_bytes: int) -> str:
    shown, truncated = cap_bytes(data, max_bytes)
    text = shown.decode("utf-8", "replace")
    if text == "":
        lines: list[str] = []
    else:
        lines = text.split("\n")
        if text.endswith("\n"):
            lines = lines[:-1]
    body = "".join(f"+{line}\n" for line in lines)
    if text and not text.endswith("\n"):
        body += "\\ No newline at end of file\n"
    hunk = f"@@ -0,0 +1,{len(lines)} @@" if lines else "@@ -0,0 +0,0 @@"
    left = quote_path(f"a/{rel}")
    right = quote_path(f"b/{rel}")
    header = (
        f"diff --git {left} {right}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ {right}\n"
        f"{hunk}\n"
    )
    note = ""
    if truncated:
        note = f"# truncated: showed {len(shown)} of {len(data)} bytes\n"
    return note + header + body


def split_diffs(text: str) -> list[str]:
    if not text.strip():
        return []
    chunks: list[str] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("diff --git ") and current:
            chunks.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append("".join(current))
    return chunks


def cap_diff(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return text
    pieces = []
    for chunk in split_diffs(text):
        raw = chunk.encode("utf-8")
        shown, truncated = cap_bytes(raw, max_bytes)
        if truncated:
            pieces.append(
                f"# truncated: showed {len(shown)} of {len(raw)} bytes\n"
                + shown.decode("utf-8", "replace")
            )
        else:
            pieces.append(chunk)
    return "".join(pieces)


def rel_to_root(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def terminal_columns() -> int:
    return shutil.get_terminal_size(fallback=(80, 24)).columns


def render_repo(repo: Repo, context: int, max_bytes: int, include_build: bool, columns: int) -> str:
    if not (repo.path / ".git").exists():
        raise RuntimeError(f"{repo.name} is not a git checkout ({rel_to_root(repo.path)})")

    try:
        branch = git_text(repo.path, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    except RuntimeError:
        branch = "(no HEAD)"
    try:
        head = git_text(repo.path, ["rev-parse", "--short", "HEAD"]).strip()
    except RuntimeError:
        head = "(no commits)"

    status = git_bytes(repo.path, ["status", "--porcelain=v1", "-uall", "-z"])
    if status.returncode != 0:
        err = status.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(err or "git status failed")
    entries = parse_status(status.stdout)

    skipped: list[str] = []
    visible: list[StatusEntry] = []
    for entry in entries:
        if entry.untracked and skip_untracked(entry.path, include_build):
            skipped.append(entry.path)
            continue
        visible.append(entry)

    staged_n = sum(1 for entry in visible if entry.staged)
    unstaged_n = sum(1 for entry in visible if entry.unstaged and not entry.untracked)
    untracked_entries = [entry for entry in visible if entry.untracked]
    counts = f"{staged_n} staged, {unstaged_n} unstaged, {len(untracked_entries)} untracked"
    if skipped:
        counts += f", {len(skipped)} skipped"

    rule = "=" * columns
    thin = "-" * columns
    lines = [
        rule,
        repo.name,
        f"path: {rel_to_root(repo.path)}",
        f"branch: {branch}",
        f"head: {head}",
        counts,
        thin,
    ]
    if not visible:
        lines.append("clean")
        if skipped:
            lines.append(skipped_note(skipped))
        lines.append(rule)
        return "\n".join(lines) + "\n"

    for entry in visible:
        lines.append(entry.label())
    if skipped:
        lines.append(skipped_note(skipped))

    staged = git_text(
        repo.path,
        ["diff", "--cached", "--no-ext-diff", "--find-renames", f"-U{context}"],
    )
    unstaged = git_text(
        repo.path,
        ["diff", "--no-ext-diff", "--find-renames", f"-U{context}"],
    )
    lines.append("")
    lines.append("## staged")
    lines.append("")
    staged_body = cap_diff(staged, max_bytes).strip()
    lines.append(staged_body or "(none)")

    lines.append("")
    lines.append("## unstaged")
    lines.append("")
    unstaged_body = cap_diff(unstaged, max_bytes).strip()
    lines.append(unstaged_body or "(none)")

    lines.append("")
    lines.append("## untracked")
    lines.append("")
    untracked_body = render_untracked(repo, untracked_entries, max_bytes, include_build)
    lines.append(untracked_body or "(none)")
    lines.append(rule)
    return "\n".join(lines) + "\n"


def render_untracked(
    repo: Repo,
    entries: list[StatusEntry],
    max_bytes: int,
    include_build: bool,
) -> str:
    chunks: list[str] = []
    for entry in entries:
        for rel in expand_untracked(repo.path, entry.path):
            if skip_untracked(rel, include_build):
                continue
            path = repo.path / rel
            try:
                data = path.read_bytes()
            except OSError as exc:
                chunks.append(f"# could not read {rel}: {exc}")
                continue
            if looks_binary(data):
                chunks.append(f"# binary file {rel} ({len(data)} bytes) — contents omitted")
                continue
            chunks.append(new_file_diff(rel, data, max_bytes).rstrip("\n"))
    return "\n\n".join(chunks)


def summary_line(repo: Repo, text: str) -> str:
    for line in text.splitlines():
        if " staged, " in line and " unstaged, " in line:
            return f"# {repo.name}: {line}"
    return f"# {repo.name}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ape-code-diff.py",
        description=(
            "Print staged, unstaged, and untracked diffs for APE checkouts, "
            "grouped by module, as plain text for an LLM."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  ./ape-code-diff.py Ape.Module.CUAS Ape.Module.CuasDataChallenge \\
      Ape.Module.Spatial Math Ape.Module.Geo Ape.Module.Experiments

  ./ape-code-diff.py Ape.Core Ape.Module.CUAS

  ./ape-code-diff.py --list

  ./ape-code-diff.py -o - Ape.Module.CUAS

On a terminal the full text is written to ape-code-diff.txt in the
skeleton root (the scrollback would drop the start). A pipe still
prints the full text on stdout. -o - forces stdout. -o PATH picks a file.

Names may be the checkout folder, the short name (CUAS, Core, Maths),
or a unique prefix (Math matches Ape.Module.Maths).
Untracked files under bin/, obj/, and similar build folders are omitted.
""",
    )
    parser.add_argument(
        "modules",
        nargs="*",
        help="checkout names, short names, or unique prefixes",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list checkouts and exit",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=12,
        metavar="N",
        help="unified-diff context lines (default: 12)",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=0,
        metavar="N",
        help="cap each file diff at N bytes (0 = full text, the default)",
    )
    parser.add_argument(
        "--include-build",
        action="store_true",
        help="include untracked files under bin/, obj/, and other build folders",
    )
    parser.add_argument(
        "-o",
        "--out",
        metavar="PATH",
        help="write the full diff to PATH, or '-' for stdout. "
        "Default: ape-code-diff.txt on a terminal, stdout when piped",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.context < 0 or args.max_bytes < 0:
        print("context and max-bytes must be >= 0", file=sys.stderr)
        return 2

    repos = discover()
    if args.list:
        for repo in repos:
            mark = "git" if (repo.path / ".git").exists() else "no-git"
            print(f"{repo.name}\t{mark}\t{rel_to_root(repo.path)}")
        return 0
    if not args.modules:
        print("name at least one checkout. known:", file=sys.stderr)
        for repo in repos:
            mark = "git" if (repo.path / ".git").exists() else "no-git"
            print(f"{repo.name}\t{mark}\t{rel_to_root(repo.path)}", file=sys.stderr)
        return 2

    try:
        selected = resolve_specs(args.modules, repos)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2

    rendered: list[tuple[Repo, str]] = []
    failed = False
    columns = terminal_columns()
    for repo in selected:
        try:
            rendered.append(
                (
                    repo,
                    render_repo(
                        repo,
                        args.context,
                        args.max_bytes,
                        args.include_build,
                        columns,
                    ),
                )
            )
        except RuntimeError as exc:
            failed = True
            print(f"{repo.name}: {exc}", file=sys.stderr)

    report = compose_report(rendered)
    write_stdout = args.out == "-" or (args.out is None and not sys.stdout.isatty())
    if write_stdout:
        sys.stdout.write(report)
        return 1 if failed else 0

    target = ROOT / "ape-code-diff.txt" if args.out is None else Path(args.out).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8")
    print(f"full diff: {target} ({report.count(chr(10))} lines)")
    for repo, text in rendered:
        print(summary_line(repo, text)[2:])
    return 1 if failed else 0


def compose_report(rendered: list[tuple[Repo, str]]) -> str:
    when = datetime.now().astimezone().isoformat(timespec="seconds")
    parts = [f"# ape-code-diff  {when}\n", LEGEND, "\n"]
    if rendered:
        parts.append("# " + ", ".join(repo.name for repo, _ in rendered) + "\n")
    for repo, text in rendered:
        parts.append(summary_line(repo, text) + "\n")
    parts.append("\n")
    for _, text in rendered:
        parts.append(text if text.endswith("\n") else text + "\n")
        parts.append("\n")
    return "".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
