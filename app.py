import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import streamlit.components.v1 as components
import os, io, json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# --- 1. SYSTEM CONFIG & MOBILE STYLING ---
st.set_page_config(page_title="Netball Pro", page_icon="🏐", layout="wide")

st.markdown("""
    <style>
    /* COMPACT MOBILE VIEW: One-screen fit for screenshots */
    .block-container { padding: 0.3rem !important; max-width: 480px !important; margin: auto !important; }
    header, footer { visibility: hidden; }
    .stHeading, .stMarkdown { text-align: center; }
    
    /* Rotation Table High-Density Styling */
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

FOLDER_ID = '1Hm9wBzP9XvTcz2i4t7Nz60TXRm-pEGee'
FILE_NAME = "netball_rotation_backup.csv"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

all_players = ["Abbie", "Zara", "Judy", "Alexandra", "Kim", "Klara", "Saga", "Audrey"]
positions = ["GS", "GA", "WA", "C", "WD", "GD", "GK"]
all_slots = positions + ["Off"]

pos_colors = {
    "GS": "#FFD1DC", "GA": "#FFECB3", "WA": "#C8E6C9", 
    "C":  "#B3E5FC", "WD": "#E1BEE7", "GD": "#D1C4E9", 
    "GK": "#F8BBD0", "Off": "#F5F5F5"
}

# --- 2. DRIVE SYNC ---
def get_drive_service():
    # Try multiple possible locations for creds.json
    candidates = [
        os.path.join(BASE_DIR, 'creds.json'),
        os.path.join(os.getcwd(), 'creds.json'),
        'creds.json',
    ]
    CREDS_FILE = next((p for p in candidates if os.path.exists(p)), None)
    if not CREDS_FILE:
        st.error("creds.json not found in: " + str(candidates))
        return None
    creds = service_account.Credentials.from_service_account_file(
        CREDS_FILE, scopes=['https://www.googleapis.com/auth/drive'])
    return build('drive', 'v3', credentials=creds)

def get_file_id(service):
    """Find the CSV file ID in the Drive folder, or None if not found."""
    query = "name = '" + FILE_NAME + "' and '" + FOLDER_ID + "' in parents and trashed = false"
    res = service.files().list(q=query, fields='files(id,name)').execute()
    files = res.get('files', [])
    return files[0]['id'] if files else None

def update_drive():
    service = get_drive_service()
    if not service:
        st.error("Drive: no service — creds not found")
        return
    csv_bytes = st.session_state.master_schedule.to_csv(index=False).encode('utf-8')
    media = MediaIoBaseUpload(io.BytesIO(csv_bytes), mimetype='text/csv', resumable=False)
    file_id = get_file_id(service)
    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        meta = {'name': FILE_NAME, 'parents': [FOLDER_ID]}
        service.files().create(body=meta, media_body=media, fields='id').execute()
    st.toast("✅ Saved to Drive")

def load_from_drive():
    try:
        service = get_drive_service()
        if not service:
            return None
        file_id = get_file_id(service)
        if not file_id:
            return None
        data = service.files().get_media(fileId=file_id).execute()
        return pd.read_csv(io.BytesIO(data))
    except Exception as e:
        st.warning("Could not load from Drive: " + str(e))
        return None

# --- 3. IMPROVED ROTATION ALGORITHM ---
def run_auto_allocation(force=False):
    """
    Priority 1: No player sits off more than 1 quarter per game.
    Priority 2: Continuity bias (same pos Q1->Q2, Q3->Q4; region switch Q2->Q3)
                but balanced against season equity (everyone gets similar counts
                in each position).
    """
    # Build schedule skeleton if needed
    if 'master_schedule' not in st.session_state or st.session_state.master_schedule.empty:
        dates = []
        curr = date(2026, 5, 11)  # CHANGED: start 11 May
        exclusions = [date(2026, 6, 8), date(2026, 7, 6), date(2026, 7, 13), date(2026, 9, 7)]
        while curr <= date(2026, 9, 21):
            if curr not in exclusions:
                dates.append(curr.strftime('%d %b %Y'))
            curr += timedelta(days=7)
        rows = []
        for i, d in enumerate(dates, 1):
            for q in range(1, 5):
                rows.append({'Week': i, 'Date': d, 'Quarter': f"Q{q}"})
        st.session_state.master_schedule = pd.DataFrame(rows)
        for col in all_slots:
            st.session_state.master_schedule[col] = None

    df = st.session_state.master_schedule

    # When forcing, wipe all existing allocations so they're fully regenerated
    if force:
        for col in all_slots:
            df[col] = None

    avail = st.session_state.availability

    # Region groupings for Q3 switch rule
    regs = {
        "GS": "Attack", "GA": "Attack", "WA": "Attack",
        "C": "Mid",
        "WD": "Defense", "GD": "Defense", "GK": "Defense"
    }

    # Cumulative season counters
    pos_counts = {p: {pos: 0 for pos in positions} for p in all_players}
    off_counts = {p: 0 for p in all_players}

    num_weeks = len(df) // 4

    for w in range(1, num_weeks + 1):
        d_str = df[df['Week'] == w]['Date'].iloc[0]
        today_players = [p for p in all_players if avail.at[d_str, p]]
        n_avail = len(today_players)

        # With 8 players and 7 positions: exactly 1 sits off each quarter.
        # With fewer players, no-one sits off (shouldn't happen but handled).
        n_off_per_q = max(0, n_avail - 7)

        # Track who has already sat off this game (Priority 1: max 1 off per game)
        sat_off_this_game = set()

        for q_idx in range(4):
            base_idx = (w - 1) * 4 + q_idx

            # If not forcing and already allocated, just count it
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

            # --- Decide who sits off this quarter ---
            if n_off_per_q > 0:
                # Must not have sat off already this game
                eligible_off = [p for p in today_players if p not in sat_off_this_game]
                if not eligible_off:
                    eligible_off = today_players  # safety fallback

                # Pick player(s) with fewest season off-counts to equalise
                eligible_off_sorted = sorted(eligible_off, key=lambda p: off_counts[p])
                off_now = eligible_off_sorted[:n_off_per_q]
            else:
                off_now = []

            for p in off_now:
                off_counts[p] += 1
                sat_off_this_game.add(p)

            df.at[base_idx, 'Off'] = off_now[0] if off_now else "N/A"
            on_now = [p for p in today_players if p not in off_now]

            EQUITY_WEIGHT  = 100
            CONT_WEIGHT    = 180
            REG_WEIGHT     = 60
            CHANGE_WEIGHT  = 120

            prev_pos = {}  # player → position in q_idx-1
            if q_idx > 0:
                prev_idx = base_idx - 1
                for pos in positions:
                    p_prev = df.at[prev_idx, pos]
                    if isinstance(p_prev, str):
                        prev_pos[p_prev] = pos

            assigned = {}   # pos → player
            remaining = list(on_now)

            def score(p, pos):
                s = pos_counts[p][pos] * EQUITY_WEIGHT
                if p in prev_pos:
                    last = prev_pos[p]
                    if q_idx in [1, 3]:  # Q2 or Q4 – continuity half
                        if last == pos:
                            s -= CONT_WEIGHT
                        elif regs.get(last) == regs.get(pos):
                            s -= REG_WEIGHT
                    elif q_idx == 2:  # Q3 – want region change from Q2
                        if regs.get(last) == regs.get(pos):
                            s += CHANGE_WEIGHT
                return s

            def pos_spread(pos):
                scores = [score(p, pos) for p in remaining]
                return max(scores) - min(scores)

            pos_order = sorted(positions, key=pos_spread, reverse=True)

            for pos in pos_order:
                if not remaining:
                    break
                best = min(remaining, key=lambda p: score(p, pos))
                assigned[pos] = best
                remaining.remove(best)

            # Write back to dataframe
            for pos in positions:
                p = assigned.get(pos, "N/A")
                df.at[base_idx, pos] = p
                if isinstance(p, str) and p in pos_counts:
                    pos_counts[p][pos] += 1

    st.session_state.master_schedule = df


# --- 4. COMPUTE DEFAULT WEEK (current or next game) ---
def get_default_week(date_list):
    """Return the 1-based week index for today's game or the next upcoming game."""
    today = date.today()
    for i, d_str in enumerate(date_list):
        game_date = datetime.strptime(d_str, '%d %b %Y').date()
        if game_date >= today:
            return i + 1
    return len(date_list)  # If season over, show last round


# --- 5. INITIALIZATION ---
if 'availability' not in st.session_state:
    dates = []
    curr = date(2026, 5, 11)  # CHANGED: start 11 May
    exclusions = [date(2026, 6, 8), date(2026, 7, 6), date(2026, 7, 13), date(2026, 9, 7)]
    while curr <= date(2026, 9, 21):
        if curr not in exclusions:
            dates.append(curr.strftime('%d %b %Y'))
        curr += timedelta(days=7)
    st.session_state.availability = pd.DataFrame(True, index=dates, columns=all_players)

if 'master_schedule' not in st.session_state:
    loaded = load_from_drive()
    # Discard stale Drive backup if it still contains the old May 4 start date
    if loaded is not None and '04 May 2026' in loaded['Date'].values:
        loaded = None
    st.session_state.master_schedule = loaded if loaded is not None else pd.DataFrame()
    run_auto_allocation()

# Set default week based on today's date (only on first load)
if 'round_selection' not in st.session_state:
    date_list = list(st.session_state.availability.index)
    st.session_state.round_selection = get_default_week(date_list)

# --- 6. PAGES ---
with st.sidebar:
    st.title("🏐 Netball Pro")
    page = st.radio("Menu", ["Rotation", "Availability", "Stats"])
    if st.button("🚨 Reset Rotation"):
        run_auto_allocation(force=True)
        update_drive()
        st.rerun()

# PAGE 1: ROTATION (FIXED HEADINGS)
if page == "Rotation":

    # Catch edit posted via query param from JS.
    # JS writes ?edit=week_pIdx_qIdx_newPos instead of postMessage
    # (postMessage from components.html never returns a value to Python).
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
            old_pos   = next((c for c in all_slots
                              if st.session_state.master_schedule.at[m_idx, c] == p_name), None)
            displaced = st.session_state.master_schedule.at[m_idx, new_pos]
            st.session_state.master_schedule.at[m_idx, new_pos] = p_name
            if old_pos:
                st.session_state.master_schedule.at[m_idx, old_pos] = displaced
            st.session_state['round_selection'] = edit_week
            update_drive()
        except Exception as e:
            st.toast(f"Edit error: {e}")
        st.query_params.clear()
        st.rerun()

    rd = st.session_state.get('round_selection', 1)
    st.markdown(f"### {st.session_state.availability.index[rd-1]}")
    st.slider("Match Week", 1, len(st.session_state.availability.index), key="round_selection", label_visibility="collapsed")

    view_df = st.session_state.master_schedule[st.session_state.master_schedule['Week'] == rd].copy()
    
    matrix_data = []
    for p in all_players:
        row = {"name": p, "Qs": []}
        for i in range(4):
            q_data = view_df.iloc[i]
            pos = next((c for c in all_slots if q_data[c] == p), "Off")
            row["Qs"].append(pos)
        matrix_data.append(row)

    # HTML GRID: Fixed Name Width (18%) and Quarter Width (20.5%)
    html_grid = f"""
    <div id="grid-root"></div>
    <script>
    const players = {json.dumps(all_players)};
    const slots = {json.dumps(all_slots)};
    const colors = {json.dumps(pos_colors)};
    let matrix = {json.dumps(matrix_data)};

    function render() {{
        let h = `<table style="width:100%; border-collapse:collapse; font-family:sans-serif; table-layout:fixed;">
            <thead><tr style="background:#eee; font-size:10px;">
                <th style="width:18%; padding:4px; border:1px solid #ddd;">NAME</th>
                <th style="width:20.5%; border:1px solid #ddd;">Q1</th><th style="width:20.5%; border:1px solid #ddd;">Q2</th>
                <th style="width:20.5%; border:1px solid #ddd;">Q3</th><th style="width:20.5%; border:1px solid #ddd;">Q4</th>
            </tr></thead><tbody>`;
        
        matrix.forEach((row, pIdx) => {{
            h += `<tr style="height:38px;">
                <td style="font-size:10px; font-weight:bold; padding-left:3px; border:1px solid #ddd; background:#fff; overflow:hidden; white-space:nowrap; text-overflow:ellipsis;">${{row.name}}</td>`;
            row.Qs.forEach((pos, qIdx) => {{
                h += `<td style="padding:0; border:1px solid #ddd; background:${{colors[pos]}};">
                    <select onchange="send(${{pIdx}}, ${{qIdx}}, this.value)" style="width:100%; height:100%; border:none; background:transparent; font-size:10px; font-weight:bold; text-align:center; appearance:none; cursor:pointer;">
                        ${{slots.map(s => `<option value="${{s}}" ${{s===pos ? 'selected' : ''}}>${{s}}</option>`).join('')}}
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
        url.searchParams.set('edit', '{rd}' + '_' + pIdx + '_' + qIdx + '_' + val);
        window.parent.location.href = url.toString();
    }}
    render();
    </script>
    """
    
    components.html(html_grid, height=350)

# PAGE 2: AVAILABILITY (RESTORED)
elif page == "Availability":
    st.markdown("### Availability Planner")
    u = st.data_editor(st.session_state.availability, use_container_width=True)
    if st.button("Apply & Re-Balance"):
        st.session_state.availability = u
        run_auto_allocation(force=True)
        update_drive()
        st.rerun()

# PAGE 3: STATS (RESTORED)
else:
    st.markdown("### Season Statistics")
    # Bar chart for position counts
    melted = st.session_state.master_schedule.melt(id_vars=['Week'], value_vars=positions, value_name='Player')
    st.bar_chart(melted['Player'].value_counts())
    
    # Crosstab for detailed breakdown
    st.markdown("#### Position Breakdown by Player")
    st.dataframe(pd.crosstab(melted['Player'], melted.variable), use_container_width=True)
