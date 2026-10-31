/* Ostryn ghost-data audit explorer — reads audit_results, deletion_requests
   and evidence_files straight from Appwrite TablesDB. config.js provides
   APPWRITE_ENDPOINT, APPWRITE_PROJECT_ID, APPWRITE_DATABASE_ID. Tables are
   granted read("any") at seed time, so no auth is needed for this explorer. */

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

const state = {
  cases: [],
  results: {},
  evidence: {}, // file_id -> manifest row (live join, straight from TablesDB)
  filter: "ALL",
  q: "",
  sel: null,
};

function chip(status) {
  return `<span class="chip ${status}">${status.replace(/_/g, " ")}</span>`;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (m) => `&#${m.charCodeAt(0)};`);
}

/* ---------------------------------------------------------- case list */

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
    li.innerHTML =
      `<div><div class="cid"><span class="dot ${st}"></span>${c.case_id}</div>` +
      `<div class="em">${esc(c.email || "")}</div></div>${chip(st)}`;
    li.onclick = () => select(c.case_id);
    ul.appendChild(li);
  }
}

/* ------------------------------------------------------------- detail */

const PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"];

function evChip(id) {
  if (!id) return `<span style="color:var(--dim)">—</span>`;
  return `<button class="ev" data-ev="${esc(id)}">${esc(id)}</button>`;
}

function renderDetail(c) {
  const r = state.results[c.case_id] || {};
  const steps = JSON.parse(r.steps_json || "[]");
  const st = r.deletion_status || "—";
  const d = document.getElementById("detail");
  const groups = {};
  for (const s of steps) (groups[s.phase] ||= []).push(s);
  const verdictTxt = {
    COMPLETE: "clean — verified absent",
    INCOMPLETE: "ghost data found",
    NOT_APPLICABLE: "no deletion authorized",
  }[st] || "unaudited";
  d.innerHTML = `
    <div class="case">
    <div class="verdict v-${st}">
      <div class="stamp">${st.replace(/_/g, " ")}</div>
      <div>
        <h2>${c.case_id}</h2>
        <div class="meta">${verdictTxt} · ${steps.length} audit steps</div>
      </div>
      <button class="copybtn" id="copyjson" style="margin-left:auto">copy steps_json</button>
    </div>
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
        <table><thead><tr><th>#</th><th>system</th><th>resource</th><th>operation</th><th>state</th><th>evidence</th></tr></thead>
        <tbody>${(groups[p] || [])
          .map(
            (s) => `<tr>
            <td class="mono">${s.step_no}</td>
            <td class="mono">${esc(s.system_id)}</td>
            <td class="mono">${esc(s.resource_id)}</td>
            <td>${esc(s.operation)}</td>
            <td>${esc(s.state)}</td>
            <td>${evChip(s.evidence_id)}</td>
          </tr>`,
          )
          .join("")}
        </tbody></table>
      </div>`,
    ).join("")}
    </div>`;
  d.scrollTop = 0;
  document.getElementById("copyjson").onclick = (e) => {
    navigator.clipboard.writeText(r.steps_json || "[]");
    e.target.textContent = "copied ✓";
    setTimeout(() => (e.target.textContent = "copy steps_json"), 1200);
  };
  d.querySelectorAll(".ev").forEach((b) => {
    b.onclick = (e) => showEvidence(e.currentTarget.dataset.ev, e);
  });
}

function select(caseId) {
  state.sel = caseId;
  renderList();
  renderDetail(state.cases.find((c) => c.case_id === caseId));
}

/* ------------------------------------------- evidence popover (join) */

const pop = () => document.getElementById("evpop");

function showEvidence(id, evt) {
  const p = pop();
  const f = state.evidence[id];
  if (!f) {
    p.innerHTML = `<div class="t">${esc(id)}</div><div class="r"><b>not in evidence_files</b></div>`;
  } else {
    p.innerHTML = `<div class="t">${esc(id)}</div><div class="r">
      <b>path</b><span>${esc(f.file_path)}</span>
      <b>origin</b><span>${esc(f.origin)}</span>
      <b>captured</b><span>${esc(f.captured_at)}</span>
    </div>`;
  }
  p.hidden = false;
  const x = Math.min(evt.clientX + 12, innerWidth - 360);
  const y = Math.min(evt.clientY + 12, innerHeight - 160);
  p.style.left = x + "px";
  p.style.top = y + "px";
  evt.stopPropagation();
}
document.addEventListener("click", () => (pop().hidden = true));

/* ------------------------------------------------------------- stats */

function renderStats() {
  const counts = {};
  for (const c of state.cases) {
    const st = state.results[c.case_id]?.deletion_status || "UNAUDITED";
    counts[st] = (counts[st] || 0) + 1;
  }
  const el = document.getElementById("stats");
  el.innerHTML = Object.entries(counts)
    .map(([k, v]) => `<div class="c-${k}"><b data-n="${v}">0</b>${k.replace(/_/g, " ")}</div>`)
    .join("");
  // count-up
  const t0 = performance.now();
  (function tick(t) {
    const k = Math.min((t - t0) / 900, 1);
    el.querySelectorAll("b").forEach((b) => {
      b.textContent = Math.round(+b.dataset.n * (1 - Math.pow(1 - k, 3)));
    });
    if (k < 1) requestAnimationFrame(tick);
  })(t0);
}

/* -------------------------------------------------------------- boot */

async function boot() {
  const [cases, results, manifest] = await Promise.all([
    listAll("deletion_requests"),
    listAll("audit_results"),
    listAll("evidence_files"),
  ]);
  state.cases = cases;
  state.results = Object.fromEntries(results.map((r) => [r.case_id, r]));
  state.evidence = Object.fromEntries(manifest.map((f) => [f.file_id, f]));
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
    `<div class="empty"><p>Failed to reach Appwrite TablesDB:</p><p class="mono">${esc(e.message)}</p>` +
    `<p>Check web/config.js and table read permissions.</p></div>`;
});
