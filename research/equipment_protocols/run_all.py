#!/usr/bin/env python3
"""Regenerate results.json, results.md and figures/*.png from scratch.

    .venv/bin/python run_all.py            # full study (5 repetitions per configuration)
    .venv/bin/python run_all.py --reps 1 --only q1   # quick partial run (does not overwrite outputs
                                                      # unless --write is given)

Requires: the venv from requirements.txt and /usr/sbin/mosquitto (apt package mosquitto).
Raw per-run logs go to a temporary directory that is deleted afterwards (keep with --keep-runs DIR).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path

STUDY = Path(__file__).resolve().parent
sys.path.insert(0, str(STUDY))

from eqp import broker  # noqa: E402
from experiments import q1_wire, q2_latency, q3_semantics  # noqa: E402
from experiments.common import Ctx  # noqa: E402


def leftover_processes(workdir: Path) -> list[str]:
    """Role processes (argv: python -m eqp.roles ...) or brokers configured inside workdir."""
    found = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            args = [a.decode(errors="replace") for a in (d / "cmdline").read_bytes().split(b"\0") if a]
        except OSError:
            continue
        is_role = "-m" in args and "eqp.roles" in args
        is_broker = bool(args) and args[0].endswith("mosquitto") and any(str(workdir) in a for a in args)
        if is_role or is_broker:
            found.append(f"{d.name}: {' '.join(args)[:120]}")
    return found


def meta() -> dict:
    pkgs = {}
    for p in ("asyncua", "paho-mqtt", "pysparkplug", "protobuf", "secsgem", "numpy", "matplotlib"):
        try:
            pkgs[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            pkgs[p] = None
    try:
        cpu = next(line.split(":", 1)[1].strip() for line in open("/proc/cpuinfo") if line.startswith("model name"))
    except Exception:
        cpu = platform.processor()
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu_model": cpu,
        "cpus": os.cpu_count(),
        "mosquitto": broker.version(),
        "packages": pkgs,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


async def main_async(args, workdir: Path) -> dict:
    ctx = Ctx(workdir=workdir, reps=args.reps)
    results: dict = {"meta": meta(), "reps": args.reps, "timing_s": {}}
    sections = args.only or ["q1", "q2", "q3"]
    for name, mod in (("q1", q1_wire), ("q2", q2_latency), ("q3", q3_semantics)):
        if name not in sections:
            continue
        t = time.monotonic()
        load_before = os.getloadavg()
        results[name] = await mod.run(ctx)
        results["timing_s"][name] = round(time.monotonic() - t, 1)
        results.setdefault("loadavg", {})[name] = {"before": [round(x, 2) for x in load_before],
                                                   "after": [round(x, 2) for x in os.getloadavg()]}
        ctx.log(f"{name} done in {results['timing_s'][name]} s")
    results["timing_s"]["total"] = round(time.monotonic() - ctx.t0, 1)
    results["failures"] = ctx.failures
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=5, help="repetitions per configuration (default 5)")
    ap.add_argument("--only", nargs="+", choices=["q1", "q2", "q3"], help="run only these sections")
    ap.add_argument("--keep-runs", metavar="DIR", help="keep raw per-run logs in DIR")
    ap.add_argument("--write", action="store_true", help="with --only/--reps<5: still overwrite outputs")
    ap.add_argument("--out", default=str(STUDY), help="directory for results.json/results.md/figures")
    args = ap.parse_args()
    if not shutil.which("mosquitto") and not Path(broker.MOSQUITTO).exists():
        print("mosquitto not found (apt-get install -y mosquitto)", file=sys.stderr)
        return 2
    if args.keep_runs:
        workdir = Path(args.keep_runs).resolve()
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True)
    else:
        workdir = Path(tempfile.mkdtemp(prefix="eqp-runs-"))
    try:
        results = asyncio.run(main_async(args, workdir))
    finally:
        if not args.keep_runs:
            shutil.rmtree(workdir, ignore_errors=True)
        leftovers = leftover_processes(workdir)
        if leftovers:
            print(f"WARNING: leftover processes {leftovers}", file=sys.stderr)
    out = Path(args.out)
    partial = bool(args.only) or args.reps < 5
    if partial and not args.write:
        tmp = out / "results.partial.json"
        tmp.write_text(json.dumps(results, indent=1, default=str))
        print(f"partial run: wrote {tmp} only")
        return 0
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str))
    from report import write_report

    write_report(results, out)
    print(f"wrote {out / 'results.json'}, {out / 'results.md'}, figures/  (total {results['timing_s']['total']} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
