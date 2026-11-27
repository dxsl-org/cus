// Tab "Soạn": structured editor for every TaskItem kind. New item, new version of an item, or a reviewer's
// correction. Fields the editor does not manage (meta.external_id, …) are kept from the loaded version.
const NEG_SEP = "\n---\n";
let draft = { mode: "new", item: null, kind: "sft", source: "", base: {} };

function openCompose(item, mode) {
  draft = item ? { mode, item, kind: item.kind, source: item.source_id, base: structuredClone(item.content) }
    : { mode: "new", item: null, kind: draft.kind, source: draft.source, base: {} };
  showTab("compose");
  renderCompose();
}

function addGrounding(g) {
  if ($("#compose-form")) draft.base = readCompose();
  draft.base.grounding = [...(draft.base.grounding || []), g];
  if ($("#compose-form")) renderCompose();
  toast(`Đã gắn đoạn vào mục đang soạn (${draft.base.grounding.length} đoạn): mở tab Soạn`);
}

const show = (kinds) => kinds.includes(draft.kind);
const ta = (name, value, rows = 4, ph = "") => `<textarea name="${name}" rows="${rows}" placeholder="${esc(ph)}">${esc(value ?? "")}</textarea>`;

function turnsHtml(messages) {
  return (messages.length ? messages : [{ role: "user", content: "" }]).map((m, i) => `<div class="turn">
    ${selectHtml("role", "role", m.role, null, ["system", "user", "assistant"])}${ta("turn", m.content, 3)}
    <button type="button" class="btn danger del-turn" data-i="${i}" title="Xoá lượt">×</button></div>`).join("");
}

function verifierHtml(v) {
  const norm = v.normalize || ["nfc"];
  const spec = v.type === "unit_test" ? (v.spec || {}).test : v.type === "regex" ? v.spec : "";
  return `<fieldset><legend>Verifier</legend><div class="filters">
    <label>Loại${selectHtml("vtype", "vtype", v.type || "", "— không có —", ["exact", "numeric", "regex", "choice", "unit_test"])}</label>
    <label>Sai số tuyệt đối<input type="text" name="tol_abs" value="${esc((v.tolerance || {}).abs ?? "")}" size="6"></label>
    <label>Sai số tương đối<input type="text" name="tol_rel" value="${esc((v.tolerance || {}).rel ?? "")}" size="6"></label>
    <span class="skills">${["nfc", "lower", "strip_punct", "fold_accents"].map((n) =>
      `<label class="check"><input type="checkbox" name="norm" value="${n}" ${norm.includes(n) ? "checked" : ""}>${n}</label>`).join("")}</span></div>
    <label>Regex / mã kiểm thử Python (unit_test)${ta("vspec", spec, 4)}</label>
    <label>Mã chuẩn bị (setup, unit_test)${ta("vsetup", (v.spec || {}).setup, 2)}</label>
    <label>Câu sai mẫu — verifier phải đánh trượt (ngăn cách bằng dòng ---)${ta("negatives", ((draft.base.meta || {}).negatives || []).join(NEG_SEP), 4)}</label></fieldset>`;
}

