// Tab "Corpus" (read-only pretrain data): hand-label sheets, random samples, search -> grounding, s5 token table.
let corpusInfo = null, sheet = null, sheetActive = 0, sheetFrom = 0;
const SHEET_PAGE = 20;
const SHEET_HELP = { dung: "1 = quyết định của bộ lọc đúng, 0 = sai", diem: "0–5 theo thang giá trị kiến thức (như nhãn LLM)" };

async function loadCorpus() {
  corpusInfo = await guard(() => api("corpus"));
  const sheets = await guard(() => api("sheets")) || [];
  if (!corpusInfo) return;
  const stages = Object.keys(corpusInfo.stages);
  $("#corpus").innerHTML = `<div class="panel"><h2>Chấm tay</h2>
      <table>${sheets.map((s) => `<tr class="click" data-path="${esc(s.path)}"><td><b>${esc(s.name)}</b></td><td>${esc(SHEET_HELP[s.mode])}</td>
        <td>${s.done}/${s.rows}</td></tr>`).join("")}</table>
      <form id="sheet-open" class="filters"><label>Hoặc mở file CSV khác<input type="text" name="path" style="width:420px"></label><button class="btn">Mở</button></form>
      <div id="sheet"></div></div>
    <div class="panel"><h2>Xem mẫu &amp; tìm trong corpus</h2><form id="corpus-form" class="filters">
      <label>Tầng${selectHtml("stage", "stage", stages.includes("final") ? "final" : stages.at(-1), null, stages)}</label>
      <label>Nguồn<span id="corpus-sources"></span></label>
      <label>Tier (final)${selectHtml("tier", "tier", "", "— tất cả —", corpusInfo.tiers)}</label>
      <label>Era${selectHtml("era", "era", "", "— tất cả —", corpusInfo.eras)}</label>
      <label>Số tài liệu<input type="number" name="n" value="20" min="1" max="100" style="width:70px"></label>
      <button class="btn primary" data-act="sample">Lấy mẫu ngẫu nhiên</button>
      <label>Từ khoá (không cần dấu)<input type="text" name="q"></label><button class="btn" data-act="search">Tìm trong cả nguồn</button>
    </form><div id="corpus-result"></div></div>
    <div class="panel"><h2>Bảng tài liệu / âm tiết sau s5</h2>${finalStatsHtml(corpusInfo.final_stats)}
      <p class="muted">Manifest trộn (mixes/*.yaml) và số token theo tokenizer riêng có ở phase 10.</p></div>`;
  const f = $("#corpus-form");
  const fillSources = () => ($("#corpus-sources").innerHTML = selectHtml("source", "source", "", null, corpusInfo.stages[f.stage.value]));
  f.stage.onchange = fillSources;
  fillSources();
  f.querySelectorAll("[data-act]").forEach((b) => (b.onclick = (e) => { e.preventDefault(); corpusQuery(b.dataset.act); }));
  document.querySelectorAll("#corpus tr.click").forEach((tr) => (tr.onclick = () => openSheet(tr.dataset.path)));
  $("#sheet-open").onsubmit = (e) => { e.preventDefault(); openSheet(e.target.path.value.trim().replace(/^"|"$/g, "")); };
}

function finalStatsHtml(stats) {
  if (!stats) return '<p class="muted">Chưa có: chạy s5 (stage_final) sau khi train q_edu.</p>';
  return `<pre class="log">${esc(JSON.stringify(stats, null, 1)).slice(0, 20000)}</pre>`;
}

async function corpusQuery(act) {
  const p = Object.fromEntries(new FormData($("#corpus-form")));
  const out = $("#corpus-result");
  out.innerHTML = '<p class="muted">Đang đọc…</p>';
  if (act === "sample") {
    const docs = await guard(() => api("corpus/sample?" + new URLSearchParams({ ...p, seed: Date.now() })));
    out.innerHTML = docs ? docs.map((d, i) => docHtml(d, i)).join("") || '<p class="muted">Không có tài liệu khớp.</p>' : "";
    if (docs) bindDocs(docs);
  } else {
    const r = await guard(() => api("corpus/search?" + new URLSearchParams(p)));
    if (!r) return (out.innerHTML = "");
    out.innerHTML = `<p class="muted">${r.matches} tài liệu khớp trong ${r.files_scanned}/${r.files} file đã quét${r.complete ? "" :
      " (dừng sau 20 giây: nguồn quá lớn)"}; hiện ${r.hits.length} kết quả đầu.</p>` +
      r.hits.map((d, i) => docHtml(d, i)).join("");
    bindDocs(r.hits);
  }
}

function docHtml(d, i) {
  return `<div class="doc" data-i="${i}"><div class="meta muted">${esc(d.source_id)} · ${esc(d.era)} · ${esc(d.publish_date || "không rõ ngày")}
    ${d.q_edu != null ? ` · q_edu ${(+d.q_edu).toFixed(2)}` : ""}${d.reason ? ` · loại: ${esc(d.reason)}` : ""} · ${d.chars} ký tự ·
    <a href="${esc(d.url)}" target="_blank">${esc(d.title || d.url || d.doc_id)}</a></div>
    <div class="txt">${esc(d.snippet || d.text)}</div>
    <div class="actions"><button class="btn act-full">Xem đầy đủ</button>
      <button class="btn act-ground" title="Bôi đen một đoạn để chỉ gắn đoạn đó">Gắn vào mục đang soạn</button></div></div>`;
}

