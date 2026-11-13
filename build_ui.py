"""Render jobs.json into a self-contained dashboard.html.

The page prefers live data: it polls jobs.json and re-renders when the scraper
writes a new one. The inlined copy is only a seed, for when fetch() is blocked
(the published artifact runs under a CSP that forbids it).

    python3 build_ui.py     # rebuild the page
    python3 serve.py        # serve it locally, live
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).parent


def build():
    raw = (ROOT / "jobs.json").read_text()
    payload = raw.replace("</", "<\\/")
    out = ROOT / "dashboard.html"
    out.write_text(TEMPLATE.replace("__DATA__", payload))

    data = json.loads(raw)
    total = sum(len(r["jobs"]) for r in data)
    print(f"dashboard.html  {out.stat().st_size // 1024} KB  "
          f"{total} openings / {len(data)} companies")


TEMPLATE = r"""<title>Nepal Tech Openings</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Serif:ital,wght@0,600;1,400&display=swap">
<style>
:root{
  --ground:#f6f7f8; --surface:#ffffff; --surface-2:#f0f2f4;
  --ink:#12171c; --ink-2:#48535f; --ink-3:#78848f;
  --rule:#dfe3e7; --rule-2:#eaedf0;
  --accent:#0b6e6b; --accent-soft:#e2f0ef;
  --verified:#0b6e6b; --verified-bg:#e2f0ef;
  --inferred:#9a6206; --inferred-bg:#f8eedd;
  --none:#a32c24;     --none-bg:#f7e5e3;
  --shadow:0 1px 2px rgba(18,23,28,.05), 0 6px 16px -10px rgba(18,23,28,.22);
  --sans:"IBM Plex Sans",ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  --serif:"IBM Plex Serif",Georgia,"Times New Roman",serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#0e1214; --surface:#161c1f; --surface-2:#1d2528;
    --ink:#e7ecee; --ink-2:#a3b0b6; --ink-3:#75838a;
    --rule:#283237; --rule-2:#202a2e;
    --accent:#4bb3ad; --accent-soft:#12312f;
    --verified:#4bb3ad; --verified-bg:#12312f;
    --inferred:#d19a3e; --inferred-bg:#33270f;
    --none:#d97b70;     --none-bg:#331b18;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 20px -12px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"]{
  --ground:#0e1214; --surface:#161c1f; --surface-2:#1d2528;
  --ink:#e7ecee; --ink-2:#a3b0b6; --ink-3:#75838a;
  --rule:#283237; --rule-2:#202a2e;
  --accent:#4bb3ad; --accent-soft:#12312f;
  --verified:#4bb3ad; --verified-bg:#12312f;
  --inferred:#d19a3e; --inferred-bg:#33270f;
  --none:#d97b70;     --none-bg:#331b18;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 20px -12px rgba(0,0,0,.7);
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--ground); color:var(--ink);
  font-family:var(--sans); font-size:15px; line-height:1.5;
  -webkit-font-smoothing:antialiased;
}
a{color:inherit}
.wrap{max-width:1120px; margin:0 auto; padding:0 24px}

