#!/usr/bin/env python3
"""Sample only a named simulator app while a finite XCTest command runs.

Use optimized simulator builds. CPU is percent of one host core, not device
energy; footprint includes system-owned allocations charged to the app. The
test emits timestamped LUMINA_PERF JSON markers for settled view windows.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import threading
import time


class Usage(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_byte * 16)] + [(name, ctypes.c_uint64) for name in (
        "user", "system", "idle_wakeups", "interrupt_wakeups", "pageins",
        "wired", "resident", "footprint", "start", "exit", "child_user",
        "child_system", "child_idle", "child_interrupt", "child_pageins",
        "child_elapsed", "read", "written",
    )]


def stop_process_group(process: subprocess.Popen[str], *, grace_seconds: int = 20,
                       kill_seconds: int = 20) -> list[str]:
    """Stop an owned process group without allowing teardown to run forever."""
    failures: list[str] = []
    for sig, seconds in ((signal.SIGINT, grace_seconds),
                         (signal.SIGTERM, grace_seconds),
                         (signal.SIGKILL, kill_seconds)):
        if process.poll() is not None:
            return failures
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return failures
        except OSError as error:
            failures.append(f"could not send {sig.name}: {error}")
            return failures
        try:
            process.wait(timeout=seconds)
            return failures
        except subprocess.TimeoutExpired:
            continue
    if process.poll() is None:
        failures.append("process group remained alive after SIGKILL")
    return failures


def summarize(samples: list[dict], markers: list[dict], settled: bool = True) -> list[dict]:
    windows = []
    ready = {}
    starts = {}
    for marker in markers:
        key = (marker["phase"], marker.get("cycle", 0))
        if marker["event"] == "begin":
            starts[key] = marker
            if not settled:
                ready[key] = marker
        elif marker["event"] == "ready" and settled:
            ready[key] = marker
        elif marker["event"] == "end" and key in ready:
            opening = ready.pop(key)
            values = [s for s in samples if opening["timestamp"] <= s["timestamp"] <= marker["timestamp"]]
            row = {"phase": key[0], "cycle": key[1], "sample_count": len(values)}
            if key in starts:
                row["workflow_ms"] = (marker["timestamp"] - starts[key]["timestamp"]) * 1000
            same_process = len(values) >= 2 and len({sample["pid"] for sample in values}) == 1
            seconds = values[-1]["timestamp"] - values[0]["timestamp"] if same_process else 0
            if same_process and seconds > 0:
                first, last = values[0], values[-1]
                row.update(
                    seconds=seconds,
                    cpu_one_core_percent=(last["cpu_ns"] - first["cpu_ns"]) / 1e9 / seconds * 100,
                    wakeups_per_second=(last["wakeups"] - first["wakeups"]) / seconds,
                    footprint_start_mib=first["footprint"] / 2**20,
                    footprint_end_mib=last["footprint"] / 2**20,
                    footprint_peak_mib=max(s["footprint"] for s in values) / 2**20,
                    rss_peak_mib=max(s["resident"] for s in values) / 2**20,
                    host_load1_median=statistics.median(s["load1"] for s in values),
                )
            windows.append(row)
    return windows


def consume_test_output(output, log_path: Path, markers: list[dict], markers_lock: threading.Lock,
                        error_event: threading.Event, errors: list[str], errors_lock: threading.Lock) -> None:
    """Copy test output and markers, turning a reader I/O failure into capture state."""
    try:
        with log_path.open("w") as log:
            for line in output:
                log.write(line)
                log.flush()
                if "LUMINA_PERF {" not in line:
                    continue
                try:
                    marker = json.loads(line[line.index("LUMINA_PERF ") + len("LUMINA_PERF "):].strip())
                    if not isinstance(marker, dict) or not {"event", "phase", "timestamp"} <= marker.keys():
                        continue
                    with markers_lock:
                        markers.append(marker)
                    print(json.dumps(marker), flush=True)
                except (ValueError, KeyError):
                    pass
    except Exception as error:
        # Includes opening the file, writing/flushing a line, and closing it. A
        # daemon thread must never be the sole observer of an ENOSPC failure.
        message = f"test output reader error: {type(error).__name__}: {error}"
        with errors_lock:
            errors.append(message)
        error_event.set()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulator", required=True)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--trace", action="store_true", help="Also attempt an app-scoped Time Profiler capture")
    parser.add_argument("--trace-template", choices=("Time Profiler", "Animation Hitches"), default="Time Profiler")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a finite test command is required after --")
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]

    def app_pid() -> int | None:
        # proc_listallpids returns a byte count. Allocate generously because the
        # process list can grow between its sizing and filling calls.
        size = max(4096, lib.proc_listallpids(None, 0) + 256)
        pids = (ctypes.c_int * size)()
        byte_count = lib.proc_listallpids(pids, ctypes.sizeof(pids))
        count = max(0, min(byte_count // ctypes.sizeof(ctypes.c_int), size))
        candidates: list[tuple[int, int]] = []
        for pid in pids[:count]:
            path = ctypes.create_string_buffer(4096)
            if lib.proc_pidpath(pid, path, len(path)) <= 0:
                continue
            value = os.fsdecode(path.value)
            if args.simulator not in value or not value.endswith(".app/" + args.executable):
                continue
            usage = Usage()
            if lib.proc_pid_rusage(pid, 2, ctypes.byref(usage)) == 0:
                candidates.append((usage.start, pid))
        return max(candidates, default=(0, None))[1]

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1, start_new_session=True)
    markers: list[dict] = []
    markers_lock = threading.Lock()
    samples: list[dict] = []
    failures: list[str] = []
    reader_error_event = threading.Event()
    reader_errors: list[str] = []
    reader_errors_lock = threading.Lock()
    timed_out = False
    interrupted = False
    reader_incomplete = False
    captured_pid: int | None = None
    current_pid: int | None = None
    observed_pids: set[int] = set()
    trace: subprocess.Popen[str] | None = None
    trace_log = None
    trace_started = None
    trace_attached_pid: int | None = None
    trace_attempted = False
    pid_change_reported = False

    def login_form_is_settled() -> bool:
        with markers_lock:
            return any(marker.get("phase") == "login_form" and marker.get("event") in {"ready", "end"}
                       for marker in markers)

    assert process.stdout is not None
    reader = threading.Thread(
        target=consume_test_output,
        args=(process.stdout, args.output / "test.log", markers, markers_lock,
              reader_error_event, reader_errors, reader_errors_lock),
        daemon=True,
    )
    reader.start()
    deadline = time.monotonic() + args.timeout
    try:
        with (args.output / "samples.jsonl").open("w") as stream:
            while process.poll() is None:
                if reader_error_event.is_set():
                    with reader_errors_lock:
                        failures.extend(message for message in reader_errors if message not in failures)
                    break
                if time.monotonic() > deadline:
                    timed_out = True
                    failures.append("native performance tour exceeded its finite budget")
                    break
                # XCTest resets the app before this settled login surface. Waiting
                # for it prevents attachment to a leftover app from a prior run.
                if not login_form_is_settled():
                    time.sleep(0.1)
                    continue
                if current_pid is None:
                    current_pid = app_pid()
                if current_pid is not None:
                    if captured_pid is None:
                        captured_pid = current_pid
                    elif current_pid != captured_pid and not pid_change_reported:
                        failures.append(f"app PID changed from {captured_pid} to {current_pid}")
                        pid_change_reported = True
                    if args.trace and not trace_attempted:
                        trace_attempted = True
                        trace_attached_pid = current_pid
                        trace_started = time.time()
                        trace_log = (args.output / "instruments.log").open("w")
                        trace = subprocess.Popen([
                            "xcrun", "xctrace", "record", "--template", args.trace_template,
                            "--device", args.simulator, "--attach", str(current_pid),
                            "--time-limit", f"{args.timeout}s", "--output", str(args.output / "app.trace"),
                        ], stdout=trace_log, stderr=subprocess.STDOUT, start_new_session=True)
                    usage = Usage()
                    if lib.proc_pid_rusage(current_pid, 2, ctypes.byref(usage)) == 0:
                        row = dict(timestamp=time.time(), pid=current_pid, cpu_ns=usage.user + usage.system,
                                   wakeups=usage.idle_wakeups + usage.interrupt_wakeups,
                                   footprint=usage.footprint, resident=usage.resident, load1=os.getloadavg()[0])
                        samples.append(row)
                        observed_pids.add(current_pid)
                        stream.write(json.dumps(row) + "\n")
                        stream.flush()
                    else:
                        current_pid = None
                time.sleep(0.5)
    except KeyboardInterrupt:
        interrupted = True
        failures.append("capture interrupted")
    except Exception as error:
        failures.append(f"capture error: {type(error).__name__}: {error}")
    finally:
        if process.poll() is None:
            failures.extend(stop_process_group(process))
        reader.join(timeout=10)
        reader_incomplete = reader.is_alive()
        with reader_errors_lock:
            captured_reader_errors = list(reader_errors)
        failures.extend(message for message in captured_reader_errors if message not in failures)
        if reader_incomplete:
            failures.append("test output reader did not finish; markers may be incomplete")
        if trace is not None and trace.poll() is None:
            failures.extend(stop_process_group(trace, grace_seconds=60))
        if trace_log is not None:
            trace_log.close()

        with markers_lock:
            captured_markers = list(markers)
        login_form_settled = login_form_is_settled()
        if not login_form_settled:
            failures.append("did not observe a settled login_form marker")
        elif captured_pid is None:
            failures.append("did not find the app process after the settled login_form marker")
        pid_mismatch = bool(captured_pid is not None and any(pid != captured_pid for pid in observed_pids))
        if pid_mismatch and not any(message.startswith("app PID changed") for message in failures):
            failures.append(f"sampled app PID differed from initial PID {captured_pid}")
        result = dict(
            exit_code=process.returncode,
            simulator=args.simulator,
            sample_count=len(samples),
            markers=captured_markers,
            windows=summarize(samples, captured_markers),
            workloads=summarize(samples, captured_markers, settled=False),
            capture=dict(
                login_form_settled=login_form_settled,
                captured_pid=captured_pid,
                observed_pids=sorted(observed_pids),
                pid_mismatch=pid_mismatch,
                timed_out=timed_out,
                interrupted=interrupted,
                incomplete_reader=reader_incomplete,
                reader_error=reader_error_event.is_set(),
                reader_errors=captured_reader_errors,
                failures=failures,
            ),
        )
        if trace is not None:
            result["instruments"] = dict(started=trace_started, attached_pid=trace_attached_pid,
                                          exit_code=trace.returncode, template=args.trace_template,
                                          path=str(args.output / "app.trace"))
        try:
            (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
        except OSError as error:
            failures.append(f"could not write capture report: {type(error).__name__}: {error}")
            print(f"Could not write capture report: {error}", file=sys.stderr, flush=True)
        else:
            print(f"Report: {args.output / 'report.json'}", flush=True)

    valid_windows = sum(window.get("sample_count", 0) >= 3 and "seconds" in window
                        for window in result["windows"])
    failed_capture = bool(failures or reader_incomplete or pid_mismatch or timed_out or interrupted)
    if interrupted:
        return 130
    return process.returncode or (0 if valid_windows and not failed_capture else 1)


if __name__ == "__main__":
    raise SystemExit(main())
