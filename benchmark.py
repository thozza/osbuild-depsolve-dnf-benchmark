#!/usr/bin/env python3
"""Benchmark osbuild-depsolve-dnf tool for peak memory consumption and runtime.

Measures each command (dump, search, depsolve) over multiple iterations,
reporting average wall-clock time and average peak memory (via memray).
Optionally profiles each command with cProfile (--profile) and supports
benchmarking the dnf5 solver (--dnf5).

Runtime and memory are measured in separate passes because memray adds
overhead that would skew timing results. cProfile runs a single pass per
command to capture the call-tree without statistical averaging.

Example usage (run from the osbuild repo checkout root):

    python3 ~/projects/depsolver-api-v2-memory/benchmark.py \
        --tool-path ./tools/osbuild-depsolve-dnf \
        --queries-dir ~/projects/depsolver-api-v2-memory \
        --api-version 2 \
        --iterations 5 \
        --pythonpath . \
        --profile \
        --dnf5
"""

import argparse
import json
import os
import pstats
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ALL_COMMANDS = ["dump", "search", "depsolve"]


def resolve_query_file(queries_dir: Path, command: str, api_version: int) -> Path:
    if api_version == 1:
        return queries_dir / f"{command}.json"
    return queries_dir / f"{command}_v2.json"


def format_bytes(nbytes: float) -> str:
    if nbytes >= 1024 ** 3:
        return f"{nbytes / 1024**3:.2f} GB"
    if nbytes >= 1024 ** 2:
        return f"{nbytes / 1024**2:.2f} MB"
    if nbytes >= 1024:
        return f"{nbytes / 1024:.2f} KB"
    return f"{nbytes:.0f} B"


