// Tab "Xuất": license gate overview, run taskset.export (immutable snapshot, same content -> same hash), past exports.
async function loadExport() {
  const d = await guard(() => api("export"));
  if (!d) return;
  $("#export").innerHTML = `<div class="panel"><h2>Xuất bộ dữ liệu</h2>
    <p class="muted">Ghi <code>train/dev/test/holdout.jsonl</code>, <code>decontam.jsonl</code> và <code>MANIFEST.json</code> vào
      thư mục <code>&lt;ngày&gt;-&lt;hash&gt;</code>. Train/dev lấy mục đạt tự động, đã duyệt, đã sửa và chỉ từ nguồn "Được huấn luyện";
      test/holdout chỉ lấy mục người đã duyệt. Mục có cờ không bao giờ được xuất. Nội dung không đổi thì xuất lại ra cùng hash.</p>
    <form id="export-form" class="filters"><label>Thư mục gốc<input type="text" name="dir" value="${esc(d.default_dir)}" style="width:480px"></label>
      <button class="btn primary">Xuất</button></form><div id="export-result"></div></div>
    <div class="panel"><h2>Cổng giấy phép: mục được chấp nhận theo nguồn</h2>
    <table><tr><th>Nguồn</th><th>Giấy phép</th><th>Phạm vi dùng</th><th>Tập</th><th>Chấp nhận</th><th>Có cờ (không xuất)</th><th>Được xuất</th></tr>
    ${d.license.map((r) => { const blocked = ["train", "dev"].includes(r.split) && r.usage_scope !== "train_ok";
      return `<tr><td>${esc(r.id)}</td><td>${esc(r.license)}</td><td>${label("usage_scope", r.usage_scope)}</td><td>${label("split", r.split)}</td>
        <td>${r.accepted}</td><td>${r.flagged || ""}</td><td class="${blocked ? "err" : "ok"}">${blocked ? "không (nguồn không được huấn luyện)" : r.accepted - r.flagged}</td></tr>`; }).join("")}</table></div>
    <div class="panel"><h2>Các lần xuất</h2><table><tr><th>Thư mục</th><th>Lúc</th><th>Số mục</th><th>Bỏ (không train_ok)</th></tr>
    ${d.exports.map((x) => `<tr><td><code>${esc(x.dir)}</code></td><td>${when(x.exported_at)}</td>
      <td>${Object.entries(x.counts).map(([k, v]) => `${label("split", k)} ${v}`).join(" · ")}</td><td>${x.skipped_not_train_ok}</td></tr>`).join("")}</table></div>`;
  $("#export-form").onsubmit = async (e) => {
    e.preventDefault();
    const r = await guard(() => api("export", { method: "POST", body: { dir: e.target.dir.value } }));
    if (!r) return;
    $("#export-result").innerHTML = `<p class="ok">Đã xuất: <code>${esc(r.dir)}</code></p><pre class="log">${esc(JSON.stringify(r.manifest, null, 1))}</pre>`;
    toast("Đã xuất");
  };
}

window.addEventListener("tab", (e) => { if (e.detail === "export") loadExport(); });