/* ---------- masthead ---------- */
.masthead{padding:48px 0 28px; border-bottom:1px solid var(--rule)}
.eyebrow{
  font-family:var(--mono); font-size:11px; font-weight:500;
  letter-spacing:.14em; text-transform:uppercase; color:var(--ink-3);
  display:flex; gap:10px; align-items:center; flex-wrap:wrap;
}
.eyebrow .dot{width:4px;height:4px;border-radius:50%;background:var(--ink-3)}
.live{
  display:inline-flex; align-items:center; gap:7px; padding:3px 10px 3px 8px;
  border-radius:999px; border:1px solid var(--rule); background:var(--surface);
  letter-spacing:.06em; text-transform:none; color:var(--ink-2);
}
.live i{width:6px; height:6px; border-radius:50%; background:var(--ink-3)}
.live[data-state="idle"] i{background:var(--verified)}
.live[data-state="busy"] i{background:var(--inferred)}
.live[data-state="off"] i{background:var(--none)}
.progress-label{
  margin-top:6px; font-family:var(--mono); font-size:11px; color:var(--ink-3);
  font-variant-numeric:tabular-nums;
}
.runbar{display:flex; align-items:center; gap:14px; margin:18px 0 0}
.run{
  font:600 12px/1 var(--sans); letter-spacing:.06em; text-transform:uppercase;
  color:var(--ground); background:var(--ink); border:0; border-radius:2px;
  padding:10px 16px; cursor:pointer;
}
.run:hover:not(:disabled){background:var(--accent)}
.run:disabled{background:var(--ink-3); cursor:default}
.run:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
.prog{flex:1; display:none; align-items:center; gap:10px}
.prog[data-on]{display:flex}
.prog .track{flex:1; height:4px; background:var(--rule); border-radius:2px; overflow:hidden}
.prog .fill{
  height:100%; width:0; background:var(--accent);
  transition:width .4s ease;
}
.prog[data-indeterminate] .fill{width:35%; animation:slide 1.4s ease-in-out infinite}
@keyframes slide{0%{margin-left:-35%}100%{margin-left:100%}}
.prog .num{
  font:500 12px/1 var(--mono); color:var(--ink-2);
  font-variant-numeric:tabular-nums; white-space:nowrap;
}
@media (prefers-reduced-motion:reduce){.prog[data-indeterminate] .fill{animation:none;width:100%;opacity:.35}}
@media (prefers-reduced-motion:no-preference){
  .live[data-state="busy"] i{animation:pulse 1.6s ease-in-out infinite}
}
@keyframes pulse{50%{opacity:.25}}
h1{
  font-family:var(--serif); font-weight:600; font-size:clamp(30px,4.6vw,46px);
  line-height:1.08; letter-spacing:-.02em; margin:14px 0 0; text-wrap:balance;
}
.standfirst{
  max-width:62ch; margin:12px 0 0; color:var(--ink-2); font-size:16px;
}
.standfirst em{font-family:var(--serif); font-style:italic}

/* ---------- coverage ---------- */
.coverage{display:flex; flex-direction:column; gap:10px; margin-top:28px}
.cov-bar{
  display:flex; height:10px; border-radius:5px; overflow:hidden;
  background:var(--surface-2); box-shadow:inset 0 0 0 1px var(--rule);
}
.cov-bar span{display:block; height:100%}
.cov-key{
  display:flex; flex-wrap:wrap; gap:18px;
  font-family:var(--mono); font-size:11.5px; color:var(--ink-2);
}
.cov-key i{
  display:inline-block; width:9px; height:9px; border-radius:2px;
  margin-right:7px; vertical-align:-1px; font-style:normal;
}
.cov-key b{font-weight:600; color:var(--ink); font-variant-numeric:tabular-nums}

/* ---------- stats ---------- */
.stats{
  display:grid; grid-template-columns:repeat(auto-fit,minmax(168px,1fr));
  gap:1px; background:var(--rule); border:1px solid var(--rule);
  border-radius:10px; overflow:hidden; margin:26px 0 0;
}
.stat{background:var(--surface); padding:16px 18px}
.stat .n{
  font-family:var(--mono); font-size:27px; font-weight:600;
  letter-spacing:-.02em; font-variant-numeric:tabular-nums; line-height:1.1;
}
.stat .l{
  font-family:var(--mono); font-size:10.5px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--ink-3); margin-top:5px;
}
.stat.is-verified .n{color:var(--verified)}
.stat.is-inferred .n{color:var(--inferred)}
.stat.is-none .n{color:var(--none)}

