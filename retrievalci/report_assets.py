"""Static assets embedded into self-contained RetrievalCI HTML reports."""

REPORT_CSS = """
:root {
  color-scheme: light;
  --bg: oklch(0.975 0.006 245);
  --surface: oklch(0.995 0.004 245);
  --surface-2: oklch(0.948 0.009 245);
  --text: oklch(0.235 0.018 245);
  --muted: oklch(0.47 0.022 245);
  --line: oklch(0.855 0.014 245);
  --accent: oklch(0.50 0.14 245);
  --good: oklch(0.43 0.12 155);
  --warn: oklch(0.58 0.12 70);
  --bad: oklch(0.50 0.15 25);
  --shadow: 0 18px 50px oklch(0.42 0.03 245 / 0.10);
}
* { box-sizing: border-box; }
html, body { max-width: 100%; overflow-x: hidden; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
}
main { width: min(1180px, calc(100% - 32px)); margin: 0 auto 56px; }
header {
  border-bottom: 1px solid var(--line);
  background: var(--surface);
  box-shadow: var(--shadow);
}
.header-inner {
  width: min(1180px, calc(100% - 32px));
  margin: 0 auto;
  padding: 30px 0 28px;
  min-width: 0;
}
h1, h2, h3, h4, p { margin-top: 0; }
h1 { margin-bottom: 8px; font-size: 28px; line-height: 1.15; letter-spacing: 0; }
h2 { margin-bottom: 0; font-size: 21px; line-height: 1.2; letter-spacing: 0; }
h3 { margin: 26px 0 10px; font-size: 15px; letter-spacing: 0; }
h4 { margin: 0 0 10px; font-size: 13px; letter-spacing: 0; }
p { max-width: 74ch; }
h1, h2, h3, h4, p { overflow-wrap: anywhere; }
code {
  border: 1px solid var(--line);
  border-radius: 5px;
  padding: 1px 5px;
  background: var(--surface-2);
  font-size: 0.92em;
}
.muted { color: var(--muted); }
.eyebrow {
  margin: 0 0 5px;
  color: var(--accent);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
  margin-top: 22px;
}
.summary-item {
  min-height: 78px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-2);
  padding: 14px;
  min-width: 0;
}
.summary-item span {
  display: block;
  color: var(--muted);
  font-size: 12px;
}
.summary-item strong {
  display: block;
  margin-top: 8px;
  font-size: 18px;
  line-height: 1.2;
  word-break: break-word;
}
.band {
  margin-top: 22px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  padding: 22px;
  box-shadow: 0 10px 32px oklch(0.42 0.03 245 / 0.07);
  min-width: 0;
  max-width: 100%;
}
.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--line);
  min-width: 0;
}
.section-head > div { min-width: 0; }
.status, .severity {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  border-radius: 999px;
  padding: 4px 10px;
  border: 1px solid var(--line);
  background: var(--surface-2);
  color: var(--text);
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.good { color: var(--good); }
.warn, .warning { color: var(--warn); }
.bad, .critical { color: var(--bad); }
.info, .neutral { color: var(--accent); }
.diagnosis {
  display: grid;
  grid-template-columns: minmax(240px, 0.8fr) minmax(320px, 1.2fr);
  gap: 18px;
  margin-top: 18px;
  min-width: 0;
}
.facts {
  display: grid;
  gap: 8px;
  margin: 0;
}
.facts div {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  border-bottom: 1px solid var(--line);
  padding: 7px 0;
  min-width: 0;
}
.facts dt { color: var(--muted); }
.facts dd {
  margin: 0;
  font-weight: 650;
  text-align: right;
  min-width: 0;
  overflow-wrap: anywhere;
}
.finding-list {
  display: grid;
  gap: 8px;
  margin: 16px 0 0;
  padding: 0;
  list-style: none;
}
.finding-list li {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-2);
  padding: 10px 12px;
}
.table-wrap {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
}
table {
  width: 100%;
  border-collapse: collapse;
  min-width: 720px;
  background: var(--surface);
}
th, td {
  border-bottom: 1px solid var(--line);
  padding: 9px 10px;
  text-align: left;
  vertical-align: top;
}
th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--surface-2);
  color: var(--muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
th button {
  all: unset;
  cursor: pointer;
}
th button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 3px;
}
tr:last-child td { border-bottom: 0; }
td { max-width: 320px; word-break: break-word; }
td:first-child { white-space: nowrap; word-break: normal; }
.example-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 14px;
}
.example-block {
  border-top: 1px solid var(--line);
  padding-top: 14px;
}
.empty {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-2);
  padding: 14px;
  color: var(--muted);
}
@media (max-width: 760px) {
  main, .header-inner { width: calc(100vw - 20px); max-width: 1180px; }
  .header-inner { padding: 22px 0; }
  h1 { font-size: 22px; }
  .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .band { padding: 16px; }
  .section-head, .diagnosis { display: grid; grid-template-columns: 1fr; }
  .status { justify-self: start; }
  h1, h2, h3, p, .summary-item strong { overflow-wrap: anywhere; }
  .facts div { display: grid; grid-template-columns: 1fr; gap: 2px; }
  .facts dd { text-align: left; }
}
@media (max-width: 520px) {
  main, .header-inner { width: calc(100vw - 20px); max-width: 360px; }
  main { margin: 0 10px 56px; }
  .header-inner { margin: 0 10px; }
  .summary-grid { grid-template-columns: 1fr; }
}
@media print {
  body { background: var(--surface); }
  header, .band { box-shadow: none; }
  th { position: static; }
}
""".strip()

SORT_TABLES_JS = """
(() => {
  const coerce = value => {
    const n = Number(String(value).replace(/,/g, ""));
    return Number.isNaN(n) ? String(value).toLowerCase() : n;
  };
  document.querySelectorAll("table[data-sortable]").forEach(table => {
    table.querySelectorAll("th").forEach((th, index) => {
      th.addEventListener("click", () => {
        const body = table.tBodies[0];
        const rows = Array.from(body.rows);
        const dir = th.dataset.dir === "asc" ? "desc" : "asc";
        table.querySelectorAll("th").forEach(item => delete item.dataset.dir);
        th.dataset.dir = dir;
        rows.sort((a, b) => {
          const av = coerce(a.cells[index]?.dataset.sortValue || "");
          const bv = coerce(b.cells[index]?.dataset.sortValue || "");
          if (av < bv) return dir === "asc" ? -1 : 1;
          if (av > bv) return dir === "asc" ? 1 : -1;
          return 0;
        });
        rows.forEach(row => body.appendChild(row));
      });
    });
  });
})();
""".strip()
