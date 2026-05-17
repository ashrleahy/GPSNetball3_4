from flask import Flask, request, jsonify, render_template_string
from datetime import datetime, date, timedelta
import os, json

app = Flask(__name__)

DATA_DIR   = os.environ.get("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)
SCHED_FILE = os.path.join(DATA_DIR, "schedule.json")
AVAIL_FILE = os.path.join(DATA_DIR, "avail.json")

ALL_PLAYERS = ["Abbie", "Alexandra", "Audrey", "Judy", "Kim", "Klara", "Saga", "Zara"]
POSITIONS   = ["GS", "GA", "WA", "C", "WD", "GD", "GK"]
ALL_SLOTS   = POSITIONS + ["Off"]
POS_COLORS  = {
    "GS": "#FFD1DC", "GA": "#FFECB3", "WA": "#C8E6C9",
    "C":  "#B3E5FC", "WD": "#E1BEE7", "GD": "#D1C4E9",
    "GK": "#F8BBD0", "Off": "#F5F5F5"
}
REGIONS = {
    "GS": "Attack", "GA": "Attack", "WA": "Attack",
    "C":  "Mid",
    "WD": "Defense", "GD": "Defense", "GK": "Defense"
}

def build_dates():
    dates, excl = [], [date(2026,6,8), date(2026,7,6), date(2026,7,13), date(2026,9,7)]
    curr = date(2026, 5, 11)
    while curr <= date(2026, 9, 21):
        if curr not in excl:
            dates.append(curr.strftime('%d %b %Y'))
        curr += timedelta(days=7)
    return dates

DATES = build_dates()

def file_read(path):
    try:
        with open(path) as f: return json.load(f)
    except: return None

def file_write(path, data):
    with open(path, "w") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())

def default_avail():
    return {d: {p: True for p in ALL_PLAYERS} for d in DATES}

def default_schedule():
    rows = []
    for wi, d in enumerate(DATES):
        for q in range(4):
            row = {"week": wi+1, "date": d, "quarter": q+1}
            for s in ALL_SLOTS: row[s] = None
            rows.append(row)
    return rows

def build_counts(schedule, up_to_week=None, up_to_qi=None):
    pos_counts = {p: {pos: 0 for pos in POSITIONS} for p in ALL_PLAYERS}
    off_counts  = {p: 0 for p in ALL_PLAYERS}
    for row in schedule:
        w  = row["week"]
        qi = row["quarter"] - 1
        if up_to_week is not None:
            if w > up_to_week: continue
            if w == up_to_week and qi >= up_to_qi: continue
        for pos in POSITIONS:
            p = row.get(pos)
            if p and p != "N/A" and p in pos_counts:
                pos_counts[p][pos] += 1
        off_p = row.get("Off")
        if off_p and off_p != "N/A" and off_p in off_counts:
            off_counts[off_p] += 1
    return pos_counts, off_counts

def run_allocation_from(schedule, avail, start_week, pos_counts, off_counts):
    df = [dict(r) for r in schedule]
    pc = {p: dict(pos_counts[p]) for p in ALL_PLAYERS}
    oc = dict(off_counts)
    EQUITY, CONT, REG, CHANGE = 100, 180, 60, 120
    for w in range(start_week, len(DATES)+1):
        d_str = DATES[w-1]
        today_players = [p for p in ALL_PLAYERS if avail.get(d_str, {}).get(p, True)]
        n_off = max(0, len(today_players) - 7)
        sat_off_game = set()
        for qi in range(4):
            idx = (w-1)*4 + qi
            for s in ALL_SLOTS: df[idx][s] = None
            off_now = []
            if n_off > 0:
                eligible = [p for p in today_players if p not in sat_off_game] or list(today_players)
                eligible.sort(key=lambda p: oc[p])
                off_now = eligible[:n_off]
            for p in off_now:
                oc[p] += 1
                sat_off_game.add(p)
            df[idx]["Off"] = off_now[0] if off_now else "N/A"
            on_now = [p for p in today_players if p not in off_now]
            prev_pos = {}
            if qi > 0:
                prev = df[(w-1)*4 + qi-1]
                for pos in POSITIONS:
                    pp = prev.get(pos)
                    if pp and pp != "N/A": prev_pos[pp] = pos
            def score(p, pos, _qi=qi, _prev=prev_pos):
                s = pc[p][pos] * EQUITY
                if p in _prev:
                    last = _prev[p]
                    if _qi in (1, 3):
                        if last == pos: s -= CONT
                        elif REGIONS.get(last) == REGIONS.get(pos): s -= REG
                    elif _qi == 2:
                        if REGIONS.get(last) == REGIONS.get(pos): s += CHANGE
                return s
            remaining = list(on_now)
            pos_order = sorted(POSITIONS, key=lambda pos: (lambda sc: max(sc)-min(sc) if sc else 0)([score(p,pos) for p in remaining]), reverse=True)
            assigned = {}
            for pos in pos_order:
                if not remaining: break
                best = min(remaining, key=lambda p: score(p, pos))
                assigned[pos] = best
                remaining.remove(best)
            for pos in POSITIONS:
                p = assigned.get(pos, "N/A")
                df[idx][pos] = p
                if p and p != "N/A" and p in pc: pc[p][pos] += 1
    return df

