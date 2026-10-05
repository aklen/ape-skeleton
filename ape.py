#!/usr/bin/env python3
"""
ApeCore CLI Tool
A unified command-line interface for building, running, and managing ApeCore.
"""

import os
import sys
import subprocess
import shutil
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence


def iter_module_dirs() -> List[Path]:
    """Checked-out module folders under src/Ape.Modules/Ape.Module.*."""
    modules_root = ROOT_DIR / "src" / "Ape.Modules"
    if not modules_root.is_dir():
        return []
    return sorted(
        p
        for p in modules_root.iterdir()
        if p.is_dir() and p.name.startswith("Ape.Module.")
    )


def iter_plugin_csprojs() -> List[Path]:
    """Plugin projects: Ape.Modules/*/Plugins and Ape.Core/*/Plugins (replica demos)."""
    found = list(ROOT_DIR.glob("src/Ape.Modules/**/Plugins/**/*.Plugin.*.csproj"))
    found.extend(ROOT_DIR.glob("src/Ape.Core/**/Plugins/**/*.Plugin.*.csproj"))
    return sorted(set(found))


def plugin_short_name(csproj: Path) -> str:
    """Ape.Module.UbloxF9.Plugin.UbloxF9 → UbloxF9."""
    stem = csproj.stem
    marker = ".Plugin."
    idx = stem.rfind(marker)
    return stem[idx + len(marker) :] if idx >= 0 else stem


def get_plugin_csproj(plugin_name: str) -> Optional[Path]:
    """Resolve a plugin by short name or full csproj stem."""
    for path in iter_plugin_csprojs():
        if path.stem == plugin_name or plugin_short_name(path) == plugin_name:
            return path
    return None


def is_ape_core_artifact_name(target: str) -> bool:
    return target == "Ape.Core"


def get_ape_module_path(module_spec: str) -> Optional[Path]:
    """e.g. Ape.Module.DeviceManager -> src/Ape.Modules/Ape.Module.DeviceManager if that folder exists."""
    if not module_spec.startswith("Ape.Module.") or module_spec == "Ape.Module.":
        return None
    p = ROOT_DIR / "src" / "Ape.Modules" / module_spec
    return p if p.is_dir() else None


def is_ape_module_artifact_name(target: str) -> bool:
    return get_ape_module_path(target) is not None

class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    BOLD = '\033[1m'
    NC = '\033[0m'  # No Color

# Configuration: default Release (matches historical ./ape behavior). Override for Debug: APE_BUILD_CONFIGURATION=Debug
CONFIGURATION = os.environ.get("APE_BUILD_CONFIGURATION", "Release")
ROOT_DIR = Path(__file__).parent.absolute()
WORKSPACE_YAML = ROOT_DIR / "workspace.yaml"
BUILD_DIR = ROOT_DIR / "build"
OUTPUT_DIR = BUILD_DIR / "bin" / "Ape.Launcher" / CONFIGURATION / "net9.0"
LAUNCHER_CSPROJ = ROOT_DIR / "src" / "Ape.Launcher" / "Ape.Launcher.csproj"


def print_header(message: str):
    """Print colored header."""
    print(f"{Colors.CYAN}{Colors.BOLD}=== {message} ==={Colors.NC}", flush=True)


def print_success(message: str):
    """Print success message."""
    print(f"{Colors.GREEN}✓ {message}{Colors.NC}")


def print_error(message: str):
    """Print error message."""
    print(f"{Colors.RED}✗ {message}{Colors.NC}")


def print_info(message: str):
    """Print info message."""
    print(f"{Colors.BLUE}{message}{Colors.NC}")


def print_warning(message: str):
    """Print warning message."""
    print(f"{Colors.YELLOW}{message}{Colors.NC}")


def run_command(cmd: List[str], cwd: Optional[Path] = None, quiet: bool = False) -> int:
    """Run a shell command and return exit code."""
    if not quiet:
        print_info(f"Running: {' '.join(cmd)}")
    
    result = subprocess.run(
        cmd,
        cwd=cwd or ROOT_DIR,
        capture_output=quiet,
        text=True
    )
    return result.returncode


