import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.process import stage_minhash as stage

BASE = ("Ủy ban nhân dân thành phố vừa tổ chức hội nghị tổng kết công tác phòng chống thiên tai năm qua với sự "
        "tham gia của đại diện các sở ngành quận huyện và nhiều chuyên gia trong lĩnh vực khí tượng thủy văn. "
        "Theo báo cáo các địa phương đã chủ động di dời hàng nghìn hộ dân ra khỏi vùng nguy hiểm trước khi bão "
        "đổ bộ nên thiệt hại về người và tài sản giảm đáng kể so với cùng kỳ năm trước. ") * 3
OTHER = ("Đội tuyển bóng đá quốc gia đã có trận giao hữu thành công trước đối thủ đến từ châu Âu trên sân vận động "
         "Mỹ Đình tối qua với chiến thắng thuyết phục nhờ lối chơi pressing tầm cao và khả năng phối hợp nhuần "
         "nhuyễn giữa các tuyến của huấn luyện viên trưởng cùng các cầu thủ trẻ đầy triển vọng. ") * 3


def _write(root, source, rows):
    d = root / "s2_exact" / source / "hf"
    d.mkdir(parents=True)
    pq.write_table(pa.table({"doc_id": [r[0] for r in rows], "text": [r[1] for r in rows]}),
                   d / "part-00000.parquet")


def test_near_duplicate_across_sources_keeps_own_crawl_copy(tmp_path):
    _write(tmp_path, "hplt2_vie", [("h1", BASE + " Nguồn: tổng hợp."), ("h2", OTHER)])
    _write(tmp_path, "tuoitre_vn", [("t1", BASE)])

    stats = stage.run(["hplt2_vie", "tuoitre_vn"], workers=2, root=tmp_path)

    kept = {s: pq.read_table(tmp_path / "s3_minhash" / s / "hf").column("doc_id").to_pylist()
            for s in ("tuoitre_vn", "hplt2_vie")}
    assert kept == {"tuoitre_vn": ["t1"], "hplt2_vie": ["h2"]}
    assert stats["dropped_to_kept"] == {"hplt2_vie -> tuoitre_vn": 1}



def test_transitive_duplicates_form_one_cluster_keeping_the_earliest_document():
    import numpy as np
    offsets = np.array([0, 10, 20])  # three files of 10 rows
    pairs = np.array([[1, 4, 0, 7],                       # file1 row4 ~ file0 row7
                      [2, 0, 1, 4],                       # file2 row0 ~ file1 row4 (A-B, B-C)
                      [2, 5, 2, 6],                       # separate pair inside file2
                      [stage.SENTINEL, stage.SENTINEL, 0, 1]], dtype=np.uint32)
    dropped, kept_by = stage.find_drops(pairs, offsets)
    assert dict(zip(dropped.tolist(), kept_by.tolist())) == {14: 7, 20: 7, 26: 25}