/* ---------- controls ---------- */
.controls{
  position:sticky; top:0; z-index:20; background:var(--ground);
  border-bottom:1px solid var(--rule); padding:14px 0; margin-top:34px;
}
.controls-in{display:flex; gap:12px; align-items:center; flex-wrap:wrap}
.search{position:relative; flex:1 1 260px; min-width:210px}
.search input{
  width:100%; font-family:var(--sans); font-size:14px; color:var(--ink);
  background:var(--surface); border:1px solid var(--rule); border-radius:8px;
  padding:9px 12px 9px 34px; outline:none;
}
.search input::placeholder{color:var(--ink-3)}
.search input:focus-visible{border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-soft)}
.search svg{position:absolute; left:11px; top:50%; transform:translateY(-50%); color:var(--ink-3)}
.chips{display:flex; gap:6px; flex-wrap:wrap}
.chip{
  font-family:var(--mono); font-size:11.5px; letter-spacing:.03em;
  background:var(--surface); color:var(--ink-2); cursor:pointer;
  border:1px solid var(--rule); border-radius:999px; padding:7px 13px;
}
.chip:hover{border-color:var(--ink-3); color:var(--ink)}
.chip:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
.chip[aria-pressed="true"]{
  background:var(--ink); color:var(--ground); border-color:var(--ink);
}
.chip .c{opacity:.6; margin-left:6px; font-variant-numeric:tabular-nums}
.count{
  font-family:var(--mono); font-size:11.5px; color:var(--ink-3);
  margin-left:auto; white-space:nowrap; font-variant-numeric:tabular-nums;
}

/* ---------- company cards ---------- */
main{padding:26px 0 72px}
.grid{display:flex; flex-direction:column; gap:12px}
.card{
  background:var(--surface); border:1px solid var(--rule); border-radius:11px;
  box-shadow:var(--shadow); overflow:hidden; position:relative;
  padding-left:4px;
}
.card::before{
  content:""; position:absolute; inset:0 auto 0 0; width:4px; background:var(--edge);
}
.card.t-verified{--edge:var(--verified)}
.card.t-inferred{--edge:var(--inferred)}
.card.t-none{--edge:var(--rule)}
.card-head{
  display:flex; gap:14px; align-items:baseline; flex-wrap:wrap;
  padding:15px 18px; border-bottom:1px solid var(--rule-2);
}
.card.t-none .card-head{border-bottom:none}
.co{
  font-family:var(--serif); font-weight:600; font-size:18.5px;
  letter-spacing:-.01em; text-decoration:none;
}
.co:hover{text-decoration:underline; text-underline-offset:3px}
.badge{
  font-family:var(--mono); font-size:10px; font-weight:500;
  letter-spacing:.09em; text-transform:uppercase;
  padding:3.5px 8px; border-radius:4px; white-space:nowrap;
}
.b-verified{background:var(--verified-bg); color:var(--verified)}
.b-inferred{background:var(--inferred-bg); color:var(--inferred)}
.b-none{background:var(--none-bg); color:var(--none)}
.headlinks{
  margin-left:auto; display:flex; gap:14px; align-items:baseline;
  font-family:var(--mono); font-size:11.5px;
}
.headlinks a{color:var(--ink-3); text-decoration:none}
.headlinks a:hover{color:var(--accent); text-decoration:underline; text-underline-offset:3px}
.jobs{list-style:none; margin:0; padding:4px 0}
.jobs li{
  display:flex; gap:12px; align-items:baseline;
  padding:8px 18px; border-top:1px solid var(--rule-2);
}
.jobs li:first-child{border-top:none}
.jobs li:hover{background:var(--surface-2)}
.jt{font-weight:500; text-decoration:none}
.jt:hover{color:var(--accent); text-decoration:underline; text-underline-offset:3px}
.jl{
  font-family:var(--mono); font-size:11.5px; color:var(--ink-3);
  margin-left:auto; white-space:nowrap;
}
.facets{display:flex; flex-wrap:wrap; gap:8px; align-items:center}
.facets select{
  font:inherit; font-size:12px; color:var(--ink-1); background:var(--surface);
  border:1px solid var(--rule); border-radius:2px; padding:5px 8px; cursor:pointer;
  max-width:190px;
}
.facets select:hover{border-color:var(--ink-3)}
.facets select[data-on]{border-color:var(--accent); color:var(--accent)}
.facets button{
  font:inherit; font-size:11px; letter-spacing:.06em; text-transform:uppercase;
  background:none; border:0; color:var(--ink-3); cursor:pointer; padding:4px 2px;
}
.facets button:hover{color:var(--accent); text-decoration:underline}
.meta{display:flex; flex-wrap:wrap; gap:5px; margin-left:auto; align-items:center}
.m{
  font-family:var(--mono); font-size:10.5px; letter-spacing:.02em;
  padding:2px 6px; border-radius:2px; white-space:nowrap;
  background:var(--surface-2); color:var(--ink-2);
}
.m-remote{background:var(--verified-bg); color:var(--verified)}
.m-exp{background:var(--inferred-bg); color:var(--inferred)}
@media (max-width:640px){ .meta{margin-left:0; width:100%} }
.why{
  padding:0 18px 15px; font-size:13.5px; color:var(--ink-2);
  display:flex; gap:9px; align-items:baseline;
}
.why code{
  font-family:var(--mono); font-size:12px; background:var(--surface-2);
  padding:2px 6px; border-radius:4px; color:var(--ink-2);
}
.empty{
  text-align:center; padding:64px 20px; color:var(--ink-3);
  font-family:var(--mono); font-size:13px;
}

