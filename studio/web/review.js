// Tab "Duyệt": filters, item cards, keyboard review loop, verifier runs, history + comments panel.
const FILTER_KEYS = [["source", "Nguồn"], ["kind", "Loại"], ["domain", "Domain"], ["status", "Trạng thái"],
  ["flag", "Cờ"], ["split", "Tập"], ["origin", "Nguồn gốc"], ["difficulty", "Độ khó"]];
const SORTS = [["id", "Theo mã"], ["newest", "Sửa gần nhất"], ["random", "Ngẫu nhiên (lấy mẫu)"]];
let filter = { status: "pending", sort: "id", page: 1, size: 30 };
let itemsData = [], active = 0, loaded = false;

function renderFilters() {
  $("#filters").innerHTML = FILTER_KEYS.map(([k, n]) => `<label>${n}${selectHtml(k, k, filter[k])}</label>`).join("") + `
    <label>Từ khoá (không cần dấu)<input type="text" name="q" value="${esc(filter.q)}"></label>
    <label>Sắp xếp<select name="sort">${SORTS.map(([v, t]) => `<option value="${v}" ${filter.sort === v ? "selected" : ""}>${t}</option>`).join("")}</select></label>
    <label>Mỗi trang<select name="size">${[10, 30, 50, 100].map((n) => `<option ${+filter.size === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>
    <button class="btn primary">Lọc</button>`;
  $("#filters").onsubmit = (e) => {
    e.preventDefault();
    filter = { ...Object.fromEntries(new FormData(e.target)), page: 1, seed: Date.now() };
    loadItems();
  };
}

const filterOnly = () => Object.fromEntries(Object.entries(filter).filter(([k, v]) => v && !["page", "size", "sort", "seed"].includes(k)));

async function loadItems() {
  loaded = true;
  const d = await guard(() => api("items?" + new URLSearchParams(Object.entries(filter).filter(([, v]) => v !== "" && v != null))));
  if (!d) return;
  itemsData = d.items;
  $("#review-summary").innerHTML = `<b>${d.total}</b> mục khớp bộ lọc
    <button class="btn" id="filter-draft">Nhờ teacher nháp các mục đang lọc</button>`;
  $("#filter-draft").onclick = async () => {
    if (!confirm(`Gửi teacher nháp câu trả lời cho các mục nháp/chờ duyệt khớp bộ lọc (tối đa ${d.total})?`)) return;
    const r = await guard(() => api("draft", { method: "POST", body: { filter: filterOnly() } }));
    if (r) toast(`Đã xếp việc #${r.id}: ${r.items} mục`);
  };
  $("#item-list").innerHTML = itemsData.map(cardHtml).join("") || '<p class="muted">Không có mục nào.</p>';
  const pages = Math.max(1, Math.ceil(d.total / d.size));
  $("#pager").innerHTML = `<button class="btn" id="prev" ${d.page <= 1 ? "disabled" : ""}>‹ Trang trước</button>
    Trang ${d.page} / ${pages} <button class="btn" id="next" ${d.page >= pages ? "disabled" : ""}>Trang sau ›</button>`;
  const go = (n) => { filter.page = n; loadItems(); window.scrollTo(0, 0); };
  $("#prev").onclick = () => go(d.page - 1);
  $("#next").onclick = () => go(d.page + 1);
  cards().forEach((el, i) => bindCard(el, i));
  focusCard(0, false);
}

const approveBlock = (it) => it.kind === "rl_verifiable" && !(it.ref_pass && it.neg_fail)
  ? "RL kiểm chứng: cần chạy verifier (Ctrl+T) — đáp án chuẩn đạt và có câu sai mẫu bị trượt" : "";

