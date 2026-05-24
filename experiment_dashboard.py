#python -m streamlit run c:/Users/VIRTUALIAI/Documents/HiddenStates/experiment_dashboard.py

import subprocess
import time
from pathlib import Path
import pandas as pd
import streamlit as st

import queue
import threading

if 'bridge_logs' not in st.session_state: st.session_state.bridge_logs = []
if 'log_queue' not in st.session_state: st.session_state.log_queue = queue.Queue()
if 'reader_thread' not in st.session_state: st.session_state.reader_thread = None

def _enqueue_output(proc, q):
    """Runs in a background thread — reads lines and puts them in the queue."""
    try:
        for line in iter(proc.stdout.readline, ''):
            if line:
                q.put(line.strip())
    except:
        pass

# --- DYNAMIC CONFIGURATION (Replaced hardcoded C:/ paths) ---
# This finds the exact folder this dashboard script is sitting inside
BASE_DIR = Path(__file__).resolve().parent

# Maps to your repository's relative layouts
UNITY_EXE = BASE_DIR / "RustBucket_game" / "Environment.exe"
DATA_ROOT = BASE_DIR / "DadosTask"
BRIDGE_SCRIPT = BASE_DIR / "bridge.py"

st.set_page_config(page_title="Rustbucket Command Center", layout="wide")

# Persistent process tracking
if 'unity_proc' not in st.session_state: st.session_state.unity_proc = None
if 'bridge_proc' not in st.session_state: st.session_state.bridge_proc = None
if 'laptop_ip' not in st.session_state: st.session_state.laptop_ip = "192.168.1.173"

# --- MAIN LAYOUT ---
left_col, right_col = st.columns([1, 2], gap="large")

# ---------------------------------------------------------
# LEFT COLUMN: CONTROLS
# ---------------------------------------------------------
with left_col:
    st.header("Controls")
    
    # 1. Bridge Controls with Status Feedback
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Start Bridge", width="stretch"):
            if st.session_state.bridge_proc is None:
                try:
                    st.session_state.bridge_proc = subprocess.Popen(
                        ["python", "-u", str(BRIDGE_SCRIPT)],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1
                    )
                    # Start background reader thread
                    st.session_state.log_queue = queue.Queue()
                    t = threading.Thread(
                        target=_enqueue_output,
                        args=(st.session_state.bridge_proc, st.session_state.log_queue),
                        daemon=True
                    )
                    t.start()
                    st.session_state.reader_thread = t
                    st.toast("Bridge Startup Initiated!")
                except Exception as e:
                    st.error(f"Launch Failed: {e}")
    
    with c2:
        if st.button("Stop Bridge", width="stretch"):
            if st.session_state.bridge_proc:
                st.session_state.bridge_proc.terminate()
                st.session_state.bridge_proc = None
                st.toast("Bridge Process Killed")

    # Visual Status Indicator
    if st.session_state.bridge_proc:
        if st.session_state.bridge_proc.poll() is None:
            st.success("Bridge Status: RUNNING")
        else:
            st.error("Bridge Status: CRASHED/STOPPED")
            st.session_state.bridge_proc = None
    else:
        st.info("Bridge Status: IDLE")

    # 2. Pupil Labs Reminder
    st.info("**Please press record on PL**")

    # 3. Player Name
    participant_name = st.text_input("Enter player name", value="TEST_USER")

    # 4. Unity Controls (Side by Side)
    u1, u2 = st.columns(2)
    with u1:
        if st.button("Launch UNITY", type="primary", width="stretch"):
            config_file = DATA_ROOT / "next_session_config.txt"
            DATA_ROOT.mkdir(parents=True, exist_ok=True)
            with open(config_file, "w") as f:
                f.write(f"{participant_name}\n{st.session_state.laptop_ip}")
            
            # Checks if the file actually exists right before firing up to avoid bad breaks
            if UNITY_EXE.exists():
                st.session_state.unity_proc = subprocess.Popen([str(UNITY_EXE)])
                st.toast("Unity Executable Fired Up!")
            else:
                st.error(f"Could not find Unity executable at: {UNITY_EXE}")
    with u2:
        if st.button("Quit UNITY", width="stretch"):
            if st.session_state.unity_proc:
                st.session_state.unity_proc.kill()
                st.session_state.unity_proc = None
                st.toast("Unity Process Terminated")

# ---------------------------------------------------------
# RIGHT COLUMN: MONITORS
# ---------------------------------------------------------
with right_col:
    st.header("LIVE streaming information")
    error_container = st.container(border=True)

    with error_container:
        if st.session_state.bridge_proc:
            # Drain everything the thread has collected since last rerun
            while not st.session_state.log_queue.empty():
                try:
                    line = st.session_state.log_queue.get_nowait()
                    st.session_state.bridge_logs.append(line)
                except:
                    break

            # Keep last 50 lines
            st.session_state.bridge_logs = st.session_state.bridge_logs[-50:]

            if st.session_state.bridge_logs:
                st.code("\n".join(st.session_state.bridge_logs))
            else:
                st.write("Waiting for bridge output...")
        else:
            st.session_state.bridge_logs = []
            st.write("Bridge offline. Start the bridge to see logs.")

    # --- AUTO REFRESH ---
    time.sleep(2)
    st.rerun()