/* ---------- caveat ---------- */
.caveat{
  margin-top:30px; padding:16px 18px; border-radius:10px;
  background:var(--inferred-bg); border:1px solid transparent;
  font-size:13.5px; color:var(--ink-2); max-width:74ch;
}
.caveat strong{
  font-family:var(--mono); font-size:10.5px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--inferred); display:block; margin-bottom:5px;
}
footer{
  border-top:1px solid var(--rule); padding:22px 0 40px;
  font-family:var(--mono); font-size:11.5px; color:var(--ink-3);
  display:flex; gap:16px; flex-wrap:wrap;
}
@media (prefers-reduced-motion:no-preference){
  .card{transition:border-color .14s ease}
  .chip{transition:background .14s ease, color .14s ease, border-color .14s ease}
}
@media (max-width:620px){
  .headlinks{margin-left:0; width:100%}
  .jl{margin-left:0}
  .jobs li{flex-wrap:wrap; gap:4px}
}
</style>

<div class="wrap">
  <header class="masthead">
    <div class="eyebrow">
      <span>Scraped without AI</span><span class="dot"></span>
      <span>tech-companies-in-nepal</span>
      <span class="live" id="live" hidden><i></i><span></span></span>
    </div>
    <h1>Nepal Tech Openings</h1>
    <p class="standfirst">
      Every company in the <em>tech-companies-in-nepal</em> list, crawled for a careers
      page and parsed for live vacancies. Results are grouped by how the openings were
      obtained &mdash; because a structured job feed is a fact, and a pattern match is a guess.
    </p>

    <div class="runbar">
      <button class="run" id="run" type="button">Run scrape</button>
      <div class="prog" id="prog">
        <span class="track"><span class="fill" id="fill"></span></span>
        <span class="num" id="num"></span>
      </div>
    </div>

    <div class="coverage">
      <div class="cov-bar" id="covbar" role="img" aria-label="Coverage by provenance"></div>
      <div class="cov-key" id="covkey"></div>
    </div>

    <div class="stats" id="stats"></div>
  </header>

  <div class="controls">
    <div class="controls-in">
      <div class="search">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true">
          <circle cx="11" cy="11" r="7"></circle><path d="M20 20l-3.5-3.5"></path>
        </svg>
        <input id="q" type="search" placeholder="Search role or company&hellip;" aria-label="Search roles and companies">
      </div>
      <div class="chips" id="chips" role="group" aria-label="Filter by provenance"></div>
      <span class="count" id="count"></span>
    </div>
    <div class="controls-in facets" id="facets">
      <select id="f-location" aria-label="Filter by location"></select>
      <select id="f-workplace" aria-label="Filter by remote or onsite"></select>
      <select id="f-employment_type" aria-label="Filter by employment type"></select>
      <select id="f-experience_years" aria-label="Filter by experience required"></select>
      <select id="f-department" aria-label="Filter by department"></select>
      <button id="f-clear" type="button" hidden>Clear filters</button>
    </div>
  </div>

  <main>
    <div class="grid" id="grid"></div>
    <div class="caveat">
      <strong>How to read the amber rows</strong>
      Inferred openings come from matching job-title patterns against page text, either
      the raw HTML or a browser-rendered DOM. Product copy and nav links can survive the
      filter, so treat the titles as leads rather than listings. Verified rows come from
      an applicant-tracking API, schema.org markup, or the job board&rsquo;s own JSON
      feed captured while the page loaded &mdash; those are exact.
    </div>
  </main>

  <footer>
    <span id="foot-a"></span>
    <span>No LLM in the pipeline</span>
    <a href="https://github.com/mesaugat/tech-companies-in-nepal">Source list</a>
  </footer>