def get_schedule():
    s = file_read(SCHED_FILE)
    if not s:
        avail = file_read(AVAIL_FILE) or default_avail()
        pc, oc = build_counts([])
        s = run_allocation_from(default_schedule(), avail, 1, pc, oc)
        file_write(SCHED_FILE, s)
    return s

def get_default_week():
    today = date.today()
    for i, d_str in enumerate(DATES):
        if datetime.strptime(d_str, "%d %b %Y").date() >= today:
            return i + 1
    return len(DATES)

# ── API endpoints ──────────────────────────────────────────────────────────────

@app.route("/api/schedule")
def api_schedule():
    return jsonify(get_schedule())

@app.route("/api/save", methods=["POST"])
def api_save():
    try:
        payload  = request.get_json()
        save_week = payload["week"]
        matrix   = payload["matrix"]
        schedule = get_schedule()
        avail    = file_read(AVAIL_FILE) or default_avail()

        # Apply matrix to schedule
        for qi in range(4):
            m_idx = (save_week - 1) * 4 + qi
            for s in ALL_SLOTS:
                schedule[m_idx][s] = None
            for row in matrix:
                schedule[m_idx][row["Qs"][qi]] = row["name"]

        # Rebalance future weeks
        next_week = save_week + 1
        if next_week <= len(DATES):
            pc, oc   = build_counts(schedule, next_week, 0)
            schedule = run_allocation_from(schedule, avail, next_week, pc, oc)

        file_write(SCHED_FILE, schedule)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/reset", methods=["POST"])
def api_reset():
    avail = file_read(AVAIL_FILE) or default_avail()
    pc, oc = build_counts([])
    schedule = run_allocation_from(default_schedule(), avail, 1, pc, oc)
    file_write(SCHED_FILE, schedule)
    return jsonify({"ok": True})

@app.route("/api/avail")
def api_avail():
    return jsonify(file_read(AVAIL_FILE) or default_avail())

@app.route("/api/avail", methods=["POST"])
def api_save_avail():
    avail = request.get_json()
    file_write(AVAIL_FILE, avail)
    pc, oc = build_counts([])
    schedule = run_allocation_from(default_schedule(), avail, 1, pc, oc)
    file_write(SCHED_FILE, schedule)
    return jsonify({"ok": True})

