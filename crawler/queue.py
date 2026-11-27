"""
Sequential crawl queue: one source at a time, resumable, pausable.

    uv run python -m crawler.queue run            # run the whole plan
    uv run python -m crawler.queue run --only ou_jos kcb_vn
    uv run python -m crawler.queue plan

Pause: create DATA_ROOT/crawl_queue.STOP (the queue stops after the current chunk);
delete it and re-run to continue. Each chunk is crawl --max-pages N then extract, so a
kill at any point loses nothing (frontier + WARC are resumable). Rate limits come from each
manifest's rps; only one crawl process runs at any time.
"""
import argparse
import subprocess
import sys
import time

from pipeline.paths import DATA_ROOT, REPORTS_DIR

STOP_FILE = DATA_ROOT / "crawl_queue.STOP"
PAUSE_BETWEEN_CHUNKS = 20  # seconds of silence for the network between chunks

# (source_id, pages per chunk, max chunks). 0 pages = until the frontier is empty.
# Phase 1: small sources, run to completion. Phase 2: big sources in round-robin chunks.
PHASE1 = [
    ("ou_jos", 0, 1), ("hueuni_jos", 0, 1), ("udn_jst", 0, 1), ("hmu_tcncyh", 0, 1),
    ("hcmue_jos", 0, 1), ("ctu_jos", 0, 1), ("ueh_dspace", 0, 1),
    ("kcb_vn", 0, 1), ("bktt_vn", 0, 1), ("vannghequandoi_com_vn", 0, 1),
    ("vjpm_vn", 0, 1), ("tapchiyhocvietnam_vn", 0, 1),
]
PHASE2 = [("thivien_net", 3000, 40), ("webketoan_com", 3000, 50)]


def _run(*args):
    return subprocess.run([sys.executable, "-m", *args]).returncode


def _stopped():
    return STOP_FILE.exists()


def _chunk(source_id, pages):
    cmd = ["crawler.run", "crawl", source_id] + (["--max-pages", str(pages)] if pages else [])
    code = _run(*cmd)
    _run("crawler.extract", source_id)
    return code


def plan():
    for sid, pages, chunks in PHASE1 + PHASE2:
        print(f"{sid:26} {'until done' if not pages else f'{pages} pages x up to {chunks} chunks'}")


def run(only):
    log = REPORTS_DIR / "crawl" / "queue.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    def note(msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, flush=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    for sid, pages, _ in PHASE1:
        if only and sid not in only:
            continue
        if _stopped():
            return note("STOP file present; paused")
        note(f"start {sid}")
        note(f"done {sid} exit={_chunk(sid, pages)}")
        time.sleep(PAUSE_BETWEEN_CHUNKS)
    rounds = max(c for _, _, c in PHASE2)
    for r in range(rounds):
        for sid, pages, chunks in PHASE2:
            if (only and sid not in only) or r >= chunks:
                continue
            if _stopped():
                return note("STOP file present; paused")
            note(f"start {sid} chunk {r + 1}")
            note(f"done {sid} chunk {r + 1} exit={_chunk(sid, pages)}")
            time.sleep(PAUSE_BETWEEN_CHUNKS)
    note("queue finished")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "plan"])
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    plan() if args.command == "plan" else run(args.only)


if __name__ == "__main__":
    main()