</div>

<script>
const SEED = __DATA__;

const TIERS = {
  verified: {label:"Verified feed", cls:"verified"},
  inferred: {label:"Inferred",      cls:"inferred"},
  none:     {label:"No openings",   cls:"none"},
};
const RANK = {verified:0, inferred:1, none:2};

/* Provenance tier: an ATS/JSON-LD hit is a fact from a structured feed,
   a heuristic hit is a pattern guess. This drives colour, order and filters. */
function tierOf(r){
  if (!r.method) return "none";
  // xhr = the board's own JSON API, intercepted while the page rendered:
  // structured data, same standing as a published feed.
  return (r.method.startsWith("ats:") || r.method === "jsonld" || r.method === "xhr")
    ? "verified" : "inferred";
}
function reasonOf(r){
  const e = r.error || "";
  if (e === "pending")               return "Not yet crawled";
  if (e.includes("unreachable"))     return e.includes("careers") ? "Careers page unreachable" : "Site unreachable";
  if (e.includes("no careers page")) return "No careers page found";
  if (e.includes("no openings"))     return "Careers page found, no openings parsed";
  if (e.startsWith("crash"))         return "Scraper error";
  return e || "Unknown";
}
function prepare(rows){
  rows.forEach(r => {
    r.jobs = r.jobs || [];
    r.tier = tierOf(r);
    r.reason = r.jobs.length ? null : reasonOf(r);
  });
  return rows.sort((a, b) =>
    RANK[a.tier] - RANK[b.tier] ||
    b.jobs.length - a.jobs.length ||
    a.company.localeCompare(b.company));
}

let DATA = prepare(SEED);
let active = "all", query = "";

const $ = id => document.getElementById(id);
const by = t => DATA.filter(r => r.tier === t);
const jobsIn = t => by(t).reduce((n, r) => n + r.jobs.length, 0);
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));

/* ---------- summary ---------- */
const covOrder = ["verified", "inferred", "none"];

