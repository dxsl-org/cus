// Tab "Nguồn & nhập": source registry (license, usage scope, default domain), file / public eval imports, job queue.
let sourcesData = [];

async function loadSources() {
  sourcesData = await guard(() => api("sources")) || [];
  $("#source-list").innerHTML = `<tr><th>Mã</th><th>Giấy phép</th><th>Phạm vi dùng</th><th>Domain mặc định</th><th>Mục</th><th>Chờ duyệt</th></tr>` +
    sourcesData.map((s, i) => `<tr class="click" data-i="${i}"><td><b>${esc(s.id)}</b><div class="muted">${esc(s.note || "")}</div></td>
      <td>${esc(s.license)}</td><td>${label("usage_scope", s.usage_scope)}</td><td>${esc(label("domain", s.default_domain) || "—")}</td>
      <td>${s.items}</td><td>${s.pending}</td></tr>`).join("");
  document.querySelectorAll("#source-list tr.click").forEach((tr) => (tr.onclick = () => sourceForm(sourcesData[+tr.dataset.i])));
  sourceForm();
  importForms();
}

function sourceForm(s = {}) {
  $("#source-form").innerHTML = `<label>Mã nguồn<input type="text" name="id" value="${esc(s.id)}" required></label>
    <label>Giấy phép<input type="text" name="license" value="${esc(s.license)}" required placeholder="internal, cc-by-4.0…"></label>
    <label>Phạm vi dùng${selectHtml("usage_scope", "usage_scope", s.usage_scope || "train_ok", null)}</label>
    <label>Domain mặc định${selectHtml("default_domain", "domain", s.default_domain, "— không —")}</label>
    <label class="wide">Ghi chú<input type="text" name="note" value="${esc(s.note)}"></label>
    <div class="wide"><button class="btn primary">${s.id ? "Lưu nguồn" : "Thêm nguồn"}</button>
      <span class="muted">Chỉ nguồn "Được huấn luyện" mới vào train/dev; còn lại chỉ vào test/holdout.</span></div>`;
  $("#source-form").onsubmit = async (e) => {
    e.preventDefault();
    const r = await guard(() => api("sources", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }));
    if (r) { toast("Đã lưu nguồn"); await refreshOptions(); loadSources(); }
  };
}

function importForms() {
  $("#import-form").innerHTML = `<label>Đường dẫn file<input type="text" name="path" placeholder="D:\\cau-hoi.jsonl" style="width:360px"></label>
    <label>Vào nguồn${selectHtml("source", "source", "", "— chọn —")}</label><button class="btn primary">Nhập</button>`;
  $("#import-form").onsubmit = async (e) => {
    e.preventDefault();
    const params = Object.fromEntries(new FormData(e.target));
    if (!params.path.trim() || !params.source) return toast("Cần đường dẫn file và nguồn", true);
    const r = await guard(() => api("jobs", { method: "POST", body: { kind: "import_file", params } }));
    if (r) { toast(`Đã xếp việc nhập #${r.id}`); pollJobs(); }
  };
  $("#eval-form").innerHTML = OPTIONS.eval_datasets.map((d) => `<label class="check"><input type="checkbox" name="datasets" value="${d}">${d}</label>`).join("") +
    `<button class="btn primary">Nhập</button>`;
  $("#eval-form").onsubmit = async (e) => {
    e.preventDefault();
    const datasets = [...e.target.querySelectorAll(":checked")].map((x) => x.value);
    if (!datasets.length) return toast("Chọn ít nhất một bộ", true);
    const r = await guard(() => api("jobs", { method: "POST", body: { kind: "import_eval", params: { datasets } } }));
    if (r) { toast(`Đã xếp việc #${r.id}`); pollJobs(); }
  };
}

function jobMessage(j) {
  if (j.state !== "done") return esc(j.message);
  try {
    const m = JSON.parse(j.message), errs = m.errors || [];
    const head = Object.entries(m).filter(([k]) => k !== "errors").map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ");
    return esc(head) + (errs.length ? `<details><summary>${errs.length} lỗi</summary><ul class="errors">${errs.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></details>` : "");
  } catch { return esc(j.message); }
}

let lastJobsState = "";
window.addEventListener("jobs", (e) => {
  const jobs = e.detail;
  $("#job-list").innerHTML = `<tr><th>#</th><th>Việc</th><th>Trạng thái</th><th>Tiến độ</th><th>Kết quả</th><th></th></tr>` +
    jobs.map((j) => `<tr><td>${j.id}</td><td>${label("jobkind", j.kind)}<div class="muted">${esc(j.created_by)} · ${when(j.created_at)}</div></td>
      <td>${label("job", j.state)}</td><td>${j.done}/${j.total ?? "?"}</td><td>${jobMessage(j)}</td>
      <td>${j.state === "queued" ? `<button class="btn cancel-job" data-id="${j.id}">Huỷ</button>` : ""}</td></tr>`).join("");
  document.querySelectorAll(".cancel-job").forEach((b) => (b.onclick = () => guard(() => api(`jobs/${b.dataset.id}/cancel`, { method: "POST", body: {} })).then(pollJobs)));
  const state = jobs.map((j) => j.id + j.state).join();
  if (lastJobsState && state !== lastJobsState && jobs.some((j) => j.state === "done")) window.dispatchEvent(new Event("data-changed"));
  lastJobsState = state;
});

window.addEventListener("tab", (e) => { if (e.detail === "sources") loadSources(); });
