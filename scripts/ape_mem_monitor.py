#!/usr/bin/env python3
"""APE macOS live memory monitor. Standard library only.

Discovers running APE hosts and reads each peer's name and network role
from the config that process was started with. C# (`Ape.Launcher` or any
apphost under `dotnet`), Rust (`ape-host`), and any other process whose
arguments include an APE config are tracked the same way.
"""
import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from collections import deque

ESC = '\033['
PALETTE = ['36', '32', '33', '35', '34', '31', '96', '92', '93', '95', '94', '91']
SPARK = '▁▂▃▄▅▆▇█'
UNITS = {'B': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4}
LSOF = '/usr/sbin/lsof' if os.path.exists('/usr/sbin/lsof') else 'lsof'

# `dotnet run` / `cargo` keep the config on their command line, but the
# resident set that matters is the child host. Editors and language servers
# also get handed json paths and are not instances.
DOTNET_WRAPPERS = {
    'run', 'build', 'test', 'publish', 'msbuild', 'restore', 'clean',
    'watch', 'new', 'tool', 'pack', 'format', 'workload', 'nuget',
}
DENY_BASE = {
    'cargo', 'rustc', 'zsh', 'bash', 'sh', 'fish', 'node', 'git', 'rg',
    'vim', 'vi', 'nvim', 'emacs', 'code', 'cursor', 'electron', 'less',
    'cat', 'jq', 'make', 'lldb', 'lldb-rpc-server', 'osascript',
}
DENY_CMD = (
    'languageserver', 'msbuild', 'vbcscompiler', 'extension-host',
    'ape_mem_monitor', 'wallpaperagent', 'skycomputeruse',
)


def parse_size(s):
    m = re.search(r'([\d,.]+)\s*([BKMGT])(?:i?B)?\b', s, re.I)
    if not m:
        return None
    return float(m.group(1).replace(',', '')) * UNITS[m.group(2).upper()] / 1024**2


# PID of the ps/vmmap/lsof child currently in communicate(). The stop handler
# SIGKILLs it so Ctrl+C does not sit in subprocess.run() until timeout and
# then surface as TimeoutExpired (the child shares the terminal's SIGINT).
_child_pid = None
_stop = False
_cwd_cache = {}
_identity_cache = {}


def _on_stop(signum, frame):
    global _stop
    if _stop:
        # Second Ctrl+C: don't wait out a stuck sample.
        raise KeyboardInterrupt
    _stop = True
    pid = _child_pid
    if pid:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _reap(proc):
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.communicate(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run_captured(cmd, timeout):
    """Return stdout. None if the command failed to start, timed out, or Ctrl+C landed."""
    global _child_pid
    env = os.environ.copy()
    env['LC_ALL'] = 'C'
    env['LANG'] = 'C'
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            # New session: terminal Ctrl+C must not SIGINT ps/vmmap. A stuck
            # child plus the 5s/8s timeout is what turned Ctrl+C into a traceback.
            start_new_session=True,
        )
    except (FileNotFoundError, OSError):
        return None
    _child_pid = proc.pid
    try:
        try:
            out, _err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _reap(proc)
            return None
        except KeyboardInterrupt:
            _reap(proc)
            raise
    finally:
        _child_pid = None
    if _stop:
        return None
    return out


def _sleep(seconds):
    end = time.monotonic() + seconds
    while not _stop:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        # Short slices so a non-raising SIGINT handler is noticed quickly.
        # time.sleep itself retries for the full duration after EINTR (PEP 475).
        time.sleep(min(0.2, remaining))


class Inst:
    def __init__(self, pid, runtime, peer, role, start):
        self.pid = pid
        self.runtime = runtime
        self.peer = peer
        self.role = role
        self.start = start
        self.label = f'{runtime} {peer}'


def read_processes():
    """Every process: pid -> ppid, rss MiB, lstart, command. None if ps failed."""
    text = run_captured(
        ['/bin/ps', '-axo', 'pid=,ppid=,rss=,lstart=,command='], 5)
    if text is None:
        return None
    result = {}
    for line in text.splitlines():
        # lstart: Ddd Mmm dd HH:MM:SS yyyy (5 fields), then the command.
        bits = line.strip().split(None, 7)
        if len(bits) < 8 or not bits[0].isdigit() or not bits[1].isdigit():
            continue
        year, _, command = bits[7].partition(' ')
        if not command:
            continue
        try:
            rss = int(bits[2]) / 1024.0
        except ValueError:
            continue
        pid = int(bits[0])
        result[pid] = {
            'ppid': int(bits[1]),
            'rss': rss,
            'start': ' '.join(bits[3:7] + [year]),
            'command': command,
            'argv0': command.split(None, 1)[0],
        }
    return result


