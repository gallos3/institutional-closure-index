import os
import sys
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional

# ---- CONFIG ----
CPVS = [
    "33100", "33140", "33141", "33600", "33690", "34144",
    "34300", "45000", "45233", "60130", "66510", "90500"
]
YEARS = list(range(2018, 2023))  # 2018..2022 inclusive

MAX_PARALLEL = 3          # tune: 2-4 is usually safe
SLEEP_BETWEEN_POLLS = 2   # seconds
FEATURES_SCRIPT = "featuresall.py"  # adjust if needed

OUT_DIR = "out"
LOG_DIR = "logs"
# ----------------


@dataclass
class Task:
    cpv: str
    year: int
    out_path: Path
    log_path: Path


def ensure_dirs(root: Path) -> None:
    (root / OUT_DIR).mkdir(parents=True, exist_ok=True)
    (root / LOG_DIR).mkdir(parents=True, exist_ok=True)


def build_tasks(root: Path) -> List[Task]:
    tasks: List[Task] = []
    for cpv in CPVS:
        for year in YEARS:
            out_path = root / OUT_DIR / f"metrics_cpv{cpv}_y{year}.jsonl"
            log_path = root / LOG_DIR / f"metrics_cpv{cpv}_y{year}.log"
            tasks.append(Task(cpv=cpv, year=year, out_path=out_path, log_path=log_path))
    return tasks


def run_in_batches(root: Path, tasks: List[Task], max_parallel: int) -> None:
    py_exe = sys.executable  # uses same python you run this script with
    features_path = (root / FEATURES_SCRIPT)

    if not features_path.exists():
        raise FileNotFoundError(f"Cannot find {FEATURES_SCRIPT} at: {features_path}")

    running: List[subprocess.Popen] = []
    running_meta: List[Task] = []
    completed = 0
    failed = 0

    def start_task(t: Task) -> subprocess.Popen:
        # We send stdout/stderr to the log file (append) so it is always captured.
        # featuresall.py also writes logs to --log; this captures stdout/stderr.
        log_fh = open(t.log_path, "a", encoding="utf-8")
        cmd = [
            py_exe, str(features_path),
            "--cpv", t.cpv,
            "--base_year", str(t.year),
            "--out", str(t.out_path),
            "--log", str(t.log_path),
        ]
        # Use cwd=root so relative paths in featuresall behave
        p = subprocess.Popen(cmd, cwd=str(root), stdout=log_fh, stderr=log_fh)
        # Attach handle so it doesn't get GC'd; store on process object
        p._log_fh = log_fh  # type: ignore[attr-defined]
        return p

    # Simple queue
    queue = tasks.copy()

    print(f"Root: {root}")
    print(f"Total tasks: {len(queue)} (CPVs={len(CPVS)} × Years={len(YEARS)})")
    print(f"Max parallel: {max_parallel}")
    print("Starting...")

    while queue or running:
        # Fill up to max_parallel
        while queue and len(running) < max_parallel:
            t = queue.pop(0)
            # Skip if output already exists and non-empty (optional behavior)
            if t.out_path.exists() and t.out_path.stat().st_size > 0:
                print(f"SKIP (exists): cpv={t.cpv} year={t.year} -> {t.out_path.name}")
                completed += 1
                continue

            print(f"START: cpv={t.cpv} year={t.year}")
            p = start_task(t)
            running.append(p)
            running_meta.append(t)

        # Poll running
        i = 0
        while i < len(running):
            p = running[i]
            t = running_meta[i]
            rc = p.poll()
            if rc is None:
                i += 1
                continue

            # Close the log file handle
            try:
                p._log_fh.close()  # type: ignore[attr-defined]
            except Exception:
                pass

            if rc == 0:
                # sanity check output file
                ok = t.out_path.exists() and t.out_path.stat().st_size > 0
                status = "OK" if ok else "OK (empty output?)"
                print(f"DONE: cpv={t.cpv} year={t.year} rc={rc} -> {status}")
                completed += 1
            else:
                print(f"FAIL: cpv={t.cpv} year={t.year} rc={rc} (see {t.log_path})")
                failed += 1

            # Remove finished
            running.pop(i)
            running_meta.pop(i)

        time.sleep(SLEEP_BETWEEN_POLLS)

    print("\nAll tasks finished.")
    print(f"Completed: {completed}")
    print(f"Failed:    {failed}")
    print(f"Outputs in: {root / OUT_DIR}")
    print(f"Logs in:    {root / LOG_DIR}")


def main() -> None:
    root = Path.cwd()
    ensure_dirs(root)

    tasks = build_tasks(root)

    # Optional: reorder tasks (e.g., run all years for a CPV together)
    # tasks.sort(key=lambda t: (t.cpv, t.year))

    run_in_batches(root, tasks, MAX_PARALLEL)


if __name__ == "__main__":
    main()
