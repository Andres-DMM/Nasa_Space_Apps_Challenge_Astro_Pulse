import asyncio, csv, json, os, re, statistics, threading, time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
import serial
import serial.tools.list_ports
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

# ============================================================
# CONFIGURATION
# ============================================================
BAUD_RATE = 115200
RECONNECT_INTERVAL = 2
SILENCE_SECONDS = 5
OUTPUT_DIR = Path(__file__).resolve().parent / "monitor_sessions"
OUTPUT_DIR.mkdir(exist_ok=True)

# ============================================================
# SHARED STATE
# ============================================================
lock = threading.RLock()
stop_event = threading.Event()
serial_thread = None

active_serial_port = os.getenv("SERIAL_PORT", "COM8")
serial_connected = False
serial_error = "Waiting for serial connection..."

stream_active = False
current_mode = None
latest_ambient = None
latest_temperature = None
latest_bpm = None
last_device_activity = None
current_readings = []
current_started_at = None
latest_summary = None
completed_session_ids = []

existing_ids = [int(m.group(1)) for f in OUTPUT_DIR.glob("monitor_session_*.csv") if (m := re.search(r"monitor_session_(\d+)\.csv", f.name))]
session_counter = max(existing_ids, default=0)

# ============================================================
# DASHBOARD HTML (WITH LOGS, CHARTS, & CHART.JS)
# ============================================================
HTML_PAGE = r'''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ESP32 Vital Monitor & Analytics</title>
<!-- Include Chart.js for Tendency Charts -->
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
:root { color-scheme: dark; --bg: #09111f; --panel: #121f32; --line: #263b55; --muted: #9aacc2; --cyan: #42d9e8; --green: #59d6a0; --red: #ff687d; --gold: #ffd166; }
* { box-sizing: border-box; }
body { margin: 0; padding: 26px; background: var(--bg); color: #edf5ff; font: 15px Arial, sans-serif; }
main { max-width: 1100px; margin: auto; }
header { display: flex; justify-content: space-between; align-items: center; gap: 15px; flex-wrap: wrap; margin-bottom: 20px; }
h1 { margin: 0 0 6px; font-size: clamp(25px, 4vw, 36px); }
.muted { color: var(--muted); }
.pill { padding: 9px 13px; border: 1px solid var(--line); border-radius: 30px; }
.grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 15px; padding: 19px; min-width: 0; }
.wide { grid-column: 1 / -1; }
.label { font-size: 12px; letter-spacing: 1px; text-transform: uppercase; color: var(--muted); }
.big { font-size: clamp(34px, 6vw, 50px); font-weight: bold; margin: 12px 0; }
.good { color: var(--green); }
.bad { color: var(--red); }
.stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-top: 13px; }
.stat { background: #0b1728; padding: 13px; border-radius: 10px; }
.stat b { display: block; font-size: 22px; margin-top: 8px; }
.hint { margin-top: 14px; padding: 13px; background: #1b2b40; border-radius: 10px; line-height: 1.5; }
.tablewrap { overflow-x: auto; max-height: 350px; }
table { width: 100%; border-collapse: collapse; margin-top: 12px; }
td, th { text-align: left; padding: 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { color: var(--muted); background: #0b1728; position: sticky; top: 0; }
button, .download, select { display: inline-block; background: #174c68; color: white; border: 1px solid var(--cyan); border-radius: 8px; padding: 10px 16px; margin-top: 12px; cursor: pointer; text-decoration: none; font-size: 14px; }
button:hover, .download:hover { background: #226a87; }
select { background: #0b1728; padding: 9px 12px; }
.modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); z-index: 1000; overflow-y: auto; padding: 20px; }
.modal-content { background: var(--panel); border: 1px solid var(--cyan); border-radius: 15px; max-width: 1000px; margin: 40px auto; padding: 25px; position: relative; }
.close-btn { background: var(--red); float: right; padding: 6px 12px; margin-top: 0; }
.note { font-size: 12px; line-height: 1.5; color: var(--muted); margin-top: 18px; }
@media(max-width: 700px) {
    body { padding: 15px; }
    .grid { grid-template-columns: 1fr; }
    .wide { grid-column: auto; }
    .stats { grid-template-columns: repeat(2, 1fr); }
}
</style>
</head>
<body>
<main>
<header>
<div>
<h1>ESP32 Vital Monitor</h1>
<div class="muted">ESP32 · MLX90614 · MAX30102 · USB Serial</div>
</div>
<div style="display: flex; gap: 10px; align-items: center;">
<button onclick="openLogsModal()" style="margin-top:0; background: #1b4034; border-color: var(--green);">📂 View Logs & Charts</button>
<div id="connection" class="pill">Connecting...</div>
</div>
</header>

<section class="grid">
<!-- TEMPERATURE -->
<div class="card">
<div class="label">Object Temperature</div>
<div class="big"><span id="temp">--</span> °C</div>
<div class="muted" id="ambient">Ambient: -- °C</div>
</div>

<!-- HEART RATE -->
<div class="card">
<div class="label">Heart Rate</div>
<div class="big"><span id="bpm">--</span> <span style="font-size:20px">BPM</span></div>
<div class="muted" id="finger">Waiting for finger</div>
</div>

<!-- MODE -->
<div class="card">
<div class="label">Active Mode</div>
<div class="big" id="mode">IDLE</div>
<div class="muted" id="streamInfo">Press a hardware button to start</div>
<div class="muted" id="count">0 readings</div>
</div>

<!-- SESSION SUMMARY -->
<div class="card wide">
<div class="label">Latest Completed Session Statistics & Status</div>
<div class="stats">
<div class="stat">Mean<b id="mean">--</b></div>
<div class="stat">Median<b id="median">--</b></div>
<div class="stat">Average<b id="average">--</b></div>
<div class="stat">Minimum<b id="minimum">--</b></div>
<div class="stat">Maximum<b id="maximum">--</b></div>
</div>
<div class="hint" id="summaryInfo">No completed session yet.</div>
<div class="hint" id="summaryStatus" style="color: var(--cyan); font-weight: bold; margin-top: 10px;">Status: Waiting for session data...</div>
<a id="download" class="download" style="display:none">Download CSV</a>
</div>

<!-- SERIAL PORT SELECTOR -->
<div class="card wide">
<div class="label">Serial Connection & Port Selection</div>
<div id="serialInfo" class="muted" style="margin-top: 8px;">Checking COM port...</div>
<div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
<select id="portSelect"></select>
<button onclick="changePort()" style="margin-top: 12px;">Switch & Connect Port</button>
<button onclick="loadPorts()" style="margin-top: 12px; background: #1b2b40;">Refresh Ports</button>
</div>
</div>

<!-- READINGS -->
<div class="card wide">
<div class="label">Current Session Readings</div>
<div class="tablewrap">
<table>
<thead>
<tr><th>Time</th><th>Ambient °C</th><th>Object °C</th><th>BPM</th></tr>
</thead>
<tbody id="readings">
<tr><td colspan="4" class="muted">No readings yet</td></tr>
</tbody>
</table>
</div>
</div>
</section>

<p class="note">
The MAX30102 BPM output is an experimental hobby measurement, not a medical reading.<br>
The MLX90614 measures surface temperature (fever threshold adjusted to 41°C for sensor offset). Do not use this project for medical decisions.
</p>
</main>

<!-- LOGS & CHARTS MODAL -->
<div id="logsModal" class="modal">
<div class="modal-content">
<button class="close-btn" onclick="closeLogsModal()">Close ✕</button>
<h2>All Stored Sessions & Tendency Charts</h2>
<p class="muted">Review historical logs, status/results, and trend tendencies across all recorded sessions.</p>

<hr style="border-color: var(--line); margin: 20px 0;">

<h3>📈 Temperature Session Tendencies</h3>
<div style="background: #0b1728; padding: 15px; border-radius: 10px; margin-bottom: 25px;">
<canvas id="tempChart" height="100"></canvas>
</div>

<h3>📈 Heart Rate (BPM) Session Tendencies</h3>
<div style="background: #0b1728; padding: 15px; border-radius: 10px; margin-bottom: 25px;">
<canvas id="bpmChart" height="100"></canvas>
</div>

<h3>📋 Complete Session Logs</h3>
<div class="tablewrap" style="max-height: 400px;">
<table>
<thead>
<tr><th>Session ID</th><th>Mode</th><th>Started</th><th>Ended</th><th>Samples</th><th>Avg</th><th>Min</th><th>Max</th><th>Status / Result</th><th>Action</th></tr>
</thead>
<tbody id="allSessionsTable">
<tr><td colspan="10" class="muted">Loading sessions...</td></tr>
</tbody>
</table>
</div>
</div>
</div>

<script>
const $ = id => document.getElementById(id);
let lastRowsSignature = "";
let tempChartInstance = null;
let bpmChartInstance = null;

function showSummary(s) {
    if (!s) return;
    const unit = s.mode === "BPM" ? " BPM" : " °C";
    for (const k of ["mean", "median", "average", "minimum", "maximum"]) {
        $(k).textContent = Number(s[k]).toFixed(1) + unit;
    }
    $("summaryInfo").textContent = `${s.mode} session #${s.session_id} · ${s.sample_count} valid readings · ${s.started_at} → ${s.ended_at}`;
    $("summaryStatus").textContent = "Status Alert: " + (s.status_message || "Normal Range");
    $("download").href = `/api/download/${s.session_id}`;
    $("download").download = `monitor_session_${s.session_id}.csv`;
    $("download").style.display = "inline-block";
}

async function loadPorts() {
    try {
        const res = await fetch("/api/ports");
        const data = await res.json();
        const select = $("portSelect");
        select.innerHTML = "";
        data.ports.forEach(p => {
            const opt = document.createElement("option");
            opt.value = p;
            opt.textContent = p;
            if (p === data.current) opt.selected = true;
            select.appendChild(opt);
        });
    } catch(e) {}
}

async function changePort() {
    const port = $("portSelect").value;
    const res = await fetch("/api/set-port", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({port})
    });
    if (res.ok) refresh();
}

async function openLogsModal() {
    $("logsModal").style.display = "block";
    await loadAllSessionsData();
}

function closeLogsModal() {
    $("logsModal").style.display = "none";
}

async function loadAllSessionsData() {
    try {
        const res = await fetch("/api/sessions");
        const data = await res.json();
        const sessions = data.sessions || [];

        const tbody = $("allSessionsTable");
        tbody.innerHTML = "";
        if (!sessions.length) {
            tbody.innerHTML = '<tr><td colspan="10" class="muted">No recorded sessions found in folder</td></tr>';
            return;
        }

        let tempSessions = [];
        let bpmSessions = [];

        sessions.forEach(s => {
            if (s.mode === "TEMP") tempSessions.push(s);
            else if (s.mode === "BPM") bpmSessions.push(s);

            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>#${s.session_id}</td>
                <td><b>${s.mode}</b></td>
                <td>${s.started_at}</td>
                <td>${s.ended_at}</td>
                <td>${s.sample_count}</td>
                <td>${s.average}</td>
                <td>${s.minimum}</td>
                <td>${s.maximum}</td>
                <td style="color: ${s.status_message.includes('Normal') ? 'var(--green)' : 'var(--gold)'};">${s.status_message}</td>
                <td><a class="download" style="padding: 4px 10px; margin: 0; font-size: 12px;" href="/api/download/${s.session_id}" download>CSV</a></td>
            `;
            tbody.appendChild(tr);
        });

        renderCharts(tempSessions, bpmSessions);
    } catch (e) {
        $("allSessionsTable").innerHTML = `<tr><td colspan="10" class="bad">Error loading sessions: ${e.message}</td></tr>`;
    }
}

function renderCharts(tempData, bpmData) {
    // Temperature Chart
    const tempCtx = $("tempChart").getContext("2d");
    if (tempChartInstance) tempChartInstance.destroy();
    
    tempChartInstance = new Chart(tempCtx, {
        type: 'line',
        data: {
            labels: tempData.map(s => `#${s.session_id} (${s.started_at})`),
            datasets: [
                { label: 'Avg Temp (°C)', data: tempData.map(s => s.average), borderColor: '#42d9e8', backgroundColor: 'rgba(66,217,232,0.1)', fill: true, tension: 0.2 },
                { label: 'Max Temp (°C)', data: tempData.map(s => s.maximum), borderColor: '#ff687d', borderDash: [5,5], tension: 0.2 }
            ]
        },
        options: { responsive: true, plugins: { legend: { labels: { color: '#edf5ff' } } }, scales: { x: { ticks: { color: '#9aacc2' } }, y: { ticks: { color: '#9aacc2' } } } }
    });

    // BPM Chart
    const bpmCtx = $("bpmChart").getContext("2d");
    if (bpmChartInstance) bpmChartInstance.destroy();

    bpmChartInstance = new Chart(bpmCtx, {
        type: 'line',
        data: {
            labels: bpmData.map(s => `#${s.session_id} (${s.started_at})`),
            datasets: [
                { label: 'Avg BPM', data: bpmData.map(s => s.average), borderColor: '#59d6a0', backgroundColor: 'rgba(89,214,160,0.1)', fill: true, tension: 0.2 },
                { label: 'Max BPM', data: bpmData.map(s => s.maximum), borderColor: '#ffd166', borderDash: [5,5], tension: 0.2 }
            ]
        },
        options: { responsive: true, plugins: { legend: { labels: { color: '#edf5ff' } } }, scales: { x: { ticks: { color: '#9aacc2' } }, y: { ticks: { color: '#9aacc2' } } } }
    });
}

async function refresh() {
    try {
        const response = await fetch("/api/status", {cache: "no-store"});
        if (!response.ok) throw Error("HTTP " + response.status);
        const d = await response.json();

        $("connection").textContent = d.serial_connected ? "Backend + COM connected" : "Backend online · COM disconnected";
        $("connection").style.borderColor = d.serial_connected ? "var(--green)" : "var(--red)";
        $("serialInfo").textContent = d.serial_connected ? `Connected to ${d.serial_port} at ${d.baud_rate} baud.` : `Port ${d.serial_port}: ${d.serial_error}. Close Arduino Serial Monitor/Plotter.`;

        $("mode").textContent = d.stream_active ? (d.current_mode || "ACTIVE") : "IDLE";
        $("mode").className = "big " + (d.stream_active ? "good" : "bad");
        $("streamInfo").textContent = d.stream_active ? "Receiving " + d.current_mode + " measurements" : "Waiting for a hardware button";
        $("count").textContent = d.sample_count + " valid readings in current session";

        if (d.latest_temperature !== null) $("temp").textContent = d.latest_temperature;
        if (d.latest_ambient !== null) $("ambient").textContent = "Ambient: " + d.latest_ambient + " °C";

        $("bpm").textContent = d.latest_bpm === null ? "--" : d.latest_bpm;
        $("finger").textContent = d.latest_bpm === null ? "No finger / no recent pulse" : "Pulse detected";

        if (d.latest_summary) showSummary(d.latest_summary);

        const signature = JSON.stringify(d.current_readings);
        if (signature !== lastRowsSignature) {
            lastRowsSignature = signature;
            const body = $("readings");
            body.innerHTML = "";
            if (!d.current_readings.length) {
                body.innerHTML = '<tr><td colspan="4" class="muted">No readings yet</td></tr>';
            } else {
                for (const x of d.current_readings.slice(-15).reverse()) {
                    const tr = document.createElement("tr");
                    for (const v of [x.timestamp, x.ambient_c ?? "—", x.object_c ?? "—", x.bpm ?? "—"]) {
                        const td = document.createElement("td");
                        td.textContent = v;
                        tr.appendChild(td);
                    }
                    body.appendChild(tr);
                }
            }
        }
    } catch (e) {
        $("connection").textContent = "Backend disconnected";
        $("connection").style.borderColor = "var(--red)";
        $("serialInfo").textContent = e.message;
    }
}

loadPorts();
refresh();
setInterval(refresh, 500);
</script>
</body>
</html>
'''