function cardHtml(it) {
  const c = it.content, block = approveBlock(it);
  return `<div class="item" tabindex="-1" data-id="${esc(it.id)}">
    <div class="meta"><span class="badge site">${esc(it.source_id)}</span><span class="badge">${label("kind", it.kind)}</span>
      <span class="badge ${it.status}">${label("status", it.status)}</span><span class="badge split ${it.split}">${label("split", it.split)}</span>
      <span>${esc(label("domain", c.domain))}</span>${c.difficulty ? `<span>độ khó ${c.difficulty}</span>` : ""}
      <span>bản ${it.version_no} · ${esc(it.origin)}</span>${it.flags.map((f) => `<span class="warn">${label("flag", f)}</span>`).join("")}
      <span class="muted">${esc(it.id)}</span></div>
    ${contentHtml(c)}
    <div class="runs-result runs"></div>
    <div class="actions">
      <button class="btn primary act-approve" ${block ? `disabled title="${esc(block)}"` : ""}>Duyệt (Enter)</button>
      <button class="btn act-edit">Sửa…</button><button class="btn act-needs">Cần sửa (Ctrl+E)</button>
      <button class="btn danger act-drop">Bỏ (Ctrl+D)</button><button class="btn act-pending">Chờ duyệt (Ctrl+R)</button>
      ${c.verifier ? '<button class="btn act-verify">Chạy verifier (Ctrl+T)</button>' : ""}
      <button class="btn act-history">Lịch sử &amp; bình luận (Alt+H)</button>
      <input type="text" class="note" placeholder="Ghi chú cho kết luận (tuỳ chọn)"></div>
    <div class="side" hidden></div></div>`;
}

const cards = () => document.querySelectorAll("#item-list .item");
function markActive() { cards().forEach((el, k) => el.classList.toggle("active", k === active)); }

function bindCard(el, i) {
  el.addEventListener("focusin", () => { active = i; markActive(); });
  el.addEventListener("mousedown", () => { active = i; markActive(); });
  el.querySelector(".act-approve").onclick = () => act(i, "review", { verdict: "approved" }, true);
  el.querySelector(".act-needs").onclick = () => act(i, "review", { verdict: "needs_work" }, true);
  el.querySelector(".act-drop").onclick = () => act(i, "review", { verdict: "dropped" }, true);
  el.querySelector(".act-pending").onclick = () => act(i, "pending", {}, false);
  el.querySelector(".act-edit").onclick = () => openCompose(itemsData[i], "correct");
  el.querySelector(".act-verify")?.addEventListener("click", () => verify(i));
  el.querySelector(".act-history").onclick = () => toggleSide(i);
}

function focusCard(i, scroll = true) {
  if (!cards().length) return;
  active = Math.max(0, Math.min(i, cards().length - 1));
  markActive();
  const el = cards()[active];
  if (scroll) el.scrollIntoView({ block: "start" });
  el.focus({ preventScroll: !scroll });
}

function replaceCard(i, it) {
  const hadFocus = cards()[i].contains(document.activeElement);
  itemsData[i] = it;
  const fresh = document.createElement("div");
  fresh.innerHTML = cardHtml(it);
  cards()[i].replaceWith(fresh.firstElementChild);
  bindCard(cards()[i], i);
  markActive();
  if (hadFocus) cards()[i].focus({ preventScroll: true });
}

// Saves run one after another; the cursor moves on at once, so fast key presses each hit the next card.
let saving = Promise.resolve();
function act(i, action, body, next) {
  const it = itemsData[i];
  if (action === "review" && body.verdict === "approved" && approveBlock(it)) return toast(approveBlock(it), true);
  const note = cards()[i].querySelector(".note").value.trim();
  if (next && i + 1 < cards().length) focusCard(i + 1);
  else if (next) toast("Hết trang này: sang trang sau hoặc lọc lại");
  saving = saving.then(async () => {
    const saved = await guard(() => api(`items/${encodeURIComponent(it.id)}/${action}`, { method: "POST", body: { ...body, comment: note } }));
    if (saved && itemsData[i]?.id === it.id) replaceCard(i, saved);
    else if (!saved) focusCard(i);  // failed: put the cursor back on the card that needs attention
  });
  return saving;
}

