"""
Run processing stages in order for a set of sources.

    uv run --group process python -m pipeline.process.run --from s2 --to s4 [sources...] [--workers 4]

Stages: s1 filter -> s2 exact dedup -> s3 MinHash -> s4 decontam + PII -> s5 final (needs the q_edu
model). Each stage reads the previous stage's published output, so a run can resume from any stage.
Sources default to everything the first requested stage can read.
"""
import argparse
import json
import sys

from pipeline.paths import PROCESSED_DIR
from pipeline.process import stage_clean, stage_dedup_exact, stage_filter, stage_final, stage_minhash

STAGES = {"s1": (stage_filter.run, None), "s2": (stage_dedup_exact.run, stage_dedup_exact.IN_STAGE),
          "s3": (stage_minhash.run, stage_minhash.IN_STAGE), "s4": (stage_clean.run, stage_clean.IN_STAGE),
          "s5": (stage_final.run, stage_final.IN_STAGE)}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*")
    parser.add_argument("--from", dest="start", choices=list(STAGES), default="s1")
    parser.add_argument("--to", dest="end", choices=list(STAGES), default="s4")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    names = list(STAGES)
    for stage in names[names.index(args.start): names.index(args.end) + 1]:
        run, in_stage = STAGES[stage]
        sources = args.sources or (sorted(d.name for d in (PROCESSED_DIR / in_stage).iterdir() if d.is_dir())
                                   if in_stage else stage_filter._all_sources())
        print(f"=== {stage}: {len(sources)} sources", flush=True)
        result = run(sources, args.workers)
        if stage in ("s2", "s3"):
            print(json.dumps(result["sources"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
