"""One-row-at-a-time reviewer for screening sheets, in the browser.

    python -m src.data.review_app --reviewer advait            # second-review sheets (default)
    python -m src.data.review_app --reviewer win --first       # first-round sheets

Opens http://127.0.0.1:8765, shows one candidate at a time and writes each decision straight back into the
sheet CSV (data/processed/screening/[second/]{domain}.csv) when you press Save. Nothing else changes: import
the sheets as usual afterwards (``python -m src.data.screening import [--second] <sheets>``). Rows are checked
with the same rules as the import, so a saved row will import cleanly.

Keys (outside text boxes): i / e / h = include / exclude / hold, 1-9 = reason code, Enter = save and go to
the next unreviewed row, ← / → = previous / next row, n = next unreviewed, p = edit premise, r = edit note.
In a text box: Ctrl/⌘+Enter = save and next, Esc = leave the box.

The server listens on 127.0.0.1 only. Sheets contain source text and stay git-ignored.
"""

import argparse
import csv
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from src.data.sampling import TARGETS
from src.data.screening import POOL_DIR, SECOND, SHEET_DIR, SHEET_FIELDS, check_decision, load_pool

REASON_CODES = ["not_patient_facing", "news_or_policy", "insufficient_context", "no_source_evidence",
                "malformed_text", "disputed_label", "duplicate", "identifier_or_image", "other"]
EDITABLE = ["eligibility_status", "eligibility_reason", "target_false_premise"]


class Sheets:
    """Screening sheets for every domain plus the pool rows needed to validate them."""

    def __init__(self, sheet_dir, reviewer, pool_dir=POOL_DIR):
        self.sheet_dir = Path(sheet_dir)
        self.reviewer = reviewer
        self.pools = {}
        self.paths = {}
        for domain in TARGETS:
            path = self.sheet_dir / f"{domain}.csv"
            if path.exists():
                self.paths[domain] = path
                self.pools[domain] = {r["candidate_id"]: r for r in load_pool(domain, pool_dir)}
        if not self.paths:
            raise FileNotFoundError(f"no sheets in {self.sheet_dir}; run python -m src.data.screening export")

    def read(self, domain):
        with open(self.paths[domain], newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            return reader.fieldnames, list(reader)

    def rows(self, domain):
        """Sheet rows with the pool facts the UI needs (control / escalation / prefilled premise)."""
        _, rows = self.read(domain)
        out = []
        for r in rows:
            pool = self.pools[domain].get(r["candidate_id"], {})
            out.append({**r, "is_control": bool(pool.get("is_control")), "task": pool.get("task", ""),
                        "prefilled_premise": pool.get("target_false_premise") or ""})
        return out

    def save(self, domain, candidate_id, updates):
        """Write one row's decision into the sheet. Returns a list of errors (empty when saved)."""
        if domain not in self.paths:
            return [f"unknown domain {domain!r}"]
        fields, rows = self.read(domain)
        row = next((r for r in rows if r["candidate_id"] == candidate_id), None)
        if row is None:
            return [f"{candidate_id!r} not in {domain} sheet"]
        clean = {k: str(updates.get(k, "") or "").strip() for k in EDITABLE}
        if clean["eligibility_status"]:
            errors = check_decision({**clean, "review_status": row.get("review_status", "")},
                                    self.pools[domain][candidate_id])
            if errors:
                return errors
        row.update(clean)
        row["reviewed_by"] = self.reviewer if clean["eligibility_status"] else ""
        tmp = self.paths[domain].with_suffix(".csv.tmp")
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields or SHEET_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, self.paths[domain])
        return []