def read_cwds(pids):
    if not pids:
        return {}
    text = run_captured(
        [LSOF, '-a', '-p', ','.join(map(str, pids)), '-d', 'cwd', '-Fn'], 5)
    result = {}
    if not text:
        return result
    pid = None
    for line in text.splitlines():
        if len(line) > 1 and line[0] == 'p' and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith('n') and pid is not None:
            result[pid] = line[1:]
            pid = None
    return result


def _basename(path):
    return os.path.basename(path).lower()


def _denied(argv0, command):
    base = _basename(argv0)
    if base in DENY_BASE or base.startswith('python'):
        return True
    low = command.lower()
    return any(token in low for token in DENY_CMD)


def _dotnet_wrapper(argv0, command):
    if _basename(argv0) not in ('dotnet', 'dotnet.exe'):
        return False
    for tok in command.split()[1:]:
        if tok.startswith('-'):
            continue
        return tok.lower() in DOTNET_WRAPPERS
    return True


def _looks_like_host(argv0):
    base = os.path.basename(argv0)
    low = base.lower()
    if low in ('ape-host', 'ape-host.exe', 'ape.launcher', 'ape.launcher.dll', 'ape.launcher.exe'):
        return True
    return low.startswith('ape-') or low.startswith('ape.') or base.startswith('Ape.')


def _runtime(argv0, ppid, procs):
    low = _basename(argv0)
    if low in ('ape-host', 'ape-host.exe'):
        return 'Rust'
    if low in ('ape.launcher', 'ape.launcher.dll', 'ape.launcher.exe', 'dotnet', 'dotnet.exe'):
        return 'C#'
    parent = procs.get(ppid)
    if parent is not None and _basename(parent['argv0']) in ('dotnet', 'dotnet.exe'):
        return 'C#'
    if low.startswith('ape-') or low.startswith('ape.'):
        return os.path.basename(argv0)
    return os.path.basename(argv0)


def _is_ape_config(doc):
    if not isinstance(doc, dict):
        return False
    modules = doc.get('modules')
    if isinstance(modules, dict) and any(str(key).startswith('Ape.') for key in modules):
        return True
    return isinstance(doc.get('network'), dict)


def _load_config(path):
    try:
        if os.path.getsize(path) > 2_000_000:
            return None
        with open(path, 'r', encoding='utf-8-sig') as handle:
            doc = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError, ValueError):
        return None
    if not _is_ape_config(doc):
        return None
    return doc


def _peer_and_role(doc, config_path):
    """Network role defaults to server, matching both hosts when the key is absent."""
    net = None
    modules = doc.get('modules') if isinstance(doc, dict) else None
    if isinstance(modules, dict) and isinstance(modules.get('Ape.Core.Network'), dict):
        net = modules['Ape.Core.Network']
    elif isinstance(doc.get('network'), dict):
        net = doc['network']
    peer = ''
    role = ''
    if isinstance(net, dict):
        peer = str(net.get('peerName') or '').strip()
        role = str(net.get('role') or '').strip()
    if not peer and config_path:
        peer = Path(config_path).stem
    if not peer:
        peer = 'unnamed'
    if not role:
        role = 'server'
    return peer, role


def _json_tokens(command):
    tokens = []
    for tok in command.split()[1:]:
        cleaned = tok.strip('"\'')
        if cleaned.lower().endswith('.json'):
            tokens.append(cleaned)
    return tokens


def _resolve_config(token, cwd):
    path = Path(token).expanduser()
    if not path.is_absolute():
        if not cwd:
            return None
        path = Path(cwd) / path
    try:
        if not path.is_file():
            return None
        return str(path.resolve())
    except OSError:
        return None


