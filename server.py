"""
=============================================================
  Remote File Manager - Server (เครื่องหลัก)
  รันบนเครื่องหลักเพื่อดูและจัดการไฟล์เครื่องลูกผ่านเว็บ
=============================================================
"""

import os
import sys
import json
import time
import uuid
import base64
import hashlib
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template_string, request, send_file, jsonify, session
from flask_socketio import SocketIO, emit, join_room, leave_room

# ─── CONFIG ───────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-to-a-strong-secret-key")
AGENT_SECRET = os.environ.get("AGENT_SECRET", "my-agent-secret-2024")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "5000"))
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "./received_files")
MAX_CHUNK_SIZE = 1024 * 1024  # 1MB chunks for file transfer
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "500"))  # ขนาดไฟล์อัปโหลดสูงสุด (MB)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ─── LOGGING ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ─── APP ──────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = SECRET_KEY

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    max_http_buffer_size=int(MAX_UPLOAD_MB * 1024 * 1024 * 1.5),  # base64 (+33%) + framing headroom
    ping_timeout=30,
    ping_interval=10,
    async_mode="threading"
)

# ─── STATE ────────────────────────────────────────────────
agents = {}           # sid -> agent info
pending_requests = {} # request_id -> {event, data, ...}


# ═══════════════════════════════════════════════════════════
#  AGENT (เครื่องลูก) CONNECTION HANDLING
# ═══════════════════════════════════════════════════════════

@socketio.on("agent_register")
def handle_agent_register(data):
    """เครื่องลูกลงทะเบียนเข้ามา"""
    if data.get("secret") != AGENT_SECRET:
        logger.warning(f"Agent rejected - wrong secret from {request.sid}")
        emit("auth_failed", {"message": "Invalid secret key"})
        return

    agent_id = data.get("agent_id", f"agent-{len(agents)+1}")
    agents[request.sid] = {
        "agent_id": agent_id,
        "hostname": data.get("hostname", "unknown"),
        "os_info": data.get("os_info", "unknown"),
        "ip": data.get("ip", "unknown"),
        "connected_at": datetime.now().isoformat(),
        "sid": request.sid,
        "allowed_paths": data.get("allowed_paths", []),
    }
    join_room(f"agent_{agent_id}")
    logger.info(f"✅ Agent registered: {agent_id} ({data.get('hostname')}) - SID: {request.sid}")
    emit("registered", {"status": "ok", "agent_id": agent_id})
    # แจ้ง web UI ว่ามีเครื่องลูกใหม่
    socketio.emit("agents_updated", get_agents_list(), room="web_clients")


@socketio.on("disconnect")
def handle_disconnect():
    if request.sid in agents:
        agent = agents.pop(request.sid)
        logger.info(f"❌ Agent disconnected: {agent['agent_id']} ({agent['hostname']})")
        socketio.emit("agents_updated", get_agents_list(), room="web_clients")


@socketio.on("agent_response")
def handle_agent_response(data):
    """รับผลลัพธ์จากเครื่องลูก"""
    req_id = data.get("request_id")
    if req_id in pending_requests:
        pending_requests[req_id]["response"] = data
        pending_requests[req_id]["completed"] = True
        # ส่งผลลัพธ์ไปยัง web client ที่ร้องขอ
        web_sid = pending_requests[req_id].get("web_sid")
        if web_sid:
            socketio.emit("response_" + req_id, data, room=web_sid)


@socketio.on("file_chunk")
def handle_file_chunk(data):
    """รับ chunk ของไฟล์จากเครื่องลูก"""
    req_id = data.get("request_id")
    if req_id in pending_requests:
        web_sid = pending_requests[req_id].get("web_sid")
        if web_sid:
            socketio.emit("file_chunk_" + req_id, data, room=web_sid)


# ═══════════════════════════════════════════════════════════
#  WEB CLIENT (ผู้ใช้) CONNECTION HANDLING
# ═══════════════════════════════════════════════════════════

@socketio.on("web_register")
def handle_web_register(data):
    """Web client ลงทะเบียน"""
    join_room("web_clients")
    join_room(request.sid)
    emit("agents_updated", get_agents_list())
    logger.info(f"🌐 Web client connected: {request.sid}")


@socketio.on("request_list_dir")
def handle_list_dir(data):
    """ร้องขอรายการไฟล์จากเครื่องลูก"""
    req_id = send_to_agent(data["agent_id"], "list_dir", {
        "path": data.get("path", ""),
    }, request.sid)
    if req_id:
        emit("request_sent", {"request_id": req_id})
    else:
        emit("error", {"message": f"Agent '{data['agent_id']}' is offline"})