def clean(args):
    """Clean build artifacts."""
    print_header("Cleaning Build Artifacts")
    print_info(f"Configuration: {CONFIGURATION} (set APE_BUILD_CONFIGURATION to override)")
    
    # Run dotnet clean on the solution so all projects match CONFIGURATION
    sln = ROOT_DIR / "ApeCore.sln"
    clean_cmd = ["dotnet", "clean", "-c", CONFIGURATION]
    if sln.exists():
        clean_cmd.append(str(sln))
    run_command(clean_cmd)
    
    # Remove build directory
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
        print_success(f"Removed {BUILD_DIR.relative_to(ROOT_DIR)}")
    
    # Remove any stray bin/obj in source directories (just in case)
    for path in list(ROOT_DIR.glob("src/**/bin")) + list(ROOT_DIR.glob("src/**/obj")):
        if path.is_dir():
            shutil.rmtree(path)
            print_success(f"Removed {path.relative_to(ROOT_DIR)}")
    
    print_success("Clean complete")


def restore(quiet: bool = False):
    """Restore NuGet dependencies."""
    if not quiet:
        print_info("Restoring dependencies...")
    exit_code = run_command(["dotnet", "restore"], quiet=quiet)
    if exit_code == 0 and not quiet:
        print_success("Restore complete")
    return exit_code


def build_core(quiet: bool = False):
    """Build the launcher (globs modules/plugins). The .sln is IDE-only and may omit checkouts."""
    if not quiet:
        print_info(f"Building launcher (-c {CONFIGURATION})...")
    cmd = ["dotnet", "build", "-c", CONFIGURATION]
    if LAUNCHER_CSPROJ.exists():
        cmd.append(str(LAUNCHER_CSPROJ))
    else:
        sln = ROOT_DIR / "ApeCore.sln"
        if sln.exists():
            cmd.append(str(sln))
    exit_code = run_command(cmd, quiet=quiet)
    if exit_code == 0 and not quiet:
        print_success("Build complete")
    return exit_code


def build_plugin_from_csproj(plugin_csproj: Path, label: Optional[str] = None, quiet: bool = True) -> bool:
    """Build one plugin project. Launcher ProjectReference copies the DLL next to the process."""
    display = label or plugin_short_name(plugin_csproj)
    print_info(f"  Building {display}...")
    if run_command(
        ["dotnet", "build", "-c", CONFIGURATION, str(plugin_csproj)],
        quiet=quiet,
    ) != 0:
        print_error(f"  {display} (build failed)")
        return False
    print_success(f"  {display}")
    return True


def build_plugin(plugin_name: str, quiet: bool = True) -> bool:
    """Build a plugin resolved by short name or full csproj stem."""
    plugin_csproj = get_plugin_csproj(plugin_name)
    if plugin_csproj is None:
        print_error(f"Plugin not found: {plugin_name}")
        return False
    return build_plugin_from_csproj(plugin_csproj, label=plugin_name, quiet=quiet)


def build_ape_module(module_spec: str, quiet: bool = True) -> bool:
    """Build the module library and every plugin under src/Ape.Modules/<module_spec>/Plugins/."""
    mod_dir = get_ape_module_path(module_spec)
    if mod_dir is None:
        print_error(f"Module not found: {module_spec} (expected {ROOT_DIR / 'src' / 'Ape.Modules' / module_spec})")
        return False

    main_csproj = mod_dir / f"{module_spec}.csproj"
    if main_csproj.exists():
        if run_command(
            ["dotnet", "build", "-c", CONFIGURATION, str(main_csproj)],
            quiet=quiet
        ) != 0:
            print_error(f"  {module_spec} (library build failed)")
            return False
        print_success(f"  {module_spec} (library)")

    plugin_csprojs = sorted(mod_dir.glob("Plugins/**/*.csproj"))
    for csproj in plugin_csprojs:
        if not build_plugin_from_csproj(csproj, quiet=quiet):
            return False
    if not plugin_csprojs and not main_csproj.exists():
        print_warning(f"  {module_spec}: no {module_spec}.csproj and no Plugins/**/*.csproj")
    return True