def discover(procs, only_pids=None):
    """APE instances visible in `procs`. Identity is cached for the life of the pid."""
    prelim = []
    need_cwd = []
    for pid, proc in procs.items():
        if only_pids is not None and pid not in only_pids:
            continue
        argv0 = proc['argv0']
        command = proc['command']
        if _dotnet_wrapper(argv0, command) or _denied(argv0, command):
            continue
        cached = _identity_cache.get((pid, proc['start']))
        if cached is not None:
            prelim.append((pid, proc, None, True))
            continue
        jsons = _json_tokens(command)
        host = _looks_like_host(argv0) or (
            _basename(argv0) in ('dotnet', 'dotnet.exe')
            and any(_basename(tok) == 'ape.launcher.dll' for tok in command.split())
        )
        if not jsons and not host:
            continue
        if any(not os.path.isabs(tok) for tok in jsons) and pid not in _cwd_cache:
            need_cwd.append(pid)
        prelim.append((pid, proc, jsons, host))
    if need_cwd and not _stop:
        _cwd_cache.update(read_cwds(need_cwd))

    found = []
    for pid, proc, jsons, host in prelim:
        cached = _identity_cache.get((pid, proc['start']))
        if cached is not None:
            found.append(cached)
            continue
        peer, role, config_ok = 'unnamed', '?', False
        cwd = _cwd_cache.get(pid, '')
        for tok in jsons or []:
            path = _resolve_config(tok, cwd)
            if path is None:
                continue
            doc = _load_config(path)
            if doc is None:
                continue
            peer, role = _peer_and_role(doc, path)
            config_ok = True
            break
        if not config_ok and not host:
            continue
        inst = Inst(pid, _runtime(proc['argv0'], proc['ppid'], procs), peer, role, proc['start'])
        # A relative config can fail once if cwd is not known yet. Retry next scan.
        if config_ok or not jsons:
            _identity_cache[(pid, proc['start'])] = inst
        found.append(inst)
    return found


def read_footprint(pid):
    """Physical footprint reported by vmmap; NOT private resident memory."""
    text = run_captured(['/usr/bin/vmmap', '-summary', str(pid)], 8)
    if not text:
        return None
    match = re.search(r'^Physical footprint:\s*(.+)$', text, re.MULTILINE | re.IGNORECASE)
    return parse_size(match.group(1)) if match else None


def slope_and_r2(points):
    """Points are (monotonic seconds, MiB). slope returned in MiB/min."""
    if len(points) < 6 or points[-1][0] - points[0][0] < 20:
        return None, None
    x0 = points[0][0]
    xs = [(p[0] - x0) / 60 for p in points]
    ys = [p[1] for p in points]
    mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
    vx = sum((v-mx)**2 for v in xs)
    vy = sum((v-my)**2 for v in ys)
    if vx == 0:
        return None, None
    cov = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    slope = cov/vx
    r2 = (cov*cov/(vx*vy)) if vy > 1e-12 else 0
    return slope, r2


def spark(values, width, lo, hi):
    if not values:
        return ' ' * width
    if width < 1:
        return ''
    if len(values) > width:
        # Preserve the sampled time span by dividing it into buckets.
        vs = [values[min(len(values)-1, int((i+.5)*len(values)/width))] for i in range(width)]
    else:
        vs = values
    d = max(hi-lo, 1e-9)
    chars = ''.join(SPARK[min(7, max(0, int((v-lo)/d * 7)))] for v in vs)
    return chars.rjust(width)


def csv_path_for_run(raw):
    """Insert a local timestamp so each run writes a new file."""
    path = Path(raw).expanduser()
    stamp = dt.datetime.now().astimezone().strftime('%Y-%m-%d_%H%M%S')
    candidate = path.with_name(f'{path.stem}_{stamp}{path.suffix}')
    n = 2
    while candidate.exists():
        candidate = path.with_name(f'{path.stem}_{stamp}_{n}{path.suffix}')
        n += 1
    return candidate


def _clip(text, width):
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[:width - 1] + '…'