function bindDocs(docs) {
  document.querySelectorAll("#corpus-result .doc").forEach((el) => {
    const d = docs[+el.dataset.i];
    el.querySelector(".act-full").onclick = async () => {
      const full = await guard(() => api("corpus/doc", { method: "POST", body: { ref: d.ref } }));
      if (full) el.querySelector(".txt").textContent = full.text;
    };
    el.querySelector(".act-ground").onclick = () => {
      const sel = String(window.getSelection() || "").trim();
      const quote = sel && el.contains(window.getSelection().anchorNode) ? sel : (d.snippet || d.text).slice(0, 1500);
      addGrounding({ doc_id: d.doc_id, source_id: d.source_id, url: d.url || undefined, quote });
    };
  });
}

async function openSheet(path) {
  sheet = await guard(() => api("sheets/load?" + new URLSearchParams({ path })));
  if (!sheet) return;
  sheetFrom = Math.max(0, sheet.items.findIndex((r) => !sheet.values.includes(String(r[sheet.mode]).trim())));
  drawSheet();
}

function drawSheet(focus = 0) {
  const rows = sheet.items.slice(sheetFrom, sheetFrom + SHEET_PAGE);
  $("#sheet").innerHTML = `<h3>${esc(sheet.name)}: ${sheet.done}/${sheet.rows} đã chấm · ${esc(SHEET_HELP[sheet.mode])}</h3>
    <p class="keys">Phím: ${sheet.values.join(", ")} chấm &amp; sang dòng sau · Alt+↑/↓ dòng trước/sau · Ghi chú lưu khi rời ô.</p>` +
    rows.map((r) => `<div class="sheet-row" tabindex="-1" data-row="${r.row}"><div class="meta muted">#${r.row + 1} · ${esc(r.source)} ·
      ${esc(r.status ? (r.status === "giu" ? "GIỮ" : "LOẠI " + r.reason) : "")} · ${esc(r.doc_id)}</div>
      <div class="txt">${esc(r[sheet.text_column])}</div>
      <div class="actions marks">${sheet.values.map((v) => `<button class="btn ${String(r[sheet.mode]).trim() === v ? "on" : ""}" data-v="${v}">${v}</button>`).join("")}
      <input type="text" class="note" value="${esc(r.ghi_chu)}" placeholder="Ghi chú"></div></div>`).join("") +
    `<div id="sheet-pager" class="filters"><button class="btn" id="sheet-prev" ${sheetFrom ? "" : "disabled"}>‹ Trước</button>
      Dòng ${sheetFrom + 1}–${sheetFrom + rows.length}<button class="btn" id="sheet-next" ${sheetFrom + SHEET_PAGE < sheet.rows ? "" : "disabled"}>Sau ›</button></div>`;
  $("#sheet-prev").onclick = () => { sheetFrom = Math.max(0, sheetFrom - SHEET_PAGE); drawSheet(); };
  $("#sheet-next").onclick = () => { sheetFrom += SHEET_PAGE; drawSheet(); };
  sheetRows().forEach((el, k) => {
    el.addEventListener("focusin", () => { sheetActive = k; markSheet(); });
    el.querySelectorAll("[data-v]").forEach((b) => (b.onclick = () => mark(k, b.dataset.v)));
    el.querySelector(".note").onchange = () => mark(k, null);
  });
  focusSheet(focus);
}

const sheetRows = () => document.querySelectorAll("#sheet .sheet-row");
const markSheet = () => sheetRows().forEach((el, k) => el.classList.toggle("active", k === sheetActive));
function focusSheet(k) {
  const rows = sheetRows();
  if (!rows.length) return;
  sheetActive = Math.max(0, Math.min(k, rows.length - 1));
  markSheet();
  rows[sheetActive].focus();
  rows[sheetActive].scrollIntoView({ block: "nearest" });
}

// The mark shows and the cursor moves at once; saves to the CSV run one after another behind it.
let sheetSaving = Promise.resolve();
function mark(k, value) {
  const el = sheetRows()[k], row = +el.dataset.row, item = sheet.items[row], path = sheet.path;
  const v = value ?? String(item[sheet.mode]).trim(), note = el.querySelector(".note").value;
  item[sheet.mode] = v;
  item.ghi_chu = note;
  sheetSaving = sheetSaving.then(async () => {
    const s = await guard(() => api("sheets/save", { method: "POST", body: { path, row, value: v, note } }));
    if (s && sheet.path === path) { sheet.done = s.done; $("#sheet h3").textContent = `${sheet.name}: ${s.done}/${sheet.rows} đã chấm · ${SHEET_HELP[sheet.mode]}`; }
    else if (!s && sheet.path === path) { item[sheet.mode] = ""; drawSheet(k); }  // not saved: show it unmarked
  });
  if (value === null) return;
  if (k + 1 < sheetRows().length) drawSheet(k + 1);
  else if (sheetFrom + SHEET_PAGE < sheet.rows) { sheetFrom += SHEET_PAGE; drawSheet(0); }
  else { drawSheet(k); toast("Đã chấm hết trang cuối"); }
}

document.addEventListener("keydown", (e) => {
  if (!$("#tab-corpus").classList.contains("active") || !sheet || !sheetRows().length) return;
  // Marks apply to the highlighted row unless the user is typing or working in the corpus browser below.
  if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) || e.target.closest("#corpus-form, #corpus-result")) return;
  if (!e.ctrlKey && !e.altKey && sheet.values.includes(e.key)) { e.preventDefault(); mark(sheetActive, e.key); }
  else if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); focusSheet(sheetActive + 1); }
  else if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); focusSheet(sheetActive - 1); }
});

window.addEventListener("tab", (e) => {
  if (e.detail !== "corpus") return;
  if (!corpusInfo) loadCorpus();
  else if (sheet) focusSheet(sheetActive);
});