function renderCompose() {
  const c = draft.base, editing = draft.mode !== "new";
  const ref = c.reference_answer, refList = Array.isArray(ref);
  const title = { new: "Soạn mục mới", edit: `Sửa ${draft.item?.id} (lưu thành bản mới)`, correct: `Sửa khi duyệt ${draft.item?.id}` }[draft.mode];
  $("#compose").innerHTML = `<div class="compose-grid"><form id="compose-form" class="panel"><h2>${esc(title)}</h2>
    <div class="filters">
      <label>Loại${editing ? `<b>${label("kind", draft.kind)}</b>` : selectHtml("kind", "kind", draft.kind, null)}</label>
      <label>Nguồn${editing ? `<b>${esc(draft.source)}</b>` : selectHtml("source", "source", draft.source, "— chọn nguồn —")}</label>
      <label>Domain${selectHtml("domain", "domain", c.domain, "— chọn —")}</label>
      <label>Độ khó${selectHtml("difficulty", "difficulty", c.difficulty, "—")}</label>
      <label>Ngôn ngữ<input type="text" name="lang" value="${esc(c.lang || "vi")}" size="4"></label></div>
    <div class="skills">${Object.entries(OPTIONS.skill_names).map(([s, n]) =>
      `<label class="check"><input type="checkbox" name="skills" value="${s}" ${(c.skills || []).includes(s) ? "checked" : ""}>${esc(n)}</label>`).join("")}</div>
    <h3>Hội thoại</h3><div id="turns">${turnsHtml(c.messages || [])}</div>
    <button type="button" class="btn" id="add-turn">+ Thêm lượt</button>
    ${show(["preference"]) ? `<div class="pair"><label>Chosen (tốt hơn)${ta("chosen", c.chosen, 6)}</label><label>Rejected${ta("rejected", c.rejected, 6)}</label></div>` : ""}
    ${show(["eval_mcq"]) ? `<label>Các lựa chọn (mỗi dòng một lựa chọn, A, B, C…)${ta("choices", (c.choices || []).join("\n"), 5)}</label>
      <label>Đáp án đúng${selectHtml("answer_key", "letter", c.answer_key, "—", LETTERS.split(""))}</label>` : ""}
    ${show(["rl_verifiable", "eval_open", "eval_code", "reasoning"]) ? `<label>Đáp án chuẩn${ta("reference", refList ? ref.join("\n") : ref, 5)}</label>
      <label class="check"><input type="checkbox" name="ref_list" ${refList ? "checked" : ""}>Mỗi dòng là một đáp án được chấp nhận</label>
      <label>Rubric chấm${ta("rubric", c.rubric, 3)}</label>` : ""}
    ${show(["rl_verifiable", "eval_code", "eval_mcq", "eval_open"]) ? verifierHtml(c.verifier || {}) : ""}
    <h3>Grounding</h3>${(c.grounding || []).map((g, i) => `<div class="msg system">${esc(g.source_id)} ${esc(g.url || g.doc_id)}
      <button type="button" class="btn danger del-ground" data-i="${i}">Gỡ</button>\n${esc((g.quote || "").slice(0, 400))}</div>`).join("")
      || '<p class="muted">Tìm đoạn ở tab Corpus rồi bấm "Gắn vào mục đang soạn".</p>'}
    <div class="actions" style="margin-top:10px">${draft.mode === "new" ? `<button class="btn primary" data-save="submit">Lưu &amp; gửi duyệt</button>
      <button class="btn" data-save="draft">Lưu nháp</button><button class="btn" data-save="teacher">Lưu nháp &amp; nhờ teacher trả lời</button>`
      : draft.mode === "correct" ? `<button class="btn primary" data-save="correct">Lưu bản sửa (Đã sửa)</button><button class="btn" data-save="edit">Lưu bản mới, về chờ duyệt</button>`
      : `<button class="btn primary" data-save="edit">Lưu bản mới</button>`}
      <button type="button" class="btn" id="compose-new">Soạn mục mới</button></div></form>
    <div><div class="panel"><h2>Kiểm tra</h2><div id="compose-check"></div></div><div class="panel"><h2>Xem trước</h2><div id="compose-preview"></div></div></div></div>`;
  bindCompose();
  checkCompose();
}

function readCompose() {
  const f = $("#compose-form"), out = structuredClone(draft.base), val = (n) => f.elements[n]?.value ?? "";
  if (draft.mode === "new") { draft.kind = val("kind") || draft.kind; draft.source = val("source"); }
  out.domain = val("domain") || undefined;
  out.difficulty = val("difficulty") ? +val("difficulty") : undefined;
  out.lang = val("lang").trim();
  out.skills = [...f.querySelectorAll("[name=skills]:checked")].map((x) => x.value);
  const roles = [...f.querySelectorAll("[name=role]")], texts = [...f.querySelectorAll("[name=turn]")];
  out.messages = roles.map((r, i) => ({ role: r.value, content: texts[i].value })).filter((m) => m.content.trim());
  if (f.elements.chosen) { out.chosen = val("chosen") || undefined; out.rejected = val("rejected") || undefined; }
  if (f.elements.choices) { out.choices = val("choices").split("\n").map((s) => s.trim()).filter(Boolean); out.answer_key = val("answer_key") || undefined; }
  if (f.elements.reference) {
    const lines = val("reference").split("\n").map((s) => s.trim()).filter(Boolean);
    out.reference_answer = f.elements.ref_list.checked ? lines : (val("reference").trim() || undefined);
    out.rubric = val("rubric").trim() || undefined;
  }
  if (f.elements.vtype) readVerifier(f, out, val);
  for (const k of Object.keys(out)) if (out[k] === undefined || (Array.isArray(out[k]) && !out[k].length && k !== "skills" && k !== "messages")) delete out[k];
  return out;
}