def main():
    p = argparse.ArgumentParser(
        description='APE live RSS/physical footprint tracking for macOS. '
                    'Discovers APE processes and reads peer name and network role from each config.')
    p.add_argument('--pid', type=int, action='append', default=None,
                   help='only track this pid (repeatable). Default: every APE process')
    p.add_argument('--interval', type=float, default=1, help='RSS sampling interval in seconds')
    p.add_argument('--footprint-every', type=float, default=60, help='vmmap sampling interval (seconds); 0=off')
    p.add_argument('--window', type=int, default=300, help='rolling window in seconds')
    p.add_argument('--csv', default='ape_memory.csv',
                   help='CSV path; a second-resolution timestamp is inserted before the extension')
    p.add_argument('--stage-file', default='/tmp/ape-memory-stage', help='file containing current benchmark stage')
    a = p.parse_args()
    if a.interval <= 0 or a.window < 20 or a.footprint_every < 0:
        p.error('interval > 0, window >= 20, footprint-every >= 0 required')
    only = set(a.pid) if a.pid else None
    maxpoints = max(100, int(a.window / a.interval * 1.5) + 10)
    order = []
    info = {}
    history = {}
    footprint = {}
    peak = {}
    proc_starts = {}
    active = {}
    global _stop
    _stop = False
    signal.signal(signal.SIGINT, _on_stop)
    signal.signal(signal.SIGTERM, _on_stop)
    path = csv_path_for_run(a.csv)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run_loop(a, only, maxpoints, order, info, history, footprint, peak, proc_starts, active, path)
    except KeyboardInterrupt:
        pass
    print(f'\nStopped. Saved CSV: {path}')


def _ensure(order, info, history, footprint, peak, proc_starts, active, inst, maxpoints):
    if inst.pid not in info:
        order.append(inst.pid)
        history[inst.pid] = deque(maxlen=maxpoints)
        footprint[inst.pid] = None
        peak[inst.pid] = 0
    elif proc_starts.get(inst.pid) not in (None, inst.start):
        history[inst.pid].clear()
        peak[inst.pid] = 0
        footprint[inst.pid] = None
    info[inst.pid] = inst
    proc_starts[inst.pid] = inst.start
    active[inst.pid] = True