@socketio.on("request_download")
def handle_download(data):
    """ร้องขอดาวน์โหลดไฟล์จากเครื่องลูก"""
    req_id = send_to_agent(data["agent_id"], "download_file", {
        "path": data["path"],
    }, request.sid)
    if req_id:
        emit("request_sent", {"request_id": req_id})
    else:
        emit("error", {"message": f"Agent '{data['agent_id']}' is offline"})


@socketio.on("request_upload")
def handle_upload(data):
    """ส่งไฟล์ไปเครื่องลูก (แบบก้อนเดียว - ใช้กับไฟล์เล็ก)"""
    req_id = send_to_agent(data["agent_id"], "upload_file", {
        "path": data["dest_path"],
        "filename": data["filename"],
        "file_data": data["file_data"],
        "file_size": data.get("file_size", 0),
    }, request.sid)
    if req_id:
        emit("request_sent", {"request_id": req_id})
    else:
        emit("error", {"message": f"Agent '{data['agent_id']}' is offline"})


@socketio.on("request_upload_start")
def handle_upload_start(data):
    """เริ่มอัปโหลดแบบแบ่ง chunk (ไฟล์ใหญ่)"""
    req_id = send_to_agent(data["agent_id"], "upload_start", {
        "path": data["dest_path"],
        "filename": data["filename"],
        "file_size": data.get("file_size", 0),
    }, request.sid)
    if req_id:
        emit("upload_ready", {"upload_id": data.get("upload_id"), "request_id": req_id})
    else:
        emit("error", {"message": f"Agent '{data['agent_id']}' is offline"})


@socketio.on("request_upload_chunk")
def handle_upload_chunk(data):
    """ส่ง chunk ไฟล์ต่อไปยังเครื่องลูก (ใช้ req_id เดิมของการอัปโหลดนี้)"""
    agent_id = data["agent_id"]
    target_sid = None
    for sid, info in agents.items():
        if info["agent_id"] == agent_id:
            target_sid = sid
            break
    if not target_sid:
        return
    socketio.emit("command", {
        "request_id": data["request_id"],
        "action": "upload_chunk",
        "data": {"data": data.get("data", ""), "is_last": data.get("is_last", False)},
    }, room=target_sid)


@socketio.on("request_delete")
def handle_delete(data):
    """ลบไฟล์ในเครื่องลูก"""
    req_id = send_to_agent(data["agent_id"], "delete_file", {
        "path": data["path"],
    }, request.sid)
    if req_id:
        emit("request_sent", {"request_id": req_id})
    else:
        emit("error", {"message": f"Agent '{data['agent_id']}' is offline"})


@socketio.on("request_rename")
def handle_rename(data):
    """เปลี่ยนชื่อไฟล์ในเครื่องลูก"""
    req_id = send_to_agent(data["agent_id"], "rename_file", {
        "old_path": data["old_path"],
        "new_name": data["new_name"],
    }, request.sid)
    if req_id:
        emit("request_sent", {"request_id": req_id})
    else:
        emit("error", {"message": f"Agent '{data['agent_id']}' is offline"})


@socketio.on("request_move")
def handle_move(data):
    """ย้ายไฟล์ในเครื่องลูก"""
    req_id = send_to_agent(data["agent_id"], "move_file", {
        "src_path": data["src_path"],
        "dest_path": data["dest_path"],
    }, request.sid)
    if req_id:
        emit("request_sent", {"request_id": req_id})
    else:
        emit("error", {"message": f"Agent '{data['agent_id']}' is offline"})


# ═══════════════════════════════════════════════════════════
#  UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════

def get_agents_list():
    return [
        {
            "agent_id": info["agent_id"],
            "hostname": info["hostname"],
            "os_info": info["os_info"],
            "ip": info["ip"],
            "connected_at": info["connected_at"],
            "allowed_paths": info.get("allowed_paths", []),
        }
        for sid, info in agents.items()
    ]


def send_to_agent(agent_id, action, data, web_sid):
    """ส่งคำสั่งไปยังเครื่องลูก"""
    target_sid = None
    for sid, info in agents.items():
        if info["agent_id"] == agent_id:
            target_sid = sid
            break

    if not target_sid:
        return None

    req_id = str(uuid.uuid4())[:8]
    pending_requests[req_id] = {
        "action": action,
        "web_sid": web_sid,
        "agent_id": agent_id,
        "created_at": time.time(),
        "completed": False,
    }

    socketio.emit("command", {
        "request_id": req_id,
        "action": action,
        "data": data,
    }, room=target_sid)

    logger.info(f"📤 Command '{action}' sent to {agent_id} (req: {req_id})")
    return req_id