# ============================================================
# SESSION STATISTICS & THRESHOLD / MEDICAL STATEMENTS
# ============================================================
def calculate_summary(readings, mode, started, ended):
    if not readings: return None
    values = [float(row["object_c"] if mode == "TEMP" else row["bpm"]) for row in readings]
    avg = statistics.mean(values)

    # Threshold and condition evaluation (Fever threshold adjusted to >= 41.0 °C for sensor displacement)
    status_message = "Normal Range"
    if mode == "BPM":
        if avg < 60:
            status_message = "Bradycardia Range (Avg < 60 BPM)"
        elif avg > 100:
            status_message = "Tachycardia Range (Avg > 100 BPM)"
        else:
            status_message = "Normal Heart Rate Range"
    elif mode == "TEMP":
        if avg >= 41.0:
            status_message = "Elevated Temperature / Fever Range (>= 41°C)"
        elif avg < 35.0:
            status_message = "Low Temperature / Hypothermia Warning (< 35°C)"
        else:
            status_message = "Normal Surface Temperature Range"

    return {
        "session_id": session_counter,
        "mode": mode,
        "started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "ended_at": ended.strftime("%Y-%m-%d %H:%M:%S"),
        "sample_count": len(values),
        "mean": round(avg, 2),
        "median": round(statistics.median(values), 2),
        "average": round(avg, 2),
        "minimum": round(min(values), 2),
        "maximum": round(max(values), 2),
        "status_message": status_message
    }

