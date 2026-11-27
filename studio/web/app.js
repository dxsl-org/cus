// Shared helpers: API calls (token + reviewer name), labels, tabs, toast, job status line.
const LABELS = {
  status: { draft: "Nháp", pending: "Chờ duyệt", auto: "Đạt tự động", approved: "Đã duyệt", corrected: "Đã sửa",
    dropped: "Đã bỏ" },
  kind: { sft: "SFT", multi_turn: "Hội thoại nhiều lượt", preference: "Preference (A/B)", reasoning: "Suy luận",
    rl_verifiable: "RL kiểm chứng", eval_mcq: "Eval trắc nghiệm", eval_open: "Eval tự luận", eval_code: "Eval code" },
  split: { train: "Huấn luyện", dev: "Phát triển", test: "Kiểm tra", holdout: "Giữ riêng" },
  flag: { pii: "Dữ liệu cá nhân", unsafe: "Không an toàn", contaminated: "Nhiễm eval", duplicate: "Trùng", none: "Không cờ" },
  origin: { human: "Người soạn", model: "Model nháp", import: "Nhập file" },
  usage_scope: { train_ok: "Được huấn luyện", research_only: "Chỉ nghiên cứu", blocked: "Không huấn luyện" },
  job: { queued: "Đang chờ", running: "Đang chạy", done: "Xong", error: "Lỗi", cancelled: "Đã huỷ" },
  jobkind: { draft: "Teacher nháp", import_file: "Nhập file", import_eval: "Nhập eval" },
  verdict: { approved: "Duyệt", corrected: "Sửa", dropped: "Bỏ", needs_work: "Cần sửa" },
};
let OPTIONS = {};

const label = (kind, v) => (LABELS[kind] && LABELS[kind][v]) || v || "";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const $ = (sel, root = document) => root.querySelector(sel);
const pct = (x) => (x == null ? "—" : (100 * x).toFixed(1).replace(".", ",") + " %");
const when = (iso) => (iso ? new Date(iso).toLocaleString("vi-VN") : "");
const TOKEN = new URLSearchParams(location.search).get("token") || sessionStorage.getItem("studio-token") || "";
if (TOKEN) sessionStorage.setItem("studio-token", TOKEN);

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-Studio-Token": TOKEN,
    "X-Studio-User": encodeURIComponent(localStorage.getItem("studio-user") || "") } };
  if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    init.headers["Content-Type"] = "application/json";
  }
  const res = await fetch("/api/" + path, init);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (isError ? " error" : "");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => (t.className = ""), isError ? 7000 : 2500);
}

async function guard(fn) {
  try { return await fn(); } catch (e) { toast(e.message, true); }
}

function selectHtml(name, kind, value = "", empty = "— tất cả —", values = null) {
  const opts = (values || OPTIONS[kind] || []).map((v) =>
    `<option value="${esc(v)}" ${String(v) === String(value ?? "") ? "selected" : ""}>${esc(label(kind, v))}</option>`);
  return `<select name="${name}">${empty !== null ? `<option value="">${empty}</option>` : ""}${opts.join("")}</select>`;
}

function showTab(name) {
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + name));
  window.dispatchEvent(new CustomEvent("tab", { detail: name }));
}

async function refreshOptions() {
  OPTIONS = await api("options");
  LABELS.domain = { ...OPTIONS.domain_names };
  LABELS.skill = { ...OPTIONS.skill_names };
}

async function pollJobs() {
  const jobs = await api("jobs").catch(() => null);
  if (!jobs) return;
  const cur = jobs.find((j) => j.state === "running");
  const queued = jobs.filter((j) => j.state === "queued").length;
  $("#job-status").textContent = (cur ? `${label("jobkind", cur.kind)} #${cur.id}: ${cur.done}/${cur.total ?? "?"}` : "") +
    (queued ? ` · ${queued} việc chờ` : "");
  window.dispatchEvent(new CustomEvent("jobs", { detail: jobs }));
}

window.addEventListener("DOMContentLoaded", async () => {
  await guard(refreshOptions);
  const name = $("#user-name");
  name.value = localStorage.getItem("studio-user") || OPTIONS.user || "";
  name.onchange = () => { localStorage.setItem("studio-user", name.value.trim()); toast("Đã đổi người duyệt"); };
  document.querySelectorAll("nav button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  window.dispatchEvent(new Event("ready"));
  showTab("review");
  pollJobs();
  setInterval(pollJobs, 3000);
});
