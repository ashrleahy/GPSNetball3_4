import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import os, io, json

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
    DRIVE_AVAILABLE = True
except ImportError:
    DRIVE_AVAILABLE = False

# --- 1. SYSTEM CONFIG & MOBILE STYLING (UNCHANGED) ---
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

    /* Tighten selectbox labels away */
    div[data-testid="stSelectbox"] label { display: none; }
    div[data-testid="stSelectbox"] > div > div {
        font-size: 10px !important;
        font-weight: bold !important;
        min-height: 34px !important;
        padding: 2px 2px !important;
    }
    </style>
""", unsafe_allow_html=True)

FOLDER_ID = '1Hm9wBzP9XvTcz2i4t7Nz60TXRm-pEGee'
FILE_NAME = "netball_rotation_backup.csv"
LOCAL_BACKUP = "/data/netball_rotation_backup.csv"   # Fly.io persistent volume
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

all_players = ["Abbie", "Zara", "Judy", "Alexandra", "Kim", "Klara", "Saga", "Audrey"]
positions = ["GS", "GA", "WA", "C", "WD", "GD", "GK"]
all_slots = positions + ["Off"]

pos_colors = {
    "GS": "#FFD1DC", "GA": "#FFECB3", "WA": "#C8E6C9",
    "C":  "#B3E5FC", "WD": "#E1BEE7", "GD": "#D1C4E9",
    "GK": "#F8BBD0", "Off": "#F5F5F5"
}


# --- 2. DRIVE + LOCAL SYNC ---
def get_drive_service():
    if not DRIVE_AVAILABLE:
        return None
    candidates = [
        os.path.join(BASE_DIR, 'creds.json'),
        os.path.join(os.getcwd(), 'creds.json'),
        'creds.json',
    ]
    CREDS_FILE = next((p for p in candidates if os.path.exists(p)), None)
    if not CREDS_FILE:
        return None
    try:
        creds = service_account.Credentials.from_service_account_file(
            CREDS_FILE, scopes=['https://www.googleapis.com/auth/drive'])
        return build('drive', 'v3', credentials=creds)
    except Exception:
        return None

def get_file_id(service):
    query = f"name = '{FILE_NAME}' and '{FOLDER_ID}' in parents and trashed = false"
    res = service.files().list(q=query, fields='files(id,name)').execute()
    files = res.get('files', [])
    return files[0]['id'] if files else None

def save_local(df):
    """Save to Fly.io persistent volume if available, else working dir."""
    for path in [LOCAL_BACKUP, "netball_rotation_backup.csv"]:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
            df.to_csv(path, index=False)
            return
        except Exception:
            continue

def update_drive():
    """Always save locally first, then try Drive."""
    save_local(st.session_state.master_schedule)
    service = get_drive_service()
    if not service:
        st.toast("💾 Saved locally")
        return
    try:
        csv_bytes = st.session_state.master_schedule.to_csv(index=False).encode('utf-8')
        media = MediaIoBaseUpload(io.BytesIO(csv_bytes), mimetype='text/csv', resumable=False)
        file_id = get_file_id(service)
        if file_id:
            service.files().update(fileId=file_id, media_body=media).execute()
        else:
            meta = {'name': FILE_NAME, 'parents': [FOLDER_ID]}
            service.files().create(body=meta, media_body=media, fields='id').execute()
        st.toast("✅ Saved to Drive")
    except Exception as e:
        st.toast(f"⚠️ Drive failed, saved locally")

def load_saved():
    """Try local volume first, then Drive."""
    for path in [LOCAL_BACKUP, "netball_rotation_backup.csv"]:
        if os.path.exists(path):
            try:
                df = pd.read_csv(path)
                if '04 May 2026' not in df['Date'].values:
                    return df
            except Exception:
                pass
    try:
        service = get_drive_service()
        if service:
            file_id = get_file_id(service)
            if file_id:
                data = service.files().get_media(fileId=file_id).execute()
                df = pd.read_csv(io.BytesIO(data))
                if '04 May 2026' not in df['Date'].values:
                    return df
    except Exception:
        pass
    return None


# --- 3. DATE HELPERS ---
def build_date_list():
    dates = []
    curr = date(2026, 5, 11)
    exclusions = [date(2026, 6, 8), date(2026, 7, 6), date(2026, 7, 13), date(2026, 9, 7)]
    while curr <= date(2026, 9, 21):
        if curr not in exclusions:
            dates.append(curr.strftime('%d %b %Y'))
        curr += timedelta(days=7)
    return dates

def get_default_week(date_list):
    today = date.today()
    for i, d_str in enumerate(date_list):
        game_date = datetime.strptime(d_str, '%d %b %Y').date()
        if game_date >= today:
            return i + 1
    return len(date_list)


# --- 4. ROTATION ALGORITHM ---
#
# Rules (hard → soft):
#   1. HARD  – max 1 quarter off per player per game (with 8 players, exactly
#              1 sits off each quarter, but never the same player twice).
#   2. PAIR  – Q1+Q2 are a pair: each player keeps the SAME position across
#              both quarters. Same for Q3+Q4. Between the halves (Q2→Q3) every
#              player on court for both halves MUST move to a different position.
#   3. EQUITY – across the season every player accumulates the same number of
#               turns in each position (±1). This drives who gets which slot.

def run_auto_allocation(force=False):
    dates = build_date_list()

    # Build schedule skeleton
    if 'master_schedule' not in st.session_state or st.session_state.master_schedule.empty:
        rows = []
        for i, d in enumerate(dates, 1):
            for q in range(1, 5):
                rows.append({'Week': i, 'Date': d, 'Quarter': f"Q{q}"})
        df = pd.DataFrame(rows)
        for col in all_slots:
            df[col] = None
        st.session_state.master_schedule = df

    df = st.session_state.master_schedule

    if force:
        for col in all_slots:
            df[col] = None

    avail = st.session_state.availability

    # Cumulative season position counts (equity tracking)
    pos_counts = {p: {pos: 0 for pos in positions} for p in all_players}
    off_counts  = {p: 0 for p in all_players}

    num_weeks = len(df) // 4

    for w in range(1, num_weeks + 1):
        d_str = df[df['Week'] == w]['Date'].iloc[0]
        today_players = [p for p in all_players if avail.at[d_str, p]]
        n_avail = len(today_players)
        n_off_per_q = max(0, n_avail - 7)   # typically 1 with 8 players
        base = (w - 1) * 4                  # row index of Q1

        # If not forcing and already filled, just tally counts and move on
        if not force and pd.notna(df.at[base, 'GS']):
            for q_idx in range(4):
                for pos in positions:
                    p = df.at[base + q_idx, pos]
                    if isinstance(p, str) and p in pos_counts:
                        pos_counts[p][pos] += 1
                off_p = df.at[base + q_idx, 'Off']
                if isinstance(off_p, str) and off_p in off_counts:
                    off_counts[off_p] += 1
            continue

        # ── STEP A: Who sits off each quarter? ───────────────────────────────
        # Spread offs across all 4 quarters. Never same player twice per game.
        # Among eligible players pick the one with fewest season offs.
        sat_off_game = set()
        off_schedule = []   # indexed Q0..Q3

        for q_idx in range(4):
            if n_off_per_q > 0:
                eligible = [p for p in today_players if p not in sat_off_game]
                if not eligible:
                    eligible = today_players   # safety
                # sort by season offs, secondary sort deterministic
                eligible.sort(key=lambda p: (off_counts[p], all_players.index(p)))
                off_p = eligible[0]
                off_schedule.append(off_p)
                sat_off_game.add(off_p)
                off_counts[off_p] += 1
            else:
                off_schedule.append(None)

        # ── STEP B: Pair-based position assignment ────────────────────────────
        # We compute TWO half-assignments: first half (Q1Q2), second half (Q3Q4).
        # Within each half every player gets the same position for both quarters.
        # Between halves each player present in both halves must change position.

        def greedy_assign(players_to_place, available_pos, forbidden=None):
            """
            Assign each player in players_to_place to one of available_pos.
            forbidden: dict {player: set_of_positions} they must NOT receive.
            Returns {player: position}.
            Uses an iterative best-pick (lowest equity score) with swap safety.
            """
            if forbidden is None:
                forbidden = {}
            assignment = {}
            remaining_players = list(players_to_place)
            remaining_pos = list(available_pos)

            # Repeatedly pick the (player, pos) pair with the lowest equity score
            # that respects the forbidden constraint.
            MAX_ITER = len(remaining_players) * len(remaining_pos) * 2 + 10
            itr = 0
            while remaining_players and remaining_pos:
                itr += 1
                if itr > MAX_ITER:
                    break  # safety
                best_score = None
                best_p = None
                best_pos = None
                for p in remaining_players:
                    forbidden_set = forbidden.get(p, set())
                    for pos in remaining_pos:
                        if pos in forbidden_set:
                            continue
                        s = pos_counts[p][pos]
                        if best_score is None or s < best_score:
                            best_score = s
                            best_p = p
                            best_pos = pos
                if best_p is None:
                    # All remaining players are forbidden from all remaining pos
                    # → relax forbidden for the first remaining player
                    best_p = remaining_players[0]
                    best_pos = min(remaining_pos, key=lambda pos: pos_counts[best_p][pos])
                assignment[best_p] = best_pos
                remaining_players.remove(best_p)
                remaining_pos.remove(best_pos)
            return assignment

        # First half
        on_h1 = [p for p in today_players if off_schedule[0] != p and off_schedule[1] != p]
        # Some players may be off in Q1 but on in Q2 (or vice versa) — handle edge case
        on_q1 = [p for p in today_players if off_schedule[0] != p]
        on_q2 = [p for p in today_players if off_schedule[1] != p]
        on_both_h1 = [p for p in today_players if p in on_q1 and p in on_q2]
        on_q1_only  = [p for p in on_q1 if p not in on_both_h1]
        on_q2_only  = [p for p in on_q2 if p not in on_both_h1]

        h1_pos_pool = list(positions)
        h1_assign = greedy_assign(on_both_h1, h1_pos_pool)

        remaining_after_h1 = [p for p in h1_pos_pool if p not in h1_assign.values()]
        q1_extra = greedy_assign(on_q1_only, remaining_after_h1)
        q2_extra = greedy_assign(on_q2_only, remaining_after_h1)

        q1_assign = {**h1_assign, **q1_extra}
        q2_assign = {**h1_assign, **q2_extra}

        # Second half – players who played in H1 must move to a different position
        on_q3 = [p for p in today_players if off_schedule[2] != p]
        on_q4 = [p for p in today_players if off_schedule[3] != p]
        on_both_h2 = [p for p in today_players if p in on_q3 and p in on_q4]
        on_q3_only  = [p for p in on_q3 if p not in on_both_h2]
        on_q4_only  = [p for p in on_q4 if p not in on_both_h2]

        # Build forbidden map: player → position they held in H1
        forbidden_h2 = {}
        for p in on_both_h2:
            h1_pos = h1_assign.get(p)
            if h1_pos:
                forbidden_h2[p] = {h1_pos}

        h2_pos_pool = list(positions)
        h2_assign = greedy_assign(on_both_h2, h2_pos_pool, forbidden=forbidden_h2)

        remaining_after_h2 = [p for p in h2_pos_pool if p not in h2_assign.values()]
        q3_extra = greedy_assign(on_q3_only, remaining_after_h2)
        q4_extra = greedy_assign(on_q4_only, remaining_after_h2)

        q3_assign = {**h2_assign, **q3_extra}
        q4_assign = {**h2_assign, **q4_extra}

        all_quarter_assigns = [q1_assign, q2_assign, q3_assign, q4_assign]

        # ── STEP C: Write to dataframe ────────────────────────────────────────
        for q_idx, assignment in enumerate(all_quarter_assigns):
            row_idx = base + q_idx
            off_p = off_schedule[q_idx]
            df.at[row_idx, 'Off'] = off_p if off_p else "N/A"

            for pos in positions:
                player = next((p for p, ap in assignment.items() if ap == pos), "N/A")
                df.at[row_idx, pos] = player
                if player in pos_counts and player != "N/A":
                    pos_counts[player][pos] += 1

    st.session_state.master_schedule = df


# --- 5. MANUAL EDIT HANDLER ---
def apply_manual_edit(week, q_idx, player_name, new_pos):
    """Swap so grid stays consistent – clean two-way swap."""
    df = st.session_state.master_schedule
    row_idx = (week - 1) * 4 + q_idx

    old_pos = next((c for c in all_slots if df.at[row_idx, c] == player_name), None)
    if old_pos == new_pos:
        return

    displaced = df.at[row_idx, new_pos] if new_pos in df.columns else None

    df.at[row_idx, new_pos] = player_name
    if old_pos:
        df.at[row_idx, old_pos] = displaced if isinstance(displaced, str) else "N/A"

    st.session_state.master_schedule = df
    update_drive()


# --- 6. INITIALIZATION ---
if 'availability' not in st.session_state:
    dates = build_date_list()
    st.session_state.availability = pd.DataFrame(True, index=dates, columns=all_players)

if 'master_schedule' not in st.session_state:
    loaded = load_saved()
    st.session_state.master_schedule = loaded if loaded is not None else pd.DataFrame()
    run_auto_allocation()

if 'round_selection' not in st.session_state:
    date_list = build_date_list()
    st.session_state.round_selection = get_default_week(date_list)


# --- 7. SIDEBAR ---
with st.sidebar:
    st.title("🏐 Netball Pro")
    page = st.radio("Menu", ["Rotation", "Availability", "Stats"])
    if st.button("🚨 Reset Rotation"):
        run_auto_allocation(force=True)
        update_drive()
        st.rerun()


# --- PAGE 1: ROTATION ---
if page == "Rotation":
    rd = st.session_state.get('round_selection', 1)
    date_list = build_date_list()
    st.markdown(f"### {date_list[rd-1]}")
    st.slider("Match Week", 1, len(date_list), key="round_selection", label_visibility="collapsed")

    view_df = st.session_state.master_schedule[
        st.session_state.master_schedule['Week'] == rd
    ].reset_index(drop=True)

    # Header row
    header_cols = st.columns([2, 1, 1, 1, 1])
    header_cols[0].markdown("<small><b>NAME</b></small>", unsafe_allow_html=True)
    for i, q in enumerate(["Q1", "Q2", "Q3", "Q4"]):
        header_cols[i+1].markdown(f"<small><b>{q}</b></small>", unsafe_allow_html=True)

    st.markdown("<hr style='margin:2px 0'>", unsafe_allow_html=True)

    # One row per player
    for p in all_players:
        row_cols = st.columns([2, 1, 1, 1, 1])
        row_cols[0].markdown(
            f"<div style='font-size:10px; font-weight:bold; padding-top:8px;'>{p}</div>",
            unsafe_allow_html=True
        )
        for q_idx in range(4):
            q_row = view_df.iloc[q_idx]
            current_pos = next((c for c in all_slots if q_row[c] == p), "Off")
            color = pos_colors.get(current_pos, "#F5F5F5")

            new_pos = row_cols[q_idx + 1].selectbox(
                label=f"{p}_Q{q_idx+1}",
                options=all_slots,
                index=all_slots.index(current_pos),
                key=f"sel_{rd}_{p}_{q_idx}",
                label_visibility="collapsed"
            )

            # Colour strip under each selectbox to mimic original colour cells
            row_cols[q_idx + 1].markdown(
                f"<div style='background:{color}; height:5px; margin-top:-10px; border-radius:2px;'></div>",
                unsafe_allow_html=True
            )

            if new_pos != current_pos:
                apply_manual_edit(rd, q_idx, p, new_pos)
                st.rerun()

    st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)


# --- PAGE 2: AVAILABILITY ---
elif page == "Availability":
    st.markdown("### Availability Planner")
    u = st.data_editor(st.session_state.availability, use_container_width=True)
    if st.button("Apply & Re-Balance"):
        st.session_state.availability = u
        run_auto_allocation(force=True)
        update_drive()
        st.rerun()


# --- PAGE 3: STATS ---
else:
    st.markdown("### Season Statistics")
    melted = st.session_state.master_schedule.melt(
        id_vars=['Week'], value_vars=positions, value_name='Player'
    )
    st.bar_chart(melted['Player'].value_counts())
    st.markdown("#### Position Breakdown by Player")
    st.dataframe(pd.crosstab(melted['Player'], melted.variable), use_container_width=True)