def save_session_csv(readings, summary):
    path = OUTPUT_DIR / f"monitor_session_{summary['session_id']}.csv"
    headers = ["Timestamp", "Mode", "Ambient (°C)", "Object (°C)", "BPM", "Mean", "Median", "Average", "Minimum", "Maximum", "Status"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for item in readings:
            writer.writerow([item.get("timestamp", ""), summary["mode"], item.get("ambient_c", ""), item.get("object_c", ""), item.get("bpm", ""), "", "", "", "", "", ""])
        writer.writerow(["SUMMARY", summary["mode"], "", "", "", summary["mean"], summary["median"], summary["average"], summary["minimum"], summary["maximum"], summary["status_message"]])
    return path

def finish_session(ser=None):
    global current_readings, current_started_at, latest_summary, session_counter, stream_active, last_device_activity
    if not current_readings:
        stream_active = False
        current_started_at = None
        last_device_activity = None
        return
    ended = datetime.now()
    session_counter += 1
    mode = current_mode or "TEMP"
    summary = calculate_summary(current_readings, mode, current_started_at or ended, ended)
    summary["session_id"] = session_counter
    save_session_csv(current_readings, summary)
    latest_summary = summary
    completed_session_ids.append(session_counter)
    
    current_readings, current_started_at, last_device_activity, stream_active = [], None, None, False

    if ser is not None:
        try:
            message = "SUMMARY," + json.dumps({"summary": summary}, separators=(",", ":")) + "\n"
            ser.write(message.encode("utf-8"))
            ser.flush()
        except (serial.SerialException, OSError):
            pass

# ============================================================
# SERIAL PROTOCOL
# ============================================================
def handle_line(line, ser=None):
    global stream_active, current_mode, latest_ambient, latest_temperature, latest_bpm, current_started_at, last_device_activity
    parts = line.strip().split(",")
    if not parts: return

    if len(parts) == 3 and parts[0] == "STREAM" and parts[2].upper() == "ON":
        new_mode = parts[1].upper()
        if new_mode not in ("TEMP", "BPM"): return
        if current_readings and (new_mode != current_mode or not stream_active):
            finish_session(ser)
        current_mode = new_mode
        stream_active = True
        last_device_activity = time.monotonic()
        if new_mode == "BPM": latest_bpm = None
        return

    if len(parts) == 2 and parts[0] == "STREAM" and parts[1].upper() == "OFF":
        stream_active = False
        finish_session(ser)
        return

    if len(parts) == 3 and parts[0] == "TEMP":
        if current_mode != "TEMP" or not stream_active: return
        try:
            ambient, obj = int(parts[1]), int(parts[2])
        except ValueError:
            return
        if not (-40 <= ambient <= 150 and -40 <= obj <= 150): return
        now = datetime.now()
        if not current_readings: current_started_at = now
        current_readings.append({"timestamp": now.strftime("%Y-%m-%d %H:%M:%S"), "ambient_c": ambient, "object_c": obj, "bpm": None})
        latest_ambient, latest_temperature, last_device_activity = ambient, obj, time.monotonic()
        return

    if len(parts) == 2 and parts[0] == "BPM":
        if current_mode != "BPM" or not stream_active: return
        try: bpm = float(parts[1])
        except ValueError: return
        last_device_activity = time.monotonic()
        if bpm <= 0:
            latest_bpm = None
            return
        if not (20 <= bpm <= 250): return
        now = datetime.now()
        if not current_readings: current_started_at = now
        current_readings.append({"timestamp": now.strftime("%Y-%m-%d %H:%M:%S"), "ambient_c": None, "object_c": None, "bpm": round(bpm, 1)})
        latest_bpm = round(bpm, 1)
        return

# ============================================================
# SERIAL WORKER
# ============================================================
def serial_worker():
    global serial_connected, serial_error, last_device_activity
    while not stop_event.is_set():
        with lock:
            target_port = active_serial_port
        try:
            with serial.Serial(target_port, BAUD_RATE, timeout=0.2, write_timeout=1) as ser:
                with lock:
                    serial_connected = True
                    serial_error = ""
                    last_device_activity = time.monotonic()
                time.sleep(2)
                
                while not stop_event.is_set():
                    with lock:
                        if active_serial_port != target_port:
                            break
                    
                    raw = ser.readline()
                    with lock:
                        if raw:
                            line = raw.decode("utf-8", errors="replace").strip()
                            if line:
                                last_device_activity = time.monotonic()
                                handle_line(line, ser)
                        
                        if current_readings and last_device_activity is not None:
                            if (time.monotonic() - last_device_activity) >= SILENCE_SECONDS:
                                finish_session(ser)
        except (serial.SerialException, OSError) as exc:
            with lock:
                serial_connected = False
                serial_error = str(exc)
                if current_readings: finish_session()
            stop_event.wait(RECONNECT_INTERVAL)
        except Exception as exc:
            with lock:
                serial_connected = False
                serial_error = f"Serial worker error: {exc}"
                if current_readings: finish_session()
            stop_event.wait(RECONNECT_INTERVAL)
    with lock:
        serial_connected = False

# ============================================================
# FASTAPI APPLICATION & LIFESPAN
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global serial_thread
    stop_event.clear()
    serial_thread = threading.Thread(target=serial_worker, name="esp32-serial", daemon=True)
    serial_thread.start()
    yield
    stop_event.set()
    if serial_thread and serial_thread.is_alive():
        await asyncio.to_thread(serial_thread.join, 3)

app = FastAPI(title="ESP32 Vital Monitor", lifespan=lifespan)

class PortRequest(BaseModel):
    port: str

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTML_PAGE

@app.get("/api/ports")
async def get_ports():
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return {"ports": ports, "current": active_serial_port}

@app.post("/api/set-port")
async def set_port(req: PortRequest):
    global active_serial_port
    with lock:
        active_serial_port = req.port
        if current_readings:
            finish_session()
    return {"status": "success", "port": active_serial_port}

@app.get("/api/status")
async def status():
    with lock:
        return {
            "serial_connected": serial_connected,
            "serial_port": active_serial_port,
            "serial_error": serial_error,
            "baud_rate": BAUD_RATE,
            "stream_active": stream_active,
            "current_mode": current_mode,
            "latest_ambient": latest_ambient,
            "latest_temperature": latest_temperature,
            "latest_bpm": latest_bpm,
            "sample_count": len(current_readings),
            "current_readings": current_readings[-15:],
            "latest_summary": latest_summary,
            "completed_session_ids": completed_session_ids[:]
        }

@app.get("/api/sessions")
async def get_all_sessions():
    """Scans OUTPUT_DIR for all saved session CSVs, parses summary info, and returns them for logs and charts."""
    sessions = []
    for f in sorted(OUTPUT_DIR.glob("monitor_session_*.csv")):
        try:
            session_id_match = re.search(r"monitor_session_(\d+)\.csv", f.name)
            if not session_id_match: continue
            s_id = int(session_id_match.group(1))

            mode = "TEMP"
            started_at = ""
            ended_at = ""
            sample_count = 0
            avg = 0.0
            min_val = 0.0
            max_val = 0.0
            status_msg = "Normal Range"

            with f.open("r", encoding="utf-8-sig") as csv_file:
                reader = csv.reader(csv_file)
                rows = list(reader)
                if len(rows) > 1:
                    # Look at data rows and summary row
                    data_rows = [r for r in rows[1:] if r and r[0] != "SUMMARY"]
                    if data_rows:
                        started_at = data_rows[0][0]
                        ended_at = data_rows[-1][0]
                        sample_count = len(data_rows)
                    
                    summary_row = next((r for r in rows if r and r[0] == "SUMMARY"), None)
                    if summary_row:
                        mode = summary_row[1] if len(summary_row) > 1 else "TEMP"
                        avg = float(summary_row[7]) if len(summary_row) > 7 and summary_row[7] else 0.0
                        min_val = float(summary_row[8]) if len(summary_row) > 8 and summary_row[8] else 0.0
                        max_val = float(summary_row[9]) if len(summary_row) > 9 and summary_row[9] else 0.0
                        status_msg = summary_row[10] if len(summary_row) > 10 and summary_row[10] else "Normal Range"

            sessions.append({
                "session_id": s_id,
                "mode": mode,
                "started_at": started_at,
                "ended_at": ended_at,
                "sample_count": sample_count,
                "average": avg,
                "minimum": min_val,
                "maximum": max_val,
                "status_message": status_msg
            })
        except Exception:
            continue
    return {"sessions": sessions}

@app.get("/api/download/{session_id}")
async def download(session_id: int):
    path = OUTPUT_DIR / f"monitor_session_{session_id}.csv"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Session CSV not found")
    return FileResponse(path, media_type="text/csv", filename=path.name)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)