def build(args):
    """Build launcher (glob modules + plugins) and optional extra targets."""
    build_core_flag = False
    build_plugins_flag = False
    specific_plugins: List[str] = []
    module_artifacts: List[str] = []

    if not args.targets:
        build_core_flag = True
        print_header("Building: launcher (checked-out modules + plugins)")
    else:
        for target in args.targets:
            if target.lower() == "core" or is_ape_core_artifact_name(target):
                build_core_flag = True
            elif is_ape_module_artifact_name(target):
                if target not in module_artifacts:
                    module_artifacts.append(target)
            elif target.lower() == "plugins":
                build_plugins_flag = True
            elif get_plugin_csproj(target) is not None:
                if target not in specific_plugins:
                    specific_plugins.append(target)
            else:
                if target.lower() == "services":
                    print_error("services/ is no longer a separate publish step")
                    print_info("Modules are Ape.Module.* under src/Ape.Modules; launcher ProjectReference copies them next to the process")
                elif target.startswith("Ape.Module."):
                    print_error(f"Unknown target: {target} (no {ROOT_DIR / 'src' / 'Ape.Modules' / target})")
                else:
                    print_error(f"Unknown target: {target}")
                    plugins = ", ".join(plugin_short_name(p) for p in iter_plugin_csprojs()) or "(none checked out)"
                    print_info(f"Plugins: {plugins}")
                print_info(f"Also supported: {Colors.CYAN}Ape.Core{Colors.NC} and {Colors.CYAN}Ape.Module.<Name>{Colors.NC}")
                sys.exit(1)

        parts = []
        if build_core_flag:
            parts.append("Ape.Core")
        for m in module_artifacts:
            parts.append(m)
        if build_plugins_flag:
            parts.append("All plugins")
        elif specific_plugins:
            parts.append(f"Plugins: {', '.join(specific_plugins)}")
        print_header(f"Building: {' + '.join(parts)}")

    print()

    if restore() != 0:
        print_error("Restore failed")
        sys.exit(1)

    if build_core_flag or build_plugins_flag:
        if build_core() != 0:
            print_error("Launcher / solution build failed")
            sys.exit(1)

    for module_spec in module_artifacts:
        print()
        print_info(f"Building module {module_spec}...")
        if not build_ape_module(module_spec):
            sys.exit(1)

    if specific_plugins:
        print()
        print_info("Building plugins...")
        built_count = sum(1 for plugin in specific_plugins if build_plugin(plugin))
        print()
        if built_count != len(specific_plugins):
            print_warning(f"Built {built_count}/{len(specific_plugins)} plugins (some failed)")
            sys.exit(1)
        print_success(f"Built {built_count}/{len(specific_plugins)} plugins")

    if (module_artifacts or specific_plugins) and not (build_core_flag or build_plugins_flag):
        print()
        print_info("Refreshing launcher output...")
        if build_core() != 0:
            print_error("Launcher build failed")
            sys.exit(1)

    print()
    print_header("Build Complete")
    print_info(f"Process output: {OUTPUT_DIR.relative_to(ROOT_DIR)}")
    print_info(f"Run with: {Colors.YELLOW}./ape run{Colors.NC}")