# ─── Cleanup old requests ────────────────────────────────
def cleanup_pending():
    now = time.time()
    expired = [k for k, v in pending_requests.items() if now - v["created_at"] > 300]
    for k in expired:
        del pending_requests[k]


# ═══════════════════════════════════════════════════════════
#  WEB UI
# ═══════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template_string(WEB_UI_HTML.replace("__MAX_UPLOAD_MB__", str(MAX_UPLOAD_MB)))


@app.route("/agent.py")
def serve_agent_py():
    """ให้เครื่องลูกดาวน์โหลด agent.py ตัวล่าสุดจาก server ได้ตรงๆ"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.py")
    return send_file(p, mimetype="text/plain", as_attachment=False)


# ═══════════════════════════════════════════════════════════
#  HTML TEMPLATE (embedded)
# ═══════════════════════════════════════════════════════════

WEB_UI_HTML = r"""
<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Remote File Manager</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

  :root {
    --bg-primary: #0a0e17;
    --bg-secondary: #111827;
    --bg-card: #1a2235;
    --bg-hover: #243049;
    --border: #2a3a55;
    --text-primary: #e8ecf4;
    --text-secondary: #8494ad;
    --text-dim: #556883;
    --accent: #3b82f6;
    --accent-glow: rgba(59, 130, 246, 0.3);
    --success: #22c55e;
    --warning: #f59e0b;
    --danger: #ef4444;
    --folder: #f59e0b;
    --file: #64748b;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: 'IBM Plex Sans Thai', sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    min-height: 100vh;
  }

  /* ── HEADER ── */
  .header {
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    padding: 16px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .header h1 {
    font-size: 20px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .header h1 .icon { font-size: 24px; }
  .status-badge {
    font-size: 12px;
    padding: 4px 12px;
    border-radius: 20px;
    font-weight: 500;
  }
  .status-online { background: rgba(34,197,94,0.15); color: var(--success); }
  .status-offline { background: rgba(239,68,68,0.15); color: var(--danger); }

  /* ── LAYOUT ── */
  .main-layout {
    display: grid;
    grid-template-columns: 280px 1fr;
    min-height: calc(100vh - 60px);
  }

  /* ── SIDEBAR ── */
  .sidebar {
    background: var(--bg-secondary);
    border-right: 1px solid var(--border);
    padding: 16px;
    overflow-y: auto;
  }
  .sidebar-title {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--text-dim);
    margin-bottom: 12px;
    font-weight: 600;
  }
  .agent-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    margin-bottom: 10px;
    cursor: pointer;
    transition: all 0.2s;
    position: relative;
  }
  .agent-card:hover { border-color: var(--accent); background: var(--bg-hover); }
  .agent-card.active {
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent), 0 0 20px var(--accent-glow);
  }
  .agent-card .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--success);
    position: absolute;
    top: 14px; right: 14px;
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  .agent-card h3 {
    font-size: 14px;
    font-weight: 600;
    margin-bottom: 4px;
  }
  .agent-card .meta {
    font-size: 11px;
    color: var(--text-secondary);
    font-family: 'JetBrains Mono', monospace;
  }
  .no-agents {
    color: var(--text-dim);
    text-align: center;
    padding: 40px 16px;
    font-size: 13px;
    line-height: 1.8;
  }

  /* ── CONTENT AREA ── */
  .content {
    padding: 20px 24px;
    overflow-y: auto;
  }

  /* ── TOOLBAR ── */
  .toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }
  .breadcrumb {
    display: flex;
    align-items: center;
    gap: 4px;
    flex: 1;
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    color: var(--text-secondary);
    min-width: 200px;
    background: var(--bg-card);
    padding: 8px 14px;
    border-radius: 8px;
    border: 1px solid var(--border);
    overflow-x: auto;
    white-space: nowrap;
  }
  .breadcrumb span {
    cursor: pointer;
    color: var(--accent);
    transition: opacity 0.2s;
  }
  .breadcrumb span:hover { opacity: 0.7; }
  .breadcrumb .sep { color: var(--text-dim); cursor: default; }

  .btn {
    padding: 8px 16px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--bg-card);
    color: var(--text-primary);
    font-size: 13px;
    font-family: inherit;
    cursor: pointer;
    transition: all 0.2s;
    display: flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
  }
  .btn:hover { border-color: var(--accent); background: var(--bg-hover); }
  .btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .btn-primary:hover { background: #2563eb; }
  .btn-danger { border-color: var(--danger); color: var(--danger); }
  .btn-danger:hover { background: rgba(239,68,68,0.1); }

  /* ── FILE LIST ── */
  .file-table {
    width: 100%;
    border-collapse: collapse;
  }
  .file-table th {
    text-align: left;
    padding: 10px 14px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text-dim);
    font-weight: 600;
    border-bottom: 1px solid var(--border);
    user-select: none;
  }
  .file-table td {
    padding: 10px 14px;
    font-size: 13px;
    border-bottom: 1px solid rgba(42,58,85,0.5);
    vertical-align: middle;
  }
  .file-table tr:hover td {
    background: var(--bg-hover);
  }
  .file-table tr { cursor: pointer; }

  .file-name {
    display: flex;
    align-items: center;
    gap: 10px;
    font-weight: 500;
  }
  .file-icon { font-size: 18px; flex-shrink: 0; }
  .file-size, .file-date {
    color: var(--text-secondary);
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
  }
  .file-actions {
    display: flex;
    gap: 6px;
    opacity: 0;
    transition: opacity 0.2s;
  }
  tr:hover .file-actions { opacity: 1; }
  .file-actions .btn { padding: 4px 10px; font-size: 11px; }

  /* ── MODALS ── */
  .modal-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.7);
    z-index: 200;
    align-items: center;
    justify-content: center;
    backdrop-filter: blur(4px);
  }
  .modal-overlay.show { display: flex; }
  .modal {
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 28px;
    min-width: 400px;
    max-width: 500px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5);
  }
  .modal h2 {
    font-size: 18px;
    margin-bottom: 16px;
    font-weight: 600;
  }
  .modal input[type="text"] {
    width: 100%;
    padding: 10px 14px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text-primary);
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    margin-bottom: 16px;
  }
  .modal input:focus { outline: none; border-color: var(--accent); }
  .modal-buttons {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
  }

  /* ── UPLOAD ZONE ── */
  .upload-zone {
    border: 2px dashed var(--border);
    border-radius: 12px;
    padding: 40px;
    text-align: center;
    color: var(--text-secondary);
    transition: all 0.3s;
    margin-bottom: 16px;
    cursor: pointer;
  }
  .upload-zone:hover, .upload-zone.dragover {
    border-color: var(--accent);
    background: rgba(59,130,246,0.05);
    color: var(--accent);
  }
  .upload-zone .icon { font-size: 40px; margin-bottom: 8px; }

  /* ── PROGRESS ── */
  .progress-bar {
    height: 4px;
    background: var(--bg-card);
    border-radius: 4px;
    overflow: hidden;
    margin: 8px 0;
  }
  .progress-fill {
    height: 100%;
    background: var(--accent);
    border-radius: 4px;
    transition: width 0.3s;
  }

  /* ── TOAST ── */
  .toast-container {
    position: fixed;
    bottom: 24px;
    right: 24px;
    z-index: 300;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .toast {
    padding: 12px 20px;
    border-radius: 10px;
    font-size: 13px;
    animation: slideIn 0.3s ease;
    display: flex;
    align-items: center;
    gap: 8px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.3);
  }
  .toast-success { background: rgba(34,197,94,0.15); border: 1px solid var(--success); color: var(--success); }
  .toast-error { background: rgba(239,68,68,0.15); border: 1px solid var(--danger); color: var(--danger); }
  .toast-info { background: rgba(59,130,246,0.15); border: 1px solid var(--accent); color: var(--accent); }
  @keyframes slideIn { from { transform: translateX(100px); opacity: 0; } }

  /* ── LOADING ── */
  .loading {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 60px;
    color: var(--text-dim);
    gap: 12px;
  }
  .spinner {
    width: 20px; height: 20px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .empty-state {
    text-align: center;
    padding: 80px 20px;
    color: var(--text-dim);
  }
  .empty-state .icon { font-size: 60px; margin-bottom: 16px; }
  .empty-state h3 { font-size: 18px; margin-bottom: 8px; color: var(--text-secondary); }

  /* ── RESPONSIVE ── */
  @media (max-width: 768px) {
    .main-layout { grid-template-columns: 1fr; }
    .sidebar {
      border-right: none;
      border-bottom: 1px solid var(--border);
      display: flex;
      gap: 8px;
      padding: 12px;
      overflow-x: auto;
    }
    .sidebar-title { display: none; }
    .agent-card { min-width: 160px; margin-bottom: 0; }
  }
</style>
</head>
<body>

<!-- HEADER -->
<div class="header">
  <h1>
    <span class="icon">📁</span>
    Remote File Manager
  </h1>
  <div>
    <span class="status-badge status-online" id="connStatus">● เชื่อมต่อแล้ว</span>
  </div>
</div>

<!-- MAIN LAYOUT -->
<div class="main-layout">
  <!-- SIDEBAR -->
  <div class="sidebar">
    <div class="sidebar-title">เครื่องลูก (Agents)</div>
    <div id="agentList">
      <div class="no-agents">
        ⏳<br>รอเครื่องลูกเชื่อมต่อ...<br>
        <small>เปิด agent.py ที่เครื่องลูก</small>
      </div>
    </div>
  </div>

  <!-- CONTENT -->
  <div class="content" id="contentArea">
    <div class="empty-state">
      <div class="icon">🖥️</div>
      <h3>เลือกเครื่องลูกเพื่อเริ่มต้น</h3>
      <p>เลือกเครื่องลูกจากแถบด้านซ้ายเพื่อดูไฟล์</p>
    </div>
  </div>
</div>

<!-- RENAME MODAL -->
<div class="modal-overlay" id="renameModal">
  <div class="modal">
    <h2>✏️ เปลี่ยนชื่อ</h2>
    <input type="text" id="renameInput" placeholder="ชื่อใหม่...">
    <div class="modal-buttons">
      <button class="btn" onclick="closeModal('renameModal')">ยกเลิก</button>
      <button class="btn btn-primary" onclick="confirmRename()">บันทึก</button>
    </div>
  </div>
</div>

<!-- UPLOAD MODAL -->
<div class="modal-overlay" id="uploadModal">
  <div class="modal">
    <h2>📤 อัปโหลดไฟล์ไปเครื่องลูก</h2>
    <div class="upload-zone" id="uploadZone" onclick="document.getElementById('fileInput').click()">
      <div class="icon">📎</div>
      <div>คลิกเลือกไฟล์ หรือลากไฟล์มาวาง</div>
      <small>ขนาดไม่เกิน __MAX_UPLOAD_MB__MB</small>
    </div>
    <input type="file" id="fileInput" style="display:none" multiple>
    <div id="uploadList"></div>
    <div class="modal-buttons">
      <button class="btn" onclick="closeModal('uploadModal')">ปิด</button>
    </div>
  </div>
</div>

<!-- TOAST CONTAINER -->
<div class="toast-container" id="toasts"></div>

<script>
// ═══════════════════════════════════════════════════════════
//  STATE
// ═══════════════════════════════════════════════════════════
let socket = null;
let currentAgent = null;
let currentPath = '';
let currentFiles = [];
let renameTarget = null;
let agentsData = [];

// ═══════════════════════════════════════════════════════════
//  SOCKET CONNECTION
// ═══════════════════════════════════════════════════════════
function initSocket() {
  socket = io({ transports: ['websocket', 'polling'] });

  socket.on('connect', () => {
    socket.emit('web_register', {});
    document.getElementById('connStatus').className = 'status-badge status-online';
    document.getElementById('connStatus').textContent = '● เชื่อมต่อแล้ว';
  });

  socket.on('disconnect', () => {
    document.getElementById('connStatus').className = 'status-badge status-offline';
    document.getElementById('connStatus').textContent = '● ขาดการเชื่อมต่อ';
  });

  socket.on('agents_updated', (agents) => {
    renderAgents(agents);
  });

  socket.on('error', (data) => {
    toast(data.message, 'error');
  });
}

// ═══════════════════════════════════════════════════════════
//  RENDER AGENTS
// ═══════════════════════════════════════════════════════════
function renderAgents(agents) {
  agentsData = agents || [];
  const el = document.getElementById('agentList');
  if (!agents || agents.length === 0) {
    el.innerHTML = '<div class="no-agents">⏳<br>รอเครื่องลูกเชื่อมต่อ...<br><small>เปิด agent.py ที่เครื่องลูก</small></div>';
    return;
  }
  el.innerHTML = agents.map(a => `
    <div class="agent-card ${currentAgent === a.agent_id ? 'active' : ''}"
         onclick="selectAgent('${a.agent_id}')">
      <div class="dot"></div>
      <h3>🖥️ ${escHtml(a.hostname)}</h3>
      <div class="meta">${escHtml(a.agent_id)}</div>
      <div class="meta">${escHtml(a.ip)}</div>
      <div class="meta" style="margin-top:4px; color: var(--text-dim)">${escHtml(a.os_info)}</div>
    </div>
  `).join('');
}

// ═══════════════════════════════════════════════════════════
//  AGENT SELECTION & BROWSING
// ═══════════════════════════════════════════════════════════
function selectAgent(agentId) {
  currentAgent = agentId;
  currentPath = '';
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
  event.currentTarget.classList.add('active');
  // ถ้าเครื่องลูกจำกัดโฟลเดอร์ไว้ → เข้าโฟลเดอร์นั้นตรงๆ (ข้ามหน้าเลือกไดรฟ์ที่อาจค้าง)
  const a = agentsData.find(x => x.agent_id === agentId);
  const startPath = (a && a.allowed_paths && a.allowed_paths.length > 0) ? a.allowed_paths[0] : '';
  loadDir(startPath);
}

function loadDir(path) {
  currentPath = path;
  showLoading();

  const reqHandler = (data) => {
    socket.off('response_' + data.request_id);

    socket.on('response_' + data.request_id, (resp) => {
      socket.off('response_' + data.request_id);
      if (resp.error) {
        toast(resp.error, 'error');
        showEmpty('เกิดข้อผิดพลาด: ' + resp.error);
        return;
      }
      currentFiles = resp.files || [];
      renderFiles(currentFiles, resp.path || path);
    });
  };

  socket.once('request_sent', reqHandler);
  socket.emit('request_list_dir', { agent_id: currentAgent, path: path });
}

// ═══════════════════════════════════════════════════════════
//  RENDER FILES
// ═══════════════════════════════════════════════════════════
function renderFiles(files, path) {
  const content = document.getElementById('contentArea');

  // Sort: folders first, then by name
  files.sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  const breadcrumb = buildBreadcrumb(path);

  content.innerHTML = `
    <div class="toolbar">
      <div class="breadcrumb">${breadcrumb}</div>
      <button class="btn" onclick="loadDir(currentPath)">🔄 รีเฟรช</button>
      <button class="btn btn-primary" onclick="openUpload()">📤 อัปโหลด</button>
    </div>
    ${files.length === 0 ? '<div class="empty-state"><div class="icon">📭</div><h3>โฟลเดอร์ว่าง</h3></div>' : `
    <table class="file-table">
      <thead>
        <tr>
          <th style="width:50%">ชื่อ</th>
          <th>ขนาด</th>
          <th>แก้ไขล่าสุด</th>
          <th style="width:140px"></th>
        </tr>
      </thead>
      <tbody>
        ${files.map((f, i) => `
          <tr ondblclick="${f.is_dir ? `loadDir('${escAttr(f.full_path)}')` : `downloadFile('${escAttr(f.full_path)}', '${escAttr(f.name)}')`}">
            <td>
              <div class="file-name">
                <span class="file-icon">${f.is_dir ? '📂' : getFileIcon(f.name)}</span>
                ${escHtml(f.name)}
              </div>
            </td>
            <td class="file-size">${f.is_dir ? '-' : formatSize(f.size)}</td>
            <td class="file-date">${f.modified || '-'}</td>
            <td>
              <div class="file-actions">
                ${!f.is_dir ? `<button class="btn" onclick="event.stopPropagation(); downloadFile('${escAttr(f.full_path)}', '${escAttr(f.name)}')">💾</button>` : ''}
                <button class="btn" onclick="event.stopPropagation(); startRename('${escAttr(f.full_path)}', '${escAttr(f.name)}')">✏️</button>
                <button class="btn btn-danger" onclick="event.stopPropagation(); deleteFile('${escAttr(f.full_path)}', '${escAttr(f.name)}')">🗑️</button>
              </div>
            </td>
          </tr>
        `).join('')}
      </tbody>
    </table>
    `}
  `;
}

function buildBreadcrumb(path) {
  if (!path) return `<span onclick="loadDir('')">💻 ${escHtml(currentAgent)}</span>`;

  // Handle Windows paths
  let parts;
  const isWin = path.includes('\\') || /^[A-Z]:/i.test(path);
  const sep = isWin ? '\\' : '/';
  parts = path.split(/[\\\/]/).filter(Boolean);

  let crumbs = `<span onclick="loadDir('')">💻 ${escHtml(currentAgent)}</span>`;
  let accumulated = '';

  for (let i = 0; i < parts.length; i++) {
    accumulated += (i === 0 && isWin ? '' : sep) + parts[i];
    if (i === 0 && isWin && !accumulated.endsWith(':')) {
      // keep going
    }
    crumbs += `<span class="sep"> ▸ </span><span onclick="loadDir('${escAttr(accumulated)}')">${escHtml(parts[i])}</span>`;
  }
  return crumbs;
}

// ═══════════════════════════════════════════════════════════
//  FILE OPERATIONS
// ═══════════════════════════════════════════════════════════

// Download
function downloadFile(filePath, fileName) {
  toast('กำลังดาวน์โหลด ' + fileName + '...', 'info');

  socket.once('request_sent', (data) => {
    let chunks = [];

    socket.on('file_chunk_' + data.request_id, (chunk) => {
      if (chunk.error) {
        toast('ดาวน์โหลดล้มเหลว: ' + chunk.error, 'error');
        socket.off('file_chunk_' + data.request_id);
        return;
      }
      chunks.push(chunk.data);

      if (chunk.is_last) {
        socket.off('file_chunk_' + data.request_id);
        // Combine and download
        const combined = chunks.join('');
        const bytes = atob(combined);
        const arr = new Uint8Array(bytes.length);
        for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
        const blob = new Blob([arr]);
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = fileName;
        a.click();
        URL.revokeObjectURL(url);
        toast('ดาวน์โหลด ' + fileName + ' สำเร็จ!', 'success');
      }
    });

    socket.on('response_' + data.request_id, (resp) => {
      socket.off('response_' + data.request_id);
      if (resp.error) {
        toast('ดาวน์โหลดล้มเหลว: ' + resp.error, 'error');
      }
    });
  });

  socket.emit('request_download', { agent_id: currentAgent, path: filePath });
}

// Delete
function deleteFile(filePath, fileName) {
  if (!confirm(`ต้องการลบ "${fileName}" จริงหรือไม่?\n\n⚠️ การลบไม่สามารถกู้คืนได้`)) return;

  socket.once('request_sent', (data) => {
    socket.on('response_' + data.request_id, (resp) => {
      socket.off('response_' + data.request_id);
      if (resp.error) {
        toast('ลบล้มเหลว: ' + resp.error, 'error');
      } else {
        toast('ลบ ' + fileName + ' สำเร็จ', 'success');
        loadDir(currentPath);
      }
    });
  });

  socket.emit('request_delete', { agent_id: currentAgent, path: filePath });
}

// Rename
function startRename(filePath, fileName) {
  renameTarget = filePath;
  document.getElementById('renameInput').value = fileName;
  document.getElementById('renameModal').classList.add('show');
  setTimeout(() => document.getElementById('renameInput').focus(), 100);
}

function confirmRename() {
  const newName = document.getElementById('renameInput').value.trim();
  if (!newName) return;

  socket.once('request_sent', (data) => {
    socket.on('response_' + data.request_id, (resp) => {
      socket.off('response_' + data.request_id);
      if (resp.error) {
        toast('เปลี่ยนชื่อล้มเหลว: ' + resp.error, 'error');
      } else {
        toast('เปลี่ยนชื่อสำเร็จ', 'success');
        loadDir(currentPath);
      }
    });
  });

  socket.emit('request_rename', { agent_id: currentAgent, old_path: renameTarget, new_name: newName });
  closeModal('renameModal');
}

// Upload
function openUpload() {
  document.getElementById('uploadList').innerHTML = '';
  document.getElementById('uploadModal').classList.add('show');
}

function handleUpload(files) {
  const listEl = document.getElementById('uploadList');

  Array.from(files).forEach(file => {
    if (file.size > __MAX_UPLOAD_MB__ * 1024 * 1024) {
      toast(file.name + ' ใหญ่เกิน __MAX_UPLOAD_MB__MB', 'error');
      return;
    }

    const itemId = 'up_' + Math.random().toString(36).substr(2, 6);
    listEl.innerHTML += `
      <div id="${itemId}" style="margin-bottom:8px">
        <div style="display:flex; justify-content:space-between; font-size:13px">
          <span>📎 ${escHtml(file.name)}</span>
          <span class="file-size">${formatSize(file.size)}</span>
        </div>
        <div class="progress-bar"><div class="progress-fill" style="width:0%"></div></div>
      </div>
    `;

    const reader = new FileReader();
    reader.onload = (e) => {
      const base64 = e.target.result.split(',')[1];

      // Calculate dest path
      const destPath = currentPath
        ? currentPath + (currentPath.includes('/') ? '/' : '\\') + file.name
        : file.name;

      // ── อัปโหลดแบบแบ่ง chunk (512KB, หารด้วย 4 ลงตัว = base64 aligned) ──
      const CHUNK = 512 * 1024;
      const total = Math.ceil(base64.length / CHUNK) || 1;

      const onReady = (info) => {
        if (info.upload_id !== itemId) return;   // ไม่ใช่ของไฟล์นี้
        socket.off('upload_ready', onReady);
        const rid = info.request_id;

        socket.on('response_' + rid, (resp) => {
          socket.off('response_' + rid);
          const el = document.getElementById(itemId);
          if (resp.error) {
            if (el) el.querySelector('.progress-fill').style.background = 'var(--danger)';
            toast('อัปโหลด ' + file.name + ' ล้มเหลว: ' + resp.error, 'error');
          } else {
            if (el) el.querySelector('.progress-fill').style.width = '100%';
            if (resp.extracted) {
              toast('อัปโหลด + แตกไฟล์ ' + file.name + ' สำเร็จ!', 'success');
            } else if (resp.extract_error) {
              toast('อัปโหลดสำเร็จ แต่แตก zip ไม่ได้: ' + resp.extract_error, 'error');
            } else {
              toast('อัปโหลด ' + file.name + ' สำเร็จ!', 'success');
            }
            loadDir(currentPath);
          }
        });

        // ส่ง chunk ทีละก้อน
        for (let i = 0; i < total; i++) {
          const chunk = base64.slice(i * CHUNK, (i + 1) * CHUNK);
          socket.emit('request_upload_chunk', {
            agent_id: currentAgent,
            request_id: rid,
            data: chunk,
            is_last: (i === total - 1),
          });
          const el = document.getElementById(itemId);
          if (el) el.querySelector('.progress-fill').style.width = Math.min(Math.round(((i + 1) / total) * 100), 99) + '%';
        }
      };

      socket.on('upload_ready', onReady);
      socket.emit('request_upload_start', {
        agent_id: currentAgent,
        upload_id: itemId,
        dest_path: destPath,
        filename: file.name,
        file_size: file.size,
      });
    };
    reader.readAsDataURL(file);
  });
}

// ═══════════════════════════════════════════════════════════
//  UTILITIES
// ═══════════════════════════════════════════════════════════
function formatSize(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
}

function getFileIcon(name) {
  const ext = (name.split('.').pop() || '').toLowerCase();
  const icons = {
    pdf: '📕', doc: '📘', docx: '📘', xls: '📗', xlsx: '📗', ppt: '📙', pptx: '📙',
    jpg: '🖼️', jpeg: '🖼️', png: '🖼️', gif: '🖼️', bmp: '🖼️', svg: '🖼️', webp: '🖼️',
    mp4: '🎬', avi: '🎬', mkv: '🎬', mov: '🎬', mp3: '🎵', wav: '🎵', flac: '🎵',
    zip: '📦', rar: '📦', '7z': '📦', tar: '📦', gz: '📦',
    py: '🐍', js: '📜', html: '🌐', css: '🎨', json: '📋',
    exe: '⚙️', msi: '⚙️', bat: '⚙️', cmd: '⚙️',
    txt: '📄', log: '📄', csv: '📊', sql: '🗃️', db: '🗃️',
  };
  return icons[ext] || '📄';
}

function showLoading() {
  document.getElementById('contentArea').innerHTML = '<div class="loading"><div class="spinner"></div>กำลังโหลด...</div>';
}
function showEmpty(msg) {
  document.getElementById('contentArea').innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><h3>${escHtml(msg)}</h3></div>`;
}

function toast(msg, type = 'info') {
  const icons = { success: '✅', error: '❌', info: 'ℹ️' };
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = `${icons[type] || ''} ${escHtml(msg)}`;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

function closeModal(id) {
  document.getElementById(id).classList.remove('show');
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}
function escAttr(s) {
  return (s || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"');
}

// ── DRAG & DROP ──
document.addEventListener('DOMContentLoaded', () => {
  initSocket();

  const zone = document.getElementById('uploadZone');
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('dragover');
    handleUpload(e.dataTransfer.files);
  });

  document.getElementById('fileInput').addEventListener('change', (e) => {
    handleUpload(e.target.files);
    e.target.value = '';
  });

  // Enter key for rename
  document.getElementById('renameInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') confirmRename();
    if (e.key === 'Escape') closeModal('renameModal');
  });

  // Close modal on overlay click
  document.querySelectorAll('.modal-overlay').forEach(m => {
    m.addEventListener('click', (e) => {
      if (e.target === m) m.classList.remove('show');
    });
  });
});
</script>
</body>
</html>
"""

# ═══════════════════════════════════════════════════════════
#  RUN
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    logger.info("=" * 55)
    logger.info("  Remote File Manager - Server")
    logger.info(f"  http://localhost:{SERVER_PORT}")
    logger.info(f"  Agent Secret: {AGENT_SECRET}")
    logger.info("=" * 55)
    socketio.run(app, host="0.0.0.0", port=SERVER_PORT, debug=False, allow_unsafe_werkzeug=True)
