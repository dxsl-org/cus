import json

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.process import stage_dedup_exact as stage


def _write(root, source, rows):
    d = root / "s1_filter" / source / "hf"
    d.mkdir(parents=True)
    pq.write_table(pa.table({"doc_id": [r[0] for r in rows], "text_sha256": [r[1] for r in rows],
                             "text": [r[2] for r in rows]}), d / "part-00000.parquet")


def test_cross_source_duplicate_keeps_higher_priority_copy(tmp_path):
    same, other = "a" * 64, "b" * 64
    _write(tmp_path, "tuoitre_vn", [("t1", same, "bài gốc")])
    _write(tmp_path, "hplt2_vie", [("h1", same, "bài gốc"), ("h2", other, "bài khác"), ("h3", other, "bài khác")])

    stats = stage.run(["hplt2_vie", "tuoitre_vn"], workers=2, root=tmp_path)

    kept = {s: pq.read_table(tmp_path / "s2_exact" / s / "hf").column("doc_id").to_pylist()
            for s in ("tuoitre_vn", "hplt2_vie")}
    assert kept == {"tuoitre_vn": ["t1"], "hplt2_vie": ["h2"]}
    assert stats["dropped_to_kept"] == {"hplt2_vie -> hplt2_vie": 1, "hplt2_vie -> tuoitre_vn": 1}
    assert json.loads((tmp_path / "s2_exact" / "stats.json").read_text())["sources"]["hplt2_vie"] == \
        {"docs_in": 3, "docs_out": 1}
