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

    # ลบ connection เก่าของ agent_id เดียวกัน (กัน zombie ค้าง ทำให้คำสั่งวิ่งเข้าตัวที่ตายแล้ว → timeout)
    stale_sids = [sid for sid, info in agents.items()
                  if info.get("agent_id") == agent_id and sid != request.sid]
    for sid in stale_sids:
        agents.pop(sid, None)
        logger.info(f"🧹 แทนที่ connection เก่าของ {agent_id} (sid {sid[:6]}…)")

    agents[request.sid] = {
        "agent_id": agent_id,
        "name": data.get("name", ""),
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
    # เอา request ออกจากคิวเลยหลังตอบ (กัน pending_requests บวมจาก live view/คำสั่งถี่ๆ)
    req = pending_requests.pop(req_id, None)
    if req:
        web_sid = req.get("web_sid")
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
    payload = {
        "path": data["dest_path"],
        "filename": data["filename"],
        "file_size": data.get("file_size", 0),
    }
    # โหมด broadcast: ส่งต่อ base_match/subpath ให้ agent วางไฟล์ในโฟลเดอร์ input-id เอง
    if data.get("base_match") is not None:
        payload["base_match"] = data.get("base_match")
    if data.get("subpath") is not None:
        payload["subpath"] = data.get("subpath")
    req_id = send_to_agent(data["agent_id"], "upload_start", payload, request.sid)
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


@socketio.on("request_delete_many")
def handle_delete_many(data):
    """ลบหลายไฟล์ในคำสั่งเดียว (เร็วกว่าลบทีละไฟล์)"""
    req_id = send_to_agent(data["agent_id"], "delete_many", {
        "paths": data.get("paths", []),
    }, request.sid)
    if req_id:
        emit("request_sent", {"request_id": req_id})
    else:
        emit("error", {"message": f"Agent '{data['agent_id']}' is offline"})


@socketio.on("request_count_heroes")
def handle_count_heroes(data):
    """ขอให้เครื่องลูกนับไฟล์ตามชื่อฮีโร่ในโฟลเดอร์ found-hero"""
    req_id = send_to_agent(data["agent_id"], "count_heroes", {
        "names": data.get("names", []),
        "subpath": data.get("subpath", "found-hero"),
    }, request.sid)
    if req_id:
        emit("request_sent", {"request_id": req_id})
    else:
        emit("error", {"message": f"Agent '{data['agent_id']}' is offline"})


@socketio.on("request_list_ids")
def handle_list_ids(data):
    """ขอให้เครื่องลูกดึงรายชื่อ id ในโฟลเดอร์ (เช่น cookie-run\\id-found)"""
    req_id = send_to_agent(data["agent_id"], "list_ids", {
        "subpath": data.get("subpath", "id-found"),
        "base_match": data.get("base_match", "cookie-run"),
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


@socketio.on("request_shutdown")
def handle_shutdown_req(data):
    """สั่งปิดโปรแกรม agent ที่เครื่องลูกจากระยะไกล"""
    req_id = send_to_agent(data["agent_id"], "shutdown", {}, request.sid)
    if req_id:
        emit("request_sent", {"request_id": req_id})
    else:
        emit("error", {"message": f"Agent '{data['agent_id']}' is offline"})


@socketio.on("request_self_update")
def handle_self_update_req(data):
    """สั่งให้ agent ดึงโค้ดใหม่จาก GitHub + รีสตาร์ทตัวเอง"""
    req_id = send_to_agent(data["agent_id"], "self_update", {}, request.sid)
    if req_id:
        emit("request_sent", {"request_id": req_id})
    else:
        emit("error", {"message": f"Agent '{data['agent_id']}' is offline"})


@socketio.on("request_screenshot")
def handle_screenshot_req(data):
    """สั่งให้ agent จับภาพหน้าจอส่งกลับ (live view / PC monitor)"""
    req_id = send_to_agent(data["agent_id"], "screenshot", {
        "width": data.get("width", 640),
        "quality": data.get("quality", 55),
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
            "name": info.get("name", ""),
            "hostname": info["hostname"],
            "os_info": info["os_info"],
            "ip": info["ip"],
            "connected_at": info["connected_at"],
            "allowed_paths": info.get("allowed_paths", []),
        }
        for sid, info in agents.items()
    ]


def send_to_agent(agent_id, action, data, web_sid):
    """ส่งคำสั่งไปยังเครื่องลูก (เลือก connection ล่าสุดของ agent_id กันตัวค้างเก่า)"""
    matches = [(info.get("connected_at", ""), sid)
               for sid, info in agents.items() if info.get("agent_id") == agent_id]
    if not matches:
        return None
    matches.sort()
    target_sid = matches[-1][1]

    # กันบวม: ลบ request ที่ค้างนานเกิน 60 วิ (เครื่องที่ตาย/ไม่ตอบกลับ)
    if len(pending_requests) > 40:
        cutoff = time.time() - 60
        for k in [k for k, v in pending_requests.items() if v.get("created_at", 0) < cutoff]:
            pending_requests.pop(k, None)

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


@app.route("/autoupdate.bat")
def serve_autoupdate_bat():
    """ให้เครื่องลูกดาวน์โหลด autoupdate.bat (ตัวติดตั้ง/อัปเดต agent) จาก server"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autoupdate.bat")
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
<script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>
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
  .agent-card .power-btn {
    position: absolute;
    bottom: 10px;
    right: 10px;
    width: 28px; height: 28px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: rgba(239,68,68,0.10);
    color: var(--danger);
    font-size: 14px;
    line-height: 1;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    opacity: 0.55;
    transition: opacity 0.15s ease, background 0.15s ease, color 0.15s ease;
  }
  .agent-card:hover .power-btn { opacity: 1; }
  .agent-card .power-btn:hover { background: var(--danger); color: #fff; }
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
  select.project-select {
    background: var(--bg-card);
    color: var(--text-primary);
    font-weight: 600;
  }
  select.project-select option {
    background: var(--bg-secondary);
    color: var(--text-primary);
  }

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
  .file-check, #selectAll {
    width: 16px; height: 16px;
    cursor: pointer;
    accent-color: var(--accent);
  }

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

  /* ── BROADCAST agent chips ── */
  .bc-chip {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 7px 12px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--bg-card);
    font-size: 13px;
    cursor: pointer;
    user-select: none;
    transition: border-color 0.15s, background 0.15s;
  }
  .bc-chip:hover { border-color: var(--accent); }
  .bc-chip:has(input:checked) { border-color: var(--accent); background: var(--bg-hover); }
  .bc-chip input { cursor: pointer; }

  /* ── LIVE VIEW / PC MONITOR ── */
  .pc-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 14px;
  }
  .pc-grid.single { grid-template-columns: 1fr; }
  .pc-tile {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    cursor: pointer;
    transition: border-color .15s, transform .15s;
  }
  .pc-tile:hover { border-color: var(--accent); transform: translateY(-2px); }
  .pc-shot {
    position: relative;
    width: 100%;
    aspect-ratio: 16 / 9;
    background: #000;
    display: flex; align-items: center; justify-content: center;
  }
  .pc-shot img { width: 100%; height: 100%; object-fit: contain; display: none; }
  .pc-noimg { position: absolute; color: var(--text-dim); font-size: 13px; }
  .pc-tile-bar {
    display: flex; justify-content: space-between; align-items: center;
    padding: 9px 12px; font-size: 13px; font-weight: 600;
  }
  .pc-live-dot { font-size: 11px; font-weight: 500; color: var(--text-dim); }
  .pc-pager {
    display: flex; justify-content: center; align-items: center; gap: 14px;
    margin-top: 22px; color: var(--text-secondary); font-size: 13px;
  }
  .pc-pager .btn { padding: 6px 13px; }
  .pc-pager .btn:disabled { opacity: .4; cursor: default; }

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

  /* ── DASHBOARD ── */
  .stat-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 14px;
    margin-bottom: 26px;
  }
  .stat-tile {
    background: linear-gradient(140deg, var(--bg-card), var(--bg-secondary));
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 20px 22px;
    position: relative;
    overflow: hidden;
  }
  .stat-tile::before {
    content: '';
    position: absolute;
    top: 0; left: 0;
    width: 3px; height: 100%;
    background: var(--accent);
    opacity: 0.7;
  }
  .stat-label {
    font-size: 11px;
    color: var(--text-dim);
    margin-bottom: 10px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    font-weight: 600;
  }
  .stat-val { font-size: 32px; font-weight: 800; line-height: 1; letter-spacing: -0.5px; }

  .dash-search {
    padding: 9px 14px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text-primary);
    font-family: inherit;
    font-size: 13px;
    min-width: 240px;
  }
  .dash-search:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
  .dash-search::placeholder { color: var(--text-dim); }

  .hero-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(135px, 1fr));
    gap: 9px;
  }
  .hero-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-height: 62px;
    transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
  }
  .hero-card:hover {
    border-color: var(--accent);
    transform: translateY(-2px);
    box-shadow: 0 6px 18px rgba(0,0,0,0.3);
  }
  .hero-name {
    font-size: 12px;
    font-weight: 500;
    color: var(--text-secondary);
    line-height: 1.3;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: break-word;
  }
  .hero-count {
    font-size: 22px;
    font-weight: 800;
    color: var(--accent);
    line-height: 1;
    letter-spacing: -0.3px;
    margin-top: auto;
  }
  /* combo (หลายฮีโร่รวมกัน) ใช้สีส้มแยกจากฮีโร่เดี่ยว */
  .hero-card.combo { border-color: rgba(245,158,11,0.35); background: linear-gradient(140deg, var(--bg-card), rgba(245,158,11,0.05)); }
  .hero-card.combo .hero-count { color: var(--warning); }

  /* ── COOKIE-RUN id cards (แสดงชื่อ id) ── */
  .id-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
    gap: 9px;
  }
  .id-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 11px 13px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-height: 58px;
    transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
  }
  .id-card:hover {
    border-color: var(--accent);
    transform: translateY(-2px);
    box-shadow: 0 6px 18px rgba(0,0,0,0.3);
  }
  .id-name-big {
    font-size: 14px;
    font-weight: 700;
    color: var(--text-primary);
    line-height: 1.35;
    word-break: break-all;
  }
  .id-machine {
    font-size: 11px;
    color: var(--text-dim);
    font-family: 'JetBrains Mono', monospace;
    margin-top: auto;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .id-badge {
    display: inline-block;
    font-size: 18px;
    font-weight: 800;
    color: var(--warning);
    background: rgba(245,158,11,0.15);
    border-radius: 8px;
    padding: 2px 10px;
    margin-left: 6px;
    vertical-align: middle;
    line-height: 1.2;
  }

  .agent-stats { display: flex; flex-direction: column; gap: 8px; }
  .agent-stat {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 13px;
    padding: 12px 16px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
  }

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
  <div style="display:flex; align-items:center; gap:12px">
    <button class="btn" onclick="openDashboard()">⚽ Dashboard PES</button>
    <button class="btn" onclick="openCookieDashboard()">🍪 Dashboard Cookie-Run</button>
    <button class="btn" onclick="openBroadcastInput()">📤 ส่งเข้า input-id (ทุกเครื่อง)</button>
    <button class="btn" onclick="openLiveView()">🖥️ Live View</button>
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
//  DASHBOARD (นับ found-hero รวมทุกเครื่อง)
// ═══════════════════════════════════════════════════════════
const HERO_LIST = ["Fabio Cannavaro","Paolo Maldini","Daniele De Rossi","Didier Drogba","Mohamed Salah","Nico Paz","Federico Dimarco","Luka","rgson","Arribas","Aubameyang","Ramedhan Saifullah","Chrigor","Lamine=x2","Mbappe","Joan Garcia","Martin Odegaard","Atep","Gareth Bale","Marcelo","Peter Schmeichel","Leonardo Bonucci","Ronald Koeman","Casemiro","Erling Haaland","Hugo Ekitike","Declan Rice","Hidetoshi Nakata","Seigo Narazaki","Shunsuke Nakamura","Vitinha","David Raya","Kvaratskhelia","Johan Cruyff","Filippo Inzaghi","Jordi Alba","Oliver Kahn","David Beckham","Rivaldo","Gianluigi Buffon","Andrea Pirlo","Gialuca Zambrotta","Lilian Thuram","Patrick Vieira","Marcel Desailly","Luis Suarez","Schweinsteiger","Bronckhorst"];

let dashboardScope = 'ALL';  // 'ALL' = รวมทุกเครื่อง, หรือ agent_id ของเครื่องที่เลือก
let cookieScope = 'ALL';     // scope แยกของ dashboard cookie-run

function pcSelectHtml(scopeVal, onchangeExpr) {
  const agents = agentsData || [];
  let opts = `<option value="ALL"${scopeVal === 'ALL' ? ' selected' : ''}>🖥️ รวมทุกเครื่อง</option>`;
  opts += agents.map(a => `<option value="${escHtml(a.agent_id)}"${scopeVal === a.agent_id ? ' selected' : ''}>🖥️ ${escHtml(a.name || a.hostname || a.agent_id)}</option>`).join('');
  return `<select class="btn project-select" onchange="${onchangeExpr}" title="เลือกเครื่องที่จะแสดง">${opts}</select>`;
}

function countHeroesOnAgent(agentId) {
  return new Promise((resolve, reject) => {
    let settled = false;
    socket.once('request_sent', (data) => {
      const rid = data.request_id;
      socket.once('response_' + rid, (resp) => {
        settled = true;
        if (resp.error) reject(new Error(resp.error)); else resolve(resp);
      });
    });
    socket.emit('request_count_heroes', { agent_id: agentId, names: HERO_LIST, subpath: 'found-hero' });
    setTimeout(() => { if (!settled) reject(new Error('timeout')); }, 20000);
  });
}

async function openDashboard() {
  currentAgent = null;
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
  const content = document.getElementById('contentArea');
  const allAgents = agentsData || [];
  if (dashboardScope !== 'ALL' && !allAgents.some(a => a.agent_id === dashboardScope)) dashboardScope = 'ALL';
  const agents = dashboardScope === 'ALL' ? allAgents : allAgents.filter(a => a.agent_id === dashboardScope);

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">⚽ Dashboard PES — found-hero</h2>
      ${pcSelectHtml(dashboardScope, 'dashboardScope=this.value; openDashboard()')}
      <button class="btn btn-primary" onclick="openDashboard()">🔄 รีเฟรช</button>
    </div>
    <div class="loading"><div class="spinner"></div>กำลังดึงข้อมูลจาก ${agents.length} เครื่อง...</div>`;

  if (!allAgents.length) {
    content.innerHTML = '<div class="empty-state"><div class="icon">🖥️</div><h3>ยังไม่มีเครื่องลูกออนไลน์</h3></div>';
    return;
  }

  const comboTotals = {};
  let grandTotal = 0, onlineCount = 0;
  const perAgent = [];

  for (const a of agents) {
    try {
      const res = await countHeroesOnAgent(a.agent_id);
      onlineCount++;
      grandTotal += res.total_files || 0;
      perAgent.push({ name: a.name || a.hostname || a.agent_id, total: res.total_files || 0, exists: res.exists });
      const combos = res.combos || {};
      for (const k in combos) comboTotals[k] = (comboTotals[k] || 0) + combos[k];
    } catch (e) {
      perAgent.push({ name: a.name || a.hostname || a.agent_id, error: String(e.message || e) });
    }
  }
  renderDashboard(comboTotals, grandTotal, perAgent, agents.length, onlineCount);
}

function filterHeroCards(q) {
  q = (q || '').trim().toLowerCase();
  let shown = 0;
  document.querySelectorAll('.hero-card, .id-card').forEach(card => {
    const match = !q || (card.dataset.name || '').toLowerCase().includes(q);
    card.style.display = match ? '' : 'none';
    if (match) shown++;
  });
  const noRes = document.getElementById('dashNoResult');
  if (noRes) noRes.style.display = shown === 0 ? '' : 'none';
}

function renderDashboard(comboTotals, grandTotal, perAgent, totalMachines, onlineCount) {
  const content = document.getElementById('contentArea');
  const sorted = Object.keys(comboTotals)
    .map(k => ({ name: k, count: comboTotals[k] }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  const matchedTotal = sorted.reduce((s, h) => s + h.count, 0);
  const cards = sorted.length ? sorted.map(h => `
    <div class="hero-card${h.name.includes('+') ? ' combo' : ''}" data-name="${escHtml(h.name)}">
      <div class="hero-name" title="${escHtml(h.name)}">${escHtml(h.name)}</div>
      <div class="hero-count">${h.count}</div>
    </div>`).join('') : '<div class="empty-state" style="grid-column:1/-1"><div class="icon">📭</div><h3>ไม่พบไฟล์ที่ตรงกับชื่อฮีโร่</h3></div>';
  const agentRows = perAgent.map(p => `
    <div class="agent-stat">
      <span>🖥️ ${escHtml(p.name)}</span>
      <span>${p.error ? '<span style="color:var(--danger)">' + escHtml(p.error) + '</span>' : (p.exists === false ? '<span style="color:var(--warning)">ไม่พบโฟลเดอร์ found-hero</span>' : p.total + ' ไฟล์')}</span>
    </div>`).join('');

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">⚽ Dashboard PES — found-hero</h2>
      ${pcSelectHtml(dashboardScope, 'dashboardScope=this.value; openDashboard()')}
      <input type="text" class="dash-search" placeholder="🔍 ค้นหาชื่อฮีโร่ / combo..." oninput="filterHeroCards(this.value)">
      <button class="btn btn-primary" onclick="openDashboard()">🔄 รีเฟรช</button>
    </div>
    <div class="stat-row">
      <div class="stat-tile"><div class="stat-label">เครื่องทั้งหมด</div><div class="stat-val">${totalMachines}</div></div>
      <div class="stat-tile"><div class="stat-label">ออนไลน์ (ตอบกลับ)</div><div class="stat-val" style="color:var(--success)">${onlineCount}</div></div>
      <div class="stat-tile"><div class="stat-label">ไฟล์ทั้งหมด (ทุกไฟล์)</div><div class="stat-val" style="color:var(--accent)">${grandTotal}</div></div>
      <div class="stat-tile"><div class="stat-label">id ที่ตรงชื่อฮีโร่</div><div class="stat-val" style="color:var(--success)">${matchedTotal}</div></div>
      <div class="stat-tile"><div class="stat-label">จำนวนแบบ (combo)</div><div class="stat-val">${sorted.length}</div></div>
    </div>
    <div class="hero-grid">${cards}</div>
    <div id="dashNoResult" style="display:none; text-align:center; padding:36px; color:var(--text-dim)">🔍 ไม่พบชื่อที่ค้นหา</div>
    <h3 style="margin:24px 0 12px; font-size:14px; color:var(--text-secondary)">รายเครื่อง</h3>
    <div class="agent-stats">${agentRows}</div>
  `;
}

// ═══════════════════════════════════════════════════════════
//  DASHBOARD COOKIE-RUN (ดึงชื่อ id จากโฟลเดอร์ id-found)
// ═══════════════════════════════════════════════════════════
function listIdsOnAgent(agentId) {
  return new Promise((resolve, reject) => {
    let settled = false;
    socket.once('request_sent', (data) => {
      const rid = data.request_id;
      socket.once('response_' + rid, (resp) => {
        settled = true;
        if (resp.error) reject(new Error(resp.error)); else resolve(resp);
      });
    });
    socket.emit('request_list_ids', { agent_id: agentId, subpath: 'id-found', base_match: 'cookie-run' });
    setTimeout(() => { if (!settled) reject(new Error('timeout')); }, 20000);
  });
}

async function openCookieDashboard() {
  currentAgent = null;
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
  const content = document.getElementById('contentArea');
  const allAgents = agentsData || [];
  if (cookieScope !== 'ALL' && !allAgents.some(a => a.agent_id === cookieScope)) cookieScope = 'ALL';
  const agents = cookieScope === 'ALL' ? allAgents : allAgents.filter(a => a.agent_id === cookieScope);

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">🍪 Dashboard Cookie-Run — id-found</h2>
      ${pcSelectHtml(cookieScope, 'cookieScope=this.value; openCookieDashboard()')}
      <button class="btn btn-primary" onclick="openCookieDashboard()">🔄 รีเฟรช</button>
    </div>
    <div class="loading"><div class="spinner"></div>กำลังดึงข้อมูลจาก ${agents.length} เครื่อง...</div>`;

  if (!allAgents.length) {
    content.innerHTML = '<div class="empty-state"><div class="icon">🖥️</div><h3>ยังไม่มีเครื่องลูกออนไลน์</h3></div>';
    return;
  }

  const idMap = {};   // idName -> [machine, ...]
  let grandTotal = 0, onlineCount = 0;
  const perAgent = [];

  for (const a of agents) {
    const mname = a.name || a.hostname || a.agent_id;
    try {
      const res = await listIdsOnAgent(a.agent_id);
      onlineCount++;
      let accepted = 0;
      (res.ids || []).forEach(rawId => {
        const id = String(rawId)
          .replace(/\[[^\]]*\]/g, '')  // ลบส่วน [ ... ] ทั้งก้อน เช่น Trader+[BYSWR6250] -> Trader+
          .replace(/[\[\]]/g, '')       // เก็บกวาดวงเล็บที่ค้างข้างเดียว (ถ้ามี)
          .replace(/\d+$/, '')          // ตัดเลขท้ายออก เช่น +CHNVX1752 -> +CHNVX
          .trim();
        if (!id) return;
        accepted++;
        if (!idMap[id]) idMap[id] = [];
        idMap[id].push(mname);
      });
      grandTotal += accepted;
      perAgent.push({ name: mname, total: accepted, exists: res.exists });
    } catch (e) {
      perAgent.push({ name: mname, error: String(e.message || e) });
    }
  }
  renderCookieDashboard(idMap, grandTotal, perAgent, agents.length, onlineCount);
}

function renderCookieDashboard(idMap, grandTotal, perAgent, totalMachines, onlineCount) {
  const content = document.getElementById('contentArea');
  const ids = Object.keys(idMap).sort((a, b) => a.localeCompare(b));
  const uniqueCount = ids.length;
  const cards = ids.length ? ids.map(id => {
    const machines = idMap[id];
    const uniqMachines = [...new Set(machines)];
    const machineLabel = uniqMachines.join(', ');
    const dup = machines.length > 1;
    return `
    <div class="id-card" data-name="${escHtml(id)}">
      <div class="id-name-big" title="${escHtml(id)}">🍪 ${escHtml(id)}${dup ? `<span class="id-badge">×${machines.length}</span>` : ''}</div>
      <div class="id-machine" title="${escHtml(machineLabel)}">🖥️ ${escHtml(machineLabel)}</div>
    </div>`;
  }).join('') : '<div class="empty-state" style="grid-column:1/-1"><div class="icon">📭</div><h3>ไม่พบ id ในโฟลเดอร์ id-found</h3></div>';

  const agentRows = perAgent.map(p => {
    let status;
    if (p.error) {
      // agent เวอร์ชันเก่า/ยังไม่รองรับ list_ids → ตอบ "Unknown action"
      status = /unknown action/i.test(p.error)
        ? '<span style="color:var(--text-dim)">⚙️ ยังไม่ได้ตั้งค่าเชื่อมโฟลเดอร์</span>'
        : '<span style="color:var(--danger)">' + escHtml(p.error) + '</span>';
    } else if (p.exists === false) {
      status = '<span style="color:var(--warning)">ไม่พบโฟลเดอร์ id-found</span>';
    } else {
      status = p.total + ' id';
    }
    return `
    <div class="agent-stat">
      <span>🖥️ ${escHtml(p.name)}</span>
      <span>${status}</span>
    </div>`;
  }).join('');

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">🍪 Dashboard Cookie-Run — id-found</h2>
      ${pcSelectHtml(cookieScope, 'cookieScope=this.value; openCookieDashboard()')}
      <input type="text" class="dash-search" placeholder="🔍 ค้นหาชื่อ id..." oninput="filterHeroCards(this.value)">
      <button class="btn btn-primary" onclick="openCookieDashboard()">🔄 รีเฟรช</button>
    </div>
    <div class="stat-row">
      <div class="stat-tile"><div class="stat-label">เครื่องทั้งหมด</div><div class="stat-val">${totalMachines}</div></div>
      <div class="stat-tile"><div class="stat-label">ออนไลน์ (ตอบกลับ)</div><div class="stat-val" style="color:var(--success)">${onlineCount}</div></div>
      <div class="stat-tile"><div class="stat-label">id ทั้งหมด</div><div class="stat-val" style="color:var(--accent)">${grandTotal}</div></div>
      <div class="stat-tile"><div class="stat-label">id ไม่ซ้ำ</div><div class="stat-val" style="color:var(--success)">${uniqueCount}</div></div>
    </div>
    <div class="id-grid">${cards}</div>
    <div id="dashNoResult" style="display:none; text-align:center; padding:36px; color:var(--text-dim)">🔍 ไม่พบชื่อที่ค้นหา</div>
    <h3 style="margin:24px 0 12px; font-size:14px; color:var(--text-secondary)">รายเครื่อง</h3>
    <div class="agent-stats">${agentRows}</div>
  `;
}

// ═══════════════════════════════════════════════════════════
//  BROADCAST → input-id (ส่งไฟล์/เคลียร์ ทุกเครื่องพร้อมกัน)
// ═══════════════════════════════════════════════════════════
function openBroadcastInput() {
  currentAgent = null;
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
  const n = (agentsData || []).length;
  document.getElementById('contentArea').innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">📤 ส่งเข้า input-id — เลือกเกม + เครื่อง</h2>
      <select class="btn project-select" id="bcGame" title="เลือกเกมปลายทาง (โฟลเดอร์ input-id ของเกมนั้น)">
        <option value="pes">⚽ PES</option>
        <option value="ro">🗡️ RO</option>
        <option value="cookie-run" selected>🍪 Cookie-Run</option>
      </select>
      <button class="btn" onclick="updateSelectedAgents()" title="ดึงโค้ดใหม่จาก GitHub + รีสตาร์ท agent">⬆️ อัปเดต agent (เครื่องที่เลือก)</button>
      <button class="btn" style="border-color:var(--danger); color:var(--danger)" onclick="clearInputAll()">🗑️ Clear input-id (เครื่องที่เลือก)</button>
    </div>
    <div class="stat-row">
      <div class="stat-tile"><div class="stat-label">เครื่องออนไลน์</div><div class="stat-val" style="color:var(--success)">${n}</div></div>
      <div class="stat-tile"><div class="stat-label">เลือกส่ง</div><div class="stat-val" style="color:var(--accent)"><span id="bcSelCount">${n}</span> เครื่อง</div></div>
    </div>
    <div style="margin: 2px 0 14px">
      <label style="display:inline-flex; align-items:center; gap:8px; font-size:13px; font-weight:700; cursor:pointer; margin-bottom:10px">
        <input type="checkbox" id="bcAll" checked onchange="bcToggleAll(this.checked)"> เลือกทุกเครื่อง
      </label>
      <div id="bcAgents" style="display:flex; flex-wrap:wrap; gap:8px">
        ${(agentsData || []).map(a => `
          <label class="bc-chip">
            <input type="checkbox" class="bc-agent" value="${escAttr(a.agent_id)}" checked onchange="bcSyncAll()">
            🖥️ ${escHtml(a.name || a.hostname || a.agent_id)}
          </label>`).join('') || '<span style="color:var(--text-dim)">ยังไม่มีเครื่องออนไลน์</span>'}
      </div>
    </div>
    <div class="upload-zone" id="bcZone" onclick="document.getElementById('bcFile').click()">
      <div class="icon">📥</div>
      <div>ลากไฟล์มาวางที่นี่ หรือคลิกเพื่อเลือก</div>
      <small>ไฟล์จะถูกส่งเข้าโฟลเดอร์ <b>input-id</b> ของเครื่องที่เลือก พร้อมกัน — สูงสุด __MAX_UPLOAD_MB__MB/ไฟล์</small>
    </div>
    <input type="file" id="bcFile" style="display:none" multiple>
    <h3 style="margin:20px 0 10px; font-size:14px; color:var(--text-secondary)">ผลการทำงาน</h3>
    <div id="bcLog" style="display:flex; flex-direction:column; gap:6px; font-size:13px"></div>`;

  const zone = document.getElementById('bcZone');
  const input = document.getElementById('bcFile');
  input.addEventListener('change', (e) => { broadcastFiles(e.target.files); input.value = ''; });
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('dragover');
    broadcastFiles(e.dataTransfer.files);
  });
  bcSyncAll();
}

function getSelectedAgents() {
  const ids = new Set(Array.from(document.querySelectorAll('.bc-agent:checked')).map(c => c.value));
  return (agentsData || []).filter(a => ids.has(a.agent_id));
}
function getBcGame() {
  const el = document.getElementById('bcGame');
  return el ? el.value : 'cookie-run';
}
function bcToggleAll(checked) {
  document.querySelectorAll('.bc-agent').forEach(c => { c.checked = checked; });
  bcUpdateCount();
}
function bcSyncAll() {
  const all = document.querySelectorAll('.bc-agent').length;
  const checked = document.querySelectorAll('.bc-agent:checked').length;
  const master = document.getElementById('bcAll');
  if (master) master.checked = all > 0 && checked === all;
  bcUpdateCount();
}
function bcUpdateCount() {
  const el = document.getElementById('bcSelCount');
  if (el) el.textContent = document.querySelectorAll('.bc-agent:checked').length;
}

function bcLog(html, danger) {
  const el = document.getElementById('bcLog');
  if (!el) return;
  const row = document.createElement('div');
  row.style.cssText = 'padding:8px 12px; background:var(--bg-card); border:1px solid var(--border); border-radius:8px' + (danger ? '; color:var(--danger)' : '');
  row.innerHTML = html;
  el.insertBefore(row, el.firstChild);
}

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = (e) => resolve(e.target.result.split(',')[1]);
    r.onerror = () => reject(new Error('read error'));
    r.readAsDataURL(file);
  });
}

// อัปโหลด 1 ไฟล์ไปเครื่องเดียว โดยให้ agent วางในโฟลเดอร์ <game>/input-id เอง (base_match+subpath)
function uploadToInput(agentId, filename, size, base64, game) {
  return new Promise((resolve, reject) => {
    const uploadId = 'bc_' + Math.random().toString(36).substr(2, 9);
    const CHUNK = 512 * 1024;
    const total = Math.ceil(base64.length / CHUNK) || 1;
    let done = false;
    const cleanup = () => socket.off('upload_ready', onReady);
    const timer = setTimeout(() => { if (!done) { cleanup(); reject(new Error('timeout')); } }, 30000);

    function onReady(info) {
      if (info.upload_id !== uploadId) return;
      cleanup();
      const rid = info.request_id;
      const onResp = (resp) => {
        socket.off('response_' + rid, onResp);
        done = true; clearTimeout(timer);
        if (resp.error) reject(new Error(resp.error)); else resolve(resp);
      };
      socket.on('response_' + rid, onResp);
      for (let i = 0; i < total; i++) {
        socket.emit('request_upload_chunk', {
          agent_id: agentId, request_id: rid,
          data: base64.slice(i * CHUNK, (i + 1) * CHUNK),
          is_last: (i === total - 1),
        });
      }
    }
    socket.on('upload_ready', onReady);
    socket.emit('request_upload_start', {
      agent_id: agentId, upload_id: uploadId,
      filename: filename, file_size: size, dest_path: filename,
      base_match: game, subpath: 'input-id',
    });
  });
}

async function broadcastFiles(fileList) {
  const files = Array.from(fileList || []);
  const agents = getSelectedAgents();
  const game = getBcGame();
  if (!agents.length) { toast('ยังไม่ได้เลือกเครื่อง (ติ๊กเครื่องที่จะส่งก่อน)', 'error'); return; }
  if (!files.length) return;

  for (const file of files) {
    if (file.size > __MAX_UPLOAD_MB__ * 1024 * 1024) {
      toast(file.name + ' ใหญ่เกิน __MAX_UPLOAD_MB__MB', 'error');
      continue;
    }
    let base64;
    try { base64 = await readAsBase64(file); }
    catch (e) { bcLog('❌ อ่านไฟล์ ' + escHtml(file.name) + ' ไม่ได้', true); continue; }

    let ok = 0; const fails = [];
    await Promise.all(agents.map(a => {
      const mname = a.name || a.hostname || a.agent_id;
      return uploadToInput(a.agent_id, file.name, file.size, base64, game)
        .then(() => { ok++; })
        .catch(e => { fails.push(mname + ': ' + (e.message || e)); });
    }));
    const failHtml = fails.length ? ' <span style="color:var(--danger)">❌ ' + fails.length + '</span>' : '';
    bcLog(`📎 <b>${escHtml(file.name)}</b> → [${escHtml(game)}] ✅ ส่งเข้า input-id สำเร็จ ${ok}/${agents.length} เครื่อง${failHtml}` +
          (fails.length ? '<br><small style="color:var(--text-dim)">' + escHtml(fails.join(' | ')) + '</small>' : ''), false);
    toast(`ส่ง ${file.name} → ${ok}/${agents.length} เครื่อง`, fails.length ? 'error' : 'success');
  }
}

// ถาม path จริงของไฟล์ใน <game>/input-id (ใช้ list_ids ที่คืน entries = full path)
function listInputEntries(agentId, game) {
  return new Promise((resolve, reject) => {
    let settled = false;
    socket.once('request_sent', (data) => {
      const rid = data.request_id;
      socket.once('response_' + rid, (resp) => {
        settled = true;
        if (resp.error) reject(new Error(resp.error)); else resolve(resp);
      });
    });
    socket.emit('request_list_ids', { agent_id: agentId, subpath: 'input-id', base_match: game });
    setTimeout(() => { if (!settled) reject(new Error('timeout')); }, 20000);
  });
}

// ลบหลายไฟล์ด้วยกลไก "ลบปกติ" เดียวกับ file browser (request_delete_many)
function deleteManyOnAgent(agentId, paths) {
  return new Promise((resolve, reject) => {
    let settled = false;
    socket.once('request_sent', (data) => {
      const rid = data.request_id;
      socket.once('response_' + rid, (resp) => {
        settled = true;
        if (resp.error) reject(new Error(resp.error)); else resolve(resp);
      });
    });
    socket.emit('request_delete_many', { agent_id: agentId, paths: paths });
    setTimeout(() => { if (!settled) reject(new Error('timeout')); }, 30000);
  });
}

async function clearInputAll() {
  const agents = getSelectedAgents();
  const game = getBcGame();
  if (!agents.length) { toast('ยังไม่ได้เลือกเครื่อง (ติ๊กเครื่องก่อน)', 'error'); return; }
  if (!confirm(`⚠️ ลบข้อมูลทั้งหมดในโฟลเดอร์ ${game}\\input-id ของเครื่องที่เลือก (${agents.length} เครื่อง) ?\n\nการลบนี้กู้คืนไม่ได้`)) return;

  // ทำทีละเครื่อง: ถาม path จริง → ลบด้วย request_delete_many (ตัวลบปกติ)
  let okMachines = 0, totalDeleted = 0;
  for (const a of agents) {
    const mname = a.name || a.hostname || a.agent_id;
    try {
      const info = await listInputEntries(a.agent_id, game);
      if (info.exists === false) {
        bcLog(`🗑️ <b>${escHtml(mname)}</b> → <span style="color:var(--warning)">ไม่พบโฟลเดอร์ input-id</span>`, false);
        continue;
      }
      const paths = info.entries || [];
      if (!paths.length) {
        okMachines++;
        bcLog(`🗑️ <b>${escHtml(mname)}</b> → ว่างอยู่แล้ว (0 รายการ)`, false);
        continue;
      }
      const res = await deleteManyOnAgent(a.agent_id, paths);
      okMachines++; totalDeleted += (res.deleted || 0);
      bcLog(`🗑️ <b>${escHtml(mname)}</b> → ลบ ${res.deleted || 0} รายการ` +
            (res.failed ? ` <span style="color:var(--warning)">(พลาด ${res.failed})</span>` : ''), false);
    } catch (e) {
      bcLog(`❌ <b>${escHtml(mname)}</b> → ${escHtml(String(e.message || e))}`, true);
    }
  }
  toast(`เคลียร์ input-id เสร็จ: ${okMachines}/${agents.length} เครื่อง (ลบรวม ${totalDeleted})`, 'success');
}

// ── อัปเดต agent ทางไกล (ดึงโค้ดใหม่ + รีสตาร์ท) ──
function updateOneAgent(agentId) {
  return new Promise((resolve, reject) => {
    let settled = false;
    socket.once('request_sent', (data) => {
      const rid = data.request_id;
      socket.once('response_' + rid, (resp) => {
        settled = true;
        if (resp.error) reject(new Error(resp.error)); else resolve(resp);
      });
    });
    socket.emit('request_self_update', { agent_id: agentId });
    setTimeout(() => { if (!settled) reject(new Error('timeout (เครื่องอาจหลุด/ปิดไปแล้ว)')); }, 25000);
  });
}

async function updateSelectedAgents() {
  const agents = getSelectedAgents();
  if (!agents.length) { toast('ยังไม่ได้เลือกเครื่อง (ติ๊กเครื่องก่อน)', 'error'); return; }
  if (!confirm(`สั่งอัปเดต agent + รีสตาร์ท ${agents.length} เครื่องที่เลือก ?\n\nแต่ละเครื่องจะดึงโค้ดใหม่จาก GitHub แล้วรีสตาร์ทตัวเอง (หลุดแล้วกลับมาเองในไม่กี่วิ)`)) return;

  let ok = 0;
  for (const a of agents) {
    const mname = a.name || a.hostname || a.agent_id;
    try {
      const res = await updateOneAgent(a.agent_id);
      ok++;
      if (res && res.updated === false) {
        bcLog(`✅ <b>${escHtml(mname)}</b> → เป็นเวอร์ชันล่าสุดอยู่แล้ว (ไม่ต้องอัปเดต)`, false);
      } else {
        bcLog(`⬆️ <b>${escHtml(mname)}</b> → มีของใหม่! กำลังอัปเดต + รีสตาร์ท...`, false);
      }
    } catch (e) {
      const msg = String(e.message || e);
      const isOld = /unknown action/i.test(msg);
      bcLog(`❌ <b>${escHtml(mname)}</b> → ${isOld ? 'agent เก่ายังไม่รองรับ (ต้องอัปเดตด้วยมือครั้งแรกก่อน)' : escHtml(msg)}`, true);
    }
  }
  toast(`เช็กอัปเดต ${ok}/${agents.length} เครื่องเสร็จ`, 'success');
}

// ═══════════════════════════════════════════════════════════
//  LIVE VIEW / PC MONITOR (ดูหน้าจอเครื่องลูกแบบสด)
// ═══════════════════════════════════════════════════════════
let liveScope = 'ALL';
let livePage = 0;
let liveGen = 0;              // generation กันมี loop ซ้อนกัน
const LIVE_PAGE_SIZE = 6;

function liveFilteredAgents() {
  const all = agentsData || [];
  return liveScope === 'ALL' ? all : all.filter(a => a.agent_id === liveScope);
}
function liveCurrentPageAgents() {
  const agents = liveFilteredAgents();
  const totalPages = Math.max(1, Math.ceil(agents.length / LIVE_PAGE_SIZE));
  if (livePage >= totalPages) livePage = totalPages - 1;
  if (livePage < 0) livePage = 0;
  return agents.slice(livePage * LIVE_PAGE_SIZE, (livePage + 1) * LIVE_PAGE_SIZE);
}

function openLiveView() {
  currentAgent = null;
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
  livePage = 0;
  renderLiveView();
  const gen = ++liveGen;
  liveLoop(gen);
}

function renderLiveView() {
  const content = document.getElementById('contentArea');
  const all = liveFilteredAgents();
  const totalPages = Math.max(1, Math.ceil(all.length / LIVE_PAGE_SIZE));
  if (livePage >= totalPages) livePage = totalPages - 1;
  const pageAgents = liveCurrentPageAgents();

  const options = `<option value="ALL"${liveScope === 'ALL' ? ' selected' : ''}>🖥️ All PCs</option>` +
    (agentsData || []).map(a => `<option value="${escAttr(a.agent_id)}"${liveScope === a.agent_id ? ' selected' : ''}>🖥️ ${escHtml(a.name || a.hostname || a.agent_id)}</option>`).join('');

  const cards = pageAgents.length ? pageAgents.map(a => {
    const mname = a.name || a.hostname || a.agent_id;
    const aid = escAttr(a.agent_id);
    return `
    <div class="pc-tile" onclick="liveToggleZoom('${aid}')" title="คลิกเพื่อซูม/ยกเลิกซูม">
      <div class="pc-shot">
        <img data-aid="${aid}" alt="${escHtml(mname)}">
        <div class="pc-noimg" data-noimg="${aid}">⏳ กำลังโหลดภาพ...</div>
      </div>
      <div class="pc-tile-bar">
        <span>🖥️ ${escHtml(mname)}</span>
        <span class="pc-live-dot" data-st="${aid}">●</span>
      </div>
    </div>`;
  }).join('') : '<div class="empty-state" style="grid-column:1/-1"><div class="icon">🖥️</div><h3>No PCs found</h3><p>ยังไม่มีเครื่องลูกออนไลน์</p></div>';

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">🖥️ PC Monitor — Live View
        <span style="color:var(--text-dim); font-weight:400; font-size:13px">(${all.length} PCs)</span></h2>
      <select class="btn project-select" onchange="liveScope=this.value; livePage=0; renderLiveView()">${options}</select>
    </div>
    <div class="pc-grid${liveScope !== 'ALL' ? ' single' : ''}" id="pcGrid">${cards}</div>
    <div class="pc-pager">
      <button class="btn" onclick="livePage=Math.max(0,livePage-1); renderLiveView()" ${livePage === 0 ? 'disabled' : ''}>‹</button>
      <span>Page ${livePage + 1} of ${totalPages}</span>
      <button class="btn" onclick="livePage=Math.min(${totalPages - 1},livePage+1); renderLiveView()" ${livePage >= totalPages - 1 ? 'disabled' : ''}>›</button>
    </div>`;
}

function liveToggleZoom(aid) {
  liveScope = (liveScope === aid) ? 'ALL' : aid;
  livePage = 0;
  renderLiveView();
}

function screenshotOnAgent(agentId) {
  return new Promise((resolve, reject) => {
    let settled = false, rid = null, onResp = null;
    const onSent = (data) => {
      rid = data.request_id;
      onResp = (resp) => {
        if (settled) return;
        settled = true;
        socket.off('response_' + rid, onResp);
        if (resp.error) reject(new Error(resp.error)); else resolve(resp);
      };
      socket.once('response_' + rid, onResp);
    };
    socket.once('request_sent', onSent);
    socket.emit('request_screenshot', { agent_id: agentId, width: 720, quality: 55 });
    setTimeout(() => {
      if (settled) return;
      settled = true;
      socket.off('request_sent', onSent);                 // เก็บกวาด listener กัน browser บวม
      if (rid && onResp) socket.off('response_' + rid, onResp);
      reject(new Error('timeout'));
    }, 12000);
  });
}

// ลูปรีเฟรชภาพ: ทำทีละเครื่อง (กัน request_sent ชนกัน) แล้ววนใหม่ หยุดเองเมื่อออกจากหน้า
async function liveLoop(gen) {
  while (gen === liveGen && document.getElementById('pcGrid')) {
    const agents = liveCurrentPageAgents();
    if (!agents.length) { await _sleep(1000); continue; }
    for (const a of agents) {
      if (gen !== liveGen || !document.getElementById('pcGrid')) return;
      const st = document.querySelector('#pcGrid [data-st="' + cssEsc(a.agent_id) + '"]');
      try {
        const res = await screenshotOnAgent(a.agent_id);
        if (gen !== liveGen) return;
        const img = document.querySelector('#pcGrid img[data-aid="' + cssEsc(a.agent_id) + '"]');
        const no = document.querySelector('#pcGrid [data-noimg="' + cssEsc(a.agent_id) + '"]');
        if (img && res.image) {
          img.src = 'data:image/jpeg;base64,' + res.image;
          img.style.display = 'block';
          if (no) no.style.display = 'none';
          if (st) { st.textContent = '🟢 live'; st.style.color = 'var(--success)'; }
        }
      } catch (e) {
        if (st) { st.textContent = '⚠️ ' + String(e.message || e).slice(0, 24); st.style.color = 'var(--warning)'; }
        const no = document.querySelector('#pcGrid [data-noimg="' + cssEsc(a.agent_id) + '"]');
        if (no) no.textContent = '⚠️ ดูภาพไม่ได้';
      }
    }
    await _sleep(500);   // เว้นจังหวะก่อนรอบใหม่
  }
}

function _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function cssEsc(s) { return String(s).replace(/["\\]/g, '\\$&'); }

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
      <h3>🖥️ ${escHtml(a.name || a.hostname)}</h3>
      <div class="meta">${escHtml(a.name ? a.hostname : a.agent_id)}</div>
      <div class="meta">${escHtml(a.ip)}</div>
      <div class="meta" style="margin-top:4px; color: var(--text-dim)">${escHtml(a.os_info)}</div>
      <button class="power-btn" title="ปิดโปรแกรม agent ที่เครื่องนี้"
              onclick="event.stopPropagation(); shutdownAgent('${escAttr(a.agent_id)}','${escAttr(a.name || a.hostname)}')">⏻</button>
    </div>
  `).join('');
}

function shutdownAgent(agentId, name) {
  if (!confirm(`ปิดโปรแกรม agent ที่เครื่อง "${name}" ?\n\n⚠️ เครื่องนี้จะหลุดการเชื่อมต่อทันที และจะกลับมาก็ต่อเมื่อเปิด agent ใหม่ที่เครื่องนั้นเอง`)) return;
  socket.emit('request_shutdown', { agent_id: agentId });
  toast('⏻ ส่งคำสั่งปิด agent: ' + name, 'success');
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
  const agent = agentsData.find(a => a.agent_id === currentAgent);
  const allowed = (agent && agent.allowed_paths) || [];

  // Sort: folders first, then by name
  files.sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  const breadcrumb = buildBreadcrumb(path);
  const projectSelect = allowed.length ? `
      <select class="btn project-select" onchange="if(this.value) loadDir(this.value)" title="เลือกโปรเจกต์/โฟลเดอร์">
        ${allowed.map(p => `<option value="${escHtml(p)}" ${sameRoot(path, p) ? 'selected' : ''}>📁 ${escHtml(baseName(p))}</option>`).join('')}
      </select>` : '';

  content.innerHTML = `
    <div class="toolbar">
      ${projectSelect}
      <div class="breadcrumb">${breadcrumb}</div>
      <button class="btn" onclick="downloadSelected()">💾 โหลดที่เลือก</button>
      <button class="btn btn-danger" onclick="deleteSelected()">🗑️ ลบที่เลือก</button>
      <button class="btn" onclick="loadDir(currentPath)">🔄 รีเฟรช</button>
      <button class="btn btn-primary" onclick="openUpload()">📤 อัปโหลด</button>
    </div>
    ${files.length === 0 ? '<div class="empty-state"><div class="icon">📭</div><h3>โฟลเดอร์ว่าง</h3></div>' : `
    <table class="file-table">
      <thead>
        <tr>
          <th style="width:36px; text-align:center"><input type="checkbox" id="selectAll" onclick="toggleSelectAll(this)" title="เลือกทั้งหมด"></th>
          <th style="width:46%">ชื่อ</th>
          <th>ขนาด</th>
          <th>แก้ไขล่าสุด</th>
          <th style="width:140px"></th>
        </tr>
      </thead>
      <tbody>
        ${files.map((f, i) => `
          <tr ondblclick="${f.is_dir ? `loadDir('${escAttr(f.full_path)}')` : `downloadFile('${escAttr(f.full_path)}', '${escAttr(f.name)}')`}">
            <td style="text-align:center" onclick="event.stopPropagation()" ondblclick="event.stopPropagation()">
              ${f.name === '..' ? '' : `<input type="checkbox" class="file-check" data-index="${i}">`}
            </td>
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

function baseName(p) {
  return (p || '').split(/[\\\/]/).filter(Boolean).pop() || p;
}
function sameRoot(path, base) {
  if (!path || !base) return false;
  const np = path.replace(/\\/g, '/').toLowerCase();
  const nb = base.replace(/\\/g, '/').toLowerCase();
  return np === nb || np.indexOf(nb + '/') === 0;
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

// ── Bulk select / delete / download ──
function toggleSelectAll(cb) {
  document.querySelectorAll('.file-check').forEach(x => x.checked = cb.checked);
}

function getSelectedFiles() {
  const sel = [];
  document.querySelectorAll('.file-check:checked').forEach(cb => {
    const f = currentFiles[parseInt(cb.dataset.index)];
    if (f) sel.push(f);
  });
  return sel;
}

function deleteSelected() {
  const sel = getSelectedFiles();
  if (!sel.length) { toast('ยังไม่ได้เลือกไฟล์', 'info'); return; }
  if (!confirm(`ต้องการลบ ${sel.length} รายการที่เลือกจริงหรือไม่?\n\n⚠️ การลบไม่สามารถกู้คืนได้`)) return;

  const paths = sel.map(f => f.full_path);
  toast(`กำลังลบ ${paths.length} รายการ...`, 'info');

  // ส่งคำสั่งเดียว ให้ agent ลบทั้งหมดในเครื่อง (เร็วกว่าลบทีละไฟล์มาก)
  socket.once('request_sent', (data) => {
    socket.on('response_' + data.request_id, (resp) => {
      socket.off('response_' + data.request_id);
      if (resp.error) {
        toast('ลบล้มเหลว: ' + resp.error, 'error');
      } else {
        toast(`ลบแล้ว ${resp.deleted} รายการ` + (resp.failed ? `, ล้มเหลว ${resp.failed}` : ''), resp.failed ? 'error' : 'success');
        loadDir(currentPath);
      }
    });
  });
  socket.emit('request_delete_many', { agent_id: currentAgent, paths: paths });
}

// ดึง bytes ของไฟล์เดียว (ใช้สำหรับรวมเป็น zip) - เรียกทีละไฟล์เท่านั้น
function fetchFileBytes(filePath) {
  return new Promise((resolve, reject) => {
    socket.once('request_sent', (data) => {
      const rid = data.request_id;
      let chunks = [];
      const onChunk = (chunk) => {
        if (chunk.error) { socket.off('file_chunk_' + rid, onChunk); reject(new Error(chunk.error)); return; }
        chunks.push(chunk.data);
        if (chunk.is_last) {
          socket.off('file_chunk_' + rid, onChunk);
          const bin = atob(chunks.join(''));
          const arr = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
          resolve(arr);
        }
      };
      socket.on('file_chunk_' + rid, onChunk);
      socket.once('response_' + rid, (resp) => {
        if (resp.error) { socket.off('file_chunk_' + rid, onChunk); reject(new Error(resp.error)); }
      });
    });
    socket.emit('request_download', { agent_id: currentAgent, path: filePath });
  });
}

async function downloadSelected() {
  const all = getSelectedFiles();
  const files = all.filter(f => !f.is_dir);
  const skippedDirs = all.length - files.length;
  if (!files.length) { toast('เลือกไฟล์ (ไม่ใช่โฟลเดอร์) ที่จะดาวน์โหลดก่อน', 'info'); return; }

  // ถ้ามีไฟล์เดียว → โหลดไฟล์นั้นตรงๆ ไม่ต้องห่อ zip
  if (files.length === 1) { downloadFile(files[0].full_path, files[0].name); return; }

  if (typeof JSZip === 'undefined') {
    toast('โหลดตัวบีบอัด (JSZip) ไม่ได้ - ดาวน์โหลดแยกไฟล์แทน', 'error');
    files.forEach(f => downloadFile(f.full_path, f.name));
    return;
  }

  toast(`กำลังรวม ${files.length} ไฟล์เป็น zip...`, 'info');
  const zip = new JSZip();
  let ok = 0, fail = 0;
  for (const f of files) {
    try {
      zip.file(f.name, await fetchFileBytes(f.full_path));
      ok++;
    } catch (e) {
      fail++;
    }
  }
  if (!ok) { toast('ดึงไฟล์ไม่สำเร็จ', 'error'); return; }

  const folderName = (currentPath.split(/[\\\/]/).filter(Boolean).pop() || 'download').replace(/[:*?"<>|]/g, '_');
  const blob = await zip.generateAsync({ type: 'blob' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = folderName + '.zip';
  a.click();
  URL.revokeObjectURL(url);
  toast(`ดาวน์โหลด ${folderName}.zip สำเร็จ (${ok} ไฟล์` + (fail ? `, พลาด ${fail}` : '') + (skippedDirs ? `, ข้ามโฟลเดอร์ ${skippedDirs}` : '') + ')', fail ? 'error' : 'success');
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