function paintSummary(){
  const withJobs = DATA.filter(r => r.jobs.length);
  const totalJobs = withJobs.reduce((n, r) => n + r.jobs.length, 0);

  $("covbar").innerHTML = covOrder.map(t => {
    const pct = DATA.length ? by(t).length / DATA.length * 100 : 0;
    return `<span style="width:${pct}%;background:var(--${t})" title="${TIERS[t].label}"></span>`;
  }).join("");
  $("covkey").innerHTML = covOrder.map(t =>
    `<span><i style="background:var(--${t})"></i>${TIERS[t].label} <b>${by(t).length}</b></span>`
  ).join("") + `<span style="color:var(--ink-3)">of <b>${DATA.length}</b> companies</span>`;

  $("stats").innerHTML = [
    {n: totalJobs,          l: "Open positions",   c: ""},
    {n: withJobs.length,    l: "Companies hiring", c: ""},
    {n: jobsIn("verified"), l: "From job feeds",   c: "is-verified"},
    {n: jobsIn("inferred"), l: "Pattern-matched",  c: "is-inferred"},
    {n: by("none").length,  l: "No result",        c: "is-none"},
  ].map(s => `<div class="stat ${s.c}"><div class="n">${s.n}</div><div class="l">${s.l}</div></div>`).join("");

  const defs = [
    {k:"all",      t:"All companies",      n:DATA.length},
    {k:"verified", t:TIERS.verified.label, n:by("verified").length},
    {k:"inferred", t:TIERS.inferred.label, n:by("inferred").length},
    {k:"none",     t:TIERS.none.label,     n:by("none").length},
  ];
  $("chips").innerHTML = defs.map(c =>
    `<button class="chip" data-k="${c.k}" aria-pressed="${c.k === active}">${c.t}<span class="c">${c.n}</span></button>`
  ).join("");

  $("foot-a").textContent = `${DATA.length} companies crawled · ${totalJobs} openings found`;
}

/* ---------- list ---------- */
/* Facets live on the job, not the company, so a company shows only while at
   least one of its roles still matches — and the card then lists just those. */
const FACETS = ["location", "workplace", "employment_type", "experience_years",
                "department"];
const FACET_LABEL = {location: "Location", workplace: "Remote/onsite",
                     employment_type: "Type", experience_years: "Experience",
                     department: "Department"};
const picked = Object.fromEntries(FACETS.map(k => [k, ""]));
const anyFacet = () => FACETS.some(k => picked[k]);

function jobMatches(r, j){
  for (const k of FACETS){
    const want = picked[k];
    if (!want) continue;
    if (k === "experience_years"){
      const y = j.experience_years;
      if (want === "none"){ if (y !== null && y !== undefined) return false; continue; }
      if (y === null || y === undefined) return false;
      // buckets are "0", "1", "3", "5" meaning at-least-this-many
      const [lo, hi] = {"0": [0, 0], "1": [1, 2], "3": [3, 4], "5": [5, 99]}[want];
      if (y < lo || y > hi) return false;
    } else if ((j[k] || "") !== want) return false;
  }
  if (query && !(j.title || "").toLowerCase().includes(query)
            && !r.company.toLowerCase().includes(query)) return false;
  return true;
}

function shownJobs(r){
  return (query || anyFacet()) ? r.jobs.filter(j => jobMatches(r, j)) : r.jobs;
}

function matches(r){
  if (active !== "all" && r.tier !== active) return false;
  if (!query && !anyFacet()) return true;
  // a facet is a question about roles, so a company with none cannot answer it
  if (anyFacet()) return shownJobs(r).length > 0;
  if (r.company.toLowerCase().includes(query)) return true;
  return r.jobs.some(j => (j.title || "").toLowerCase().includes(query));
}

/* Only what the page actually stated: an absent field is left absent rather
   than filled with a plausible default. */
function jobMeta(j){
  const bits = [];
  if (j.workplace) bits.push([j.workplace, j.workplace === "remote" ? "m-remote" : ""]);
  if (j.employment_type) bits.push([j.employment_type, ""]);
  if (j.experience) bits.push([j.experience, "m-exp"]);
  else if (j.experience_years != null) bits.push([`${j.experience_years}+ yrs`, "m-exp"]);
  if (j.department) bits.push([j.department, ""]);
  if (j.salary) bits.push([j.salary, ""]);
  if (j.deadline) bits.push([`by ${j.deadline}`, ""]);
  if (j.location) bits.push([j.location, ""]);
  return bits.map(([t, c]) => `<span class="m ${c}">${esc(t)}</span>`).join("");
}