def _run_loop(a, only, maxpoints, order, info, history, footprint, peak, proc_starts, active, path):
    last_fp = -1e10
    with path.open('a', newline='', buffering=1) as f:
        writer = csv.writer(f)
        writer.writerow([
            'timestamp', 'stage', 'instance', 'runtime', 'peer', 'role', 'pid',
            'rss_mib', 'physical_footprint_mib', 'rss_peak_mib', 'rss_slope_mib_per_min', 'trend_r2',
        ])
        started = time.monotonic()
        next_tick = started
        while not _stop:
            now = time.monotonic()
            iso = dt.datetime.now().astimezone().isoformat(timespec='seconds')
            try:
                stage = Path(a.stage_file).read_text().strip()[:60]
                if not stage:
                    stage = 'unlabeled'
            except OSError:
                stage = 'unlabeled'
            procs = read_processes()
            if _stop:
                break
            scanned = procs is not None
            if scanned:
                found = discover(procs, only)
                found_pids = {inst.pid for inst in found}
                fresh = [inst for inst in found if inst.pid not in info]
                fresh.sort(key=lambda inst: (inst.runtime, inst.peer, inst.pid))
                for inst in fresh:
                    _ensure(order, info, history, footprint, peak, proc_starts, active, inst, maxpoints)
                fresh_pids = {inst.pid for inst in fresh}
                for inst in found:
                    if inst.pid not in fresh_pids:
                        _ensure(order, info, history, footprint, peak, proc_starts, active, inst, maxpoints)
                for pid in order:
                    if pid not in found_pids:
                        active[pid] = False
                for pid in order:
                    if not active.get(pid):
                        continue
                    sample = procs.get(pid)
                    if not sample:
                        active[pid] = False
                        continue
                    history[pid].append((now, sample['rss']))
                    peak[pid] = max(peak[pid], sample['rss'])
            if _stop:
                break
            fp_due = a.footprint_every > 0 and scanned and now - last_fp >= a.footprint_every
            if fp_due:
                last_fp = now
            if scanned:
                for pid in order:
                    inst = info[pid]
                    sample_ok = active.get(pid) and pid in procs
                    if fp_due and sample_ok and not _stop:
                        footprint[pid] = read_footprint(pid)
                    if _stop:
                        break
                    windowed = [(t, v) for t, v in history[pid] if now - t <= a.window]
                    slope, r2 = slope_and_r2(windowed)
                    rss = procs[pid]['rss'] if sample_ok else None
                    writer.writerow([
                        iso, stage, inst.label, inst.runtime, inst.peer, inst.role, pid,
                        f'{rss:.3f}' if rss is not None else '',
                        f'{footprint[pid]:.3f}' if footprint[pid] is not None and sample_ok else '',
                        f'{peak[pid]:.3f}' if sample_ok else '',
                        f'{slope:.5f}' if slope is not None and sample_ok else '',
                        f'{r2:.4f}' if r2 is not None and sample_ok else '',
                    ])
            if _stop:
                break
            labels = [info[pid].label for pid in order]
            dupes = {label for label in labels if labels.count(label) > 1}

            def shown(pid):
                inst = info[pid]
                if inst.label in dupes:
                    return f'{inst.label} #{pid}'
                return inst.label

            name_w = 22
            role_w = 6
            if order:
                name_w = min(36, max(name_w, max(len(shown(pid)) for pid in order)))
                role_w = min(16, max(role_w, max(len(info[pid].role) for pid in order)))
            width = max(16, min(88, shutil.get_terminal_size((110, 30)).columns - 14))
            allvals = [v for pid in order if active.get(pid) for t, v in history[pid] if now - t <= a.window]
            lo = min(allvals) if allvals else 0
            hi = max(allvals) if allvals else 1
            if hi - lo < 1:
                mid = (hi + lo) / 2
                lo, hi = mid - .5, mid + .5
            watched = 'selected pids' if only else 'every APE process'
            lines = [
                f'APE LIVE MEMORY | {iso} | Window: {a.window}s | Stage: {stage}',
                f'RSS every {a.interval:g}s | vmmap every {a.footprint_every:g}s | {watched} | CSV: {path}',
                'Physical footprint (PF) includes compressed memory; it is NOT Private RSS.',
                'Peer name and role are read from each process config (Ape.Core.Network).',
                '',
                f'{"INSTANCE":<{name_w}} {"ROLE":<{role_w}} {"RSS MiB":>7} {"PF MiB":>7} {"PEAK MiB":>8}  {"TREND MiB/min":>12}  R²',
            ]
            if not order:
                lines.append('  waiting for an APE host (Ape.Launcher, ape-host, or any process with an APE config)')
            for index, pid in enumerate(order):
                inst = info[pid]
                pts = [(t, v) for t, v in history[pid] if now - t <= a.window]
                sl, r2 = slope_and_r2(pts)
                alive = active.get(pid)
                rss = pts[-1][1] if pts and alive else None

                def fmt(value):
                    return f'{value:7.2f}' if value is not None else '    N/A'

                head = (
                    f'{_clip(shown(pid), name_w):<{name_w}} {_clip(inst.role, role_w):<{role_w}} '
                    f'{fmt(rss)} {fmt(footprint[pid] if alive else None)} {fmt(peak[pid] if alive else None)} '
                )
                if alive and sl is not None:
                    line = head + f'{sl:+12.3f}'
                else:
                    line = head + f'{"N/A":>12}'
                if alive and r2 is not None:
                    line += f'  {r2:.2f}'
                else:
                    line += '   -'
                lines.append(line)
                vals = [v for t, v in pts] if alive else []
                bar = spark(vals, width, lo, hi)
                if sys.stdout.isatty():
                    color = PALETTE[index % len(PALETTE)]
                    bar = f'{ESC}{color}m{bar}{ESC}0m'
                lines.append(f'  {pid:<8} | {bar}')
            lines += [
                '',
                f'Shared chart scale: {lo:.2f} ... {hi:.2f} MiB | newest sample on right',
                'Trend: linear regression over rolling window; R² measures linear fit, NOT leak certainty.',
                f'Change test stage: echo 1000 > {a.stage_file}  (e.g. idle, 100, 1000, 10000, cleanup)',
                'Ctrl+C to stop. Missing PF = vmmap unavailable/permission denied/timeout.',
            ]
            if sys.stdout.isatty():
                sys.stdout.write('\033[H\033[2J' + '\n'.join(lines) + '\n')
            else:
                sys.stdout.write('\n'.join(lines) + '\n')
            sys.stdout.flush()
            next_tick += a.interval
            remaining = next_tick - time.monotonic()
            if remaining > 0:
                _sleep(remaining)
            else:
                next_tick = time.monotonic()


if __name__ == '__main__':
    main()