async function chooseKey(i, letter) {
  const it = itemsData[i];
  if (letter === it.content.answer_key) return act(i, "review", { verdict: "approved" }, true);
  if (!confirm(`Đổi đáp án ${it.content.answer_key} → ${letter} (lưu thành bản mới, kết luận "Đã sửa")?`)) return;
  return act(i, "correct", { content: { ...it.content, answer_key: letter } }, true);
}

async function verify(i) {
  const r = await guard(() => api(`items/${encodeURIComponent(itemsData[i].id)}/verify`, { method: "POST", body: {} }));
  if (!r) return;
  replaceCard(i, r.item);
  cards()[i].querySelector(".runs-result").innerHTML = r.runs.map((x) => `<div class="${x.ok ? "ok" : "bad"}">
    ${x.role === "reference" ? "Đáp án chuẩn" : "Câu sai mẫu"}: ${x.passed ? "đạt" : "trượt"} ${x.ok ? "✓" : "✗ (sai kỳ vọng)"} — ${esc(x.detail)}
    <span class="muted">${esc(x.candidate.slice(0, 80))}</span></div>`).join("") +
    (r.runs.length < 2 && itemsData[i].kind === "rl_verifiable" ? '<div class="bad">Thiếu câu sai mẫu (meta.negatives): mở Sửa… để thêm.</div>' : "");
  focusCard(i, false);
}

async function toggleSide(i) {
  const side = cards()[i].querySelector(".side");
  if (!side.hidden) { side.hidden = true; return; }
  const d = await guard(() => api(`items/${encodeURIComponent(itemsData[i].id)}`));
  if (!d) return;
  const draw = (d) => {
    side.innerHTML = historyHtml(d) + "<h4>Bình luận</h4>" + commentsHtml(d);
    side.querySelector(".comment-form").onsubmit = async (e) => {
      e.preventDefault();
      const nd = await guard(() => api(`items/${encodeURIComponent(itemsData[i].id)}/comment`, { method: "POST", body: { body: e.target.body.value } }));
      if (nd) draw(nd);
    };
  };
  draw(d);
  side.hidden = false;
}

document.addEventListener("keydown", (e) => {
  if (!$("#tab-review").classList.contains("active") || !cards().length) return;
  if (e.target.closest && e.target.closest("#filters, #pager, #review-summary, .side")) return;
  const tag = e.target.tagName, inField = tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA";
  const k = e.key.toLowerCase(), plain = !e.ctrlKey && !e.altKey && !e.metaKey, it = itemsData[active];
  if (e.key === "Enter" && plain && tag !== "BUTTON" && tag !== "SUMMARY" && tag !== "SELECT") {
    e.preventDefault(); act(active, "review", { verdict: "approved" }, true);
  } else if (plain && !inField && /^[1-9]$/.test(k) && it.content.choices) {
    const letter = LETTERS[+k - 1];
    if (+k <= it.content.choices.length) { e.preventDefault(); chooseKey(active, letter); }
  } else if (e.ctrlKey && k === "d") { e.preventDefault(); act(active, "review", { verdict: "dropped" }, true); }
  else if (e.ctrlKey && k === "e") { e.preventDefault(); act(active, "review", { verdict: "needs_work" }, true); }
  else if (e.ctrlKey && k === "r") { e.preventDefault(); act(active, "pending", {}, false); }
  else if ((e.ctrlKey || e.altKey) && k === "t") { e.preventDefault(); if (it.content.verifier) verify(active); }
  else if (e.altKey && k === "h") { e.preventDefault(); toggleSide(active); }
  else if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); focusCard(active + 1); }
  else if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); focusCard(active - 1); }
});

window.addEventListener("tab", (e) => { if (e.detail === "review" && !loaded) { renderFilters(); loadItems(); } });
window.addEventListener("data-changed", () => { if (loaded) loadItems(); });
