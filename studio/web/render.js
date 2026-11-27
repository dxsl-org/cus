// Read-only rendering of TaskItem content, version diffs and the item history panel.
const LETTERS = "ABCDEFGHIJ";

function messagesHtml(messages) {
  return (messages || []).map((m) =>
    `<div class="msg ${esc(m.role)}"><span class="role">${esc(m.role)}</span>${esc(m.content)}</div>`).join("");
}

function answerText(a) {
  return Array.isArray(a) ? a.map((x, i) => `${i + 1}. ${x}`).join("\n") : String(a ?? "");
}

function contentHtml(c) {
  const parts = [messagesHtml(c.messages)];
  if (c.choices) parts.push(`<div class="field"><b>Lựa chọn</b>${c.choices.map((ch, i) =>
    `<div class="choice ${LETTERS[i] === c.answer_key ? "key" : ""}"><b>${LETTERS[i]}.</b> ${esc(ch)}</div>`).join("")}</div>`);
  if (c.chosen != null) parts.push(`<div class="pair"><div class="field"><b>Chosen (tốt hơn)</b>
    <div class="msg assistant">${esc(answerText(c.chosen))}</div></div><div class="field"><b>Rejected</b>
    <div class="msg">${esc(answerText(c.rejected))}</div></div></div>`);
  if (c.reference_answer != null) parts.push(`<div class="field"><b>Đáp án chuẩn${Array.isArray(c.reference_answer) ?
    " (chấp nhận nhiều)" : ""}</b><div class="msg assistant">${esc(answerText(c.reference_answer))}</div></div>`);
  if (c.rubric) parts.push(`<div class="field"><b>Rubric</b><div class="msg">${esc(answerText(c.rubric))}</div></div>`);
  if (c.verifier) parts.push(`<div class="field"><b>Verifier</b><code>${esc(JSON.stringify(c.verifier).slice(0, 400))}</code></div>`);
  const neg = (c.meta || {}).negatives;
  if (neg && neg.length) parts.push(`<div class="field"><b>Câu sai mẫu</b>${neg.map((n) => `<div class="msg">${esc(n)}</div>`).join("")}</div>`);
  if (c.grounding && c.grounding.length) parts.push(`<div class="field"><b>Grounding</b>${c.grounding.map((g) =>
    `<div class="msg system">${esc(g.source_id || "")} ${esc(g.url || g.doc_id || "")}\n${esc((g.quote || "").slice(0, 600))}</div>`).join("")}</div>`);
  return parts.join("");
}

// Word-level LCS diff (as SAOS studio): words only in `b` are <ins>, words only in `a` are <del>.
function diffHtml(a, b) {
  const x = a.split(/\s+/).filter(Boolean), y = b.split(/\s+/).filter(Boolean);
  if (x.length * y.length > 4e6) return `<del>${esc(a)}</del><br><ins>${esc(b)}</ins>`;
  const L = Array.from({ length: x.length + 1 }, () => new Uint32Array(y.length + 1));
  for (let i = x.length - 1; i >= 0; i--) for (let j = y.length - 1; j >= 0; j--)
    L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < x.length || j < y.length) {
    if (i < x.length && j < y.length && x[i] === y[j]) { out.push(esc(y[j])); i++; j++; }
    else if (j < y.length && (i >= x.length || L[i][j + 1] >= L[i + 1][j])) out.push(`<ins>${esc(y[j++])}</ins>`);
    else out.push(`<del>${esc(x[i++])}</del>`);
  }
  return out.join(" ");
}

// Flattened text of one version, so a diff shows every field that changed.
function contentText(c) {
  const lines = (c.messages || []).map((m) => `[${m.role}] ${m.content}`);
  for (const k of ["choices", "answer_key", "chosen", "rejected", "reference_answer", "rubric", "domain", "difficulty", "skills"])
    if (c[k] != null) lines.push(`[${k}] ${Array.isArray(c[k]) ? c[k].join(" | ") : c[k]}`);
  if (c.verifier) lines.push(`[verifier] ${JSON.stringify(c.verifier)}`);
  if ((c.meta || {}).negatives) lines.push(`[negatives] ${c.meta.negatives.join(" | ")}`);
  return lines.join("\n");
}

function historyHtml(d) {
  const versions = d.versions.map((v, k) => {
    const prev = k ? contentText(d.versions[k - 1].content) : "";
    const body = k ? diffHtml(prev, contentText(v.content)) : esc(contentText(v.content));
    return `<details ${k === d.versions.length - 1 ? "open" : ""}><summary>Bản ${v.version_no} · ${esc(v.origin)} ·
      ${esc(v.created_by)} · ${when(v.created_at)}</summary><div class="msg" style="white-space:pre-wrap">${body}</div></details>`;
  }).join("");
  const reviews = d.reviews.map((r) => `<div>${when(r.at)} · ${esc(r.reviewer)}: <b>${label("verdict", r.verdict)}</b>
    (bản id ${r.version_id})${r.comment ? " — " + esc(r.comment) : ""}</div>`).join("") || "<span class=muted>chưa có</span>";
  const runs = d.runs.slice(-12).map((r) => `<div>${when(r.at)} · ${r.role}: ${r.passed ? "đạt" : "trượt"} — ${esc(r.detail || "")}</div>`).join("")
    || "<span class=muted>chưa chạy</span>";
  const edits = d.edits.map((e) => `<div>${when(e.at)} · ${esc(e.by)}: ${esc(e.field)} ${esc(e.old_value ?? "—")} → ${esc(e.new_value ?? "—")}</div>`).join("");
  return `<h4>Phiên bản</h4>${versions}<h4>Kết luận duyệt</h4>${reviews}<h4>Verifier</h4><div class="runs">${runs}</div>
    <details><summary>Nhật ký sửa (${d.edits.length})</summary>${edits}</details>`;
}

function commentsHtml(d) {
  return `<div class="comments">${d.comments.map((c) => `<div class="c"><b>${esc(c.author)}</b> · ${when(c.at)}: ${esc(c.body)}</div>`).join("")
    || '<span class="muted">Chưa có bình luận.</span>'}</div>
    <form class="comment-form filters"><input type="text" name="body" placeholder="Viết bình luận rồi Enter" style="flex:1">
    <button class="btn">Gửi</button></form>`;
}