function card(r){
  const tier = TIERS[r.tier];
  const SRC = {xhr: "live API", rendered: "rendered page", jsonld: "schema.org",
               llm: "LLM read", heuristic: null};
  const raw = r.method ? r.method.replace("ats:", "") : null;
  const src = raw in SRC ? SRC[raw] : raw;
  const badge = r.tier === "none" ? tier.label
    : `${tier.label}${src ? " &middot; " + esc(src) : ""}`;

  // When searching or filtering, show only the roles that matched — the point
  // is finding one, not admiring the company.
  const shown = shownJobs(r);

  const jobs = shown.length ? `<ul class="jobs">${shown.map(j => `
      <li>
        ${j.url ? `<a class="jt" href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a>`
                : `<span class="jt">${esc(j.title)}</span>`}
        <span class="meta">${jobMeta(j)}</span>
      </li>`).join("")}</ul>` : "";

  const why = r.tier === "none"
    ? `<div class="why"><code>${esc(r.reason || "unknown")}</code></div>` : "";

  return `<article class="card t-${tier.cls}">
    <div class="card-head">
      <a class="co" href="${esc(r.site)}" target="_blank" rel="noopener">${esc(r.company)}</a>
      <span class="badge b-${tier.cls}">${badge}</span>
      <span class="headlinks">
        ${r.jobs.length ? `<span>${r.jobs.length} open</span>` : ""}
        ${r.careers_url ? `<a href="${esc(r.careers_url)}" target="_blank" rel="noopener">careers page &rarr;</a>` : ""}
      </span>
    </div>
    ${jobs}${why}
  </article>`;
}

function render(){
  const rows = DATA.filter(matches);
  const n = rows.reduce((a, r) => a + r.jobs.length, 0);
  $("count").textContent =
    `${rows.length} ${rows.length === 1 ? "company" : "companies"} · ${n} ${n === 1 ? "role" : "roles"}`;
  $("grid").innerHTML = rows.length
    ? rows.map(card).join("")
    : `<div class="empty">Nothing matches &ldquo;${esc(query)}&rdquo;</div>`;
}

function renderAll(){ paintSummary(); buildFacets(); render(); }

/* ---------- controls ---------- */
$("chips").addEventListener("click", e => {
  const b = e.target.closest(".chip");
  if (!b) return;
  active = b.dataset.k;
  document.querySelectorAll(".chip").forEach(c => c.setAttribute("aria-pressed", c === b));
  render();
});
const qi = $("q");
qi.addEventListener("input", () => { query = qi.value.trim().toLowerCase(); render(); });

/* ---------- facets ----------
   Options come from the data, so a field nothing reports simply never offers a
   filter, and a rescrape that finds new locations picks them up on its own. */
const EXP_BUCKETS = [["0", "No experience needed"], ["1", "1\u20132 years"],
                     ["3", "3\u20134 years"], ["5", "5+ years"],
                     ["none", "Not stated"]];