def make_handler(sheets, title):
    page = PAGE.replace("__CONFIG__", json.dumps({
        "domains": list(sheets.paths), "reviewer": sheets.reviewer, "reasons": REASON_CODES, "title": title,
    }))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                return self._send(200, page, "text/html")
            if self.path.startswith("/api/rows/"):
                domain = self.path.rsplit("/", 1)[1]
                if domain in sheets.paths:
                    return self._send(200, sheets.rows(domain))
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/save":
                return self._send(404, {"error": "not found"})
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            errors = sheets.save(body.get("domain", ""), body.get("candidate_id", ""), body)
            self._send(400 if errors else 200, {"errors": errors})

    return Handler


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reviewer", required=True, help="your reviewer ID, written to reviewed_by")
    p.add_argument("--first", action="store_true", help="review first-round sheets instead of second-review ones")
    p.add_argument("--sheet-dir", help="override the sheet directory")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args(argv)

    sheet_dir = Path(args.sheet_dir) if args.sheet_dir else SHEET_DIR if args.first else SHEET_DIR / SECOND
    sheets = Sheets(sheet_dir, args.reviewer)
    title = "First-round screening" if args.first else "Second review (blind)"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(sheets, title))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"{title}: {', '.join(str(p) for p in sheets.paths.values())}")
    print(f"open {url}  (Ctrl+C to stop; then import the sheets)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Screening Review</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--muted:#6b6a65;--line:#e3e1db;--accent:#2f5d8a;
--inc:#2e7d4f;--exc:#b3412f;--hold:#a37311;--soft:#efeee9;--warn:#fff4dc}
@media (prefers-color-scheme:dark){:root{--bg:#181816;--card:#22221f;--ink:#ecebe6;--muted:#a09e96;--line:#36352f;
--accent:#7fb0de;--inc:#5cbf84;--exc:#e07a66;--hold:#d9a93f;--soft:#2b2a26;--warn:#3a321c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;
display:flex;flex-wrap:wrap;gap:10px;align-items:center}
h1{font-size:15px;margin:0 8px 0 0}
.tabs{display:flex;gap:6px;flex-wrap:wrap}
.tab{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:5px 10px;cursor:pointer;font:inherit;font-size:13px}
.tab.on{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.bar{flex:1;min-width:120px;height:6px;background:var(--soft);border-radius:3px;overflow:hidden}
.bar>i{display:block;height:100%;background:var(--accent);width:0}
.count{font-size:13px;color:var(--muted);white-space:nowrap}
main{max-width:920px;margin:0 auto;padding:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:12px}
.meta{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:10px;font-size:13px}
.pill{background:var(--soft);border-radius:999px;padding:2px 9px}
.pill.gold{background:var(--accent);color:var(--bg);font-weight:600}
.pill.flag{background:var(--warn)}
.label{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:14px 0 4px}
.question{font-size:18px;line-height:1.55;white-space:pre-wrap}
.evidence{white-space:pre-wrap}
details{margin-top:10px;font-size:13px;color:var(--muted)}details pre{white-space:pre-wrap;word-break:break-word;margin:6px 0 0}
.claim{background:var(--warn);padding:6px 10px;border-radius:8px;margin-top:6px;font-size:14px;color:var(--ink)}
.status{display:flex;gap:8px;flex-wrap:wrap}
.status button,.reasons button,.nav button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);
border-radius:8px;padding:7px 12px;cursor:pointer}
.status button.on[data-s=include]{background:var(--inc);border-color:var(--inc);color:#fff}
.status button.on[data-s=exclude]{background:var(--exc);border-color:var(--exc);color:#fff}
.status button.on[data-s=hold]{background:var(--hold);border-color:var(--hold);color:#fff}
.reasons{display:flex;flex-wrap:wrap;gap:6px}
.reasons button{font-size:13px;padding:4px 9px}.reasons button.on{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
kbd{font:11px ui-monospace,monospace;background:var(--soft);border:1px solid var(--line);border-radius:4px;padding:0 4px;margin-right:4px}
input[type=text],textarea{width:100%;font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px}
textarea{min-height:64px;resize:vertical}
.hint{font-size:13px;color:var(--muted);margin-top:4px}
.nav{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:16px}
.nav .primary{background:var(--accent);border-color:var(--accent);color:var(--bg);font-weight:600}
.msg{font-size:13px;min-height:20px;margin-top:8px}.msg.err{color:var(--exc)}.msg.ok{color:var(--inc)}
.done{text-align:center;padding:40px 16px}
.keys{font-size:12px;color:var(--muted);margin-top:4px}
</style></head><body>
<header><h1 id="title"></h1><div class="tabs" id="tabs"></div><div class="bar"><i id="bar"></i></div><span class="count" id="count"></span></header>
<main id="main"></main>
<script>
const CFG = __CONFIG__;
const GOLD = {correction_required:"Positive: has a false premise", no_correction:"Control: no false premise",
  "0A":"0A clarification needed","0B":"0B self-care / monitor","1A":"1A non-urgent care","1B":"1B urgent / emergency care"};
let domain = CFG.domains[0], rows = [], idx = 0, dirty = false;
const $ = s => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) k === "class" ? e.className = v : k.startsWith("on") ? e[k] = v : e.setAttribute(k, v);
  for (const c of kids) e.append(c); return e; };
document.getElementById("title").textContent = CFG.title + " · " + CFG.reviewer;
document.title = "Review · " + CFG.reviewer;

async function load(d) {
  domain = d; rows = await (await fetch("/api/rows/" + d)).json();
  const first = rows.findIndex(r => !done(r)); idx = first < 0 ? 0 : first; render();
}
// A row is done once a reviewer saved it. A status without reviewed_by is an unconfirmed draft.
function done(r) { return !!(r.eligibility_status && r.reviewed_by); }
function isDraft(r) { return !!(r.eligibility_status && !r.reviewed_by); }
function reviewed() { return rows.filter(done).length; }
function needsPremise(r) { return !r.is_control && r.task !== "escalation"; }
function renderTabs() {
  const t = $("#tabs"); t.replaceChildren();
  for (const d of CFG.domains) t.append(el("button", {class: "tab" + (d === domain ? " on" : ""), onclick: () => guard(() => load(d))}, d));
  const n = reviewed(); $("#bar").style.width = (rows.length ? 100 * n / rows.length : 0) + "%";
  $("#count").textContent = `${n}/${rows.length} reviewed · row ${idx + 1}`;
}
function guard(fn) { if (!dirty || confirm("Discard unsaved changes on this row?")) { dirty = false; fn(); } }
function reasonCode(s) { return (s || "").split(":")[0].trim(); }
function reasonNote(s) { const i = (s || "").indexOf(":"); return i < 0 ? "" : s.slice(i + 1).trim(); }

function render() {
  renderTabs(); const m = $("#main"); m.replaceChildren();
  if (!rows.length) { m.append(el("div", {class: "card done"}, "This sheet is empty.")); return; }
  const r = rows[idx]; let meta = {}; try { meta = JSON.parse(r.source_meta || "{}"); } catch (e) {}
  const pills = el("div", {class: "meta"}, el("span", {class: "pill gold"}, GOLD[r.gold_label_mapped] || r.gold_label_mapped),
    el("span", {class: "pill"}, r.candidate_id), el("span", {class: "pill"}, "stratum: " + (r.stratum || "–")));
  for (const f of (r.auto_flags || "").split(/[;,|]/).map(s => s.trim()).filter(Boolean)) pills.append(el("span", {class: "pill flag"}, "⚑ " + f));
  const src = el("div", {class: "card"}, pills, el("div", {class: "label"}, "Patient question"), el("div", {class: "question"}, r.original_question),
    el("div", {class: "label"}, "Source evidence"), el("div", {class: "evidence"}, r.source_evidence || "—"));
  if (meta.claim) src.append(el("div", {class: "label"}, "Fact-checked claim"), el("div", {class: "claim"}, meta.claim));
  src.append(el("details", {}, el("summary", {}, "Source metadata"), el("pre", {}, JSON.stringify(meta, null, 2))));

  const form = el("div", {class: "card"});
  if (isDraft(r)) form.append(el("div", {class: "claim"},
    "Draft, not yet confirmed. Check it against the rubric, change anything you disagree with, then save (↵) to confirm."));
  const status = el("div", {class: "status"});
  for (const [s, k] of [["include", "i"], ["exclude", "e"], ["hold", "h"]])
    status.append(el("button", {"data-s": s, class: r.eligibility_status === s ? "on" : "", onclick: () => setStatus(s)}, el("kbd", {}, k), s));
  const reasons = el("div", {class: "reasons"});
  CFG.reasons.forEach((c, i) => reasons.append(el("button", {"data-c": c, class: reasonCode(r.eligibility_reason) === c ? "on" : "",
    onclick: () => setReason(c)}, el("kbd", {}, String(i + 1)), c)));
  const note = el("input", {type: "text", id: "note", placeholder: "optional note (saved as code: note)"}); note.value = reasonNote(r.eligibility_reason);
  if (r.eligibility_reason && !CFG.reasons.includes(reasonCode(r.eligibility_reason))) note.value = r.eligibility_reason;
  note.oninput = () => dirty = true;
  form.append(el("div", {class: "label"}, "Eligibility"), status, el("div", {class: "label"}, "Reason (required for exclude / hold)"), reasons, note);
  if (needsPremise(r)) {
    const prem = el("textarea", {id: "premise", placeholder: "The false belief in the question, as one plain sentence"});
    prem.value = r.target_false_premise || ""; prem.oninput = () => { dirty = true; premHint(); };
    const hint = el("div", {class: "hint", id: "premhint"});
    form.append(el("div", {class: "label"}, "False premise (required to include)"), prem, hint);
  } else form.append(el("div", {class: "hint"}, r.task === "escalation" ? "Triage case: no premise." : "Control: no premise."));
  form.append(el("div", {class: "nav"},
    el("button", {onclick: () => guard(() => go(idx - 1))}, "← Prev"), el("button", {onclick: () => guard(() => go(idx + 1))}, "Next →"),
    el("button", {onclick: () => guard(nextOpen)}, el("kbd", {}, "n"), "Next unconfirmed"),
    el("button", {class: "primary", onclick: saveNext}, "Save & next ", el("kbd", {}, "↵")),
    el("button", {onclick: clearRow}, "Clear")),
    el("div", {class: "msg", id: "msg"}),
    el("div", {class: "keys"}, "Keys: i/e/h status · 1–9 reason · ↵ save & next · ←/→ move · n next unconfirmed · p premise · r note · ⌘/Ctrl+↵ save from a text box · Esc leave box"));
  m.append(src, form); premHint();
}
function premHint() {
  const h = $("#premhint"); if (!h) return; const r = rows[idx], v = $("#premise").value.trim();
  h.textContent = r.prefilled_premise && v === r.prefilled_premise.trim()
    ? "Prefilled from the source claim. Rewrite it as a clear sentence if it has search syntax or fragments." : "";
}
function current() { const s = $(".status button.on"); return s ? s.dataset.s : ""; }
function setStatus(s) { document.querySelectorAll(".status button").forEach(b => b.classList.toggle("on", b.dataset.s === s)); dirty = true;
  if (s === "include") document.querySelectorAll(".reasons button").forEach(b => b.classList.remove("on")); }
function setReason(c) { document.querySelectorAll(".reasons button").forEach(b => b.classList.toggle("on", b.dataset.c === c && !b.classList.contains("on")));
  if (current() === "include" || !current()) setStatus("exclude"); dirty = true; }
function reasonValue() { const b = $(".reasons button.on"), n = $("#note").value.trim();
  if (!b) return n; return n ? `${b.dataset.c}: ${n}` : b.dataset.c; }
function msg(t, cls) { const m = $("#msg"); if (m) { m.textContent = t; m.className = "msg " + (cls || ""); } }
function go(i) { if (i >= 0 && i < rows.length) { idx = i; render(); } }
function nextOpen() { for (let k = 1; k <= rows.length; k++) { const j = (idx + k) % rows.length; if (!done(rows[j])) return go(j); }
  render(); msg("Every row on this sheet is reviewed.", "ok"); }
async function post(body) {
  const res = await fetch("/api/save", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  return (await res.json()).errors || [];
}
async function saveNext() {
  const r = rows[idx], s = current();
  if (!s) return msg("Pick include, exclude or hold first.", "err");
  const body = {domain, candidate_id: r.candidate_id, eligibility_status: s,
    eligibility_reason: s === "include" ? $("#note").value.trim() : reasonValue(),
    target_false_premise: $("#premise") && s !== "exclude" ? $("#premise").value.trim() : ""};
  if (s !== "include" && !body.eligibility_reason) return msg("Pick a reason code for " + s + ".", "err");
  if (s === "include" && $("#premise") && !body.target_false_premise) return msg("Write the false premise to include this row.", "err");
  // Advance at once so keys typed while the request is in flight land on the next row; jump back on failure.
  const before = {...r}, at = idx, dom = domain;
  Object.assign(r, body, {reviewed_by: CFG.reviewer}); dirty = false; nextOpen();
  let errors;
  try { errors = await post(body); } catch (err) { errors = ["could not reach the review server: " + err]; }
  if (!errors.length) return renderTabs();
  Object.assign(r, before);
  if (domain === dom) {
    idx = at; render(); setStatus(body.eligibility_status);
    if ($("#premise")) $("#premise").value = body.target_false_premise;
  }
  msg(`Not saved (${r.candidate_id}): ` + errors.join(" · "), "err");
}
async function clearRow() {
  const r = rows[idx]; const errors = await post({domain, candidate_id: r.candidate_id, eligibility_status: "", eligibility_reason: "",
    target_false_premise: r.prefilled_premise || ""});
  if (errors.length) return msg(errors.join(" · "), "err");
  Object.assign(r, {eligibility_status: "", eligibility_reason: "", target_false_premise: r.prefilled_premise || "", reviewed_by: ""});
  dirty = false; render(); msg("Cleared.", "ok");
}
document.addEventListener("keydown", e => {
  const typing = ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName);
  if (typing) { if (e.key === "Escape") document.activeElement.blur();
    else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); saveNext(); } return; }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key;
  if (k === "i") setStatus("include"); else if (k === "e") setStatus("exclude"); else if (k === "h") setStatus("hold");
  else if (/^[1-9]$/.test(k) && CFG.reasons[+k - 1]) setReason(CFG.reasons[+k - 1]);
  else if (k === "Enter") { e.preventDefault(); saveNext(); }
  else if (k === "ArrowRight") guard(() => go(idx + 1)); else if (k === "ArrowLeft") guard(() => go(idx - 1));
  else if (k === "n") guard(nextOpen);
  else if (k === "p" && $("#premise")) { e.preventDefault(); const p = $("#premise"); p.focus();
    // An unedited source claim is selected so typing replaces it.
    if (rows[idx].prefilled_premise && p.value.trim() === rows[idx].prefilled_premise.trim()) p.select(); }
  else if (k === "r") { e.preventDefault(); $("#note").focus(); }
});
window.addEventListener("beforeunload", e => { if (dirty) e.preventDefault(); });
load(domain);
</script></body></html>
"""

if __name__ == "__main__":
    main()
