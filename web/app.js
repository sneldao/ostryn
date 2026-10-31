/* Ostryn dashboard — reads audit_results + deletion_requests from Appwrite
   TablesDB. config.js provides APPWRITE_ENDPOINT, APPWRITE_PROJECT_ID,
   APPWRITE_DATABASE_ID. Tables are granted read("any") at seed time, so no
   auth is needed for this read-only explorer. */

const client = new Appwrite.Client()
  .setEndpoint(APPWRITE_ENDPOINT)
  .setProject(APPWRITE_PROJECT_ID);

// TablesDB on current SDKs; Databases kept as a fallback for older bundles.
const tdb = Appwrite.TablesDB
  ? new Appwrite.TablesDB(client)
  : new Appwrite.Databases(client);

const Q = Appwrite.Query;

async function listAll(table) {
  const out = [];
  let cursor = null;
  for (;;) {
    const queries = [Q.limit(500), Q.orderAsc("$id")];
    if (cursor) queries.push(Q.cursorAfter(cursor));
    const res = tdb.listRows
      ? await tdb.listRows(APPWRITE_DATABASE_ID, table, queries)
      : await tdb.listDocuments(APPWRITE_DATABASE_ID, table, queries);
    const rows = res.rows || res.documents;
    if (!rows.length) break;
    out.push(...rows);
    if (rows.length < 500) break;
    cursor = rows[rows.length - 1].$id;
  }
  return out;
}

const state = { cases: [], results: {}, filter: "ALL", q: "", sel: null };

function chip(status) {
  return `<span class="chip ${status}">${status.replace(/_/g, " ")}</span>`;
}

function renderList() {
  const q = state.q.toLowerCase();
  const ul = document.getElementById("cases");
  ul.innerHTML = "";
  for (const c of state.cases) {
    const st = state.results[c.case_id]?.deletion_status || "—";
    if (state.filter !== "ALL" && st !== state.filter) continue;
    if (q && !c.case_id.includes(q) && !(c.email || "").toLowerCase().includes(q))
      continue;
    const li = document.createElement("li");
    if (c.case_id === state.sel) li.className = "sel";
    li.innerHTML = `<div><div class="cid">${c.case_id}</div><div class="em">${c.email || ""}</div></div>${chip(st)}`;
    li.onclick = () => select(c.case_id);
    ul.appendChild(li);
  }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (m) => `&#${m.charCodeAt(0)};`);
}

const PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"];

function renderDetail(c) {
  const r = state.results[c.case_id] || {};
  const steps = JSON.parse(r.steps_json || "[]");
  const d = document.getElementById("detail");
  const groups = {};
  for (const s of steps) (groups[s.phase] ||= []).push(s);
  d.innerHTML = `
    <h2>${c.case_id} ${chip(r.deletion_status || "—")}</h2>
    <dl class="kv">
      <dt>requester</dt><dd>${esc(c.email)}</dd>
      <dt>requested_at</dt><dd>${esc(c.requested_at)}</dd>
      <dt>reported</dt><dd>${esc(c.reported_status)} @ ${esc(c.reported_at)}</dd>
      <dt>audit_at</dt><dd>${esc(c.audit_at)}</dd>
      <dt>audited_at</dt><dd>${esc(r.audited_at || "—")}</dd>
    </dl>
    <div class="request">${esc(c.request_text || "")}</div>
    ${PHASES.map(
      (p) => `
      <div class="phase">
        <h3>${p} · ${(groups[p] || []).length} steps</h3>
        <table><tr><th>#</th><th>system</th><th>resource</th><th>operation</th><th>state</th><th>evidence</th></tr>
        ${(groups[p] || [])
          .map(
            (s) => `<tr>
            <td class="mono">${s.step_no}</td>
            <td class="mono">${esc(s.system_id)}</td>
            <td class="mono">${esc(s.resource_id)}</td>
            <td>${esc(s.operation)}</td>
            <td>${esc(s.state)}</td>
            <td class="mono">${esc(s.evidence_id || "—")}</td>
          </tr>`,
          )
          .join("")}
        </table>
      </div>`,
    ).join("")}`;
}

function select(caseId) {
  state.sel = caseId;
  renderList();
  renderDetail(state.cases.find((c) => c.case_id === caseId));
}

function renderStats() {
  const counts = {};
  for (const c of state.cases) {
    const st = state.results[c.case_id]?.deletion_status || "UNAUDITED";
    counts[st] = (counts[st] || 0) + 1;
  }
  document.getElementById("stats").innerHTML = Object.entries(counts)
    .map(([k, v]) => `<div><b>${v}</b>${k.replace(/_/g, " ")}</div>`)
    .join("");
}

async function boot() {
  const [cases, results] = await Promise.all([
    listAll("deletion_requests"),
    listAll("audit_results"),
  ]);
  state.cases = cases;
  state.results = Object.fromEntries(results.map((r) => [r.case_id, r]));
  renderStats();
  renderList();
}

document.getElementById("search").oninput = (e) => {
  state.q = e.target.value;
  renderList();
};
document.querySelectorAll("#filters button").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll("#filters button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    state.filter = b.dataset.f;
    renderList();
  };
});

boot().catch((e) => {
  document.getElementById("detail").innerHTML =
    `<div class="empty">Failed to load from Appwrite: ${esc(e.message)}<br>` +
    `Check web/config.js and table read permissions.</div>`;
});