function facetValues(key){
  if (key === "experience_years"){
    const seen = new Set();
    for (const r of DATA) for (const j of r.jobs){
      const y = j.experience_years;
      if (y == null){ seen.add("none"); continue; }
      seen.add(y === 0 ? "0" : y <= 2 ? "1" : y <= 4 ? "3" : "5");
    }
    return EXP_BUCKETS.filter(([v]) => seen.has(v));
  }
  const counts = new Map();
  for (const r of DATA) for (const j of r.jobs){
    const v = j[key];
    if (v) counts.set(v, (counts.get(v) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([v, n]) => [v, `${v} (${n})`]);
}

function buildFacets(){
  let anyOption = false;
  for (const key of FACETS){
    const sel = $("f-" + key), vals = facetValues(key);
    if (!vals.length){ sel.hidden = true; continue; }
    anyOption = true;
    sel.hidden = false;
    // a value that vanished after a rescrape must not stay silently active
    if (picked[key] && !vals.some(([v]) => v === picked[key])) picked[key] = "";
    sel.innerHTML = `<option value="">${FACET_LABEL[key]}: any</option>` +
      vals.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
    sel.value = picked[key];
    sel.toggleAttribute("data-on", !!picked[key]);
  }
  $("facets").hidden = !anyOption;
  $("f-clear").hidden = !anyFacet();
}

$("facets").addEventListener("change", e => {
  const key = (e.target.id || "").replace("f-", "");
  if (!FACETS.includes(key)) return;
  picked[key] = e.target.value;
  e.target.toggleAttribute("data-on", !!e.target.value);
  $("f-clear").hidden = !anyFacet();
  render();
});

$("f-clear").addEventListener("click", () => {
  FACETS.forEach(k => picked[k] = "");
  buildFacets();
  render();
});

renderAll();

/* ---------- live data ----------
   Served locally, the page follows jobs.json and re-renders when the scraper
   writes a new one. Under the artifact CSP the fetch fails, the catch swallows
   it, and the page simply keeps the seeded data. */
const live = $("live");
let stamp = null, everLive = false;

function setLive(state, text){
  live.dataset.state = state;
  live.querySelector("span").textContent = text;
  live.hidden = false;
}

const runBtn = $("run"), prog = $("prog");

runBtn.addEventListener("click", async () => {
  runBtn.disabled = true;
  runBtn.textContent = "Starting\u2026";
  try {
    const r = await fetch("run", {method: "POST"});
    if (!r.ok) throw new Error(r.status === 409 ? "already running" : "failed");
  } catch (e) {
    runBtn.disabled = false;
    runBtn.textContent = "Run scrape";
    setLive("off", "Could not start \u2014 serve with python3 serve.py");
    return;
  }
  poll();
});

function eta(st){
  // the server times each stage separately, so the rate is this stage's rate
  // rather than an average smeared across a fast pass and a slow browser pass
  if (!st.elapsed || !st.done) return "";
  const left = Math.round(st.elapsed / st.done * (st.total - st.done));
  if (left < 60) return ` \u00b7 ~${left}s left`;
  const m = Math.round(left / 60);
  return m < 60 ? ` \u00b7 ~${m}m left` : ` \u00b7 ~${(left/3600).toFixed(1)}h left`;
}

function setProgress(st){
  if (st.public){            // read-only mirror: no process starting from here
    runBtn.hidden = true;
    prog.removeAttribute("data-on");
    return;
  }
  const busy = st.scraping;
  runBtn.disabled = busy;
  runBtn.textContent = busy ? "Running\u2026" : "Run scrape";
  if (!busy){ prog.removeAttribute("data-on"); return; }
  prog.setAttribute("data-on", "");
  const stage = st.stage || "scraping";
  if (st.total){
    prog.removeAttribute("data-indeterminate");
    $("fill").style.width = (100 * st.done / st.total).toFixed(1) + "%";
    $("num").textContent = `${stage} \u00b7 ${st.done}/${st.total}${eta(st)}`;
  } else {
    // no n/total in the log yet: show motion, not a fake number
    prog.setAttribute("data-indeterminate", "");
    $("fill").style.width = "";
    $("num").textContent = stage;
  }
}

async function poll(){
  try {
    const s = await fetch("status", {cache: "no-store"});
    if (!s.ok) throw new Error("no status endpoint");
    const st = await s.json();
    everLive = true;

    if (st.updated !== stamp){
      if (stamp !== null){
        const fresh = await fetch("jobs.json", {cache: "no-store"});
        DATA = prepare(await fresh.json());
        renderAll();
      }
      stamp = st.updated;
    }
    const when = new Date(st.updated * 1000)
      .toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
    setLive(st.scraping ? "busy" : "idle",
      st.scraping ? `Scraping · ${st.pages} pages fetched` : `Data as of ${when}`);
    setProgress(st);
  } catch (e) {
    if (everLive) setLive("off", "Server not responding");
    runBtn.disabled = false;
    prog.removeAttribute("data-on");
  }
}

poll();
setInterval(poll, 4000);
</script>
"""

if __name__ == "__main__":
    build()
