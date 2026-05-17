import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import streamlit.components.v1 as components
import os, json, time

# --- CONFIG ---
st.set_page_config(page_title="Netball Pro", page_icon="🏐", layout="wide")

# Force light theme via injected style
st.markdown("""
    <style>
    :root, [data-theme="dark"], [data-theme="light"] {
        color-scheme: light !important;
    }
    html, body, [class*="css"] {
        background-color: #ffffff !important;
        color: #262730 !important;
    }
    [data-testid="stSidebar"] { display: none !important; }
    [data-testid="collapsedControl"] { display: none !important; }
    #MainMenu { visibility: hidden; }
    header, footer { visibility: hidden; }
    .block-container { padding: 0.3rem !important; max-width: 480px !important; margin: auto !important; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    th { font-size: 10px; color: #444; padding: 4px; border: 1px solid #ddd; background: #f8f9fa; }
    td { border: 1px solid #ddd; height: 38px; padding: 0; }
    .debug-box { background: #fff3cd; border: 1px solid #ffc107; padding: 8px; font-size: 11px; font-family: monospace; margin-bottom: 8px; white-space: pre-wrap; word-break: break-all; }
    </style>
""", unsafe_allow_html=True)

# --- PERSISTENCE ---
DATA_DIR   = os.environ.get("DATA_DIR", "/data")
SCHED_FILE = os.path.join(DATA_DIR, "schedule.json")
AVAIL_FILE = os.path.join(DATA_DIR, "avail.json")
DEBUG_FILE = os.path.join(DATA_DIR, "debug.log")

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        with open(DEBUG_FILE, "a") as f:
            f.write(line)
    except:
        pass