function readVerifier(f, out, val) {
  const type = val("vtype");
  const negs = val("negatives").split(/\n---\n/).map((s) => s.trim()).filter(Boolean);
  out.meta = { ...(out.meta || {}) };
  if (negs.length) out.meta.negatives = negs; else delete out.meta.negatives;
  if (!Object.keys(out.meta).length) delete out.meta;
  if (!type) { delete out.verifier; return; }
  const prev = out.verifier && out.verifier.type === type ? out.verifier : {};
  const v = { ...prev, type, normalize: [...f.querySelectorAll("[name=norm]:checked")].map((x) => x.value) };
  const abs = parseFloat(val("tol_abs")), rel = parseFloat(val("tol_rel"));
  if (type === "numeric" && (!isNaN(abs) || !isNaN(rel))) v.tolerance = { ...(isNaN(abs) ? {} : { abs }), ...(isNaN(rel) ? {} : { rel }) };
  else delete v.tolerance;
  if (type === "regex") v.spec = val("vspec");
  if (type === "unit_test") v.spec = { ...(prev.spec || {}), language: "python", test: val("vspec"), ...(val("vsetup").trim() ? { setup: val("vsetup") } : {}) };
  out.verifier = v;
}

let checkTimer;
function checkCompose() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(async () => {
    const content = readCompose();
    $("#compose-preview").innerHTML = contentHtml(content);
    const r = await api("validate", { method: "POST", body: { kind: draft.kind, content } }).catch((e) => ({ errors: [e.message] }));
    $("#compose-check").innerHTML = r.errors.length ? `<ul class="errors-box">${r.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`
      : `<span class="ok">Hợp lệ.</span> ${draft.mode === "new" ? `Sẽ vào tập <b>${label("split", r.split)}</b> (cố định theo câu hỏi).` : ""}`;
  }, 250);
}

function bindCompose() {
  const f = $("#compose-form");
  f.oninput = checkCompose;
  f.onchange = (e) => { if (["kind", "vtype"].includes(e.target.name)) { draft.base = readCompose(); renderCompose(); } };
  $("#add-turn").onclick = () => {
    draft.base = readCompose();
    const last = (draft.base.messages || []).at(-1)?.role;
    draft.base.messages = [...(draft.base.messages || []), { role: last === "user" ? "assistant" : "user", content: " " }];
    renderCompose();
  };
  f.querySelectorAll(".del-turn").forEach((b) => (b.onclick = () => { b.closest(".turn").remove(); checkCompose(); }));
  f.querySelectorAll(".del-ground").forEach((b) => (b.onclick = () => { draft.base = readCompose(); draft.base.grounding.splice(+b.dataset.i, 1); renderCompose(); }));
  $("#compose-new").onclick = () => openCompose(null, "new");
  f.querySelectorAll("[data-save]").forEach((b) => (b.onclick = (e) => { e.preventDefault(); saveCompose(b.dataset.save); }));
}

async function saveCompose(how) {
  const content = readCompose();
  const id = draft.item && encodeURIComponent(draft.item.id);
  const saved = await guard(() => how === "edit" ? api(`items/${id}/content`, { method: "PUT", body: { content } })
    : how === "correct" ? api(`items/${id}/correct`, { method: "POST", body: { content } })
    : api("items", { method: "POST", body: { kind: draft.kind, source: draft.source, content, submit: how === "submit" } }));
  if (!saved) return;
  if (how === "teacher") {
    const job = await guard(() => api("draft", { method: "POST", body: { item_ids: [saved.id] } }));
    if (job) toast(`Đã lưu ${saved.id} và xếp việc teacher #${job.id}`);
  } else toast(`Đã lưu ${saved.id} (bản ${saved.version_no}, ${label("status", saved.status)})`);
  window.dispatchEvent(new Event("data-changed"));
  if (draft.mode === "correct") showTab("review");
  else openCompose(how === "edit" ? saved : null, how === "edit" ? "edit" : "new");
}

window.addEventListener("tab", (e) => { if (e.detail === "compose" && !$("#compose-form")) renderCompose(); });