def run_apecore(args):
    """Run ApeCore with optional config."""
    print_header("Running ApeCore")
    print()
    
    cmd = ["dotnet", "run", "--project", str(LAUNCHER_CSPROJ), "-c", CONFIGURATION]
    
    if args.config:
        cmd.append("--")
        cmd.append(args.config)
    
    # macOS: Set DYLD_FALLBACK_LIBRARY_PATH for libmsquic (QUIC transport)
    env = os.environ.copy()
    if sys.platform == "darwin":  # macOS
        try:
            brew_prefix = subprocess.run(
                ["brew", "--prefix"],
                capture_output=True,
                text=True,
                check=True
            ).stdout.strip()
            
            lib_path = f"{brew_prefix}/lib"
            current_path = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
            
            if current_path:
                env["DYLD_FALLBACK_LIBRARY_PATH"] = f"{current_path}:{lib_path}"
            else:
                env["DYLD_FALLBACK_LIBRARY_PATH"] = lib_path
            
            print_info(f"macOS: Set DYLD_FALLBACK_LIBRARY_PATH={env['DYLD_FALLBACK_LIBRARY_PATH']}")
            print()
            sys.stdout.flush()  # Force flush before dotnet takes over
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Homebrew not installed or brew command failed
            pass
    
    # Popen + explicit wait on Ctrl+C: otherwise Python exits on KeyboardInterrupt while dotnet
    # is still shutting down, the shell redraws the prompt, and late host logs appear after it.
    proc = subprocess.Popen(cmd, cwd=ROOT_DIR, env=env)
    try:
        proc.wait()
    except KeyboardInterrupt:
        print()
        print_info("Shutdown requested")
        sys.stdout.flush()
        if proc.poll() is None:
            try:
                proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


def publish(args):
    """Publish ApeCore for distribution."""
    runtime = args.runtime or "linux-x64"
    
    print_header(f"Publishing for {runtime}")
    
    if restore() != 0:
        print_error("Restore failed")
        sys.exit(1)
    
    cmd = [
        "dotnet", "publish", str(LAUNCHER_CSPROJ),
        "-c", CONFIGURATION,
        "-r", runtime,
        "--self-contained", "false"
    ]
    
    exit_code = run_command(cmd)
    
    if exit_code == 0:
        print_success("Publish complete")
    else:
        print_error("Publish failed")
        sys.exit(1)


@dataclass(frozen=True)
class WorkspaceEntry:
    """One checkout from workspace.yaml (core, launcher, or module)."""

    role: str
    name: str
    url: str
    ref: str
    path: str

    def dest(self, root: Path) -> Path:
        """Disk path: yaml path + name, unless path already ends with name."""
        base = Path(self.path)
        if base.name == self.name:
            return (root / base).resolve()
        return (root / base / self.name).resolve()


def _strip_yaml_comment(line: str) -> str:
    in_single = False
    in_double = False
    out: List[str] = []
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or line[i - 1].isspace():
                break
        out.append(ch)
    return "".join(out)


