import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import streamlit.components.v1 as components
import os, json

# --- 1. CONFIG & STYLING ---
st.set_page_config(page_title="Netball Pro", page_icon="🏐", layout="wide")

st.markdown("""
    <style>
    .block-container { padding: 0.3rem !important; max-width: 480px !important; margin: auto !important; }
    header, footer { visibility: hidden; }
    section[data-testid="stSidebar"] { display: none; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    th { font-size: 10px; color: #444; padding: 4px; border: 1px solid #ddd; background: #f8f9fa; }
    td { border: 1px solid #ddd; height: 38px; padding: 0; }
    .app-header {
        text-align: center;
        background: linear-gradient(135deg, #1a5276, #2e86c1);
        border-radius: 10px;
        padding: 14px 8px 10px 8px;
        margin-bottom: 10px;
    }
    .app-header h2 { margin: 0 0 3px 0; font-size: 18px; font-weight: 900; color: #ffffff; }
    .app-header p  { margin: 0; font-size: 11px; color: #aed6f1; letter-spacing: 1.5px; text-transform: uppercase; }
    </style>
""", unsafe_allow_html=True)

# --- 2. PERSISTENCE ---
DATA_DIR   = "/data" if os.path.isdir("/data") else os.path.dirname(os.path.abspath(__file__))
SCHED_FILE = os.path.join(DATA_DIR, "schedule.csv")
AVAIL_FILE = os.path.join(DATA_DIR, "availability.csv")

def save_data():
    st.session_state.master_schedule.to_csv(SCHED_FILE, index=False)
    st.session_state.availability.to_csv(AVAIL_FILE)

def load_schedule():
    if os.path.exists(SCHED_FILE):
        return pd.read_csv(SCHED_FILE)
    return None

def load_availability():
    if os.path.exists(AVAIL_FILE):
        return pd.read_csv(AVAIL_FILE, index_col=0)
    return None

# --- 3. CONFIG ---
all_players = ["Abbie", "Alexandra", "Audrey", "Judy", "Kim", "Klara", "Saga", "Zara"]
positions   = ["GS", "GA", "WA", "C", "WD", "GD", "GK"]
all_slots   = positions + ["Off"]

pos_colors = {
    "GS": "#FFD1DC", "GA": "#FFECB3", "WA": "#C8E6C9",
    "C":  "#B3E5FC", "WD": "#E1BEE7", "GD": "#D1C4E9",
    "GK": "#F8BBD0", "Off": "#F5F5F5"
}

SEASON_START = date(2026, 5, 11)
SEASON_END   = date(2026, 9, 21)
EXCLUSIONS   = {date(2026, 6, 8), date(2026, 7, 6), date(2026, 7, 13), date(2026, 9, 7)}

def season_dates():
    dates, curr = [], SEASON_START
    while curr <= SEASON_END:
        if curr not in EXCLUSIONS:
            dates.append(curr.strftime('%d %b %Y'))
        curr += timedelta(days=7)
    return dates

# --- 4. ROTATION ALGORITHM ---
def run_auto_allocation(force=False):
    dates = season_dates()

    if 'master_schedule' not in st.session_state or st.session_state.master_schedule.empty:
        rows = []
        for i, d in enumerate(dates, 1):
            for q in range(1, 5):
                rows.append({'Week': i, 'Date': d, 'Quarter': f"Q{q}"})
        st.session_state.master_schedule = pd.DataFrame(rows)
        for col in all_slots:
            st.session_state.master_schedule[col] = None

    df    = st.session_state.master_schedule
    avail = st.session_state.availability

    if force:
        for col in all_slots:
            df[col] = None

    regs = {
        "GS": "Attack", "GA": "Attack", "WA": "Attack",
        "C": "Mid",
        "WD": "Defense", "GD": "Defense", "GK": "Defense"
    }

    EQUITY_WEIGHT = 100
    CONT_WEIGHT   = 180
    REG_WEIGHT    = 60
    CHANGE_WEIGHT = 120

    pos_counts = {p: {pos: 0 for pos in positions} for p in all_players}
    off_counts = {p: 0 for p in all_players}
    num_weeks  = len(df) // 4

    for w in range(1, num_weeks + 1):
        d_str         = df[df['Week'] == w]['Date'].iloc[0]
        today_players = [p for p in all_players if avail.at[d_str, p]]
        n_avail       = len(today_players)
        n_off_per_q   = max(0, n_avail - 7)
        sat_off_this_game = set()

        for q_idx in range(4):
            base_idx = (w - 1) * 4 + q_idx

            if not force and pd.notna(df.at[base_idx, 'GS']):
                for pos in positions:
                    p = df.at[base_idx, pos]
                    if p in pos_counts:
                        pos_counts[p][pos] += 1
                off_p = df.at[base_idx, 'Off']
                if isinstance(off_p, str) and off_p in off_counts:
                    off_counts[off_p] += 1
                    sat_off_this_game.add(off_p)
                continue

            if n_off_per_q > 0:
                eligible = [p for p in today_players if p not in sat_off_this_game] or today_players
                off_now  = sorted(eligible, key=lambda p: off_counts[p])[:n_off_per_q]
            else:
                off_now = []

            for p in off_now:
                off_counts[p] += 1
                sat_off_this_game.add(p)

            df.at[base_idx, 'Off'] = off_now[0] if off_now else "N/A"
            on_now = [p for p in today_players if p not in off_now]

            prev_pos = {}
            if q_idx > 0:
                prev_idx = base_idx - 1
                for pos in positions:
                    p_prev = df.at[prev_idx, pos]
                    if isinstance(p_prev, str):
                        prev_pos[p_prev] = pos

            def score(p, pos):
                s = pos_counts[p][pos] * EQUITY_WEIGHT
                if p in prev_pos:
                    last = prev_pos[p]
                    if q_idx in [1, 3]:
                        if last == pos:
                            s -= CONT_WEIGHT
                        elif regs.get(last) == regs.get(pos):
                            s -= REG_WEIGHT
                    elif q_idx == 2:
                        if regs.get(last) == regs.get(pos):
                            s += CHANGE_WEIGHT
                return s

            def pos_spread(pos):
                scores = [score(p, pos) for p in remaining]
                return max(scores) - min(scores)

            assigned  = {}
            remaining = list(on_now)
            pos_order = sorted(positions, key=pos_spread, reverse=True)

            for pos in pos_order:
                if not remaining:
                    break
                best = min(remaining, key=lambda p: score(p, pos))
                assigned[pos] = best
                remaining.remove(best)

            for pos in positions:
                p = assigned.get(pos, "N/A")
                df.at[base_idx, pos] = p
                if isinstance(p, str) and p in pos_counts:
                    pos_counts[p][pos] += 1

    st.session_state.master_schedule = df