def file_read(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
        log(f"READ OK {path} size={os.path.getsize(path)}")
        return data
    except Exception as e:
        log(f"READ FAIL {path} err={e}")
        return None

def file_write(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        size = os.path.getsize(path)
        log(f"WRITE OK {path} size={size}")
    except Exception as e:
        log(f"WRITE FAIL {path} err={e}")
        st.error(f"Write failed: {e}")

def read_debug_log():
    try:
        with open(DEBUG_FILE, "r") as f:
            lines = f.readlines()
        return "".join(lines[-30:])  # last 30 lines
    except:
        return "No log yet"

# --- CONSTANTS ---
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

def default_avail():
    return {d: {p: True for p in ALL_PLAYERS} for d in DATES}

def default_schedule():
    rows = []
    for wi, d in enumerate(DATES):
        for q in range(4):
            row = {"week": wi+1, "date": d, "quarter": q+1}
            for s in ALL_SLOTS:
                row[s] = None
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
            for s in ALL_SLOTS:
                df[idx][s] = None

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
                    if pp and pp != "N/A":
                        prev_pos[pp] = pos

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

            def spread(pos, _rem=list(remaining)):
                if not _rem: return 0
                sc = [score(p, pos) for p in _rem]
                return max(sc) - min(sc)

            pos_order = sorted(POSITIONS, key=spread, reverse=True)
            assigned = {}
            for pos in pos_order:
                if not remaining: break
                best = min(remaining, key=lambda p: score(p, pos))
                assigned[pos] = best
                remaining.remove(best)

            for pos in POSITIONS:
                p = assigned.get(pos, "N/A")
                df[idx][pos] = p
                if p and p != "N/A" and p in pc:
                    pc[p][pos] += 1

    return df

# --- INIT ---
log(f"=== APP RUN session_id={id(st.session_state)} loaded={'loaded' in st.session_state} ===")

if "loaded" not in st.session_state:
    st.session_state.loaded = True
    st.session_state.page   = "Rotation"
    log(f"DATA_DIR={DATA_DIR} exists={os.path.exists(DATA_DIR)} writable={os.access(DATA_DIR, os.W_OK)}")
    log(f"SCHED_FILE exists={os.path.exists(SCHED_FILE)}")

    saved_avail = file_read(AVAIL_FILE)
    st.session_state.avail = saved_avail if saved_avail else default_avail()

    saved_sched = file_read(SCHED_FILE)
    if saved_sched:
        st.session_state.schedule = saved_sched
        log(f"Loaded schedule rows={len(saved_sched)} W1Q1_GS={saved_sched[0].get('GS')}")
    else:
        log("No saved schedule - generating fresh")
        pc, oc = build_counts([])
        st.session_state.schedule = run_allocation_from(
            default_schedule(), st.session_state.avail, 1, pc, oc
        )
        file_write(SCHED_FILE, st.session_state.schedule)

    today = date.today()
    st.session_state.week = len(DATES)
    for i, d_str in enumerate(DATES):
        if datetime.strptime(d_str, "%d %b %Y").date() >= today:
            st.session_state.week = i + 1
            break

# --- HANDLE EDIT ---
qp = st.query_params
if "edit" in qp:
    log(f"EDIT param received: {qp['edit']}")
    try:
        parts     = qp["edit"].split("_")
        edit_week = int(parts[0])
        p_idx     = int(parts[1])
        q_idx     = int(parts[2])
        new_pos   = parts[3]
        p_name    = ALL_PLAYERS[p_idx]
        m_idx     = (edit_week - 1) * 4 + q_idx
        sched     = st.session_state.schedule

        old_pos   = next((c for c in ALL_SLOTS if sched[m_idx].get(c) == p_name), None)
        displaced = sched[m_idx].get(new_pos)
        sched[m_idx][new_pos] = p_name
        if old_pos:
            sched[m_idx][old_pos] = displaced

        log(f"Edit: {p_name} {old_pos}->{new_pos} week={edit_week} q={q_idx}")

        next_week = edit_week + 1
        if next_week <= len(DATES):
            pc, oc = build_counts(sched, next_week, 0)
            sched  = run_allocation_from(sched, st.session_state.avail, next_week, pc, oc)

        st.session_state.schedule = sched
        st.session_state.week     = edit_week
        st.session_state.page     = "Rotation"
        file_write(SCHED_FILE, st.session_state.schedule)
        log(f"After write: file_exists={os.path.exists(SCHED_FILE)} size={os.path.getsize(SCHED_FILE) if os.path.exists(SCHED_FILE) else 0}")
    except Exception as e:
        log(f"EDIT ERROR: {e}")
        st.toast(f"Edit error: {e}")
    st.query_params.clear()
    st.rerun()

# --- NAV ---
st.markdown("### 🏐 Netball Pro")
c1, c2, c3, c4 = st.columns(4)
with c1:
    if st.button("Rotation", use_container_width=True):
        st.session_state.page = "Rotation"
        st.rerun()
with c2:
    if st.button("Availability", use_container_width=True):
        st.session_state.page = "Availability"
        st.rerun()
with c3:
    if st.button("Stats", use_container_width=True):
        st.session_state.page = "Stats"
        st.rerun()
with c4:
    if st.button("🚨 Reset", use_container_width=True):
        pc, oc = build_counts([])
        st.session_state.schedule = run_allocation_from(
            default_schedule(), st.session_state.avail, 1, pc, oc
        )
        file_write(SCHED_FILE, st.session_state.schedule)
        st.rerun()

page = st.session_state.page

# --- DEBUG PANEL ---
with st.expander("🔍 Debug (tap to expand)"):
    st.code(read_debug_log(), language=None)
    col1, col2 = st.columns(2)
    with col1:
        st.write("DATA_DIR:", DATA_DIR)
        st.write("exists:", os.path.exists(DATA_DIR))
        st.write("writable:", os.access(DATA_DIR, os.W_OK))
    with col2:
        st.write("schedule.json:", os.path.exists(SCHED_FILE))
        if os.path.exists(SCHED_FILE):
            st.write("size:", os.path.getsize(SCHED_FILE), "bytes")
            st.write("modified:", datetime.fromtimestamp(os.path.getmtime(SCHED_FILE)).strftime("%H:%M:%S"))

# --- ROTATION PAGE ---
if page == "Rotation":
    w = st.session_state.week
    st.markdown(f"### {DATES[w-1]}")
    st.slider("Match Week", 1, len(DATES), key="week", label_visibility="collapsed")

    week_rows   = [r for r in st.session_state.schedule if r["week"] == w]
    matrix_data = []
    for p in ALL_PLAYERS:
        row = {"name": p, "Qs": []}
        for qi in range(4):
            qrow = week_rows[qi]
            pos  = next((c for c in ALL_SLOTS if qrow.get(c) == p), "Off")
            row["Qs"].append(pos)
        matrix_data.append(row)

    html_grid = f"""
    <div id="grid-root"></div>
    <script>
    const players = {json.dumps(ALL_PLAYERS)};
    const slots   = {json.dumps(ALL_SLOTS)};
    const colors  = {json.dumps(POS_COLORS)};
    let matrix    = {json.dumps(matrix_data)};
    const week    = {w};

    function render() {{
        let h = `<table style="width:100%;border-collapse:collapse;font-family:sans-serif;table-layout:fixed;">
            <thead><tr style="background:#eee;font-size:10px;">
                <th style="width:18%;padding:4px;border:1px solid #ddd;">NAME</th>
                <th style="width:20.5%;border:1px solid #ddd;">Q1</th>
                <th style="width:20.5%;border:1px solid #ddd;">Q2</th>
                <th style="width:20.5%;border:1px solid #ddd;">Q3</th>
                <th style="width:20.5%;border:1px solid #ddd;">Q4</th>
            </tr></thead><tbody>`;
        matrix.forEach((row, pIdx) => {{
            h += `<tr style="height:38px;">
                <td style="font-size:10px;font-weight:bold;padding-left:3px;border:1px solid #ddd;background:#fff;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">${{row.name}}</td>`;
            row.Qs.forEach((pos, qIdx) => {{
                const opts = slots.map(s => `<option value="${{s}}" ${{s===pos?'selected':''}}>${{s}}</option>`).join('');
                h += `<td style="padding:0;border:1px solid #ddd;background:${{colors[pos]||'#F5F5F5'}};">
                    <select onchange="send(${{pIdx}},${{qIdx}},this.value)"
                        style="width:100%;height:100%;border:none;background:transparent;font-size:10px;font-weight:bold;text-align:center;appearance:none;cursor:pointer;">
                        ${{opts}}
                    </select>
                </td>`;
            }});
            h += `</tr>`;
        }});
        h += `</tbody></table>`;
        document.getElementById('grid-root').innerHTML = h;
    }}

    function send(pIdx, qIdx, val) {{
        const oldPos = matrix[pIdx].Qs[qIdx];
        const displacedIdx = matrix.findIndex(r => r.Qs[qIdx] === val);
        if (displacedIdx !== -1) matrix[displacedIdx].Qs[qIdx] = oldPos;
        matrix[pIdx].Qs[qIdx] = val;
        render();
        const url = new URL(window.parent.location.href);
        url.searchParams.set('edit', week + '_' + pIdx + '_' + qIdx + '_' + val);
        window.parent.location.href = url.toString();
    }}
    render();
    </script>
    """
    components.html(html_grid, height=350)

elif page == "Availability":
    st.markdown("### Availability Planner")
    avail_df = pd.DataFrame(st.session_state.avail).T[ALL_PLAYERS]
    updated  = st.data_editor(avail_df, use_container_width=True)
    if st.button("Apply & Re-Balance"):
        st.session_state.avail = updated.to_dict(orient="index")
        file_write(AVAIL_FILE, st.session_state.avail)
        pc, oc = build_counts([])
        st.session_state.schedule = run_allocation_from(
            default_schedule(), st.session_state.avail, 1, pc, oc
        )
        file_write(SCHED_FILE, st.session_state.schedule)
        st.rerun()

elif page == "Stats":
    st.markdown("### Season Statistics")
    counts = {p: {pos: 0 for pos in POSITIONS} | {"Off": 0} for p in ALL_PLAYERS}
    for row in st.session_state.schedule:
        for pos in POSITIONS:
            p = row.get(pos)
            if p and p != "N/A" and p in counts:
                counts[p][pos] += 1
        off_p = row.get("Off")
        if off_p and off_p != "N/A" and off_p in counts:
            counts[off_p]["Off"] += 1
    stats_df = pd.DataFrame(counts).T[POSITIONS + ["Off"]]
    st.dataframe(stats_df, use_container_width=True)