def parse_workspace_yaml(text: str) -> List[WorkspaceEntry]:
    """Minimal parser for core/launcher maps and a modules list. No PyYAML required."""
    entries: List[WorkspaceEntry] = []
    role: Optional[str] = None
    current: dict = {}

    def flush() -> None:
        nonlocal current
        if not current:
            return
        missing = [k for k in ("name", "url", "ref", "path") if not current.get(k)]
        if missing:
            raise ValueError(
                f"workspace.yaml: incomplete {role or 'entry'} (missing {', '.join(missing)})"
            )
        entries.append(
            WorkspaceEntry(
                role=role or "module",
                name=current["name"],
                url=current["url"],
                ref=current["ref"],
                path=current["path"],
            )
        )
        current = {}

    for raw in text.splitlines():
        line = _strip_yaml_comment(raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0 and stripped.endswith(":") and not stripped.startswith("-"):
            flush()
            key = stripped[:-1].strip()
            role = "module" if key == "modules" else key
            current = {}
            continue

        if stripped.startswith("- "):
            flush()
            role = "module"
            current = {}
            stripped = stripped[2:].strip()

        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        current[key.strip()] = value.strip().strip("'").strip('"')

    flush()
    if not entries:
        raise ValueError("workspace.yaml: no checkout entries")
    return entries


def load_workspace_entries(yaml_path: Path = WORKSPACE_YAML) -> List[WorkspaceEntry]:
    if not yaml_path.is_file():
        raise FileNotFoundError(f"workspace.yaml not found: {yaml_path}")
    return parse_workspace_yaml(yaml_path.read_text(encoding="utf-8"))


def _git(args: Sequence[str], cwd: Optional[Path] = None, quiet: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git", *args]
    if not quiet:
        loc = f"  (in {cwd})" if cwd else ""
        print_info(f"Running: {' '.join(cmd)}{loc}")
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=quiet)


def _git_toplevel(path: Path) -> Optional[Path]:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def _git_dirty(path: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _is_nested_clone(dest: Path) -> bool:
    """True if dest is its own git checkout, not a folder of the skeleton/monorepo."""
    if not dest.is_dir():
        return False
    top = _git_toplevel(dest)
    if top is None:
        return False
    return top == dest.resolve()


def _filter_workspace_entries(
    entries: Sequence[WorkspaceEntry], names: Optional[Sequence[str]]
) -> List[WorkspaceEntry]:
    if not names:
        return list(entries)
    wanted = set(names)
    matched = [e for e in entries if e.name in wanted]
    missing = wanted - {e.name for e in matched}
    if missing:
        known = ", ".join(e.name for e in entries)
        raise ValueError(
            f"unknown workspace name(s): {', '.join(sorted(missing))} (have: {known})"
        )
    return matched


def sync_workspace(args) -> None:
    """Clone or update checkouts listed in workspace.yaml (path + name)."""
    print_header("Workspace sync")
    try:
        entries = _filter_workspace_entries(load_workspace_entries(), args.names)
    except (OSError, ValueError) as ex:
        print_error(str(ex))
        sys.exit(1)

    dry = args.dry_run
    failed = False
    for entry in entries:
        dest = entry.dest(ROOT_DIR)
        try:
            rel = dest.relative_to(ROOT_DIR)
        except ValueError:
            rel = dest
        label = f"{entry.name} → {rel}"

        if dest.exists() and not _is_nested_clone(dest):
            print_warning(
                f"{label}: skip (folder exists in this repo; not an independent clone yet)"
            )
            continue

        if not dest.exists():
            msg = f"{label}: clone {entry.url} @ {entry.ref}"
            if dry:
                print_info(f"dry-run  {msg}")
                continue
            print_info(msg)
            dest.parent.mkdir(parents=True, exist_ok=True)
            clone = _git(["clone", "--branch", entry.ref, "--", entry.url, str(dest)])
            if clone.returncode != 0:
                if dest.exists():
                    shutil.rmtree(dest)
                clone = _git(["clone", "--", entry.url, str(dest)])
                if clone.returncode != 0:
                    print_error(f"{label}: clone failed")
                    failed = True
                    continue
                check = _git(["checkout", entry.ref], cwd=dest)
                if check.returncode != 0:
                    print_error(f"{label}: checkout {entry.ref} failed")
                    failed = True
                    continue
            print_success(f"{label}: cloned")
            continue

        if _git_dirty(dest) and not args.force:
            print_warning(f"{label}: skip (dirty working tree; commit/stash or pass --force)")
            continue

        msg = f"{label}: fetch + reset {entry.ref}"
        if dry:
            print_info(f"dry-run  {msg}")
            continue
        print_info(msg)
        fetched = _git(["fetch", "--tags", "origin"], cwd=dest)
        if fetched.returncode != 0:
            print_error(f"{label}: fetch failed")
            failed = True
            continue
        origin_ref = f"origin/{entry.ref}"
        origin_has_ref = (
            subprocess.run(
                ["git", "rev-parse", "--verify", f"{origin_ref}^{{commit}}"],
                cwd=dest,
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )
        if origin_has_ref:
            # -B + origin/ref follows rewritten history (force-push); --force discards local edits.
            checkout_cmd = ["checkout", "-B", entry.ref, origin_ref]
            if args.force:
                checkout_cmd.insert(1, "--force")
        else:
            checkout_cmd = ["checkout"]
            if args.force:
                checkout_cmd.append("--force")
            checkout_cmd.append(entry.ref)
        checked = _git(checkout_cmd, cwd=dest)
        if checked.returncode != 0:
            print_error(f"{label}: checkout {entry.ref} failed")
            failed = True
            continue
        print_success(f"{label}: at {entry.ref}")

    if failed:
        sys.exit(1)
    print()
    print_success("Sync complete" if not dry else "Dry-run complete")


def list_workspace(args):
    """List checked-out modules and plugin projects."""
    print_header("Modules")
    print()
    modules = iter_module_dirs()
    if not modules:
        print_warning("  (none under src/Ape.Modules)")
    for mod in modules:
        has_lib = (mod / f"{mod.name}.csproj").is_file()
        has_plugins = any(mod.glob("Plugins/**/*.csproj"))
        marker = f"{Colors.GREEN}✓{Colors.NC}" if has_lib or has_plugins else f"{Colors.YELLOW}?{Colors.NC}"
        note = "" if has_lib else " (plugins only)" if has_plugins else " (no csproj)"
        print(f"  {marker} {mod.name}{note}")

    print()
    print_header("Plugins")
    print()
    plugins = iter_plugin_csprojs()
    if not plugins:
        print_warning("  (none under Ape.Modules/*/Plugins or Ape.Core/*/Plugins)")
    for csproj in plugins:
        try:
            rel = csproj.relative_to(ROOT_DIR)
        except ValueError:
            rel = csproj
        print(f"  {Colors.GREEN}✓{Colors.NC} {plugin_short_name(csproj):28} {rel}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="ApeCore CLI - Build, run, and manage ApeCore",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ./ape build                      Build launcher + checked-out modules/plugins
  ./ape build Ape.Core             Same as ./ape build (solution / launcher)
  ./ape build Ape.Module.DeviceManager       That module + its Plugins/, then refresh launcher copy
  ./ape build plugins              Build launcher (glob copies all plugin DLLs)
  ./ape build UbloxF9              One plugin by short name, then refresh launcher copy
  ./ape run                        Run launcher (-c Release) with optional config after --
  ./ape run -c server-ping.json    Run with specific config
  ./ape sync                       Clone/update checkouts from workspace.yaml
  ./ape sync --dry-run             Print clone/update plan without git
  ./ape sync Ape.Core Ape.Launcher Update only named entries
  ./ape clean                      Clean build artifacts
  ./ape list                       List checked-out modules and plugins

Note: ./ape uses CONFIGURATION=Release by default (output under build/bin/Ape.Launcher/Release/...).
Plain "dotnet run" without -c uses Debug — a different output folder. Use one workflow consistently.
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Clean command
    parser_clean = subparsers.add_parser('clean', help='Clean build artifacts')
    parser_clean.set_defaults(func=clean)
    
    # Build command
    parser_build = subparsers.add_parser('build', help='Build ApeCore and plugins')
    parser_build.add_argument(
        'targets',
        nargs='*',
        help='Targets: Ape.Core, Ape.Module.<Name>, plugins, or short plugin names (UbloxF9, SmsClient, ...)',
    )
    parser_build.set_defaults(func=build)
    
    # Run command
    parser_run = subparsers.add_parser('run', help='Run ApeCore')
    parser_run.add_argument('-c', '--config', help='Path to config file')
    parser_run.set_defaults(func=run_apecore)
    
    # Publish command
    parser_publish = subparsers.add_parser('publish', help='Publish for distribution')
    parser_publish.add_argument('-r', '--runtime', help='Target runtime (e.g., linux-x64, win-x64, osx-arm64)')
    parser_publish.set_defaults(func=publish)
    
    # Sync command
    parser_sync = subparsers.add_parser(
        "sync",
        help="Clone or update remotes from workspace.yaml",
    )
    parser_sync.add_argument(
        "names",
        nargs="*",
        help="Optional checkout names (e.g. Ape.Core, Ape.Module.Elinga). Default: all.",
    )
    parser_sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be cloned or updated, without running git",
    )
    parser_sync.add_argument(
        "--force",
        action="store_true",
        help="Discard a dirty nested working tree; remotes are always reset to origin/ref",
    )
    parser_sync.set_defaults(func=sync_workspace)

    # List command
    parser_list = subparsers.add_parser('list', help='List checked-out modules and plugins')
    parser_list.set_defaults(func=list_workspace)
    
    # Parse arguments
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Execute command
    args.func(args)


if __name__ == "__main__":
    main()