# --- 5. DEFAULT WEEK ---
def get_default_week(date_list):
    today = date.today()
    for i, d_str in enumerate(date_list):
        if datetime.strptime(d_str, '%d %b %Y').date() >= today:
            return i + 1
    return len(date_list)


# --- 6. INITIALISATION ---
if 'availability' not in st.session_state:
    loaded_avail = load_availability()
    if loaded_avail is not None:
        st.session_state.availability = loaded_avail
    else:
        dates = season_dates()
        st.session_state.availability = pd.DataFrame(True, index=dates, columns=all_players)

if 'master_schedule' not in st.session_state:
    loaded_sched = load_schedule()
    st.session_state.master_schedule = loaded_sched if loaded_sched is not None else pd.DataFrame()
    run_auto_allocation()

if 'round_selection' not in st.session_state:
    date_list = list(st.session_state.availability.index)
    st.session_state.round_selection = get_default_week(date_list)


# --- 7. HEADER ---
st.markdown("""
    <div class="app-header">
        <h2>🏐 Goodwood Primary School Yr3/4 Jets</h2>
        <p>Netball Rotation Planner</p>
    </div>
""", unsafe_allow_html=True)

# --- 8. PAGES (tabs) ---
tab1, tab2, tab3 = st.tabs(["📋 Rotation", "✅ Availability", "📊 Stats"])

# TAB 1: ROTATION
with tab1:
    rd = st.session_state.get('round_selection', 1)
    st.markdown(f"**{st.session_state.availability.index[rd-1]}**")
    st.slider("Match Week", 1, len(st.session_state.availability.index),
              key="round_selection", label_visibility="collapsed")

    if st.button("🚨 Reset Rotation", key="reset"):
        run_auto_allocation(force=True)
        save_data()
        st.rerun()

    view_df     = st.session_state.master_schedule[st.session_state.master_schedule['Week'] == rd].copy()
    matrix_data = []
    for p in all_players:
        row = {"name": p, "Qs": []}
        for i in range(4):
            q_data = view_df.iloc[i]
            pos = next((c for c in all_slots if q_data[c] == p), "Off")
            row["Qs"].append(pos)
        matrix_data.append(row)

    html_grid = f"""
    <div id="grid-root"></div>
    <script>
    const slots  = {json.dumps(all_slots)};
    const colors = {json.dumps(pos_colors)};
    let matrix   = {json.dumps(matrix_data)};

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
                h += `<td style="padding:0;border:1px solid #ddd;background:${{colors[pos]}};">
                    <select onchange="send(${{pIdx}},${{qIdx}},this.value)"
                        style="width:100%;height:100%;border:none;background:transparent;font-size:10px;font-weight:bold;text-align:center;appearance:none;cursor:pointer;">
                        ${{slots.map(s=>`<option value="${{s}}" ${{s===pos?'selected':''}}>${{s}}</option>`).join('')}}
                    </select></td>`;
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
        window.parent.postMessage({{type:'streamlit:setComponentValue',value:{{pIdx,qIdx,val,old:oldPos}}}},'*');
    }}
    render();
    </script>
    """

    update_data = components.html(html_grid, height=350)

    if isinstance(update_data, dict) and 'val' in update_data:
        m_idx       = ((rd - 1) * 4) + update_data['qIdx']
        p_name      = all_players[update_data['pIdx']]
        new_pos     = update_data['val']
        old_pos     = update_data['old']
        displaced_p = st.session_state.master_schedule.at[m_idx, new_pos]
        st.session_state.master_schedule.at[m_idx, new_pos] = p_name
        st.session_state.master_schedule.at[m_idx, old_pos] = displaced_p
        save_data()
        st.rerun()

# TAB 2: AVAILABILITY
with tab2:
    st.markdown("### Availability Planner")
    u = st.data_editor(st.session_state.availability, use_container_width=True)
    if st.button("Apply & Re-Balance"):
        st.session_state.availability = u
        run_auto_allocation(force=True)
        save_data()
        st.rerun()

# TAB 3: STATS
with tab3:
    st.markdown("### Season Statistics")
    melted = st.session_state.master_schedule.melt(id_vars=['Week'], value_vars=positions, value_name='Player')
    st.bar_chart(melted['Player'].value_counts())
    st.markdown("#### Position Breakdown by Player")
    st.dataframe(pd.crosstab(melted['Player'], melted.variable), use_container_width=True)