# ── Main HTML app ──────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0"/>
<title>Netball Pro 🏐</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: sans-serif; background: #f0f0f0; }
.app { max-width: 480px; margin: 0 auto; background: #fff; min-height: 100vh; padding: 6px; }
.top { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.top h2 { font-size: 14px; font-weight: bold; flex: 1; }
.nav { display: flex; gap: 4px; margin-bottom: 8px; }
.nav button { flex: 1; font-size: 11px; padding: 4px; border: 1px solid #ccc;
              border-radius: 4px; background: #f8f8f8; cursor: pointer; }
.nav button.active { background: #333; color: #fff; border-color: #333; }
.btn-danger { border-color: #f44 !important; color: #c00 !important; }
.page-title { font-size: 13px; font-weight: bold; text-align: center; margin-bottom: 4px; }
.slider-wrap { margin-bottom: 6px; }
.slider-wrap input { width: 100%; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; }
th { font-size: 10px; color: #444; padding: 4px; border: 1px solid #ddd; background: #f8f9fa; }
td { border: 1px solid #ddd; height: 38px; padding: 0; }
.name-cell { font-size: 10px; font-weight: bold; padding-left: 3px; background: #fff;
             overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
select { width: 100%; height: 100%; border: none; background: transparent; font-size: 10px;
         font-weight: bold; text-align: center; appearance: none; cursor: pointer; }
.save-btn { width: 100%; margin-top: 6px; padding: 8px; background: #ff4b4b; color: #fff;
            border: none; border-radius: 4px; font-size: 13px; font-weight: bold; cursor: pointer; }
.save-btn:disabled { background: #ccc; }
.status { font-size: 11px; color: #888; text-align: center; margin-top: 3px; min-height: 16px; }
.mtime { font-size: 10px; color: #aaa; text-align: right; margin-bottom: 4px; }
.avail-table { width: 100%; border-collapse: collapse; font-size: 10px; }
.avail-table th, .avail-table td { border: 1px solid #ddd; padding: 4px; text-align: center; }
.avail-table td:first-child { text-align: left; }
.stats-table { width: 100%; border-collapse: collapse; font-size: 10px; }
.stats-table th, .stats-table td { border: 1px solid #ddd; padding: 4px; text-align: center; }
.stats-table td:first-child { text-align: left; font-weight: bold; }
</style>
</head>
<body>
<div class="app">
  <div class="top">
    <h2>🏐 Netball Pro</h2>
    <button class="nav" style="flex:none;padding:4px 8px;font-size:11px;border:1px solid #f44;
            color:#c00;border-radius:4px;background:#f8f8f8;cursor:pointer"
            onclick="doReset()">🚨 Reset</button>
  </div>
  <div class="nav">
    <button id="nav-rotation" class="active" onclick="showPage('rotation')">Rotation</button>
    <button id="nav-availability" onclick="showPage('availability')">Availability</button>
    <button id="nav-stats" onclick="showPage('stats')">Stats</button>
  </div>
  <div id="page-rotation"></div>
  <div id="page-availability" style="display:none"></div>
  <div id="page-stats" style="display:none"></div>
</div>

<script>
const ALL_PLAYERS = """ + json.dumps(ALL_PLAYERS) + """;
const POSITIONS   = """ + json.dumps(POSITIONS) + """;
const ALL_SLOTS   = """ + json.dumps(ALL_SLOTS) + """;
const POS_COLORS  = """ + json.dumps(POS_COLORS) + """;
const DATES       = """ + json.dumps(DATES) + """;

let schedule = [];
let avail    = {};
let currentWeek = """ + str(get_default_week()) + """;
let currentPage = 'rotation';
let matrix = [];
let dirty = false;

async function loadAll() {
    const [s, a] = await Promise.all([
        fetch('/api/schedule').then(r => r.json()),
        fetch('/api/avail').then(r => r.json())
    ]);
    schedule = s;
    avail    = a;
    renderRotation();
    renderAvailability();
    renderStats();
}

function showPage(p) {
    currentPage = p;
    ['rotation','availability','stats'].forEach(name => {
        document.getElementById('page-' + name).style.display = name === p ? '' : 'none';
        document.getElementById('nav-' + name).classList.toggle('active', name === p);
    });
}

// ── ROTATION ──────────────────────────────────────────────────────────────────
function buildMatrix(w) {
    const weekRows = schedule.filter(r => r.week === w);
    return ALL_PLAYERS.map(p => ({
        name: p,
        Qs: [0,1,2,3].map(qi => {
            const row = weekRows[qi];
            return ALL_SLOTS.find(s => row && row[s] === p) || 'Off';
        })
    }));
}

function renderRotation() {
    matrix = buildMatrix(currentWeek);
    dirty  = false;
    const mtime = ''; // server doesn't expose this but that's fine
    let html = `
        <div class="page-title">${DATES[currentWeek-1]}</div>
        <div class="slider-wrap">
            <input type="range" min="1" max="${DATES.length}" value="${currentWeek}"
                   oninput="changeWeek(parseInt(this.value))"/>
        </div>
        <table>
            <thead><tr>
                <th style="width:18%">NAME</th>
                <th style="width:20.5%">Q1</th>
                <th style="width:20.5%">Q2</th>
                <th style="width:20.5%">Q3</th>
                <th style="width:20.5%">Q4</th>
            </tr></thead><tbody>`;
    matrix.forEach((row, pIdx) => {
        html += `<tr><td class="name-cell">${row.name}</td>`;
        row.Qs.forEach((pos, qIdx) => {
            const bg   = POS_COLORS[pos] || '#F5F5F5';
            const opts = ALL_SLOTS.map(s =>
                `<option value="${s}" ${s===pos?'selected':''}>${s}</option>`
            ).join('');
            html += `<td style="background:${bg}">
                <select onchange="onEdit(${pIdx},${qIdx},this.value)">${opts}</select>
            </td>`;
        });
        html += '</tr>';
    });
    html += `</tbody></table>
        <button class="save-btn" id="save-btn" onclick="saveChanges()">💾 Save &amp; Rebalance</button>
        <div class="status" id="status"></div>`;
    document.getElementById('page-rotation').innerHTML = html;
}

function changeWeek(w) {
    if (dirty && !confirm('You have unsaved changes. Discard?')) {
        document.querySelector('.slider-wrap input').value = currentWeek;
        return;
    }
    currentWeek = w;
    renderRotation();
}

function onEdit(pIdx, qIdx, newPos) {
    const oldPos = matrix[pIdx].Qs[qIdx];
    if (oldPos === newPos) return;
    const displacedIdx = matrix.findIndex(r => r.Qs[qIdx] === newPos);
    if (displacedIdx !== -1) matrix[displacedIdx].Qs[qIdx] = oldPos;
    matrix[pIdx].Qs[qIdx] = newPos;
    // Update cell background
    const cells = document.querySelectorAll('td select');
    cells.forEach(sel => {
        sel.parentElement.style.background = POS_COLORS[sel.value] || '#F5F5F5';
    });
    dirty = true;
    document.getElementById('status').textContent = '● Unsaved changes';
}

async function saveChanges() {
    if (!dirty) return;
    const btn = document.getElementById('save-btn');
    btn.disabled = true;
    btn.textContent = 'Saving...';
    try {
        const resp = await fetch('/api/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({week: currentWeek, matrix})
        });
        const result = await resp.json();
        if (result.ok) {
            // Reload schedule from server
            schedule = await fetch('/api/schedule').then(r => r.json());
            matrix = buildMatrix(currentWeek);
            dirty = false;
            document.getElementById('status').textContent = '✅ Saved!';
            btn.textContent = '💾 Save & Rebalance';
            btn.disabled = false;
            renderStats();
        } else {
            document.getElementById('status').textContent = 'Error: ' + result.error;
            btn.textContent = '💾 Save & Rebalance';
            btn.disabled = false;
        }
    } catch(e) {
        document.getElementById('status').textContent = 'Failed: ' + e.message;
        btn.textContent = '💾 Save & Rebalance';
        btn.disabled = false;
    }
}

// ── AVAILABILITY ──────────────────────────────────────────────────────────────
function renderAvailability() {
    let html = `<div class="page-title">Availability Planner</div>
        <table class="avail-table"><thead><tr>
            <th>Date</th>${ALL_PLAYERS.map(p => `<th>${p.slice(0,4)}</th>`).join('')}
        </tr></thead><tbody>`;
    DATES.forEach(d => {
        html += `<tr><td>${d}</td>`;
        ALL_PLAYERS.forEach(p => {
            const chk = avail[d] && avail[d][p] ? 'checked' : '';
            html += `<td><input type="checkbox" ${chk} onchange="availChange('${d}','${p}',this.checked)"/></td>`;
        });
        html += '</tr>';
    });
    html += `</tbody></table>
        <button onclick="saveAvail()" style="margin-top:8px;width:100%;padding:6px;
                background:#333;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px">
            Apply & Re-Balance</button>`;
    document.getElementById('page-availability').innerHTML = html;
}

function availChange(d, p, val) {
    if (!avail[d]) avail[d] = {};
    avail[d][p] = val;
}

async function saveAvail() {
    await fetch('/api/avail', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(avail)
    });
    schedule = await fetch('/api/schedule').then(r => r.json());
    renderRotation();
    renderStats();
}

// ── STATS ─────────────────────────────────────────────────────────────────────
function renderStats() {
    const counts = {};
    ALL_PLAYERS.forEach(p => { counts[p] = {}; POSITIONS.forEach(pos => counts[p][pos]=0); counts[p].Off=0; });
    schedule.forEach(row => {
        POSITIONS.forEach(pos => { if (row[pos] && counts[row[pos]]) counts[row[pos]][pos]++; });
        if (row.Off && counts[row.Off]) counts[row.Off].Off++;
    });
    let html = `<div class="page-title">Season Statistics</div>
        <table class="stats-table"><thead><tr>
            <th>Player</th>${POSITIONS.map(p=>`<th>${p}</th>`).join('')}<th>Off</th>
        </tr></thead><tbody>`;
    ALL_PLAYERS.forEach(p => {
        html += `<tr><td>${p}</td>`;
        POSITIONS.forEach(pos => html += `<td>${counts[p][pos]}</td>`);
        html += `<td>${counts[p].Off}</td></tr>`;
    });
    html += '</tbody></table>';
    document.getElementById('page-stats').innerHTML = html;
}

// ── RESET ─────────────────────────────────────────────────────────────────────
async function doReset() {
    if (!confirm('Reset entire rotation?')) return;
    await fetch('/api/reset', {method:'POST'});
    schedule = await fetch('/api/schedule').then(r => r.json());
    renderRotation();
    renderStats();
}

loadAll();
</script>
</body>
</html>"""

@app.route("/")
def index():
    return HTML

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
