// Tab "Thống kê": kind × domain × status, volume targets per kind, reviewer agreement, per-source counts.
const STATUS_COLS = ["draft", "pending", "auto", "approved", "corrected", "dropped"];

async function loadStats() {
  const s = await guard(() => api("stats"));
  if (!s) return;
  const rows = {};
  for (const r of s.grid) {
    const key = r.kind + "|" + r.domain;
    rows[key] = rows[key] || { kind: r.kind, domain: r.domain };
    rows[key][r.status] = r.n;
  }
  const a = s.agreement;
  const targets = Object.fromEntries(s.targets.map((t) => [t.kind, t]));
  $("#stats").innerHTML = `<div class="filters"><button class="btn" id="stats-refresh">Làm mới</button></div>
    <h3>Mục tiêu khối lượng (mục được chấp nhận: đạt tự động + đã duyệt + đã sửa)</h3>
    <form id="targets-form"><table><tr><th>Loại</th><th>Mục tiêu</th><th>Đã có</th><th>Tiến độ</th></tr>
    ${OPTIONS.kind.map((k) => { const t = targets[k] || { target: "", accepted: s.grid.filter((r) => r.kind === k && ["auto", "approved", "corrected"].includes(r.status)).reduce((x, r) => x + r.n, 0) };
      return `<tr><td>${label("kind", k)}</td><td><input type="number" min="0" name="${k}" value="${t.target}" style="width:100px"></td>
        <td>${t.accepted}</td><td>${t.target ? `<span class="bar" style="width:160px"><i style="width:${Math.min(100, 100 * t.accepted / t.target)}%"></i></span> ${pct(t.accepted / t.target)}` : "—"}</td></tr>`; }).join("")}
    </table><button class="btn primary">Lưu mục tiêu</button></form>
    <h3>Đồng thuận giữa người duyệt</h3>
    <p>${a.reviewers} người đã duyệt · ${a.versions} bản được từ 2 người trở lên duyệt · ${a.pairs} cặp so sánh ·
      tỷ lệ đồng ý <b>${a.agree == null ? "chưa đủ dữ liệu (cần ≥ 2 người duyệt cùng một bản)" : pct(a.agree)}</b>
      <span class="muted">(Đã sửa tính như Duyệt.)</span></p>
    <h3>Loại × domain × trạng thái</h3>
    <table><tr><th>Loại</th><th>Domain</th>${STATUS_COLS.map((c) => `<th>${label("status", c)}</th>`).join("")}<th>Tổng</th></tr>
    ${Object.values(rows).map((r) => `<tr><td>${label("kind", r.kind)}</td><td>${esc(label("domain", r.domain))}</td>
      ${STATUS_COLS.map((c) => `<td>${r[c] || ""}</td>`).join("")}<td><b>${STATUS_COLS.reduce((x, c) => x + (r[c] || 0), 0)}</b></td></tr>`).join("")}</table>
    <h3>Theo nguồn × tập × trạng thái</h3>
    <table><tr><th>Nguồn</th><th>Tập</th><th>Trạng thái</th><th>Số mục</th></tr>
    ${s.by_source.map((r) => `<tr><td>${esc(r.source_id)}</td><td>${label("split", r.split)}</td><td>${label("status", r.status)}</td><td>${r.n}</td></tr>`).join("")}</table>`;
  $("#stats-refresh").onclick = loadStats;
  $("#targets-form").onsubmit = async (e) => {
    e.preventDefault();
    const r = await guard(() => api("targets", { method: "POST", body: { targets: Object.fromEntries(new FormData(e.target)) } }));
    if (r) { toast("Đã lưu mục tiêu"); loadStats(); }
  };
}

window.addEventListener("tab", (e) => { if (e.detail === "stats") loadStats(); });
