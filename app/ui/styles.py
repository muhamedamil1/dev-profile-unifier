from __future__ import annotations

APP_CSS = """
:root {
  --bg: #f6f8fb;
  --surface: #ffffff;
  --surface-muted: #f8fafc;
  --text: #0f172a;
  --muted: #64748b;
  --border: #e2e8f0;
  --border-strong: #cbd5e1;
  --accent: #2563eb;
  --accent-strong: #1d4ed8;
  --green: #16a34a;
  --green-bg: #dcfce7;
  --amber: #d97706;
  --amber-bg: #fef3c7;
  --red: #dc2626;
  --red-bg: #fee2e2;
  --slate-bg: #e2e8f0;
  --shadow: 0 18px 45px rgba(15, 23, 42, 0.08);
  --radius-lg: 20px;
  --radius-md: 14px;
  --radius-sm: 10px;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: radial-gradient(circle at top left, #dbeafe 0, transparent 32rem), var(--bg);
  color: var(--text);
}
a { color: inherit; }
.app-shell { min-height: 100vh; }
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  backdrop-filter: blur(14px);
  background: rgba(255, 255, 255, 0.78);
  border-bottom: 1px solid rgba(226, 232, 240, 0.86);
}
.nav {
  max-width: 1180px;
  margin: 0 auto;
  padding: 16px 22px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  font-weight: 800;
  letter-spacing: -0.03em;
}
.brand-mark {
  width: 36px;
  height: 36px;
  border-radius: 12px;
  background: linear-gradient(135deg, #2563eb, #7c3aed);
  color: white;
  display: grid;
  place-items: center;
  font-weight: 900;
}
.nav-links { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.nav-link {
  text-decoration: none;
  color: var(--muted);
  font-size: 14px;
  font-weight: 650;
  padding: 9px 12px;
  border-radius: 999px;
}
.nav-link:hover, .nav-link.active { background: #eff6ff; color: var(--accent-strong); }
.container {
  max-width: 1180px;
  margin: 0 auto;
  padding: 36px 22px 64px;
}
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(320px, 0.85fr);
  gap: 24px;
  align-items: stretch;
  margin-bottom: 24px;
}
.hero-copy, .card {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
}
.hero-copy { padding: 34px; }
.kicker { color: var(--accent); font-weight: 800; font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em; }
h1 { margin: 10px 0 12px; font-size: clamp(34px, 4.4vw, 58px); line-height: 0.96; letter-spacing: -0.06em; }
h2 { margin: 0; font-size: 22px; letter-spacing: -0.03em; }
h3 { margin: 0 0 8px; font-size: 16px; }
p { color: var(--muted); line-height: 1.6; }
.hero-copy p { max-width: 720px; font-size: 17px; }
.card { padding: 22px; margin-bottom: 18px; }
.card-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.card-title { display: flex; flex-direction: column; gap: 5px; }
.subtitle, .muted { color: var(--muted); font-size: 14px; }
.grid { display: grid; gap: 18px; }
.grid-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.grid-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.grid-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.field { display: flex; flex-direction: column; gap: 7px; }
label { font-size: 13px; color: #334155; font-weight: 750; }
input {
  width: 100%;
  border: 1px solid var(--border-strong);
  background: white;
  border-radius: 12px;
  padding: 12px 13px;
  font: inherit;
  color: var(--text);
  outline: none;
}
input:focus { border-color: var(--accent); box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.12); }
.button-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 16px; }
.button {
  border: 0;
  background: var(--accent);
  color: white;
  border-radius: 999px;
  padding: 12px 18px;
  font-weight: 800;
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.button:hover { background: var(--accent-strong); }
.button.secondary { background: #e0e7ff; color: #3730a3; }
.button.ghost { background: transparent; color: var(--accent); border: 1px solid var(--border-strong); }
.status-box {
  margin-top: 14px;
  border-radius: var(--radius-md);
  padding: 12px 14px;
  background: var(--surface-muted);
  border: 1px solid var(--border);
  color: var(--muted);
  display: none;
}
.status-box.show { display: block; }
.status-box.error { background: var(--red-bg); color: #991b1b; border-color: #fecaca; }
.status-box.success { background: var(--green-bg); color: #166534; border-color: #bbf7d0; }
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: 999px;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 800;
  background: var(--slate-bg);
  color: #334155;
  white-space: nowrap;
}
.badge.high, .badge.healthy, .badge.accepted, .badge.auto-match, .badge.generated { background: var(--green-bg); color: #166534; }
.badge.medium, .badge.review, .badge.needs-review, .badge.warning, .badge.fallback { background: var(--amber-bg); color: #92400e; }
.badge.low, .badge.rejected, .badge.error, .badge.degraded, .badge.failed { background: var(--red-bg); color: #991b1b; }
.badge.info { background: #dbeafe; color: #1d4ed8; }
.metric {
  background: white;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px;
}
.metric-label { color: var(--muted); font-size: 13px; font-weight: 700; }
.metric-value { font-size: 28px; font-weight: 900; letter-spacing: -0.04em; margin-top: 6px; }
.metric-note { color: var(--muted); font-size: 12px; margin-top: 6px; }
.profile-header {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr) auto;
  gap: 18px;
  align-items: center;
}
.avatar {
  width: 84px;
  height: 84px;
  border-radius: 24px;
  background: linear-gradient(135deg, #e0e7ff, #fce7f3);
  display: grid;
  place-items: center;
  color: #3730a3;
  font-size: 30px;
  font-weight: 900;
  overflow: hidden;
}
.avatar img { width: 100%; height: 100%; object-fit: cover; }
.title-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.profile-name { font-size: 34px; font-weight: 900; letter-spacing: -0.05em; margin: 0; }
.profile-meta { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; color: var(--muted); font-size: 14px; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.chip {
  background: #f1f5f9;
  color: #334155;
  border: 1px solid #e2e8f0;
  border-radius: 999px;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 750;
}
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius-md); }
table { width: 100%; border-collapse: collapse; min-width: 680px; background: white; }
th, td { padding: 12px 14px; text-align: left; border-bottom: 1px solid var(--border); vertical-align: top; }
th { background: #f8fafc; color: #475569; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
td { font-size: 14px; color: #334155; }
tr:last-child td { border-bottom: 0; }
.link { color: var(--accent); font-weight: 750; text-decoration: none; }
.link:hover { text-decoration: underline; }
.empty-state {
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-md);
  padding: 24px;
  color: var(--muted);
  background: var(--surface-muted);
  text-align: center;
}
.warning-list { display: grid; gap: 8px; }
.warning-item { padding: 10px 12px; border-radius: 12px; background: var(--amber-bg); color: #92400e; border: 1px solid #fde68a; }
details {
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: white;
  overflow: hidden;
}
summary { cursor: pointer; padding: 14px 16px; font-weight: 800; background: #f8fafc; }
pre {
  margin: 0;
  padding: 16px;
  white-space: pre-wrap;
  word-break: break-word;
  color: #0f172a;
  font-size: 12px;
  line-height: 1.5;
  max-height: 520px;
  overflow: auto;
}
.footer { color: var(--muted); font-size: 13px; padding: 24px 0 0; }
@media (max-width: 860px) {
  .hero, .grid-2, .grid-3, .grid-4, .form-grid { grid-template-columns: 1fr; }
  .nav { align-items: flex-start; flex-direction: column; }
  .profile-header { grid-template-columns: 1fr; }
  .profile-name { font-size: 28px; }
}
"""
