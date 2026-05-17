import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import streamlit.components.v1 as components
import os, io, json, sqlite3

# --- 1. SYSTEM CONFIG & MOBILE STYLING ---
st.set_page_config(page_title="Netball Pro", page_icon="🏐", layout="wide")

st.markdown("""
    <style>
    .block-container { padding: 0.3rem !important; max-width: 480px !important; margin: auto !important; }
    header, footer { visibility: hidden; }
    .stHeading, .stMarkdown { text-align: center; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    th { font-size: 10px; color: #444; padding: 4px; border: 1px solid #ddd; background: #f8f9fa; }
    td { border: 1px solid #ddd; height: 38px; padding: 0; }
    .player-cell {
        font-size: 10px;
        font-weight: bold;
        padding-left: 4px;
        background: #fff;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    </style>
""", unsafe_allow_html=True)

# --- 2. PERSISTENCE: SQLite on a Fly volume ---
DATA_DIR = os.environ.get("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "netball.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def db_get(key):
    conn = get_db()
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    conn.close()
    return json.loads(row["value"]) if row else None

def db_set(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (key, json.dumps(value)))
    conn.commit()
    conn.close()

init_db()

# --- 3. APP DATA ---
all_players = ["Abbie", "Alexandra", "Audrey", "Judy", "Kim", "Klara", "Saga", "Zara"]
positions   = ["GS", "GA", "WA", "C", "WD", "GD", "GK"]
all_slots   = positions + ["Off"]

pos_colors = {
    "GS": "#FFD1DC", "GA": "#FFECB3", "WA": "#C8E6C9",
    "C":  "#B3E5FC", "WD": "#E1BEE7", "GD": "#D1C4E9",
    "GK": "#F8BBD0", "Off": "#F5F5F5"
}

REGIONS = {
    "GS": "Attack", "GA": "Attack", "WA": "Attack",
    "C": "Mid",
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
    return {d: {p: True for p in all_players} for d in DATES}

def default_schedule():
    rows = []
    for wi, d in enumerate(DATES):
        for q in range(4):
            row = {"week": wi+1, "date": d, "quarter": q+1}
            for s in all_slots:
                row[s] = None
            rows.append(row)
    return rows

def build_counts(schedule, up_to_week=None, up_to_qi=None):
    pos_counts = {p: {pos: 0 for pos in positions} for p in all_players}
    off_counts  = {p: 0 for p in all_players}
    for row in schedule:
        w  = row["week"]
        qi = row["quarter"] - 1
        if up_to_week is not None:
            if w > up_to_week: continue
            if w == up_to_week and qi >= up_to_qi: continue
        for pos in positions:
            p = row.get(pos)
            if p and p != "N/A" and p in pos_counts:
                pos_counts[p][pos] += 1
        off_p = row.get("Off")
        if off_p and off_p != "N/A" and off_p in off_counts:
            off_counts[off_p] += 1
    return pos_counts, off_counts

def run_allocation_from(schedule, avail, start_week, pos_counts, off_counts):
    import copy
    df = [dict(r) for r in schedule]
    pc = {p: dict(pos_counts[p]) for p in all_players}
    oc = dict(off_counts)

    EQUITY, CONT, REG, CHANGE = 100, 180, 60, 120

    for w in range(start_week, len(DATES)+1):
        d_str = DATES[w-1]
        today_players = [p for p in all_players if avail.get(d_str, {}).get(p, True)]
        n_off = max(0, len(today_players) - 7)
        sat_off_game = set()

        for qi in range(4):
            idx = (w-1)*4 + qi
            for s in all_slots:
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
                for pos in positions:
                    pp = prev.get(pos)
                    if pp and pp != "N/A":
                        prev_pos[pp] = pos

            def score(p, pos):
                s = pc[p][pos] * EQUITY
                if p in prev_pos:
                    last = prev_pos[p]
                    if qi in (1, 3):
                        if last == pos:        s -= CONT
                        elif REGIONS.get(last) == REGIONS.get(pos): s -= REG
                    elif qi == 2:
                        if REGIONS.get(last) == REGIONS.get(pos): s += CHANGE
                return s

            remaining = list(on_now)

            def spread(pos):
                if not remaining: return 0
                sc = [score(p, pos) for p in remaining]
                return max(sc) - min(sc)

            pos_order = sorted(positions, key=spread, reverse=True)
            assigned = {}
            for pos in pos_order:
                if not remaining: break
                best = min(remaining, key=lambda p: score(p, pos))
                assigned[pos] = best
                remaining.remove(best)

            for pos in positions:
                p = assigned.get(pos, "N/A")
                df[idx][pos] = p
                if p and p != "N/A" and p in pc:
                    pc[p][pos] += 1

    return df

# --- 4. INITIALIZATION ---
if 'avail' not in st.session_state:
    saved_avail = db_get('avail')
    st.session_state.avail = saved_avail if saved_avail else default_avail()

if 'schedule' not in st.session_state:
    saved_sched = db_get('schedule')
    if saved_sched:
        st.session_state.schedule = saved_sched
    else:
        pc, oc = build_counts([])
        st.session_state.schedule = run_allocation_from(
            default_schedule(), st.session_state.avail, 1, pc, oc
        )
        db_set('schedule', st.session_state.schedule)

if 'week' not in st.session_state:
    today = date.today()
    st.session_state.week = len(DATES)
    for i, d_str in enumerate(DATES):
        gd = datetime.strptime(d_str, '%d %b %Y').date()
        if gd >= today:
            st.session_state.week = i + 1
            break

# --- 5. SIDEBAR & NAV ---
with st.sidebar:
    st.title("🏐 Netball Pro")
    page = st.radio("Menu", ["Rotation", "Availability", "Stats"])
    if st.button("🚨 Reset Rotation"):
        pc, oc = build_counts([])
        st.session_state.schedule = run_allocation_from(
            default_schedule(), st.session_state.avail, 1, pc, oc
        )
        db_set('schedule', st.session_state.schedule)
        st.rerun()

# --- 6. HANDLE EDIT FROM JS (query param) ---
if page == "Rotation":
    qp = st.query_params
    if "edit" in qp:
        try:
            parts = qp["edit"].split("_")
            edit_week = int(parts[0])
            p_idx     = int(parts[1])
            q_idx     = int(parts[2])
            new_pos   = parts[3]
            p_name    = all_players[p_idx]
            m_idx     = (edit_week - 1) * 4 + q_idx
            sched     = st.session_state.schedule
            old_pos   = next((c for c in all_slots if sched[m_idx].get(c) == p_name), None)
            displaced = sched[m_idx].get(new_pos)
            sched[m_idx][new_pos] = p_name
            if old_pos:
                sched[m_idx][old_pos] = displaced

            # Rebalance all weeks after this one
            next_week = edit_week + 1
            if next_week <= len(DATES):
                pc, oc = build_counts(sched, next_week, 0)
                sched = run_allocation_from(sched, st.session_state.avail, next_week, pc, oc)

            st.session_state.schedule = sched
            st.session_state.week = edit_week
            db_set('schedule', st.session_state.schedule)
        except Exception as e:
            st.toast(f"Edit error: {e}")
        st.query_params.clear()
        st.rerun()

# --- 7. ROTATION PAGE ---
if page == "Rotation":
    w = st.session_state.week
    st.markdown(f"### {DATES[w-1]}")
    st.slider("Match Week", 1, len(DATES), key="week", label_visibility="collapsed")

    sched = st.session_state.schedule
    week_rows = [r for r in sched if r["week"] == w]

    matrix_data = []
    for p in all_players:
        row = {"name": p, "Qs": []}
        for qi in range(4):
            qrow = week_rows[qi]
            pos = next((c for c in all_slots if qrow.get(c) == p), "Off")
            row["Qs"].append(pos)
        matrix_data.append(row)

    html_grid = f"""
    <div id="grid-root"></div>
    <script>
    const players = {json.dumps(all_players)};
    const slots   = {json.dumps(all_slots)};
    const colors  = {json.dumps(pos_colors)};
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

# --- 8. AVAILABILITY PAGE ---
elif page == "Availability":
    st.markdown("### Availability Planner")
    avail_df = pd.DataFrame(st.session_state.avail).T
    avail_df = avail_df[all_players]
    updated = st.data_editor(avail_df, use_container_width=True)
    if st.button("Apply & Re-Balance"):
        st.session_state.avail = updated.to_dict(orient='index')
        db_set('avail', st.session_state.avail)
        pc, oc = build_counts([])
        st.session_state.schedule = run_allocation_from(
            default_schedule(), st.session_state.avail, 1, pc, oc
        )
        db_set('schedule', st.session_state.schedule)
        st.rerun()

# --- 9. STATS PAGE ---
else:
    st.markdown("### Season Statistics")
    counts = {p: {pos: 0 for pos in positions} | {"Off": 0} for p in all_players}
    for row in st.session_state.schedule:
        for pos in positions:
            p = row.get(pos)
            if p and p != "N/A" and p in counts:
                counts[p][pos] += 1
        off_p = row.get("Off")
        if off_p and off_p != "N/A" and off_p in counts:
            counts[off_p]["Off"] += 1
    stats_df = pd.DataFrame(counts).T[positions + ["Off"]]
    st.dataframe(stats_df, use_container_width=True)
