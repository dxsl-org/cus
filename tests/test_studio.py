import csv
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from studio import db, importer, items, server, sheets


def _sft(question="Thủ đô của Việt Nam là gì?"):
    return {"domain": "culture_history", "lang": "vi",
            "messages": [{"role": "user", "content": question}, {"role": "assistant", "content": "Hà Nội."}]}


@pytest.fixture
def studio(tmp_path):
    """A live Studio server on a fresh store; yields call(path, body=None, method=None, token=None)."""
    srv = server.make_server(tmp_path / "t.db", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/api/"

    def call(path, body=None, method=None, token=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     method=method or ("GET" if body is None else "POST"),
                                     headers={"Content-Type": "application/json", "X-Studio-Token": token or ""})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    call("sources", {"id": "own", "license": "internal", "usage_scope": "train_ok", "default_domain": "math"})
    yield call
    srv.shutdown()


def _wait_job(call, job_id):
    for _ in range(100):
        job = next(j for j in call("jobs")[1] if j["id"] == job_id)
        if job["state"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_rl_item_cannot_be_approved_until_reference_passes_and_negative_fails(studio):
    rl = {"domain": "math", "lang": "vi", "messages": [{"role": "user", "content": "Tính 2 + 2"}],
          "reference_answer": "4", "verifier": {"type": "numeric"}}
    _, item = studio("items", {"kind": "rl_verifiable", "source": "own", "content": rl})
    assert studio(f"items/{item['id']}/review", {"verdict": "approved"})[0] == 409
    _, run = studio(f"items/{item['id']}/verify", {})  # no wrong samples yet: still blocked
    assert run["item"]["ref_pass"] and not run["item"]["neg_fail"]
    assert studio(f"items/{item['id']}/review", {"verdict": "approved"})[0] == 409
    studio(f"items/{item['id']}/content", {"content": {**rl, "meta": {"negatives": ["5"]}}}, method="PUT")
    _, run = studio(f"items/{item['id']}/verify", {})
    assert [r["ok"] for r in run["runs"]] == [True, True]
    code, approved = studio(f"items/{item['id']}/review", {"verdict": "approved"})
    assert code == 200 and approved["status"] == "approved"


def test_dropped_item_returns_to_review_queue(studio):
    _, item = studio("items", {"kind": "sft", "source": "own", "content": _sft()})
    studio(f"items/{item['id']}/review", {"verdict": "dropped"})
    code, back = studio(f"items/{item['id']}/pending", {})
    assert code == 200 and back["status"] == "pending"


def test_teacher_draft_becomes_a_pending_model_version(studio, monkeypatch):
    class Teacher(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["messages"][-1]["role"] == "user"
            out = json.dumps({"choices": [{"message": {"content": "Hà Nội là thủ đô."}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    fake = ThreadingHTTPServer(("127.0.0.1", 0), Teacher)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    monkeypatch.setenv("LLM_STUDIO_TEACHER_URL", f"http://127.0.0.1:{fake.server_address[1]}/v1")
    monkeypatch.setenv("LLM_STUDIO_TEACHER_MODEL", "fake-teacher")
    _, item = studio("items", {"kind": "sft", "source": "own", "content": _sft(), "submit": False})
    _, job = studio("draft", {"item_ids": [item["id"]]})
    assert _wait_job(studio, job["id"])["state"] == "done"
    _, d = studio(f"items/{item['id']}")
    fake.shutdown()
    assert d["item"]["status"] == "pending" and d["item"]["origin"] == "model:fake-teacher"
    assert d["versions"][0]["content"]["messages"][-1]["content"] == "Hà Nội."  # human version kept
    assert d["versions"][-1]["content"]["messages"][-1]["content"] == "Hà Nội là thủ đô."


def test_lan_mode_rejects_api_calls_without_token(tmp_path):
    srv = server.make_server(tmp_path / "t.db", 0, lan=True, token="s3cret")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/api/options"
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(url)
    ok = urllib.request.urlopen(urllib.request.Request(url, headers={"X-Studio-Token": "s3cret"}))
    srv.shutdown()
    assert err.value.code == 401 and ok.status == 200


def test_csv_import_is_idempotent_and_reports_bad_rows(tmp_path):
    con = db.connect(tmp_path / "t.db")
    con.execute("INSERT INTO sources (id, license, usage_scope, default_domain) VALUES ('own', 'internal', 'train_ok', 'math')")
    path = tmp_path / "q.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, ["kind", "question", "answer", "skills"])
        w.writeheader()
        w.writerow({"kind": "sft", "question": "1 + 1 bằng mấy?", "answer": "Bằng 2.", "skills": "calculation"})
        w.writerow({"kind": "sft", "question": "Câu thiếu đáp án", "answer": ""})
    first = importer.import_file(con, path, "own")
    again = importer.import_file(con, path, "own")
    assert (first["added"], first["invalid"]) == (1, 1) and "dòng 3" in first["errors"][0]
    assert (again["added"], again["existing"]) == (0, 1)
    assert items.list_items(con, {"status": "pending", "domain": "math"})["total"] == 1


def test_search_ignores_vietnamese_accents(tmp_path):
    con = db.connect(tmp_path / "t.db")
    con.execute("INSERT INTO sources (id, license, usage_scope) VALUES ('own', 'internal', 'train_ok')")
    from taskset import store
    store.create_item(con, "sft", "own", _sft("Đường lên đỉnh Fansipan dài bao nhiêu?"), "human:a", "a")
    assert items.list_items(con, {"q": "duong len dinh"})["total"] == 1
    assert items.list_items(con, {"q": "duong xuong"})["total"] == 0


def test_sheet_marks_stay_readable_by_the_pipeline_scorers(tmp_path):
    path = tmp_path / "hand-labels.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, ["doc_id", "source", "text", "diem", "ghi_chu"])
        w.writeheader()
        w.writerows([{"doc_id": f"d{i}", "source": "s", "text": f"văn bản, có dấu phẩy\n{i}", "diem": "", "ghi_chu": ""}
                     for i in range(3)])
    sheets.save(path, 1, "4", "rõ ràng")
    with pytest.raises(ValueError):
        sheets.save(path, 0, "7")
    with open(path, encoding="utf-8-sig", newline="") as fh:  # as pipeline.quality.classifier.check reads it
        rows = list(csv.DictReader(fh))
    assert [r["diem"] for r in rows] == ["", "4", ""] and rows[1]["text"] == "văn bản, có dấu phẩy\n1"
    assert sheets.summary(path)["done"] == 1


def test_corpus_search_matches_any_accents_case_and_line_breaks(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from studio import corpus

    texts = ["Kỳ họp của HỘI ĐỒNG\nnhân dân tỉnh.", "Hội đồng quản trị họp.", "hoi dong nhan dan xa"]
    path = tmp_path / "part.parquet"
    pq.write_table(pa.table({"doc_id": ["a", "b", "c"], "text": texts}), path)
    monkeypatch.setattr(corpus, "files", lambda *a: [path])
    monkeypatch.setattr(corpus, "_allowed", lambda p: p)  # tmp_path is outside the real corpus roots
    r = corpus.search("s4_clean", "x", "Hội đồng nhân dân")
    assert r["matches"] == 2 and r["complete"]
    assert [h["doc_id"] for h in sorted(r["hits"], key=lambda h: h["doc_id"])] == ["a", "c"]
    first = next(h for h in r["hits"] if h["doc_id"] == "a")
    assert first["snippet"].startswith("Kỳ họp của HỘI") and corpus.document(first["ref"])["text"] == texts[0]