def measure_runtime(tool_path: str, query_file: Path, env: dict) -> float:
    """Run the tool and return wall-clock time in seconds."""
    with open(query_file) as stdin_f:
        start = time.perf_counter()
        result = subprocess.run(
            [sys.executable, tool_path],
            stdin=stdin_f,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
        )
        elapsed = time.perf_counter() - start

    if result.returncode != 0:
        raise RuntimeError(
            f"Tool exited with code {result.returncode}: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return elapsed


def measure_peak_memory(tool_path: str, query_file: Path, env: dict, memray_bin: str) -> int:
    """Run the tool under memray and return peak memory in bytes."""
    bin_path = None
    stats_path = None
    try:
        bin_fd, bin_path = tempfile.mkstemp(suffix=".bin", prefix="memray_")
        os.close(bin_fd)
        stats_fd, stats_path = tempfile.mkstemp(suffix=".json", prefix="memray_stats_")
        os.close(stats_fd)

        with open(query_file) as stdin_f:
            result = subprocess.run(
                [memray_bin, "run", "--force", "-o", bin_path, tool_path],
                stdin=stdin_f,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=env,
            )

        if result.returncode != 0:
            raise RuntimeError(
                f"memray run exited with code {result.returncode}: "
                f"{result.stderr.decode(errors='replace').strip()}"
            )

        result = subprocess.run(
            [memray_bin, "stats", "--json", "--force", "-o", stats_path, bin_path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"memray stats exited with code {result.returncode}: {result.stderr.strip()}"
            )

        with open(stats_path) as f:
            stats = json.load(f)

        return stats["metadata"]["peak_memory"]
    finally:
        for p in (bin_path, stats_path):
            if p and os.path.exists(p):
                os.unlink(p)


def run_cprofile(tool_path: str, query_file: Path, env: dict,
                 command: str, api_version: int) -> None:
    """Run the tool under cProfile, save .prof file, and print a summary."""
    prof_file = Path.cwd() / f"profile_{command}_v{api_version}.prof"

    with open(query_file) as stdin_f:
        result = subprocess.run(
            [sys.executable, "-m", "cProfile", "-o", str(prof_file), tool_path],
            stdin=stdin_f,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
        )

    if result.returncode != 0:
        print(
            f"    cProfile run FAILED (exit code {result.returncode}): "
            f"{result.stderr.decode(errors='replace').strip()}",
            file=sys.stderr,
        )
        return

    stats = pstats.Stats(str(prof_file))
    stats.sort_stats("cumulative")
    print(f"\n  cProfile results (top 20 by cumulative time):")
    stats.print_stats(20)
    print(f"  Profile saved to: {prof_file}")
    print(f"  (view with: snakeviz {prof_file})")


def run_benchmark(tool_path: str, query_file: Path, env: dict,
                  iterations: int, memray_bin: str, command: str,
                  profile: bool = False, api_version: int = 1) -> dict:
    """Run runtime and memory benchmarks, returning collected measurements."""
    runtimes = []
    peak_memories = []

    print(f"\n--- {command} ---")
    print(f"  Query file: {query_file}")

    print(f"  Measuring runtime ({iterations} iterations)...")
    for i in range(iterations):
        try:
            elapsed = measure_runtime(tool_path, query_file, env)
            runtimes.append(elapsed)
            print(f"    [{i+1}/{iterations}] {elapsed:.2f}s")
        except RuntimeError as e:
            print(f"    [{i+1}/{iterations}] FAILED: {e}", file=sys.stderr)

    print(f"  Measuring peak memory ({iterations} iterations)...")
    for i in range(iterations):
        try:
            peak = measure_peak_memory(tool_path, query_file, env, memray_bin)
            peak_memories.append(peak)
            print(f"    [{i+1}/{iterations}] {format_bytes(peak)}")
        except RuntimeError as e:
            print(f"    [{i+1}/{iterations}] FAILED: {e}", file=sys.stderr)

    if profile:
        print(f"  Running cProfile...")
        run_cprofile(tool_path, query_file, env, command, api_version)

    return {"runtimes": runtimes, "peak_memories": peak_memories}


def print_summary(results: dict, api_version: int, iterations: int, solver_name: str):
    print(f"\n{'=' * 90}")
    print(f"Benchmark Results (API v{api_version}, {solver_name}, {iterations} iterations)")
    print(f"{'=' * 90}")

    hdr_cmd = "Command"
    hdr_rt = "Avg Runtime (s)"
    hdr_rt_sd = "Std Dev (s)"
    hdr_mem = "Avg Peak Memory (MB)"
    hdr_mem_sd = "Std Dev (MB)"

    print(f"\n{hdr_cmd:<12} | {hdr_rt:>16} | {hdr_rt_sd:>12} | {hdr_mem:>21} | {hdr_mem_sd:>13}")
    print(f"{'-'*12}-+-{'-'*16}-+-{'-'*12}-+-{'-'*21}-+-{'-'*13}")

    for command, data in results.items():
        rts = data["runtimes"]
        mems = data["peak_memories"]

        if rts:
            avg_rt = statistics.mean(rts)
            sd_rt = statistics.stdev(rts) if len(rts) >= 2 else 0.0
            rt_str = f"{avg_rt:>16.2f}"
            rt_sd_str = f"{sd_rt:>12.2f}"
        else:
            rt_str = f"{'N/A':>16}"
            rt_sd_str = f"{'N/A':>12}"

        if mems:
            mems_mb = [m / (1024 ** 2) for m in mems]
            avg_mem = statistics.mean(mems_mb)
            sd_mem = statistics.stdev(mems_mb) if len(mems_mb) >= 2 else 0.0
            mem_str = f"{avg_mem:>21.2f}"
            mem_sd_str = f"{sd_mem:>13.2f}"
        else:
            mem_str = f"{'N/A':>21}"
            mem_sd_str = f"{'N/A':>13}"

        print(f"{command:<12} | {rt_str} | {rt_sd_str} | {mem_str} | {mem_sd_str}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark osbuild-depsolve-dnf for runtime and peak memory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--tool-path", required=True,
        help="Path to the osbuild-depsolve-dnf script",
    )
    parser.add_argument(
        "--queries-dir", required=True,
        help="Directory containing the JSON query files (dump.json, search.json, etc.)",
    )
    parser.add_argument(
        "--api-version", required=True, type=int, choices=[1, 2],
        help="API version to benchmark (1 or 2)",
    )
    parser.add_argument(
        "--iterations", type=int, default=5,
        help="Number of benchmark iterations per command (default: 5)",
    )
    parser.add_argument(
        "--pythonpath", default=".",
        help="PYTHONPATH to set when invoking the tool (default: .)",
    )
    parser.add_argument(
        "--commands", nargs="+", choices=ALL_COMMANDS, default=ALL_COMMANDS,
        help="Commands to benchmark (default: all)",
    )
    parser.add_argument(
        "--profile", action="store_true", default=False,
        help="Run each command once under cProfile, saving .prof files and printing a summary",
    )
    parser.add_argument(
        "--dnf5", action="store_true", default=False,
        help="Use the dnf5 solver instead of the default dnf solver",
    )
    args = parser.parse_args()

    tool_path = os.path.abspath(args.tool_path)
    queries_dir = Path(args.queries_dir).expanduser().resolve()
    pythonpath = os.path.abspath(args.pythonpath)

    memray_bin = shutil.which("memray")
    if not memray_bin:
        print("ERROR: 'memray' not found in PATH. Install it with: pip install memray",
              file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(tool_path):
        print(f"ERROR: Tool not found: {tool_path}", file=sys.stderr)
        sys.exit(1)

    query_files = {}
    for command in args.commands:
        qf = resolve_query_file(queries_dir, command, args.api_version)
        if not qf.is_file():
            print(f"WARNING: Query file not found, skipping '{command}': {qf}", file=sys.stderr)
            continue
        query_files[command] = qf

    if not query_files:
        print("ERROR: No valid query files found. Nothing to benchmark.", file=sys.stderr)
        sys.exit(1)

    env = {**os.environ, "PYTHONPATH": pythonpath}

    solver_config_path = None
    if args.dnf5:
        fd, solver_config_path = tempfile.mkstemp(suffix=".json", prefix="solver_config_")
        with os.fdopen(fd, "w") as f:
            json.dump({"use_dnf5": True}, f)
        env["OSBUILD_SOLVER_CONFIG"] = solver_config_path

    solver_name = "DNF5" if args.dnf5 else "DNF4"

    try:
        print(f"Benchmarking osbuild-depsolve-dnf (API v{args.api_version})")
        print(f"  Tool:       {tool_path}")
        print(f"  Solver:     {solver_name}")
        print(f"  PYTHONPATH: {pythonpath}")
        print(f"  Queries:    {queries_dir}")
        print(f"  Iterations: {args.iterations}")
        print(f"  Commands:   {', '.join(query_files.keys())}")
        if args.profile:
            print(f"  Profiling:  enabled (cProfile)")

        results = {}
        for command, query_file in query_files.items():
            results[command] = run_benchmark(
                tool_path, query_file, env, args.iterations, memray_bin, command,
                profile=args.profile, api_version=args.api_version,
            )

        print_summary(results, args.api_version, args.iterations, solver_name)
    finally:
        if solver_config_path and os.path.exists(solver_config_path):
            os.unlink(solver_config_path)


if __name__ == "__main__":
    main()
