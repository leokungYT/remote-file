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

from flask import Flask, render_template_string, request, send_file, jsonify, session, make_response
from flask_socketio import SocketIO, emit, join_room, leave_room

# ─── CONFIG ───────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-to-a-strong-secret-key")
AGENT_SECRET = os.environ.get("AGENT_SECRET", "my-agent-secret-2024")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "5000"))
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "./received_files")
MAX_CHUNK_SIZE = 1024 * 1024  # 1MB chunks for file transfer
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "500"))  # ขนาดไฟล์อัปโหลดสูงสุด (MB)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# เวลาที่ไฟล์ server.py ถูกเขียนล่าสุด → ใช้เป็น "build stamp" โชว์บนหน้าเว็บ
# (autoupdate-file.bat เขียนทับไฟล์ = mtime เปลี่ยน → เช็กได้ว่า deploy โค้ดใหม่แล้วหรือยัง)
try:
    APP_BUILD = datetime.fromtimestamp(os.path.getmtime(os.path.abspath(__file__))).strftime("%Y-%m-%d %H:%M:%S")
except Exception:
    APP_BUILD = "unknown"

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
    new_name = data.get("name", "")

    # ลบเฉพาะ connection เก่าที่เป็น "เครื่องเดียวกันจริง" (agent_id + name ตรงกัน) กัน zombie
    # แต่ไม่เตะเครื่องคนละชื่อที่ hostname/agent_id บังเอิญซ้ำกันออก
    stale_sids = [sid for sid, info in agents.items()
                  if info.get("agent_id") == agent_id and info.get("name", "") == new_name
                  and sid != request.sid]
    for sid in stale_sids:
        agents.pop(sid, None)
        logger.info(f"🧹 แทนที่ connection เก่าของ {agent_id}/{new_name} (sid {sid[:6]}…)")

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


@socketio.on("request_count_prefix_ids")
def handle_count_prefix_ids(data):
    """ขอให้เครื่องลูกนับ id ตามชื่อฮีโร่หน้าชื่อไฟล์ (Line Ranger — backup-id)"""
    req_id = send_to_agent(data["agent_id"], "count_prefix_ids", {
        "subpath": data.get("subpath", "backup-id"),
        "base_match": data.get("base_match", "main"),
        "exts": data.get("exts", []),
        "by": data.get("by", "filename"),
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


@socketio.on("request_mumu")
def handle_mumu_req(data):
    """สั่งเปิด/ปิด/ดึงรายชื่อ MuMu instance ที่เครื่องลูก"""
    req_id = send_to_agent(data["agent_id"], "mumu_control", {
        "sub": data.get("sub"),
        "indices": data.get("indices", []),
    }, request.sid)
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
    html = render_template_string(
        WEB_UI_HTML
        .replace("__MAX_UPLOAD_MB__", str(MAX_UPLOAD_MB))
        .replace("__APP_BUILD__", APP_BUILD)
    )
    # ห้าม browser cache หน้านี้ — ไม่งั้นแก้โค้ด+รีสตาร์ทแล้วยังเห็นหน้าเก่า ต้องคอยกด Ctrl+F5 เอง
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# ── รูปตัวละคร Line Ranger (img/ranger) ─────────────────────
# ลำดับที่มองหา: env HERO_IMG_PATH > img/ranger ข้างๆ server.py > โฟลเดอร์ LGR/main ที่พบบ่อย
_HERO_IMG_CACHE = {"dir": None, "index": None}


def _hero_img_dir():
    """หาโฟลเดอร์รูปตัวละคร (จำผลไว้ ไม่ต้องไล่หาทุก request)"""
    if _HERO_IMG_CACHE["dir"] and os.path.isdir(_HERO_IMG_CACHE["dir"]):
        return _HERO_IMG_CACHE["dir"]
    here = os.path.dirname(os.path.abspath(__file__))
    home = os.path.expanduser("~")
    cands = []
    env = os.environ.get("HERO_IMG_PATH", "").strip()
    if env:
        cands.append(env)
    cands += [
        os.path.join(here, "img", "ranger"),
        os.path.join(home, "Downloads", "LGR", "img", "ranger"),
        os.path.join(home, "Desktop", "LGR", "img", "ranger"),
        os.path.join(home, "Desktop", "main", "img", "ranger"),
    ]
    for c in cands:
        if c and os.path.isdir(c):
            _HERO_IMG_CACHE["dir"] = c
            _HERO_IMG_CACHE["index"] = None
            return c
    return None


def _hero_img_index(folder):
    """map ชื่อตัวพิมพ์เล็ก -> ชื่อไฟล์จริง (เทียบชื่อแบบไม่สนพิมพ์เล็ก/ใหญ่)"""
    if _HERO_IMG_CACHE["index"] is not None:
        return _HERO_IMG_CACHE["index"]
    idx = {}
    try:
        for fn in os.listdir(folder):
            stem, ext = os.path.splitext(fn)
            if ext.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                idx.setdefault(stem.lower(), fn)
    except Exception:
        pass
    _HERO_IMG_CACHE["index"] = idx
    return idx


@app.route("/hero-img/<path:name>")
def serve_hero_img(name):
    """ส่งรูปตัวละครตามชื่อ เช่น /hero-img/Kafka -> img/ranger/Kafka.png"""
    folder = _hero_img_dir()
    if not folder:
        return ("", 404)
    stem = os.path.splitext(os.path.basename(name))[0].strip()   # basename กัน path traversal
    if not stem:
        return ("", 404)
    fn = _hero_img_index(folder).get(stem.lower())
    if not fn:
        return ("", 404)
    full = os.path.join(folder, fn)
    if not os.path.isfile(full):
        return ("", 404)
    resp = make_response(send_file(full))
    resp.headers["Cache-Control"] = "public, max-age=86400"   # รูปแทบไม่เปลี่ยน ให้ browser cache ไว้
    return resp


@app.route("/hero-img-list")
def serve_hero_img_list():
    """บอกว่ามีรูปตัวไหนบ้าง (ไว้เช็คตอนตั้งค่า)"""
    folder = _hero_img_dir()
    if not folder:
        return jsonify({"folder": None, "names": []})
    return jsonify({"folder": folder, "names": sorted(_hero_img_index(folder).values())})


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
    flex-wrap: wrap;
    gap: 12px;
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
    flex-shrink: 0;        /* ห้ามบีบชื่อจนตกบรรทัด — ให้แถวปุ่มตัดบรรทัดแทน */
    white-space: nowrap;
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
  /* ตัวแบ่งหน้าของ sidebar — เครื่องเยอะจะได้ไม่ต้องเลื่อนยาว */
  .agent-pager {
    display: flex; flex-wrap: wrap; gap: 4px; align-items: center;
    margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid var(--border);
  }
  .agent-pager .btn { padding: 5px 9px; font-size: 11px; min-width: 28px; }
  .agent-pager .btn.active { border-color: var(--accent); background: var(--accent); color: #fff; font-weight: 700; }
  .agent-pager .ap-info { font-size: 11px; color: var(--text-dim); margin-left: auto; }
  /* ป้ายบอกว่าโปรเจกต์ถูกล็อกไว้ใช้กับทุกเครื่อง */
  .proj-lock {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 10px; border-radius: 8px; font-size: 12px; font-weight: 600;
    border: 1px solid rgba(16,185,129,0.45); background: rgba(16,185,129,0.08); color: var(--success);
    white-space: nowrap;
  }
  .proj-lock button {
    background: none; border: none; color: var(--text-dim); cursor: pointer;
    font-size: 13px; padding: 0 2px; line-height: 1;
  }
  .proj-lock button:hover { color: var(--danger); }
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

  .file-count {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 14px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--bg-card);
    color: var(--text-secondary);
    font-size: 13px;
    white-space: nowrap;
  }
  .file-count b { color: var(--accent); font-size: 15px; }
  .file-count .dim { color: var(--text-dim); }
  .file-count.sel-chip { border-color: var(--accent); background: rgba(59,130,246,0.12); color: var(--accent); }
  .file-count.sel-chip b { color: var(--accent); }
  .select-n { display: flex; align-items: center; gap: 6px; }
  .select-n input {
    width: 100px; padding: 8px 10px; background: var(--bg-card);
    border: 1px solid var(--border); border-radius: 8px;
    color: var(--text-primary); font-family: inherit; font-size: 13px;
  }
  .select-n input:focus { outline: none; border-color: var(--accent); }

  /* ── ลากคลุมเลือกไฟล์ (drag-to-select) ── */
  .file-table { user-select: none; -webkit-user-select: none; }  /* กันลากแล้วไปเลือกข้อความแทนติ๊กไฟล์ */
  body.no-select, body.no-select * { user-select: none !important; -webkit-user-select: none !important; }
  .file-table tbody tr[data-row] { cursor: default; }
  .file-table tbody tr:has(.file-check:checked) { background: rgba(59,130,246,0.12); }

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
  select.modal-select {
    width: 100%;
    padding: 10px 14px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text-primary);
    font-family: inherit;
    font-size: 14px;
  }
  select.modal-select option { background: var(--bg-secondary); color: var(--text-primary); }
  .modal label.fld { font-size: 13px; display: block; margin-bottom: 6px; color: var(--text-secondary); }
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
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 12px;
  }
  /* จอกว้าง = 5 คอลัมน์ → 10 จอพอดี 2 แถวเต็มความกว้าง (ไม่เหลือที่ว่างข้างขวา)
     .pc-grid.single มี 2 class เลย specificity สูงกว่า ทับ media query ตอนซูมเครื่องเดียวได้เอง */
  @media (min-width: 1600px) { .pc-grid { grid-template-columns: repeat(5, 1fr); } }
  @media (min-width: 1150px) and (max-width: 1599px) { .pc-grid { grid-template-columns: repeat(4, 1fr); } }
  @media (min-width: 820px) and (max-width: 1149px) { .pc-grid { grid-template-columns: repeat(3, 1fr); } }
  /* ปุ่มเลือกหน้าบนแถบบน — จะได้ไม่ต้องเลื่อนลงไปกดข้างล่างตอนเครื่องเยอะ */
  .live-pages { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
  .live-page-btn { padding: 6px 11px; font-size: 12px; min-width: 34px; }
  .live-page-btn.active { border-color: var(--accent); background: var(--accent); color: #fff; font-weight: 700; }
  /* zoom เครื่องเดียว: จัดกึ่งกลาง + จำกัดความกว้าง ให้ภาพพอดีความสูงจอ ไม่ต้องเลื่อน */
  .pc-grid.single { grid-template-columns: 1fr; max-width: 1500px; margin: 0 auto; }
  .pc-grid.single .pc-shot { aspect-ratio: auto; height: calc(100vh - 240px); min-height: 300px; }
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
    position: relative;
    transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
  }
  /* รูปตัวละครมุมขวาบน — combo หลายตัวจะซ้อนกันแบบไพ่ ไม่กินที่ */
  .hero-imgs { position: absolute; top: 7px; right: 8px; display: flex; pointer-events: none; }
  .hero-img {
    width: 25px; height: 25px; object-fit: cover; border-radius: 50%;
    border: 1px solid var(--border); background: var(--bg-secondary);
    box-shadow: 0 1px 4px rgba(0,0,0,.45);
    position: relative;      /* ให้ z-index ที่ตั้งจาก JS ทำงาน รูปซ้ายทับรูปขวา */
    flex: 0 0 auto;
  }
  .hero-card.with-img .hero-name { padding-right: 80px; }
  /* การ์ดที่กดดูรายละเอียดได้ */
  .hero-card.clickable { cursor: pointer; }
  .hero-card.clickable:active { transform: translateY(0) scale(0.985); }
  .hero-card.clickable::after {
    content: '›'; position: absolute; right: 9px; bottom: 5px;
    font-size: 15px; color: var(--text-dim); opacity: 0; transition: opacity .15s;
  }
  .hero-card.clickable:hover::after { opacity: 1; }

  /* การ์ดใหญ่ — ชื่อ combo ยาวๆ จะได้ไม่โดนตัดเหลือ "kappa+kukuru+..." */
  .hero-grid.big { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 11px; }
  .hero-grid.big .hero-card { min-height: 86px; padding: 12px 14px; }
  .hero-grid.big .hero-name { font-size: 13px; -webkit-line-clamp: 3; }
  .hero-grid.big .hero-count { font-size: 25px; }
  .hero-grid.big .hero-card.with-img .hero-name { padding-right: 82px; }
  @media (max-width: 620px) {
    .hero-grid.big { grid-template-columns: repeat(auto-fill, minmax(152px, 1fr)); gap: 8px; }
    .hero-grid.big .hero-count { font-size: 20px; }
  }

  /* ตัวแบ่งหน้าของ grid การ์ด */
  .grid-pager { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; margin: 0 0 10px; }
  .grid-pager .btn { padding: 5px 10px; font-size: 12px; min-width: 30px; }
  .grid-pager .btn.active { border-color: var(--accent); background: var(--accent); color: #fff; font-weight: 700; }
  .grid-pager .gp-info { font-size: 12px; color: var(--text-dim); margin-left: auto; }
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
  /* ชื่อหลัก (Line Ranger) — เน้นเขียวให้ต่างจากชื่ออื่นที่เจอในไฟล์ */
  .hero-card.main-name { border-color: rgba(16,185,129,0.4); background: linear-gradient(140deg, var(--bg-card), rgba(16,185,129,0.06)); }
  .hero-card.main-name .hero-count { color: var(--success); }
  .hero-sub { font-size: 10px; color: var(--text-dim); margin-top: 2px; }

  /* ── ตัวกรองชื่อตัว (ติ๊กเลือกจากชื่อที่เจอจริง) ── */
  .pick-panel {
    border: 1px solid var(--border); background: var(--bg-card);
    border-radius: 12px; padding: 12px 14px; margin-bottom: 18px;
  }
  .pick-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
  .pick-head .btn { padding: 5px 11px; font-size: 12px; }
  .pick-title { font-size: 13px; font-weight: 700; color: var(--text-secondary); margin-right: auto; }
  .pick-list { display: flex; flex-wrap: wrap; gap: 6px; max-height: 190px; overflow-y: auto; }
  .pick-chip {
    display: inline-flex; align-items: center; gap: 6px;
    border: 1px solid var(--border); background: var(--bg-secondary);
    border-radius: 20px; padding: 5px 11px; font-size: 12px; cursor: pointer;
    white-space: nowrap; user-select: none;
  }
  .pick-chip:hover { border-color: var(--accent); }
  .pick-chip.on { border-color: var(--success); background: rgba(16,185,129,0.12); color: var(--success); font-weight: 700; }
  .pick-chip .n { color: var(--text-dim); font-size: 11px; }
  .pick-chip.on .n { color: var(--success); }
  .pick-chip input { cursor: pointer; margin: 0; }

  /* ── MACHINE input-id cards (Dashboard input-id รายเครื่อง) ── */
  .machine-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(172px, 1fr));
    gap: 12px;
  }
  .mid-card {
    background: linear-gradient(150deg, var(--bg-card), var(--bg-secondary));
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 18px 16px;
    text-align: center;
    transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
  }
  .mid-card:hover { transform: translateY(-3px); border-color: var(--accent); box-shadow: 0 8px 20px rgba(0,0,0,0.28); }
  .mid-name {
    font-size: 13px; font-weight: 600; color: var(--text-secondary);
    margin-bottom: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .mid-count {
    font-size: 38px; font-weight: 800; line-height: 1;
    font-family: 'JetBrains Mono', monospace; letter-spacing: -1px;
  }
  .mid-label { font-size: 11px; color: var(--text-dim); margin-top: 6px; }
  .mid-badge { display: inline-block; margin-top: 8px; padding: 2px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; }
  .mid-badge.zero { background: rgba(239,68,68,0.15); color: var(--danger); }
  .mid-badge.low  { background: rgba(245,158,11,0.15); color: var(--warning); }

  /* ── MuMu control cards ── */
  .mumu-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 14px; }
  .mumu-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 14px; padding: 16px; }
  .mumu-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
  .mumu-name { font-weight: 700; font-size: 14px; }
  .mumu-actions { display: flex; gap: 6px; flex-wrap: wrap; }
  .mumu-actions .btn { padding: 6px 10px; font-size: 12px; }
  .mumu-body { min-height: 30px; }
  .mumu-inst { display: inline-flex; align-items: center; gap: 6px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; font-size: 12px; cursor: pointer; }
  .mumu-inst input { width: auto; margin: 0; }

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

  /* ═══════════════ RESPONSIVE ═══════════════
     กติกา: หน้าเว็บห้ามเลื่อนซ้าย-ขวา อะไรที่กว้างเกิน (ตาราง/แถบปุ่ม/breadcrumb)
     ให้เลื่อนอยู่ในกล่องตัวเองแทน */
  html, body { max-width: 100%; overflow-x: hidden; }
  .content, .toolbar, .stat-row, .hero-grid, .machine-grid, .id-grid, .pc-grid, .mumu-grid { min-width: 0; }
  .toolbar > * { min-width: 0; }
  .file-table { width: 100%; }
  .table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }

  /* แถบปุ่มบนหัวเว็บ: ปุ่มเยอะ พอจอแคบให้ตัดบรรทัดแทนล้นออกนอกจอ */
  .header-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; justify-content: flex-end; flex: 1 1 auto; }

  /* จอกลาง — บีบ sidebar ให้เนื้อหาได้ที่มากขึ้น */
  @media (max-width: 1400px) {
    .main-layout { grid-template-columns: 240px 1fr; }
    .header { padding: 14px 18px; }
    .content { padding: 18px; }
  }
  @media (max-width: 1150px) {
    .main-layout { grid-template-columns: 210px 1fr; }
    .header { flex-wrap: wrap; gap: 10px; }
    .header h1 { font-size: 18px; }
    .header-actions { gap: 8px; }
    .header-actions .btn { padding: 7px 11px; font-size: 12px; }
    .dash-search { min-width: 160px; flex: 1; }
    .stat-row { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
    .stat-tile { padding: 15px 16px; }
    .stat-val { font-size: 26px; }
  }

  /* แท็บเล็ต — sidebar ย้ายขึ้นบนเป็นแถบแนวนอน */
  @media (max-width: 900px) {
    .main-layout { grid-template-columns: 1fr; min-height: auto; }
    .sidebar {
      border-right: none;
      border-bottom: 1px solid var(--border);
      padding: 12px;
      max-height: 46vh;
      overflow-y: auto;
    }
    /* การ์ดเครื่องอยู่ใน #agentList — ต้องทำ flex ที่ตัวนี้ ไม่ใช่ที่ .sidebar
       ไม่งั้น .sidebar มีลูกแค่ตัวเดียว การ์ดเลยเรียงลงล่างเป็นคอลัมน์เดียวเหลือที่ว่างข้างขวา */
    #agentList { display: flex; flex-wrap: wrap; gap: 8px; align-content: flex-start; }
    .sidebar-title { display: none; }
    .agent-card { min-width: 150px; flex: 1 1 190px; max-width: 320px; margin-bottom: 0; padding: 10px 12px; }
    .agent-card h3 { font-size: 13px; }
    .agent-card .meta { font-size: 10px; }
    /* ตัวแบ่งหน้าต้องกินเต็มบรรทัด ไม่งั้นโดนบีบอยู่ข้างการ์ด */
    .agent-pager { width: 100%; flex: 1 0 100%; margin-bottom: 4px; padding-bottom: 8px; }
    .no-agents { flex: 1 0 100%; }
    /* ซูมเครื่องเดียวใน Live View — เผื่อความสูงให้พอดีจอเตี้ย */
    .pc-grid.single .pc-shot { height: calc(100vh - 300px); min-height: 220px; }
  }

  /* มือถือ */
  @media (max-width: 620px) {
    .header { padding: 12px 14px; }
    .header h1 { font-size: 16px; gap: 6px; }
    .header h1 .icon { font-size: 20px; }
    .header-actions { width: 100%; justify-content: flex-start; }
    .header-actions .btn { padding: 7px 10px; font-size: 11px; }
    .content { padding: 14px 12px; }
    .sidebar { max-height: 40vh; }
    .agent-card { min-width: 0; flex: 1 1 145px; max-width: none; }

    .toolbar { gap: 8px; }
    .toolbar h2 { font-size: 16px !important; width: 100%; }
    .toolbar .btn, .toolbar .project-select { flex: 1 1 auto; justify-content: center; }
    .dash-search { min-width: 0; width: 100%; flex: 1 0 100%; }
    .breadcrumb { flex: 1 0 100%; font-size: 12px; padding: 7px 10px; }
    .select-n { flex: 1 1 100%; }
    .select-n input { flex: 1; width: auto; }

    .stat-row { grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 18px; }
    .stat-tile { padding: 12px 13px; border-radius: 12px; }
    .stat-label { font-size: 10px; }
    .stat-val { font-size: 21px; }

    .hero-grid { grid-template-columns: repeat(auto-fill, minmax(112px, 1fr)); gap: 7px; }
    .hero-count { font-size: 18px; }
    .machine-grid { grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); gap: 8px; }
    .mid-count { font-size: 28px; }
    .id-grid { grid-template-columns: repeat(auto-fill, minmax(148px, 1fr)); }
    .mumu-grid { grid-template-columns: 1fr; }
    .pc-grid { grid-template-columns: 1fr; }
    .pc-grid.single .pc-shot { height: auto; aspect-ratio: 16/9; min-height: 0; }

    /* ตารางไฟล์: ซ่อนคอลัมน์รอง เหลือ ชื่อ + ปุ่ม ให้อ่านง่ายบนจอแคบ */
    .file-table th:nth-child(3), .file-table td:nth-child(3),
    .file-table th:nth-child(4), .file-table td:nth-child(4) { display: none; }
    .file-table th, .file-table td { padding: 9px 8px; font-size: 12px; }
    .file-table th:nth-child(2) { width: auto; }
    .file-name { word-break: break-word; }
    .file-actions .btn { padding: 4px 7px; }

    /* แถวสรุปรายเครื่อง: ชื่อกับตัวเลขคนละบรรทัด ไม่เบียดกัน */
    .agent-stat { flex-direction: column; align-items: flex-start; gap: 5px; padding: 10px 12px; font-size: 12px; }

    .modal { min-width: 0; width: 100%; padding: 20px 18px; border-radius: 14px; }
    .modal-buttons { flex-wrap: wrap; }
    .modal-buttons .btn { flex: 1 1 auto; justify-content: center; }
    .upload-zone { padding: 26px 16px; }
    .upload-zone .icon { font-size: 32px; }
    .toast-container { left: 12px; right: 12px; bottom: 12px; }
    .toast { width: auto; }
  }

  /* modal ต้องไม่ล้นจอเตี้ย/แคบ ไม่ว่าจะ breakpoint ไหน */
  .modal-overlay { padding: 16px; }
  .modal { max-height: calc(100vh - 32px); overflow-y: auto; }

  /* จอสัมผัสไม่มี hover — ปุ่มที่ซ่อนรอ hover จะกดไม่ได้เลย ต้องโชว์ค้างไว้ */
  @media (hover: none), (pointer: coarse), (max-width: 620px) {
    .file-actions { opacity: 1; }
    .agent-card .power-btn { opacity: 1; }
  }
</style>
</head>
<body>

<!-- HEADER -->
<div class="header">
  <h1>
    <span class="icon">📁</span>
    Remote File Manager
    <span style="font-size:11px; font-weight:400; color:var(--text-dim); margin-left:10px" title="เวลาที่โค้ด server ถูกอัปเดตล่าสุด — ใช้เช็กว่า deploy โค้ดใหม่สำเร็จหรือยัง">build __APP_BUILD__</span>
  </h1>
  <div class="header-actions">
    <button class="btn" onclick="openDashboard()">⚽ Dashboard PES</button>
    <button class="btn" onclick="openBackupDashboard()">🗄️ Dashboard Backup</button>
    <button class="btn" onclick="openInputIdDashboard()">📥 input-id รายเครื่อง</button>
    <button class="btn" onclick="openCookieDashboard()">🍪 Dashboard Cookie-Run</button>
    <button class="btn" onclick="openRangerDashboard()">🏹 Dashboard Line Ranger</button>
    <button class="btn" onclick="openRangerFindDashboard()">🔎 Line Ranger-Find</button>
    <button class="btn" onclick="openBroadcastInput()">📤 ส่งเข้า input-id (ทุกเครื่อง)</button>
    <button class="btn" onclick="openBroadcastBackup()">💾 ส่งเข้า backup (ทุกเครื่อง)</button>
    <button class="btn" onclick="openMumuDashboard()">🎮 MuMu</button>
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

<!-- RANGER DETAIL MODAL (กดการ์ดแล้วดูข้อมูลเต็ม) -->
<div class="modal-overlay" id="rfDetailModal" onclick="if(event.target===this)closeModal('rfDetailModal')">
  <div class="modal" style="min-width:min(560px,100%); max-width:640px">
    <div id="rfDetailBody"></div>
    <div class="modal-buttons" style="margin-top:16px">
      <button class="btn btn-primary" onclick="closeModal('rfDetailModal')">ปิด</button>
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

<!-- TRANSFER MODAL (ย้าย/คัดลอกไฟล์ ข้ามเครื่อง) -->
<div class="modal-overlay" id="transferModal">
  <div class="modal" style="min-width:480px; max-width:580px">
    <h2>📦 ย้าย / คัดลอกไฟล์ไปเครื่องอื่น</h2>
    <div id="transferSummary" style="font-size:13px; color:var(--text-secondary); margin-bottom:14px"></div>

    <label class="fld">ปลายทาง — เครื่องที่จะรับไฟล์</label>
    <select id="transferDest" class="modal-select" style="margin-bottom:14px"></select>

    <div style="display:flex; gap:10px; margin-bottom:6px">
      <div style="flex:1">
        <label class="fld">โปรเจกต์ (โฟลเดอร์หลัก)</label>
        <input type="text" id="transferBase" placeholder="เช่น pes">
      </div>
      <div style="flex:1">
        <label class="fld">โฟลเดอร์ย่อย</label>
        <input type="text" id="transferSub" placeholder="เช่น input-id">
      </div>
    </div>
    <div style="font-size:11px; color:var(--text-dim); margin-bottom:14px">
      ไฟล์จะไปอยู่ที่โฟลเดอร์เดียวกันของเครื่องปลายทาง (เครื่องปลายทาง resolve พาธเอง — ต้องมีโฟลเดอร์นี้ใน allowed_paths)
    </div>

    <label style="display:flex; align-items:center; gap:8px; font-size:13px; margin-bottom:6px; cursor:pointer">
      <input type="checkbox" id="transferDelete" checked style="width:auto"
             onchange="document.getElementById('transferStartBtn').textContent = this.checked ? '🚀 เริ่มย้าย' : '📋 เริ่มคัดลอก'">
      ลบไฟล์ต้นทางหลังส่งสำเร็จ (ติ๊ก = ย้าย, ไม่ติ๊ก = คัดลอก)
    </label>

    <div id="transferStatus" style="display:none; margin:12px 0">
      <div style="display:flex; justify-content:space-between; align-items:center; font-size:13px; margin-bottom:6px">
        <span id="txStatusText" style="font-weight:700; color:var(--accent)">กำลังเตรียม...</span>
        <span id="txStatusCount" style="font-size:12px"></span>
      </div>
      <div class="progress-bar" style="height:10px"><div id="txStatusBar" class="progress-fill" style="width:0%"></div></div>
      <div id="txStatusCur" style="font-size:11px; color:var(--text-dim); margin-top:5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis"></div>
    </div>

    <div id="transferProgress" style="max-height:160px; overflow-y:auto; margin:8px 0; font-size:12px; line-height:1.6"></div>

    <div class="modal-buttons">
      <button class="btn" id="transferCloseBtn" onclick="transferCloseOrCancel()">ปิด</button>
      <button class="btn btn-primary" id="transferStartBtn" onclick="startTransfer()">🚀 เริ่มย้าย</button>
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

let cookieScope = 'ALL';     // scope แยกของ dashboard cookie-run

function pcSelectHtml(scopeVal, onchangeExpr) {
  const agents = agentsData || [];
  let opts = `<option value="ALL"${scopeVal === 'ALL' ? ' selected' : ''}>🖥️ รวมทุกเครื่อง</option>`;
  opts += agents.map(a => `<option value="${escHtml(a.agent_id)}"${scopeVal === a.agent_id ? ' selected' : ''}>🖥️ ${escHtml(a.name || a.hostname || a.agent_id)}</option>`).join('');
  return `<select class="btn project-select" onchange="${onchangeExpr}" title="เลือกเครื่องที่จะแสดง">${opts}</select>`;
}

function countHeroesOnAgent(agentId, subpath) {
  return new Promise((resolve, reject) => {
    let settled = false;
    socket.once('request_sent', (data) => {
      const rid = data.request_id;
      socket.once('response_' + rid, (resp) => {
        settled = true;
        if (resp.error) reject(new Error(resp.error)); else resolve(resp);
      });
    });
    socket.emit('request_count_heroes', { agent_id: agentId, names: HERO_LIST, subpath: subpath || 'found-hero' });
    setTimeout(() => { if (!settled) reject(new Error('timeout')); }, 20000);
  });
}

// นับไฟล์ที่เหลือในโฟลเดอร์ pes/input-id ของเครื่องนั้น (ไม่ throw — คืน object เสมอ)
function countInputIdOnAgent(agentId) {
  return new Promise((resolve) => {
    let settled = false;
    socket.once('request_sent', (data) => {
      const rid = data.request_id;
      socket.once('response_' + rid, (resp) => { settled = true; resolve(resp || {}); });
    });
    socket.emit('request_list_ids', { agent_id: agentId, subpath: 'input-id', base_match: 'pes' });
    setTimeout(() => { if (!settled) resolve({ error: 'timeout' }); }, 20000);
  });
}

// dashboard แบบนับไฟล์ตามชื่อฮีโร่ — ใช้ทั้ง PES (found-hero) และ Backup (backup) โครงเดียวกัน ต่างแค่โฟลเดอร์
const DASH_KINDS = {
  hero:   { subpath: 'found-hero', label: 'found-hero', title: '⚽ Dashboard PES', reopen: 'openDashboard' },
};
let _dashScope = { hero: 'ALL' };

function openDashboard() { return openHeroDash('hero'); }

async function openHeroDash(kind) {
  const cfg = DASH_KINDS[kind];
  currentAgent = null;
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
  const content = document.getElementById('contentArea');
  const allAgents = agentsData || [];
  if (_dashScope[kind] !== 'ALL' && !allAgents.some(a => a.agent_id === _dashScope[kind])) _dashScope[kind] = 'ALL';
  const agents = _dashScope[kind] === 'ALL' ? allAgents : allAgents.filter(a => a.agent_id === _dashScope[kind]);

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">${cfg.title} — ${cfg.label}</h2>
      ${pcSelectHtml(_dashScope[kind], `_dashScope['${kind}']=this.value; ${cfg.reopen}()`)}
      <button class="btn btn-primary" onclick="${cfg.reopen}()">🔄 รีเฟรช</button>
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
      const res = await countHeroesOnAgent(a.agent_id, cfg.subpath);
      onlineCount++;
      grandTotal += res.total_files || 0;
      // นับไฟล์ที่เหลือใน pes/input-id ของเครื่องนี้
      const ir = await countInputIdOnAgent(a.agent_id);
      perAgent.push({
        name: a.name || a.hostname || a.agent_id,
        total: res.total_files || 0, exists: res.exists,
        inputId: (ir && typeof ir.total === 'number') ? ir.total : null,
        inputExists: ir ? ir.exists : undefined,
      });
      const combos = res.combos || {};
      for (const k in combos) comboTotals[k] = (comboTotals[k] || 0) + combos[k];
    } catch (e) {
      perAgent.push({ name: a.name || a.hostname || a.agent_id, error: String(e.message || e) });
    }
  }
  renderHeroDash(kind, comboTotals, grandTotal, perAgent, agents.length, onlineCount);
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

function renderHeroDash(kind, comboTotals, grandTotal, perAgent, totalMachines, onlineCount) {
  const cfg = DASH_KINDS[kind];
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
  const inputIdTotal = perAgent.reduce((s, p) => s + (p.inputId || 0), 0);
  const agentRows = perAgent.map(p => {
    let right;
    if (p.error) {
      right = '<span style="color:var(--danger)">' + escHtml(p.error) + '</span>';
    } else {
      const inputTxt = p.inputExists === false
        ? '<span style="color:var(--warning)">ไม่พบ input-id</span>'
        : (p.inputId == null ? '<span style="color:var(--text-dim)">-</span>'
                             : '<b style="color:var(--accent)">' + p.inputId + '</b> ไฟล์');
      const folderTxt = p.exists === false
        ? '<span style="color:var(--warning)">ไม่พบ ' + cfg.label + '</span>'
        : (p.total + ' ไฟล์');
      right = '<span title="ไฟล์ที่เหลือในโฟลเดอร์ input-id">📥 input-id: ' + inputTxt + '</span>'
            + '<span style="color:var(--text-dim); margin:0 10px">·</span>'
            + '<span style="color:var(--text-secondary)" title="ไฟล์ในโฟลเดอร์ ' + cfg.label + '">🗂️ ' + cfg.label + ': ' + folderTxt + '</span>';
    }
    return '<div class="agent-stat"><span>🖥️ ' + escHtml(p.name) + '</span><span>' + right + '</span></div>';
  }).join('');

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">${cfg.title}</h2>
      ${pcSelectHtml(_dashScope[kind], `_dashScope['${kind}']=this.value; ${cfg.reopen}()`)}
      <input type="text" class="dash-search" placeholder="🔍 ค้นหาชื่อฮีโร่ / combo..." oninput="filterHeroCards(this.value)">
      <button class="btn btn-primary" onclick="${cfg.reopen}()">🔄 รีเฟรช</button>
    </div>
    <div class="stat-row">
      <div class="stat-tile"><div class="stat-label">เครื่องทั้งหมด</div><div class="stat-val">${totalMachines}</div></div>
      <div class="stat-tile"><div class="stat-label">ออนไลน์ (ตอบกลับ)</div><div class="stat-val" style="color:var(--success)">${onlineCount}</div></div>
      <div class="stat-tile"><div class="stat-label">input-id เหลือรวม</div><div class="stat-val" style="color:var(--accent)">${inputIdTotal}</div></div>
      <div class="stat-tile"><div class="stat-label">ไฟล์ ${cfg.label} รวม</div><div class="stat-val" style="color:var(--accent)">${grandTotal}</div></div>
      <div class="stat-tile"><div class="stat-label">id ที่ตรงชื่อฮีโร่</div><div class="stat-val" style="color:var(--success)">${matchedTotal}</div></div>
      <div class="stat-tile"><div class="stat-label">จำนวนแบบ (combo)</div><div class="stat-val">${sorted.length}</div></div>
    </div>
    <div class="hero-grid">${cards}</div>
    <div id="dashNoResult" style="display:none; text-align:center; padding:36px; color:var(--text-dim)">🔍 ไม่พบชื่อที่ค้นหา</div>
    <h3 style="margin:24px 0 12px; font-size:14px; color:var(--text-secondary)">รายเครื่อง — input-id ที่เหลือ + ${cfg.label}</h3>
    <div class="agent-stats">${agentRows}</div>
  `;
}

// ═══════════════════════════════════════════════════════════
//  DASHBOARD รายเครื่อง (นับไฟล์ในโฟลเดอร์) — input-id และ backup แยกหน้ากัน โครงเดียวกัน
// ═══════════════════════════════════════════════════════════
const FOLDER_DASH = {
  inputid: { subpath: 'input-id', base: 'pes', title: '📥 Dashboard input-id — ไฟล์ที่เหลือรายเครื่อง', label: 'input-id', reopen: 'openInputIdDashboard' },
  backup:  { subpath: 'backup',   base: 'main', title: '🗄️ Dashboard Backup — ไฟล์ backup รายเครื่อง (Line Ranger)', label: 'backup', reopen: 'openBackupDashboard' },
};
let _folderScope = { inputid: 'ALL', backup: 'ALL' };

function openInputIdDashboard() { return openFolderDash('inputid'); }
function openBackupDashboard() { return openFolderDash('backup'); }

// นับไฟล์ในโฟลเดอร์ <base>/<subpath> ของเครื่องนั้น (ไม่ throw — คืน object เสมอ)
function countFolderOnAgent(agentId, subpath, base) {
  return new Promise((resolve) => {
    let settled = false;
    socket.once('request_sent', (data) => {
      const rid = data.request_id;
      socket.once('response_' + rid, (resp) => { settled = true; resolve(resp || {}); });
    });
    socket.emit('request_list_ids', { agent_id: agentId, subpath: subpath, base_match: base });
    setTimeout(() => { if (!settled) resolve({ error: 'timeout' }); }, 20000);
  });
}

async function openFolderDash(kind) {
  const cfg = FOLDER_DASH[kind];
  currentAgent = null;
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
  const content = document.getElementById('contentArea');
  const allAgents = agentsData || [];
  if (_folderScope[kind] !== 'ALL' && !allAgents.some(a => a.agent_id === _folderScope[kind])) _folderScope[kind] = 'ALL';
  const agents = _folderScope[kind] === 'ALL' ? allAgents : allAgents.filter(a => a.agent_id === _folderScope[kind]);

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">${cfg.title}</h2>
      ${pcSelectHtml(_folderScope[kind], `_folderScope['${kind}']=this.value; ${cfg.reopen}()`)}
      <button class="btn btn-primary" onclick="${cfg.reopen}()">🔄 รีเฟรช</button>
    </div>
    <div class="loading"><div class="spinner"></div>กำลังดึงข้อมูลจาก ${agents.length} เครื่อง...</div>`;

  if (!allAgents.length) {
    content.innerHTML = '<div class="empty-state"><div class="icon">🖥️</div><h3>ยังไม่มีเครื่องลูกออนไลน์</h3></div>';
    return;
  }

  const perAgent = [];
  let total = 0, onlineCount = 0;
  for (const a of agents) {
    const name = a.name || a.hostname || a.agent_id;
    const ir = await countFolderOnAgent(a.agent_id, cfg.subpath, cfg.base);
    if (ir && ir.error) {
      perAgent.push({ name, error: ir.error });
    } else {
      onlineCount++;
      const cnt = (ir && typeof ir.total === 'number') ? ir.total : 0;
      total += cnt;
      perAgent.push({ name, count: cnt, exists: ir ? ir.exists : undefined });
    }
  }
  renderFolderDash(kind, perAgent, agents.length, onlineCount, total);
}

function renderFolderDash(kind, perAgent, totalMachines, onlineCount, total) {
  const cfg = FOLDER_DASH[kind];
  const content = document.getElementById('contentArea');
  const cards = perAgent.map(p => {
    if (p.error) {
      return `<div class="mid-card" data-name="${escHtml(p.name)}">
        <div class="mid-name">🖥️ ${escHtml(p.name)}</div>
        <div class="mid-count" style="color:var(--danger); font-size:15px">${escHtml(p.error)}</div></div>`;
    }
    if (p.exists === false) {
      return `<div class="mid-card" data-name="${escHtml(p.name)}">
        <div class="mid-name">🖥️ ${escHtml(p.name)}</div>
        <div class="mid-count" style="color:var(--warning); font-size:18px">—</div>
        <div class="mid-label">ไม่พบโฟลเดอร์ ${cfg.label}</div></div>`;
    }
    const c = p.count;
    const color = c === 0 ? 'var(--danger)' : (c < 100 ? 'var(--warning)' : 'var(--accent)');
    const badge = c === 0 ? '<div class="mid-badge zero">ว่าง</div>'
                : (c < 100 ? '<div class="mid-badge low">น้อย</div>' : '');
    return `<div class="mid-card" data-name="${escHtml(p.name)}">
      <div class="mid-name">🖥️ ${escHtml(p.name)}</div>
      <div class="mid-count" style="color:${color}">${c.toLocaleString()}</div>
      <div class="mid-label">ไฟล์ใน ${cfg.label}</div>
      ${badge}
    </div>`;
  }).join('');

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">${cfg.title}</h2>
      <input type="text" class="dash-search" placeholder="🔍 ค้นหาเครื่อง..." oninput="filterMidCards(this.value)">
      ${pcSelectHtml(_folderScope[kind], `_folderScope['${kind}']=this.value; ${cfg.reopen}()`)}
      <button class="btn btn-primary" onclick="${cfg.reopen}()">🔄 รีเฟรช</button>
    </div>
    <div class="stat-row">
      <div class="stat-tile"><div class="stat-label">เครื่องทั้งหมด</div><div class="stat-val">${totalMachines}</div></div>
      <div class="stat-tile"><div class="stat-label">ออนไลน์ (ตอบกลับ)</div><div class="stat-val" style="color:var(--success)">${onlineCount}</div></div>
      <div class="stat-tile"><div class="stat-label">${cfg.label} รวม</div><div class="stat-val" style="color:var(--accent)">${total.toLocaleString()}</div></div>
    </div>
    <div class="machine-grid">${cards}</div>
    <div id="midNoResult" style="display:none; text-align:center; padding:36px; color:var(--text-dim)">🔍 ไม่พบเครื่องที่ค้นหา</div>
  `;
}

function filterMidCards(q) {
  q = (q || '').trim().toLowerCase();
  let shown = 0;
  document.querySelectorAll('.mid-card').forEach(card => {
    const match = !q || (card.dataset.name || '').toLowerCase().includes(q);
    card.style.display = match ? '' : 'none';
    if (match) shown++;
  });
  const nr = document.getElementById('midNoResult');
  if (nr) nr.style.display = shown === 0 ? '' : 'none';
}

// ═══════════════════════════════════════════════════════════
//  DASHBOARD LINE RANGER (นับ id ในโฟลเดอร์ main/backup-id รวมทุกเครื่อง)
//  ชื่อไฟล์: <ชื่อฮีโร่ต่อกันด้วย +>-<ชื่อไฟล์เดิม>.xml
//    kikoruU+-RB136_TK24_norandom408dd2d9_LINE_COCOS_PREF_KEY.xml → kikoruU
//    kikoru+Kafka+-RB136_..._LINE_COCOS_PREF_KEY.xml              → kikoru+Kafka (นับรวมเป็นชุดเดียว)
// ═══════════════════════════════════════════════════════════
const RANGER_MAIN_NAMES = ['Reno', 'hina', 'KafkaU', 'Kafka', 'kikoru', 'kikoruU'];
const RANGER_CFG = { subpath: 'backup-id', base: 'main', label: 'backup-id' };
let _rangerScope = 'ALL';

// นับ id ในโฟลเดอร์ main/backup-id ของเครื่องนั้น (ไม่ throw — คืน object เสมอ)
function countRangerOnAgent(agentId, by) {
  return new Promise((resolve) => {
    let settled = false;
    socket.once('request_sent', (data) => {
      const rid = data.request_id;
      socket.once('response_' + rid, (resp) => { settled = true; resolve(resp || {}); });
    });
    socket.emit('request_count_prefix_ids', {
      agent_id: agentId, subpath: RANGER_CFG.subpath, base_match: RANGER_CFG.base,
      exts: ['.xml'], by: by || 'filename',
    });
    setTimeout(() => { if (!settled) resolve({ error: 'timeout' }); }, 20000);
  });
}

async function openRangerDashboard() {
  currentAgent = null;
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
  const content = document.getElementById('contentArea');
  const allAgents = agentsData || [];
  if (_rangerScope !== 'ALL' && !allAgents.some(a => a.agent_id === _rangerScope)) _rangerScope = 'ALL';
  const agents = _rangerScope === 'ALL' ? allAgents : allAgents.filter(a => a.agent_id === _rangerScope);

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">🏹 Dashboard Line Ranger — ${RANGER_CFG.label}</h2>
      ${pcSelectHtml(_rangerScope, '_rangerScope=this.value; openRangerDashboard()')}
      <button class="btn btn-primary" onclick="openRangerDashboard()">🔄 รีเฟรช</button>
    </div>
    <div class="loading"><div class="spinner"></div>กำลังดึงข้อมูลจาก ${agents.length} เครื่อง...</div>`;

  if (!allAgents.length) {
    content.innerHTML = '<div class="empty-state"><div class="icon">🖥️</div><h3>ยังไม่มีเครื่องลูกออนไลน์</h3></div>';
    return;
  }

  const comboTotals = {};
  let grandTotal = 0, matchedTotal = 0, onlineCount = 0;
  const perAgent = [];

  for (const a of agents) {
    const name = a.name || a.hostname || a.agent_id;
    const res = await countRangerOnAgent(a.agent_id);
    if (!res || res.error) {
      perAgent.push({ name, error: String((res && res.error) || 'ไม่ตอบกลับ') });
      continue;
    }
    onlineCount++;
    grandTotal += res.total_files || 0;
    matchedTotal += res.matched_files || 0;
    perAgent.push({
      name, total: res.total_files || 0, matched: res.matched_files || 0, exists: res.exists,
    });
    const combos = res.combos || {};
    for (const k in combos) comboTotals[k] = (comboTotals[k] || 0) + combos[k];
  }
  renderRangerDash(comboTotals, grandTotal, matchedTotal, perAgent, agents.length, onlineCount);
}

function renderRangerDash(comboTotals, grandTotal, matchedTotal, perAgent, totalMachines, onlineCount) {
  const content = document.getElementById('contentArea');

  // 1) combo (ตามที่อยู่ในชื่อไฟล์จริง) — kikoru+Kafka นับเป็นชุดเดียว ไม่แตกออก
  const combos = Object.keys(comboTotals)
    .map(k => ({ name: k, count: comboTotals[k] }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));

  // 2) รวมรายชื่อ — ชื่อเดียวกันที่อยู่คนละ combo เอามาบวกกัน (kikoru เดี่ยว + kikoru+Kafka)
  const nameTotals = {};
  const nameCombos = {};
  combos.forEach(c => {
    c.name.split('+').map(s => s.trim()).filter(Boolean).forEach(n => {
      nameTotals[n] = (nameTotals[n] || 0) + c.count;
      nameCombos[n] = (nameCombos[n] || 0) + 1;
    });
  });
  const names = Object.keys(nameTotals).map(n => ({
    name: n, count: nameTotals[n], combos: nameCombos[n],
    main: RANGER_MAIN_NAMES.some(m => m.toLowerCase() === n.toLowerCase()),
  })).sort((a, b) => (b.main - a.main) || b.count - a.count || a.name.localeCompare(b.name));

  const nameCards = names.length ? names.map(n => `
    <div class="hero-card rg-card with-img${n.main ? ' main-name' : ''}" data-name="${escHtml(n.name)}">
      ${heroImgs(n.name)}
      <div class="hero-name" title="${escHtml(n.name)}">${escHtml(n.name)}</div>
      <div class="hero-count">${n.count.toLocaleString()}</div>
      <div class="hero-sub">${n.combos} แบบ</div>
    </div>`).join('') : '<div class="empty-state" style="grid-column:1/-1"><div class="icon">📭</div><h3>ยังไม่พบชื่อฮีโร่ในไฟล์</h3></div>';

  const comboCards = combos.length ? combos.map(c => {
    const parts = c.name.split('+').filter(Boolean);
    return `<div class="hero-card rg-card with-img${parts.length > 1 ? ' combo' : ''}" data-name="${escHtml(c.name)}">
      ${heroImgs(c.name)}
      <div class="hero-name" title="${escHtml(c.name)}">${escHtml(c.name)}</div>
      <div class="hero-count">${c.count.toLocaleString()}</div>
      <div class="hero-sub">${parts.length > 1 ? parts.length + ' ชื่อ/ไฟล์' : 'ชื่อเดียว'}</div>
    </div>`;
  }).join('') : '<div class="empty-state" style="grid-column:1/-1"><div class="icon">📭</div><h3>ไม่พบไฟล์ที่มีชื่อฮีโร่</h3></div>';

  const agentRows = perAgent.map(p => {
    let right;
    if (p.error) {
      right = '<span style="color:var(--danger)">' + escHtml(p.error) + '</span>';
    } else if (p.exists === false) {
      right = '<span style="color:var(--warning)">ไม่พบโฟลเดอร์ ' + RANGER_CFG.label + '</span>';
    } else {
      right = '<span title="ไฟล์ที่มีชื่อฮีโร่"><b style="color:var(--success)">' + p.matched.toLocaleString() + '</b> id</span>'
            + '<span style="color:var(--text-dim); margin:0 10px">·</span>'
            + '<span style="color:var(--text-secondary)" title="ไฟล์ .xml ทั้งหมดใน ' + RANGER_CFG.label + '">🗂️ ทั้งหมด ' + p.total.toLocaleString() + ' ไฟล์</span>';
    }
    return '<div class="agent-stat"><span>🖥️ ' + escHtml(p.name) + '</span><span>' + right + '</span></div>';
  }).join('');

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">🏹 Dashboard Line Ranger</h2>
      ${pcSelectHtml(_rangerScope, '_rangerScope=this.value; openRangerDashboard()')}
      <input type="text" class="dash-search" placeholder="🔍 ค้นหาชื่อ / combo..." oninput="filterRangerCards(this.value)">
      <button class="btn btn-primary" onclick="openRangerDashboard()">🔄 รีเฟรช</button>
    </div>
    <div class="stat-row">
      <div class="stat-tile"><div class="stat-label">เครื่องทั้งหมด</div><div class="stat-val">${totalMachines}</div></div>
      <div class="stat-tile"><div class="stat-label">ออนไลน์ (ตอบกลับ)</div><div class="stat-val" style="color:var(--success)">${onlineCount}</div></div>
      <div class="stat-tile"><div class="stat-label">ไฟล์ ${RANGER_CFG.label} รวม</div><div class="stat-val" style="color:var(--accent)">${grandTotal.toLocaleString()}</div></div>
      <div class="stat-tile"><div class="stat-label">id ที่มีชื่อฮีโร่</div><div class="stat-val" style="color:var(--success)">${matchedTotal.toLocaleString()}</div></div>
      <div class="stat-tile"><div class="stat-label">จำนวนแบบ (combo)</div><div class="stat-val">${combos.length}</div></div>
    </div>
    <h3 style="margin:4px 0 12px; font-size:14px; color:var(--text-secondary)">รวมรายชื่อ — ทุกเครื่อง (ชื่อเดียวกันคนละ combo บวกรวมกัน)</h3>
    <div class="hero-grid big">${nameCards}</div>
    <h3 style="margin:24px 0 12px; font-size:14px; color:var(--text-secondary)">แยกตามไฟล์ (combo) — ไฟล์ที่มี 2 ชื่อจะนับเป็นชุดเดียว เช่น kikoru+Kafka</h3>
    <div class="hero-grid big">${comboCards}</div>
    <div id="rangerNoResult" style="display:none; text-align:center; padding:36px; color:var(--text-dim)">🔍 ไม่พบชื่อที่ค้นหา</div>
    <h3 style="margin:24px 0 12px; font-size:14px; color:var(--text-secondary)">รายเครื่อง — ${RANGER_CFG.label}</h3>
    <div class="agent-stats">${agentRows}</div>
  `;
}

// ═══════════════════════════════════════════════════════════
//  หาตัว LINE RANGER — เหมือน Dashboard Line Ranger แต่แยกตามโฟลเดอร์ย่อยใน backup-id
//  backup-id\ranger, backup-id\ranger(2), ... เลือกดูทีละชุดจาก dropdown
//  แต่ละชุดมี combo ของตัวเอง ไม่ปนกัน
// ═══════════════════════════════════════════════════════════
let _rfScope = 'ALL';      // เครื่องที่เลือก
let _rfGroup = 'ALL';      // ชุด (ชื่อโฟลเดอร์ย่อย) ที่เลือก
let _rfCache = null;       // ผลรอบล่าสุด กดเปลี่ยนชุดแล้วไม่ต้องยิงถามใหม่

async function openRangerFindDashboard(useCache) {
  currentAgent = null;
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
  const content = document.getElementById('contentArea');
  const allAgents = agentsData || [];
  if (_rfScope !== 'ALL' && !allAgents.some(a => a.agent_id === _rfScope)) _rfScope = 'ALL';
  const agents = _rfScope === 'ALL' ? allAgents : allAgents.filter(a => a.agent_id === _rfScope);

  if (useCache && _rfCache) { renderRangerFind(_rfCache); return; }

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">🔎 Line Ranger-Find — ${RANGER_CFG.label}</h2>
      <button class="btn btn-primary">🔄 รีเฟรช</button>
    </div>
    <div class="loading"><div class="spinner"></div>กำลังดึงข้อมูลจาก ${agents.length} เครื่อง...</div>`;

  if (!allAgents.length) {
    content.innerHTML = '<div class="empty-state"><div class="icon">🖥️</div><h3>ยังไม่มีเครื่องลูกออนไลน์</h3></div>';
    return;
  }

  // groupCombos: ชื่อชุด -> { combo -> จำนวน }   groupFiles: ชื่อชุด -> จำนวนไฟล์ทั้งหมด
  const groupCombos = {}, groupFiles = {};
  const perAgent = [];
  let onlineCount = 0;

  for (const a of agents) {
    const name = a.name || a.hostname || a.agent_id;
    // 'folder' = อ่านชื่อตัวจากชื่อโฟลเดอร์ (backup-id\<ชุด>\<ชื่อตัว>\) ไม่ใช่จากชื่อไฟล์
    const res = await countRangerOnAgent(a.agent_id, 'folder');
    if (!res || res.error) { perAgent.push({ name, error: String((res && res.error) || 'ไม่ตอบกลับ') }); continue; }
    onlineCount++;
    // นับเฉพาะไฟล์ที่อยู่ในโฟลเดอร์ย่อย — ไฟล์ที่วางไว้ชั้นนอกของ backup-id ไม่เอา (key '')
    const gt = res.group_totals || {};
    const gc = res.groups || {};
    const myGroups = {};
    for (const g in gt) if (g !== '') myGroups[g] = gt[g];
    perAgent.push({
      name, total: res.total_files || 0, matched: res.matched_files || 0,
      exists: res.exists, groups: myGroups, legacy: !res.groups,
      byGroup: gc,          // เก็บ combo รายเครื่องไว้ ให้หน้ารายละเอียดแจกแจงได้ว่าเครื่องไหนมีเท่าไหร่
    });
    for (const g in gc) {
      if (g === '') continue;
      const dst = groupCombos[g] || (groupCombos[g] = {});
      for (const k in gc[g]) dst[k] = (dst[k] || 0) + gc[g][k];
    }
    for (const g in gt) if (g !== '') groupFiles[g] = (groupFiles[g] || 0) + gt[g];
  }

  _rfCache = { groupCombos, groupFiles, perAgent, machines: agents.length, onlineCount };
  renderRangerFind(_rfCache);
}

// รูปตัวละครตามชื่อ combo — "anya+kappa+radish" ได้ 3 รูปเรียงซ้อนกัน
// ชื่อไหนไม่มีรูป (เช่นชื่อเกียร์) <img> จะลบตัวเองทิ้ง ไม่ขึ้นไอคอนรูปแตก
function heroImgs(comboName) {
  const parts = String(comboName || '').split('+').map(s => s.trim()).filter(Boolean);
  if (!parts.length) return '';
  // โชว์ครบทุกตัว ไม่ตัดเป็น +N — ยิ่งหลายตัวยิ่งซ้อนกันถี่ขึ้น
  // ความกว้างรวมคงที่ ~SPAN px ไม่ว่าจะกี่รูป ชื่อบนการ์ดเลยไม่โดนทับ
  const n = parts.length;
  const SIZE = 25, SPAN = 74;
  const step = n <= 1 ? SIZE : Math.max(6, Math.min(19, Math.round((SPAN - SIZE) / (n - 1))));
  const overlap = SIZE - step;
  const imgs = parts.map((p, i) =>
    `<img class="hero-img" src="/hero-img/${encodeURIComponent(p)}" alt="" loading="lazy"
          style="${i ? 'margin-left:-' + overlap + 'px;' : ''}z-index:${n - i}"
          title="${escAttr(p)}" onerror="this.remove()">`).join('');
  return `<div class="hero-imgs" title="${escAttr(parts.join(' + '))}">${imgs}</div>`;
}

// ── กดการ์ดแล้วเปิดดูข้อมูลเต็ม ──
// kind='combo' ดูโฟลเดอร์ combo นั้น | kind='name' ดูตัวละครตัวเดียว (รวมทุก combo ที่มีชื่อนี้)
function rfOpenDetail(kind, key) {
  const c = _rfCache;
  if (!c) return;
  const inScope = _rfGroup === 'ALL' ? Object.keys(c.groupCombos) : [_rfGroup];
  const isName = (kind === 'name');
  const hit = (combo) => isName
    ? combo.split('+').map(s => s.trim()).includes(key)
    : combo === key;

  // แจกแจงตามชุด + เก็บ combo ที่เกี่ยวข้อง (กรณีดูรายตัว)
  const bySet = {}, byCombo = {};
  let total = 0;
  inScope.forEach(g => {
    const src = c.groupCombos[g] || {};
    for (const k in src) if (hit(k)) {
      bySet[g] = (bySet[g] || 0) + src[k];
      byCombo[k] = (byCombo[k] || 0) + src[k];
      total += src[k];
    }
  });

  // แจกแจงรายเครื่อง
  const byPc = {};
  (c.perAgent || []).forEach(p => {
    const gm = p.byGroup || {};
    let n = 0;
    for (const g in gm) {
      if (g === '' || (_rfGroup !== 'ALL' && g !== _rfGroup)) continue;
      for (const k in gm[g]) if (hit(k)) n += gm[g][k];
    }
    if (n) byPc[p.name] = n;
  });

  const rows = (obj, icon) => {
    const keys = Object.keys(obj).sort((a, b) => obj[b] - obj[a] || a.localeCompare(b));
    if (!keys.length) return '<div style="color:var(--text-dim); font-size:13px">— ไม่มี —</div>';
    return '<div class="agent-stats">' + keys.map(k =>
      `<div class="agent-stat"><span>${icon} ${escHtml(k)}</span><span><b style="color:var(--accent)">${obj[k].toLocaleString()}</b> id</span></div>`
    ).join('') + '</div>';
  };

  const parts = key.split('+').map(s => s.trim()).filter(Boolean);
  const scopeTxt = _rfGroup === 'ALL' ? 'ทุกชุด' : 'ชุด ' + escHtml(_rfGroup);
  document.getElementById('rfDetailBody').innerHTML = `
    <div style="display:flex; align-items:center; gap:12px; margin-bottom:6px">
      <div style="display:flex">${parts.map(p =>
        `<img class="hero-img" style="width:46px; height:46px; margin-left:${parts.length > 4 ? -18 : -10}px"
              src="/hero-img/${encodeURIComponent(p)}" title="${escAttr(p)}" onerror="this.remove()">`).join('')}</div>
      <h2 style="margin:0; font-size:17px; word-break:break-word">${escHtml(key)}</h2>
    </div>
    <div style="color:var(--text-dim); font-size:12px; margin-bottom:14px">
      ${isName ? 'ตัวละคร' : parts.length + ' ชื่อในโฟลเดอร์เดียว'} · นับจาก ${scopeTxt} · ${_rfScope === 'ALL' ? 'ทุกเครื่อง' : 'เฉพาะเครื่องที่เลือก'}
    </div>
    <div class="stat-row" style="margin-bottom:16px">
      <div class="stat-tile"><div class="stat-label">id ทั้งหมด</div><div class="stat-val" style="color:var(--success)">${total.toLocaleString()}</div></div>
      <div class="stat-tile"><div class="stat-label">อยู่ในชุด</div><div class="stat-val">${Object.keys(bySet).length}</div></div>
      <div class="stat-tile"><div class="stat-label">พบในเครื่อง</div><div class="stat-val">${Object.keys(byPc).length}</div></div>
    </div>
    <h3 style="font-size:13px; color:var(--text-secondary); margin:0 0 8px">แยกตามชุด</h3>
    ${rows(bySet, '📁')}
    ${isName ? `<h3 style="font-size:13px; color:var(--text-secondary); margin:16px 0 8px">อยู่ในโฟลเดอร์ไหนบ้าง (${Object.keys(byCombo).length} แบบ)</h3>${rows(byCombo, '🧩')}` : ''}
    <h3 style="font-size:13px; color:var(--text-secondary); margin:16px 0 8px">แยกตามเครื่อง</h3>
    ${rows(byPc, '🖥️')}
  `;
  document.getElementById('rfDetailModal').classList.add('show');
}

function rfGroupLabel(g) { return '📁 ' + g; }
function rfPickGroup(g) { _rfGroup = g; openRangerFindDashboard(true); }
function rfShowAll() { _rfGroup = 'ALL'; openRangerFindDashboard(true); }

// ── ตัวกรองชื่อตัว: ติ๊กเลือกจากชื่อที่ "เจอจริง" ในข้อมูล (เซ็ตว่าง = แสดงทั้งหมด) ──
let _rfPick = new Set();
let _rfPickQ = '';        // คำค้นในรายการติ๊ก
try {
  const saved = JSON.parse(localStorage.getItem('rfPick') || '[]');
  if (Array.isArray(saved)) _rfPick = new Set(saved);
} catch (e) {}

function rfSavePick() {
  try { localStorage.setItem('rfPick', JSON.stringify([..._rfPick])); } catch (e) {}
}
function rfTogglePick(n) {
  if (_rfPick.has(n)) _rfPick.delete(n); else _rfPick.add(n);
  rfSavePick(); openRangerFindDashboard(true);
}
// เลือกทั้งหมด = อ่านชื่อจากชิปในรายการ (เลี่ยงยัด JSON ลง attribute แล้วโดน quote ชนกัน)
function rfPickAllNames() {
  const chips = document.querySelectorAll('#rfPickList .pick-chip');
  _rfPick = new Set([...chips].map(c => c.dataset.name));
  rfSavePick(); openRangerFindDashboard(true);
}
function rfClearPick() { _rfPick.clear(); rfSavePick(); openRangerFindDashboard(true); }
function rfSetPickQ(v) {
  _rfPickQ = (v || '').trim().toLowerCase();
  const list = document.getElementById('rfPickList');
  if (!list) return;
  list.querySelectorAll('.pick-chip').forEach(c => {
    c.style.display = (!_rfPickQ || (c.dataset.name || '').toLowerCase().includes(_rfPickQ)) ? '' : 'none';
  });
}
// combo ผ่านตัวกรองไหม — ไม่ได้เลือกอะไรเลย = ผ่านหมด, เลือกแล้ว = ต้องมีชื่อที่เลือกอยู่ใน combo
function rfComboPass(comboName) {
  if (!_rfPick.size) return true;
  return comboName.split('+').some(p => _rfPick.has(p.trim()));
}

// ── แบ่งหน้าการ์ด combo (ข้อมูลเยอะ เลื่อนยาวไม่ไหว) ──
const RF_PER_PAGE = 40;
let _rfComboPage = 0;
let _rfQ = '';                 // คำค้นจากช่องค้นหาบนแถบเครื่องมือ

function rfGoComboPage(p) { _rfComboPage = Math.max(0, p); openRangerFindDashboard(true); }
function rfSetQuery(v) {
  _rfQ = (v || '').trim().toLowerCase();
  _rfComboPage = 0;
  openRangerFindDashboard(true);
}

// แถบเลขหน้า: ‹ 1 2 3 › + บอกช่วงที่กำลังดู
function gridPagerHtml(page, totalPages, total, perPage, fnName) {
  if (totalPages <= 1) return '';
  let nums = '';
  const win = 9;                                    // โชว์ปุ่มเลขทีละ 9 หน้า กันปุ่มล้นตอนหน้าเยอะ
  let from = Math.max(0, Math.min(page - Math.floor(win / 2), totalPages - win));
  let to = Math.min(totalPages, from + win);
  if (from > 0) nums += `<button class="btn" onclick="${fnName}(0)">1</button><span style="color:var(--text-dim)">…</span>`;
  for (let i = from; i < to; i++) {
    nums += `<button class="btn${i === page ? ' active' : ''}" onclick="${fnName}(${i})">${i + 1}</button>`;
  }
  if (to < totalPages) nums += `<span style="color:var(--text-dim)">…</span><button class="btn" onclick="${fnName}(${totalPages - 1})">${totalPages}</button>`;
  const a = page * perPage + 1, b = Math.min((page + 1) * perPage, total);
  return `<div class="grid-pager">
    <button class="btn" onclick="${fnName}(${page - 1})" ${page === 0 ? 'disabled' : ''}>‹ ก่อนหน้า</button>
    ${nums}
    <button class="btn" onclick="${fnName}(${page + 1})" ${page >= totalPages - 1 ? 'disabled' : ''}>ถัดไป ›</button>
    <span class="gp-info">${a}-${b} / ${total}</span>
  </div>`;
}

function renderRangerFind(data) {
  const { groupCombos, groupFiles, perAgent, machines, onlineCount } = data;
  const content = document.getElementById('contentArea');

  // รายชื่อชุดทั้งหมด เรียง ranger, ranger(2), ranger(3) ตามเลขในวงเล็บ
  const groupNames = Object.keys(groupFiles).length ? Object.keys(groupFiles) : Object.keys(groupCombos);
  groupNames.sort((a, b) => _numIn(a) - _numIn(b) || a.localeCompare(b));
  if (_rfGroup !== 'ALL' && !groupNames.includes(_rfGroup)) _rfGroup = 'ALL';

  // รายชื่อตัวทั้งหมดที่เจอ (ทุกชุดรวมกัน) — ใช้เป็นตัวเลือกในกล่องติ๊ก ไม่ผูกกับชุด/ตัวกรอง
  const allNameCount = {};
  groupNames.forEach(g => {
    const src = groupCombos[g] || {};
    for (const k in src) k.split('+').map(s => s.trim()).filter(Boolean).forEach(n => {
      allNameCount[n] = (allNameCount[n] || 0) + src[k];
    });
  });
  const allNames = Object.keys(allNameCount).sort((a, b) => allNameCount[b] - allNameCount[a] || a.localeCompare(b));
  // ชื่อที่เคยติ๊กไว้แต่รอบนี้ไม่มีในข้อมูลแล้ว ตัดทิ้ง กันกรองจนว่างเปล่าแบบงงๆ
  if (_rfPick.size) {
    const before = _rfPick.size;
    _rfPick = new Set([..._rfPick].filter(n => n in allNameCount));
    if (_rfPick.size !== before) rfSavePick();
  }

  // รวม combo ของชุดที่เลือก (ALL = รวมทุกชุด) แล้วกรองตามชื่อที่ติ๊ก
  const combosMap = {};
  const picked = _rfGroup === 'ALL' ? groupNames : [_rfGroup];
  picked.forEach(g => {
    const src = groupCombos[g] || {};
    for (const k in src) if (rfComboPass(k)) combosMap[k] = (combosMap[k] || 0) + src[k];
  });
  const combos = Object.keys(combosMap)
    .filter(k => !_rfQ || k.toLowerCase().includes(_rfQ))     // ช่องค้นหาบนแถบเครื่องมือ
    .map(k => ({ name: k, count: combosMap[k] }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));

  // รวมรายชื่อภายในชุดที่เลือก
  const nameTotals = {}, nameCombos = {};
  combos.forEach(c => c.name.split('+').map(s => s.trim()).filter(Boolean).forEach(n => {
    nameTotals[n] = (nameTotals[n] || 0) + c.count;
    nameCombos[n] = (nameCombos[n] || 0) + 1;
  }));
  const names = Object.keys(nameTotals)
    .filter(n => !_rfPick.size || _rfPick.has(n))     // ติ๊กแล้วโชว์เฉพาะที่ติ๊ก
    .map(n => ({
      name: n, count: nameTotals[n], combos: nameCombos[n],
      main: RANGER_MAIN_NAMES.some(m => m.toLowerCase() === n.toLowerCase()),
    })).sort((a, b) => (b.main - a.main) || b.count - a.count || a.name.localeCompare(b.name));

  const filesInScope = picked.reduce((s, g) => s + (groupFiles[g] || 0), 0);
  const idsInScope = combos.reduce((s, c) => s + c.count, 0);

  const groupOpts = `<option value="ALL"${_rfGroup === 'ALL' ? ' selected' : ''}>🗂️ รวมทุกชุด (${groupNames.length})</option>` +
    groupNames.map(g => `<option value="${escAttr(g)}"${_rfGroup === g ? ' selected' : ''}>${escHtml(rfGroupLabel(g))} — ${(groupFiles[g] || 0).toLocaleString()} ไฟล์</option>`).join('');

  // การ์ดสรุปรายชุด กดเลือกชุดได้เลย ไม่ต้องไปกด dropdown
  const groupCards = groupNames.map(g => {
    // การ์ดชุดนับตามตัวกรองด้วย จะได้ตรงกับตัวเลขข้างล่าง
    const src = groupCombos[g] || {};
    const keys = Object.keys(src).filter(rfComboPass);
    const cnt = keys.reduce((s, k) => s + src[k], 0);
    const kinds = keys.length;
    const on = (_rfGroup === g);
    return `<div class="hero-card rf-group${on ? ' main-name' : ''}" style="cursor:pointer"
                 onclick="rfPickGroup('${escAttr(g)}')"
                 title="กดเพื่อดูเฉพาะชุดนี้">
      <div class="hero-name">${escHtml(g)}</div>
      <div class="hero-count">${cnt.toLocaleString()}</div>
      <div class="hero-sub">${kinds} combo · ${(groupFiles[g] || 0).toLocaleString()} ไฟล์</div>
    </div>`;
  }).join('');

  const nameCards = names.length ? names.map(n => `
    <div class="hero-card rg-card clickable with-img${n.main ? ' main-name' : ''}" data-name="${escAttr(n.name)}"
         onclick="rfOpenDetail('name', '${escAttr(n.name)}')" title="กดดูข้อมูลเต็มของ ${escAttr(n.name)}">
      ${heroImgs(n.name)}
      <div class="hero-name" title="${escHtml(n.name)}">${escHtml(n.name)}</div>
      <div class="hero-count">${n.count.toLocaleString()}</div>
      <div class="hero-sub">${n.combos} แบบ</div>
    </div>`).join('') : '<div class="empty-state" style="grid-column:1/-1"><div class="icon">📭</div><h3>ชุดนี้ยังไม่มีชื่อฮีโร่</h3></div>';

  // แบ่งหน้า: ตัดเฉพาะการ์ดของหน้าปัจจุบันมาวาด
  const comboPages = Math.max(1, Math.ceil(combos.length / RF_PER_PAGE));
  if (_rfComboPage >= comboPages) _rfComboPage = comboPages - 1;
  const comboSlice = combos.slice(_rfComboPage * RF_PER_PAGE, (_rfComboPage + 1) * RF_PER_PAGE);
  const comboPager = gridPagerHtml(_rfComboPage, comboPages, combos.length, RF_PER_PAGE, 'rfGoComboPage');

  const comboCards = comboSlice.length ? comboSlice.map(c => {
    const parts = c.name.split('+').filter(Boolean);
    return `<div class="hero-card rg-card clickable with-img${parts.length > 1 ? ' combo' : ''}" data-name="${escAttr(c.name)}"
      onclick="rfOpenDetail('combo', '${escAttr(c.name)}')" title="กดดูข้อมูลเต็มของ ${escAttr(c.name)}">
      ${heroImgs(c.name)}
      <div class="hero-name" title="${escHtml(c.name)}">${escHtml(c.name)}</div>
      <div class="hero-count">${c.count.toLocaleString()}</div>
      <div class="hero-sub">${parts.length > 1 ? parts.length + ' ชื่อ/ไฟล์' : 'ชื่อเดียว'}</div>
    </div>`;
  }).join('') : `<div class="empty-state" style="grid-column:1/-1"><div class="icon">📭</div><h3>${_rfQ ? 'ไม่พบ combo ที่ตรงกับคำค้น' : 'ไม่พบไฟล์ที่มีชื่อฮีโร่'}</h3></div>`;

  // กล่องติ๊กเลือกชื่อตัว — รายการมาจากชื่อที่เจอจริงในข้อมูล เรียงตามจำนวนมาก→น้อย
  const pickChips = allNames.map(n => `
    <label class="pick-chip${_rfPick.has(n) ? ' on' : ''}" data-name="${escAttr(n)}" title="${escHtml(n)} — ${allNameCount[n]} id">
      <input type="checkbox" ${_rfPick.has(n) ? 'checked' : ''} onchange="rfTogglePick('${escAttr(n)}')">
      ${escHtml(n)} <span class="n">${allNameCount[n]}</span>
    </label>`).join('');
  const pickPanel = allNames.length ? `
    <div class="pick-panel">
      <div class="pick-head">
        <span class="pick-title">🎯 เลือกตัวที่จะแสดง ${_rfPick.size ? '<span style="color:var(--success)">(เลือกอยู่ ' + _rfPick.size + '/' + allNames.length + ')</span>' : '<span style="color:var(--text-dim)">(ไม่ได้เลือก = แสดงทั้งหมด ' + allNames.length + ' ตัว)</span>'}</span>
        <input type="text" class="dash-search" style="min-width:170px; padding:6px 12px" placeholder="🔍 ค้นหาชื่อในรายการ" value="${escAttr(_rfPickQ)}" oninput="rfSetPickQ(this.value)">
        <button class="btn" onclick="rfPickAllNames()">เลือกทั้งหมด</button>
        <button class="btn" onclick="rfClearPick()">ล้างตัวกรอง</button>
      </div>
      <div class="pick-list" id="rfPickList">${pickChips}</div>
    </div>` : '';

  const legacy = perAgent.some(p => p.legacy);
  const agentRows = perAgent.map(p => {
    let right;
    if (p.error) right = '<span style="color:var(--danger)">' + escHtml(p.error) + '</span>';
    else if (p.exists === false) right = '<span style="color:var(--warning)">ไม่พบโฟลเดอร์ ' + RANGER_CFG.label + '</span>';
    else {
      const gs = Object.keys(p.groups || {});
      const detail = gs.length
        ? gs.sort((a, b) => _numIn(a) - _numIn(b)).map(g => g + ' ' + p.groups[g]).join(' · ')
        : '<span style="color:var(--text-dim)">ไม่มีโฟลเดอร์ย่อย</span>';
      right = '<span title="ไฟล์ที่มีชื่อฮีโร่"><b style="color:var(--success)">' + p.matched.toLocaleString() + '</b> id</span>'
            + '<span style="color:var(--text-dim); margin:0 10px">·</span>'
            + '<span style="color:var(--text-secondary)">🗂️ ' + escHtml(detail) + '</span>';
    }
    return '<div class="agent-stat"><span>🖥️ ' + escHtml(p.name) + '</span><span>' + right + '</span></div>';
  }).join('');

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">🔎 Line Ranger-Find</h2>
      <select class="btn project-select" onchange="_rfGroup=this.value; openRangerFindDashboard(true)" title="เลือกชุด (โฟลเดอร์ย่อยใน backup-id)">${groupOpts}</select>
      ${pcSelectHtml(_rfScope, '_rfScope=this.value; openRangerFindDashboard()')}
      <input type="text" class="dash-search" id="rfSearch" placeholder="🔍 ค้นหาชื่อ / combo..." value="${escAttr(_rfQ)}"
             oninput="rfSetQuery(this.value)">
      <button class="btn btn-primary" onclick="openRangerFindDashboard()">🔄 รีเฟรช</button>
    </div>
    ${legacy ? '<div style="margin-bottom:12px; padding:10px 14px; border-radius:8px; border:1px solid rgba(245,158,11,.4); background:rgba(245,158,11,.08); color:var(--warning); font-size:13px">⚠️ บางเครื่องยังเป็น agent เวอร์ชันเก่า ยังไม่ส่งข้อมูลแยกชุดมา — ของเครื่องนั้นจะถูกรวมไว้ใน "ชั้นนอก" ให้กด self-update agent ก่อน</div>' : ''}
    <div class="stat-row">
      <div class="stat-tile"><div class="stat-label">เครื่องทั้งหมด</div><div class="stat-val">${machines}</div></div>
      <div class="stat-tile"><div class="stat-label">ออนไลน์ (ตอบกลับ)</div><div class="stat-val" style="color:var(--success)">${onlineCount}</div></div>
      <div class="stat-tile"><div class="stat-label">จำนวนชุด</div><div class="stat-val">${groupNames.length}</div></div>
      <div class="stat-tile"><div class="stat-label">ไฟล์ในชุดที่เลือก</div><div class="stat-val" style="color:var(--accent)">${filesInScope.toLocaleString()}</div></div>
      <div class="stat-tile"><div class="stat-label">id ที่มีชื่อฮีโร่</div><div class="stat-val" style="color:var(--success)">${idsInScope.toLocaleString()}</div></div>
      <div class="stat-tile"><div class="stat-label">combo ในชุดนี้</div><div class="stat-val">${combos.length}</div></div>
    </div>
    <h3 style="margin:4px 0 12px; font-size:14px; color:var(--text-secondary)">ชุดทั้งหมดใน ${RANGER_CFG.label} — กดการ์ดเพื่อดูเฉพาะชุดนั้น ${_rfGroup === 'ALL' ? '' : '<button class="btn" style="padding:4px 10px; font-size:11px; margin-left:8px" onclick="rfShowAll()">↩ กลับไปดูรวมทุกชุด</button>'}</h3>
    <div class="hero-grid">${groupCards || '<div style="color:var(--text-dim); font-size:13px">ยังไม่มีโฟลเดอร์ย่อย</div>'}</div>
    ${pickPanel}
    <h3 style="margin:24px 0 12px; font-size:14px; color:var(--text-secondary)">รวมรายชื่อ — ${_rfGroup === 'ALL' ? 'ทุกชุดรวมกัน' : 'เฉพาะชุด ' + escHtml(_rfGroup)}${_rfPick.size ? ' <span style="color:var(--success)">· กรองอยู่ ' + _rfPick.size + ' ตัว</span>' : ''}</h3>
    <div class="hero-grid big">${nameCards}</div>
    <h3 style="margin:24px 0 12px; font-size:14px; color:var(--text-secondary)">แยกตามโฟลเดอร์ (combo) — 1 ไฟล์ในโฟลเดอร์ = 1 id · โฟลเดอร์ที่มี 2 ชื่อนับเป็นชุดเดียว เช่น kikoru+Kafka</h3>
    ${comboPager}
    <div class="hero-grid big">${comboCards}</div>
    ${combos.length > RF_PER_PAGE ? comboPager : ''}
    <h3 style="margin:24px 0 12px; font-size:14px; color:var(--text-secondary)">รายเครื่อง — จำนวนไฟล์แต่ละชุด</h3>
    <div class="agent-stats">${agentRows}</div>
  `;

  // พิมพ์ค้นหาแล้วหน้าถูกวาดใหม่ ช่องค้นหาจะเสียโฟกัส ต้องคืนโฟกัส+ตำแหน่งเคอร์เซอร์ให้พิมพ์ต่อได้
  if (_rfQ) {
    const box = document.getElementById('rfSearch');
    if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
  }
  if (_rfPickQ) rfSetPickQ(_rfPickQ);   // คงคำค้นในกล่องติ๊กไว้หลังวาดใหม่
}

function filterRangerCards(q) {
  q = (q || '').trim().toLowerCase();
  let shown = 0;
  document.querySelectorAll('.rg-card').forEach(card => {
    const match = !q || (card.dataset.name || '').toLowerCase().includes(q);
    card.style.display = match ? '' : 'none';
    if (match) shown++;
  });
  const nr = document.getElementById('rangerNoResult');
  if (nr) nr.style.display = shown === 0 ? '' : 'none';
}

// ═══════════════════════════════════════════════════════════
//  MuMu Player 12 — เปิด/ปิด instance รายเครื่อง
// ═══════════════════════════════════════════════════════════
function mumuReq(agentId, sub, indices) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (!settled) { settled = true; resolve(v || {}); } };
    const onSent = (data) => { socket.once('response_' + data.request_id, (resp) => done(resp)); };
    socket.once('request_sent', onSent);
    setTimeout(() => { socket.off('request_sent', onSent); done({ error: 'หมดเวลา (เครื่องไม่ตอบ)' }); }, 45000);
    socket.emit('request_mumu', { agent_id: agentId, sub: sub, indices: indices || [] });
  });
}

function openMumuDashboard() {
  currentAgent = null;
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
  const content = document.getElementById('contentArea');
  const agents = agentsData || [];
  window._mumuAgents = agents.slice();
  if (!agents.length) {
    content.innerHTML = '<div class="empty-state"><div class="icon">🖥️</div><h3>ยังไม่มีเครื่องลูกออนไลน์</h3></div>';
    return;
  }
  const cards = agents.map((a, i) => {
    const label = escHtml(a.name || a.hostname || a.agent_id);
    return `<div class="mumu-card" id="mm_card_${i}">
      <div class="mumu-head">
        <span class="mumu-name">🖥️ ${label}</span>
        <div class="mumu-actions">
          <button class="btn" onclick="mumuLoad(${i})">🔄 โหลดจอ</button>
          <button class="btn btn-primary" onclick="mumuOpenSel(${i})">▶️ เปิดที่เลือก</button>
          <button class="btn btn-danger" onclick="mumuClose(${i})">⛔ ปิดทั้งหมด</button>
        </div>
      </div>
      <div class="mumu-body" id="mm_body_${i}"><span style="color:var(--text-dim); font-size:12px">กด "โหลดจอ" เพื่อดึงรายชื่อ instance มาติ๊กเลือก</span></div>
      <div class="mumu-status" id="mm_status_${i}" style="font-size:12px; margin-top:8px"></div>
    </div>`;
  }).join('');
  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">🎮 MuMu Player 12 — เปิด/ปิด รายเครื่อง</h2>
      <button class="btn" onclick="mumuLoadAll()">🔄 โหลดจอทุกเครื่อง</button>
      <button class="btn btn-danger" onclick="mumuCloseAll()">⛔ ปิด MuMu ทุกเครื่อง</button>
    </div>
    <div class="mumu-grid">${cards}</div>`;
}

async function mumuLoad(i) {
  const a = (window._mumuAgents || [])[i];
  if (!a) return;
  const body = document.getElementById('mm_body_' + i);
  document.getElementById('mm_status_' + i).innerHTML = '';
  body.innerHTML = '<span style="color:var(--accent); font-size:12px">⏳ กำลังโหลดรายชื่อจอ...</span>';
  const res = await mumuReq(a.agent_id, 'list', []);
  if (res.error) { body.innerHTML = `<span style="color:var(--danger); font-size:12px">❌ ${escHtml(res.error)}</span>`; return; }
  const insts = res.instances || [];
  if (!insts.length) { body.innerHTML = '<span style="color:var(--warning); font-size:12px">ไม่พบ instance</span>'; return; }
  body.innerHTML = '<div style="display:flex; flex-wrap:wrap; gap:8px">' +
    insts.map(ins => `<label class="mumu-inst" title="${ins.running ? 'กำลังเปิดอยู่' : 'ปิดอยู่'}">
      <input type="checkbox" class="mm_chk_${i}" value="${escAttr(String(ins.index))}">
      <span>${ins.running ? '🟢' : '⚪'} ${escHtml(String(ins.name))} <span style="color:var(--text-dim)">#${escHtml(String(ins.index))}</span></span>
    </label>`).join('') + '</div>';
}

async function mumuLoadAll() {
  const n = (window._mumuAgents || []).length;
  for (let i = 0; i < n; i++) await mumuLoad(i);   // ทำทีละเครื่อง กัน request ชนกัน
}

async function mumuOpenSel(i) {
  const a = (window._mumuAgents || [])[i];
  if (!a) return;
  const checked = [...document.querySelectorAll('.mm_chk_' + i + ':checked')].map(c => c.value);
  if (!checked.length) { toast('ยังไม่ได้ติ๊กจอที่จะเปิด (กด "โหลดจอ" ก่อนถ้ายังไม่มีรายการ)', 'info'); return; }
  const status = document.getElementById('mm_status_' + i);
  status.innerHTML = `<span style="color:var(--accent)">⏳ กำลังเปิดจอ ${escHtml(checked.join(', '))}...</span>`;
  const res = await mumuReq(a.agent_id, 'open', checked);
  if (res.error) { status.innerHTML = `<span style="color:var(--danger)">❌ ${escHtml(res.error)}</span>`; return; }
  status.innerHTML = `<span style="color:#22c55e">✅ สั่งเปิดจอ ${escHtml((res.opened || []).join(', '))} แล้ว</span>`;
  setTimeout(() => mumuLoad(i), 3500);   // รีเฟรชสถานะจอหลังเปิด
}

async function mumuClose(i) {
  const a = (window._mumuAgents || [])[i];
  if (!a) return;
  const name = a.name || a.hostname || a.agent_id;
  if (!confirm(`⛔ ปิด MuMu ทั้งหมดที่เครื่อง "${name}" ?\n(taskkill ทุก process ของ MuMu)`)) return;
  const status = document.getElementById('mm_status_' + i);
  status.innerHTML = '<span style="color:var(--accent)">⏳ กำลังปิด...</span>';
  const res = await mumuReq(a.agent_id, 'close', []);
  if (res.error) { status.innerHTML = `<span style="color:var(--danger)">❌ ${escHtml(res.error)}</span>`; return; }
  status.innerHTML = `<span style="color:#22c55e">✅ ปิดแล้ว ${res.count || 0} process</span>`;
  setTimeout(() => mumuLoad(i), 1500);
}

async function mumuCloseAll() {
  const agents = window._mumuAgents || [];
  if (!agents.length) return;
  if (!confirm(`⛔ ปิด MuMu ทั้งหมดของทุกเครื่อง (${agents.length} เครื่อง) ?`)) return;
  for (let i = 0; i < agents.length; i++) {
    const status = document.getElementById('mm_status_' + i);
    if (status) status.innerHTML = '<span style="color:var(--accent)">⏳ กำลังปิด...</span>';
    const res = await mumuReq(agents[i].agent_id, 'close', []);
    if (status) status.innerHTML = res.error
      ? `<span style="color:var(--danger)">❌ ${escHtml(res.error)}</span>`
      : `<span style="color:#22c55e">✅ ปิดแล้ว ${res.count || 0} process</span>`;
  }
  toast('สั่งปิด MuMu ทุกเครื่องแล้ว', 'success');
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
//  BROADCAST → input-id / backup (ส่งไฟล์/เคลียร์ ทุกเครื่องพร้อมกัน)
//  โครงเดียวกัน ต่างแค่โฟลเดอร์ปลายทาง + เกมที่เลือกไว้ให้ตั้งต้น
// ═══════════════════════════════════════════════════════════
const BC_TARGETS = {
  input:  { subpath: 'input-id', label: 'input-id', icon: '📤',
            title: 'ส่งเข้า input-id — เลือกเกม + เครื่อง', game: 'cookie-run' },
  backup: { subpath: 'backup',   label: 'backup',   icon: '💾',
            title: 'ส่งเข้า backup — เลือกเกม + เครื่อง', game: 'main' },
};
let _bcTarget = 'input';
function bcCfg() { return BC_TARGETS[_bcTarget] || BC_TARGETS.input; }

function openBroadcastInput()  { return openBroadcastPanel('input'); }
function openBroadcastBackup() { return openBroadcastPanel('backup'); }

function openBroadcastPanel(kind) {
  _bcTarget = BC_TARGETS[kind] ? kind : 'input';
  const cfg = bcCfg();
  currentAgent = null;
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
  const n = (agentsData || []).length;
  const gameOpt = (v, txt) => `<option value="${v}"${cfg.game === v ? ' selected' : ''}>${txt}</option>`;
  document.getElementById('contentArea').innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">${cfg.icon} ${cfg.title}</h2>
      <select class="btn project-select" id="bcMode" title="โหมดการส่งไฟล์" onchange="renderBcHint()">
        <option value="broadcast">📢 ส่งเหมือนกันทุกเครื่อง</option>
        <option value="split">📦 ส่งตามลำดับ (เครื่องแรก ← ไฟล์แรก)</option>
      </select>
      <select class="btn project-select" id="bcGame" title="เลือกเกมปลายทาง (โฟลเดอร์ ${cfg.label} ของเกมนั้น)">
        ${gameOpt('pes', '⚽ PES')}
        ${gameOpt('ro', '🗡️ RO')}
        ${gameOpt('cookie-run', '🍪 Cookie-Run')}
        ${gameOpt('main', '🎮 Line Ranger')}
      </select>
      <button class="btn" onclick="updateSelectedAgents()" title="ดึงโค้ดใหม่จาก GitHub + รีสตาร์ท agent">⬆️ อัปเดต agent (เครื่องที่เลือก)</button>
      <button class="btn" style="border-color:var(--danger); color:var(--danger)" onclick="clearInputAll()">🗑️ Clear ${cfg.label} (เครื่องที่เลือก)</button>
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
      <small id="bcHint"></small>
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
  renderBcHint();
}

function getSelectedAgents() {
  const ids = new Set(Array.from(document.querySelectorAll('.bc-agent:checked')).map(c => c.value));
  return (agentsData || []).filter(a => ids.has(a.agent_id));
}
function getBcGame() {
  const el = document.getElementById('bcGame');
  return el ? el.value : bcCfg().game;
}
function getBcMode() {
  const el = document.getElementById('bcMode');
  return el ? el.value : 'broadcast';
}
function renderBcHint() {
  const el = document.getElementById('bcHint');
  if (!el) return;
  const lb = bcCfg().label;
  el.innerHTML = getBcMode() === 'split'
    ? `โหมด <b>ส่งตามลำดับ</b> — ลากไฟล์ทั้งหมดครั้งเดียว ระบบเรียงเครื่องตามเลข + เรียงไฟล์ แล้วส่ง <b>ไฟล์แรก→เครื่องแรก, ไฟล์ที่สอง→เครื่องที่สอง</b> ไปเรื่อย ๆ เข้า <b>${lb}</b> — สูงสุด __MAX_UPLOAD_MB__MB/ไฟล์`
    : `โหมด <b>ส่งเหมือนกัน</b> — ทุกเครื่องที่เลือกได้ไฟล์ชุดเดียวกันครบ (เข้า <b>${lb}</b>) — สูงสุด __MAX_UPLOAD_MB__MB/ไฟล์`;
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

// อัปโหลด 1 ไฟล์ไปเครื่องเดียว โดยให้ agent วางในโฟลเดอร์ <game>/<subpath> เอง (base_match+subpath)
// subpath = input-id หรือ backup ตามปุ่มที่เปิดหน้ามา
function uploadToInput(agentId, filename, size, base64, game, subpath) {
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
      base_match: game, subpath: subpath || bcCfg().subpath,
    });
  });
}

async function broadcastFiles(fileList) {
  const files = Array.from(fileList || []);
  const agents = getSelectedAgents();
  const game = getBcGame();
  if (!agents.length) { toast('ยังไม่ได้เลือกเครื่อง (ติ๊กเครื่องที่จะส่งก่อน)', 'error'); return; }
  if (!files.length) return;

  if (getBcMode() === 'split') { return distributeFiles(files, agents, game); }

  for (const file of files) {
    if (file.size > __MAX_UPLOAD_MB__ * 1024 * 1024) {
      toast(file.name + ' ใหญ่เกิน __MAX_UPLOAD_MB__MB', 'error');
      continue;
    }
    let base64;
    try { base64 = await readAsBase64(file); }
    catch (e) { bcLog('❌ อ่านไฟล์ ' + escHtml(file.name) + ' ไม่ได้', true); continue; }

    let ok = 0; const fails = [];
    await runLimited(agents, 4, async (a) => {
      const mname = a.name || a.hostname || a.agent_id;
      try { await uploadToInput(a.agent_id, file.name, file.size, base64, game); ok++; }
      catch (e) { fails.push(mname + ': ' + (e.message || e)); }
    });
    const failHtml = fails.length ? ' <span style="color:var(--danger)">❌ ' + fails.length + '</span>' : '';
    bcLog(`📎 <b>${escHtml(file.name)}</b> → [${escHtml(game)}] ✅ ส่งเข้า ${escHtml(bcCfg().label)} สำเร็จ ${ok}/${agents.length} เครื่อง${failHtml}` +
          (fails.length ? '<br><small style="color:var(--text-dim)">' + escHtml(fails.join(' | ')) + '</small>' : ''), false);
    toast(`ส่ง ${file.name} → ${ok}/${agents.length} เครื่อง`, fails.length ? 'error' : 'success');
  }
}

// รันงานทีละ limit ตัวพร้อมกัน (กันยิงอัปโหลดทั้งหมดพร้อมกันจน server ท่วม → chunk มาก่อน session)
async function runLimited(items, limit, fn) {
  const arr = [...items];
  let idx = 0;
  async function worker() {
    while (idx < arr.length) {
      const i = idx++;
      await fn(arr[i], i);
    }
  }
  const workers = [];
  for (let w = 0; w < Math.min(limit, arr.length); w++) workers.push(worker());
  await Promise.all(workers);
}

function _numIn(s) { const m = String(s).match(/(\d+)/); return m === null ? Number.MAX_SAFE_INTEGER : parseInt(m[1], 10); }

// โหมดส่งตามลำดับ: เรียงเครื่องตามเลข + เรียงไฟล์ แล้วส่งไฟล์ที่ i ให้เครื่องที่ i (ไม่อิงเลขตรง)
async function distributeFiles(files, agents, game) {
  const valid = files.filter(f => {
    if (f.size > __MAX_UPLOAD_MB__ * 1024 * 1024) { toast(f.name + ' ใหญ่เกิน __MAX_UPLOAD_MB__MB', 'error'); return false; }
    return true;
  });
  if (!valid.length) return;

  // เรียงไฟล์ตามเลขในชื่อ (output1..output14, ไม่มีเลขไปท้าย) + เรียงเครื่องตามเลข
  const sortedFiles = [...valid].sort((a, b) => _numIn(a.name) - _numIn(b.name) || a.name.localeCompare(b.name));
  const sortedAgents = sortAgents(agents);
  const pairN = Math.min(sortedFiles.length, sortedAgents.length);

  bcLog(`📦 ส่งตามลำดับ: ${sortedFiles.length} ไฟล์ → ${sortedAgents.length} เครื่อง (จับคู่ ${pairN})`, false);

  let assigned = 0;
  const pairs = sortedAgents.slice(0, pairN).map((a, i) => ({ a, file: sortedFiles[i] }));
  await runLimited(pairs, 4, async ({ a, file }) => {
    const mname = a.name || a.hostname || a.agent_id;
    try {
      const b64 = await readAsBase64(file);
      await uploadToInput(a.agent_id, file.name, file.size, b64, game);
      assigned++;
      bcLog(`🎯 <b>${escHtml(mname)}</b> ← <b>${escHtml(file.name)}</b> [${escHtml(game)}] ✅`, false);
    } catch (e) {
      bcLog(`❌ <b>${escHtml(mname)}</b> ← ${escHtml(file.name)} → ${escHtml(String(e.message || e))}`, true);
    }
  });

  // เหลือ (ไฟล์เกิน หรือ เครื่องเกิน)
  const leftFiles = sortedFiles.slice(pairN);
  const leftAgents = sortedAgents.slice(pairN);
  if (leftFiles.length) bcLog(`⚠️ ไฟล์เกิน ไม่ได้ส่ง ${leftFiles.length}: <small style="color:var(--text-dim)">${escHtml(leftFiles.map(f => f.name).join(', '))}</small>`, false);
  if (leftAgents.length) bcLog(`➖ เครื่องเกิน ไม่ได้รับไฟล์ ${leftAgents.length}: <small style="color:var(--text-dim)">${escHtml(leftAgents.map(a => a.name || a.hostname || a.agent_id).join(', '))}</small>`, false);
  toast(`ส่งตามลำดับเสร็จ: ${assigned} คู่${leftFiles.length ? ' / เหลือ ' + leftFiles.length + ' ไฟล์' : ''}`, 'success');
}

// ถาม path จริงของไฟล์ใน <game>/<subpath> (ใช้ list_ids ที่คืน entries = full path)
function listInputEntries(agentId, game, subpath) {
  return new Promise((resolve, reject) => {
    let settled = false;
    socket.once('request_sent', (data) => {
      const rid = data.request_id;
      socket.once('response_' + rid, (resp) => {
        settled = true;
        if (resp.error) reject(new Error(resp.error)); else resolve(resp);
      });
    });
    socket.emit('request_list_ids', { agent_id: agentId, subpath: subpath || bcCfg().subpath, base_match: game });
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
  const cfg = bcCfg();
  if (!agents.length) { toast('ยังไม่ได้เลือกเครื่อง (ติ๊กเครื่องก่อน)', 'error'); return; }
  if (!confirm(`⚠️ ลบข้อมูลทั้งหมดในโฟลเดอร์ ${game}\\${cfg.subpath} ของเครื่องที่เลือก (${agents.length} เครื่อง) ?\n\nการลบนี้กู้คืนไม่ได้`)) return;

  // ทำทีละเครื่อง: ถาม path จริง → ลบด้วย request_delete_many (ตัวลบปกติ)
  let okMachines = 0, totalDeleted = 0;
  for (const a of agents) {
    const mname = a.name || a.hostname || a.agent_id;
    try {
      const info = await listInputEntries(a.agent_id, game, cfg.subpath);
      if (info.exists === false) {
        bcLog(`🗑️ <b>${escHtml(mname)}</b> → <span style="color:var(--warning)">ไม่พบโฟลเดอร์ ${escHtml(cfg.label)}</span>`, false);
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
  toast(`เคลียร์ ${cfg.label} เสร็จ: ${okMachines}/${agents.length} เครื่อง (ลบรวม ${totalDeleted})`, 'success');
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
const LIVE_PAGE_SIZE = 10;

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

  // ตอนซูมเครื่องเดียว ‹ › ให้เดินไปเครื่องถัดไป (เมื่อก่อนมันเทาค้างเพราะมีหน้าเดียว กดไม่ได้)
  const zoomed = liveScope !== 'ALL';
  const zi = zoomed ? (agentsData || []).findIndex(a => a.agent_id === liveScope) : -1;
  const zTotal = (agentsData || []).length;
  const pager = zoomed
    ? `<button class="btn" onclick="liveStepPc(-1)" ${zTotal < 2 ? 'disabled' : ''}>‹</button>
       <span>PC ${zi + 1} of ${zTotal}</span>
       <button class="btn" onclick="liveStepPc(1)" ${zTotal < 2 ? 'disabled' : ''}>›</button>`
    : `<button class="btn" onclick="liveGoPage(livePage-1)" ${livePage === 0 ? 'disabled' : ''}>‹</button>
       <span>Page ${livePage + 1} of ${totalPages}</span>
       <button class="btn" onclick="liveGoPage(livePage+1)" ${livePage >= totalPages - 1 ? 'disabled' : ''}>›</button>`;

  content.innerHTML = `
    <div class="toolbar">
      <h2 style="flex:1; font-size:18px">🖥️ PC Monitor — Live View
        <span style="color:var(--text-dim); font-weight:400; font-size:13px">(${all.length} PCs${zoomed ? '' : ` · ${LIVE_PAGE_SIZE}/หน้า`})</span></h2>
      ${livePagerButtons(totalPages, zoomed)}
      <select class="btn project-select" onchange="liveScope=this.value; livePage=0; renderLiveView()">${options}</select>
    </div>
    <div class="pc-grid${zoomed ? ' single' : ''}" id="pcGrid">${cards}</div>
    <div class="pc-pager">${pager}</div>`;
}

// ปุ่มเลขหน้าบนแถบบนขวา — กดกระโดดหน้าได้เลย ไม่ต้องเลื่อนลงไปหาปุ่มข้างล่าง
function livePagerButtons(totalPages, zoomed) {
  if (zoomed || totalPages <= 1) return '';
  let btns = '';
  for (let i = 0; i < totalPages; i++) {
    btns += `<button class="btn live-page-btn${i === livePage ? ' active' : ''}"
              onclick="liveGoPage(${i})" title="หน้า ${i + 1}">${i + 1}</button>`;
  }
  return `<div class="live-pages">${btns}</div>`;
}

function liveGoPage(p) {
  const totalPages = Math.max(1, Math.ceil(liveFilteredAgents().length / LIVE_PAGE_SIZE));
  livePage = Math.min(Math.max(0, p), totalPages - 1);
  renderLiveView();
}

// เดินไปเครื่องก่อนหน้า/ถัดไปตอนซูมอยู่ (วนกลับต้นเมื่อสุดทาง)
function liveStepPc(delta) {
  const all = agentsData || [];
  if (all.length < 2) return;
  const i = all.findIndex(a => a.agent_id === liveScope);
  if (i < 0) return;
  liveScope = all[(i + delta + all.length) % all.length].agent_id;
  renderLiveView();
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
function agentSortKey(a) {
  // เรียงเป็น 2 กลุ่ม: ชื่อปกติ (pc_1..pc_16) กลุ่ม 0 ก่อน, ชื่อขึ้นต้นเลขด้วย n (pc_n01..pc_n115) กลุ่ม 1 ต่อท้าย
  // แต่ละกลุ่มเรียงตามเลขในชื่อ (pc_n01 -> 1, pc_n115 -> 115) — ถ้าไม่มีเลขให้ไปท้ายสุด
  const name = (a.name || a.hostname || a.agent_id || '').toLowerCase();
  const nMatch = name.match(/n0*(\d+)/);   // เลขที่นำหน้าด้วย n เช่น n01, n115 → กลุ่มหลัง
  if (nMatch) return [1, parseInt(nMatch[1], 10)];
  const m = name.match(/(\d+)/);
  return [0, m ? parseInt(m[1], 10) : Number.MAX_SAFE_INTEGER];
}
function sortAgents(list) {
  return [...(list || [])].sort((a, b) => {
    const ka = agentSortKey(a), kb = agentSortKey(b);
    if (ka[0] !== kb[0]) return ka[0] - kb[0];   // กลุ่ม: ปกติก่อน แล้วค่อย n-series
    if (ka[1] !== kb[1]) return ka[1] - kb[1];   // แล้วเรียงตามเลข
    return String(a.name || a.hostname || '').localeCompare(String(b.name || b.hostname || ''));
  });
}

// ── แบ่งหน้ารายชื่อเครื่องใน sidebar (เครื่องเยอะแล้วเลื่อนหายาว) ──
const AGENT_PAGE_SIZE = 12;
let agentPage = 0;

function agentTotalPages() {
  return Math.max(1, Math.ceil((agentsData || []).length / AGENT_PAGE_SIZE));
}
function goAgentPage(p) {
  agentPage = Math.min(Math.max(0, p), agentTotalPages() - 1);
  renderAgents(agentsData);
}
function agentPagerHtml() {
  const total = agentTotalPages();
  if (total <= 1) return '';
  let nums = '';
  for (let i = 0; i < total; i++) {
    nums += `<button class="btn${i === agentPage ? ' active' : ''}" onclick="goAgentPage(${i})" title="หน้า ${i + 1}">${i + 1}</button>`;
  }
  const from = agentPage * AGENT_PAGE_SIZE + 1;
  const to = Math.min((agentPage + 1) * AGENT_PAGE_SIZE, agentsData.length);
  return `<div class="agent-pager">
    <button class="btn" onclick="goAgentPage(agentPage-1)" ${agentPage === 0 ? 'disabled' : ''} title="หน้าก่อนหน้า">‹</button>
    ${nums}
    <button class="btn" onclick="goAgentPage(agentPage+1)" ${agentPage >= total - 1 ? 'disabled' : ''} title="หน้าถัดไป">›</button>
    <span class="ap-info">${from}-${to} / ${agentsData.length}</span>
  </div>`;
}

function renderAgents(agents) {
  agentsData = sortAgents(agents);   // เรียงตามเลขในชื่อ (มีผลกับ sidebar + dropdown + broadcast + live view)
  const el = document.getElementById('agentList');
  if (!agentsData.length) {
    el.innerHTML = '<div class="no-agents">⏳<br>รอเครื่องลูกเชื่อมต่อ...<br><small>เปิด agent.py ที่เครื่องลูก</small></div>';
    return;
  }
  if (agentPage >= agentTotalPages()) agentPage = agentTotalPages() - 1;
  const pageAgents = agentsData.slice(agentPage * AGENT_PAGE_SIZE, (agentPage + 1) * AGENT_PAGE_SIZE);
  el.innerHTML = agentPagerHtml() + pageAgents.map(a => `
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
// ── ล็อกโปรเจกต์: เลือกครั้งเดียว แล้วทุกเครื่องที่กดต่อจากนี้เปิดโปรเจกต์เดียวกัน ──
// เก็บเป็นชื่อโฟลเดอร์ท้ายสุด (เช่น 'main') เพราะ path เต็มของแต่ละเครื่องไม่เหมือนกัน
// (C:\Users\Administrator\... กับ C:\Users\t\...) + จำไว้ใน localStorage ให้ข้ามการรีเฟรชได้
let lockedProject = null;
try { lockedProject = localStorage.getItem('lockedProject') || null; } catch (e) {}

function setLockedProject(name) {
  lockedProject = name ? String(name).toLowerCase() : null;
  try {
    if (lockedProject) localStorage.setItem('lockedProject', lockedProject);
    else localStorage.removeItem('lockedProject');
  } catch (e) {}
}

// เลือกโปรเจกต์จาก dropdown → ล็อกไว้ใช้กับทุกเครื่อง แล้วเปิดโฟลเดอร์นั้นของเครื่องปัจจุบัน
function pickProject(fullPath) {
  if (!fullPath) return;
  setLockedProject(baseName(fullPath));
  loadDir(fullPath);
}

function clearLockedProject() {
  setLockedProject(null);
  toast('ปลดล็อกโปรเจกต์แล้ว — กดเครื่องอื่นจะเปิดโฟลเดอร์แรกตามเดิม', 'success');
  if (currentPath) loadDir(currentPath);
}

// โฟลเดอร์เริ่มต้นของเครื่องนั้น: ใช้โปรเจกต์ที่ล็อกไว้ก่อน ถ้าเครื่องนั้นไม่มีค่อยใช้ตัวแรก
function agentStartPath(a) {
  const allowed = (a && a.allowed_paths) || [];
  if (!allowed.length) return '';
  if (lockedProject) {
    const hit = allowed.find(p => baseName(p).toLowerCase() === lockedProject);
    if (hit) return hit;
  }
  return allowed[0];
}

function selectAgent(agentId) {
  currentAgent = agentId;
  currentPath = '';
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
  event.currentTarget.classList.add('active');
  // ถ้าเครื่องลูกจำกัดโฟลเดอร์ไว้ → เข้าโฟลเดอร์นั้นตรงๆ (ข้ามหน้าเลือกไดรฟ์ที่อาจค้าง)
  const a = agentsData.find(x => x.agent_id === agentId);
  loadDir(agentStartPath(a));
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
      <select class="btn project-select" onchange="pickProject(this.value)" title="เลือกโปรเจกต์ — เลือกแล้วจะล็อกไว้ใช้กับทุกเครื่อง">
        ${allowed.map(p => `<option value="${escHtml(p)}" ${sameRoot(path, p) ? 'selected' : ''}>📁 ${escHtml(projectLabel(p))}</option>`).join('')}
      </select>` : '';
  // ป้ายบอกว่าล็อกโปรเจกต์ไหนไว้ + ปุ่มปลดล็อก
  const lockChip = lockedProject ? `
      <span class="proj-lock" title="กดเครื่องไหนก็เปิดโปรเจกต์นี้ให้เลย (ถ้าเครื่องนั้นมีโฟลเดอร์นี้)">
        🔒 ล็อก: ${escHtml(projectLabel(lockedProject))}
        <button onclick="clearLockedProject()" title="ปลดล็อก">✕</button>
      </span>` : '';

  // นับจำนวนไฟล์/โฟลเดอร์ที่เหลือในโฟลเดอร์นี้ (ไม่นับ ".." ที่เป็นปุ่มย้อนกลับ)
  const realItems = files.filter(f => f.name !== '..');
  const fileCount = realItems.filter(f => !f.is_dir).length;
  const dirCount = realItems.filter(f => f.is_dir).length;

  content.innerHTML = `
    <div class="toolbar">
      ${projectSelect}
      ${lockChip}
      <div class="breadcrumb">${breadcrumb}</div>
      <button class="btn" id="btnDownloadSel" onclick="downloadSelected()">💾 โหลดที่เลือก</button>
      <button class="btn" id="btnTransfer" onclick="openTransfer()" title="ย้าย/คัดลอกไฟล์ที่เลือกไปเครื่องอื่น">📦 ย้ายไปเครื่องอื่น</button>
      <button class="btn btn-danger" id="btnDeleteSel" onclick="deleteSelected()">🗑️ ลบที่เลือก</button>
      <button class="btn" onclick="loadDir(currentPath)">🔄 รีเฟรช</button>
      <button class="btn btn-primary" onclick="openUpload()">📤 อัปโหลด</button>
      <div class="select-n" title="กรอกจำนวน แล้วเลือกไฟล์ตั้งแต่บนสุดตามจำนวนนั้น">
        <input type="number" id="selectNInput" min="1" placeholder="เลือกกี่ไฟล์" onkeydown="if(event.key==='Enter')selectFirstN()">
        <button class="btn" onclick="selectFirstN()">✅ เลือกจำนวน</button>
      </div>
      <span class="file-count" title="จำนวนไฟล์ที่เหลือในโฟลเดอร์นี้">📄 เหลือ <b>${fileCount}</b> ไฟล์${dirCount ? ` <span class="dim">· ${dirCount} โฟลเดอร์</span>` : ''}</span>
      <span class="file-count sel-chip" id="selChip" style="display:none" title="จำนวนไฟล์ที่เลือกอยู่">✅ เลือก <b>0</b> ไฟล์</span>
    </div>
    ${files.length === 0 ? '<div class="empty-state"><div class="icon">📭</div><h3>โฟลเดอร์ว่าง</h3></div>' : `
    <div class="table-scroll">
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
          <tr ${f.name === '..' ? '' : `data-row="${i}"`} ondblclick="${f.is_dir ? `loadDir('${escAttr(f.full_path)}')` : `downloadFile('${escAttr(f.full_path)}', '${escAttr(f.name)}')`}">
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
    </div>
    `}
  `;
  updateSelectedCount();
}

function baseName(p) {
  return (p || '').split(/[\\\/]/).filter(Boolean).pop() || p;
}
// ชื่อโปรเจกต์ที่โชว์ให้สวย (โฟลเดอร์ main = เกม Line Ranger)
const PROJECT_LABELS = { 'main': 'Line Ranger' };
function projectLabel(p) {
  const b = baseName(p);
  return PROJECT_LABELS[String(b).toLowerCase()] || b;
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
      // ถอดทีละ chunk (ห้าม join ก่อน — base64 ของแต่ละ chunk มี '=' ปิดท้าย ต่อกันแล้ว atob พัง)
      try {
        chunks.push(b64ToBytes(chunk.data));
      } catch (e) {
        socket.off('file_chunk_' + data.request_id);
        toast('ดาวน์โหลดล้มเหลว: ถอดรหัสไฟล์ไม่ได้', 'error');
        return;
      }

      if (chunk.is_last) {
        socket.off('file_chunk_' + data.request_id);
        const arr = concatBytes(chunks);
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
  updateSelectedCount();
}

// นับไฟล์ที่เลือกอยู่แบบสด ๆ → โชว์ชิป "เลือก N ไฟล์" + ตัวเลขบนปุ่ม (ที่เลือก/ย้าย/ลบ)
function updateSelectedCount() {
  const n = document.querySelectorAll('.file-check:checked').length;
  const chip = document.getElementById('selChip');
  if (chip) {
    chip.style.display = n ? 'flex' : 'none';
    const b = chip.querySelector('b');
    if (b) b.textContent = n;
  }
  const setBtn = (id, base) => {
    const el = document.getElementById(id);
    if (el) el.textContent = n ? (base + ' (' + n + ')') : base;
  };
  setBtn('btnDownloadSel', '💾 โหลดที่เลือก');
  setBtn('btnTransfer', '📦 ย้ายไปเครื่องอื่น');
  setBtn('btnDeleteSel', '🗑️ ลบที่เลือก');
}

// ระหว่างลากคลุม อัปเดตตัวนับแค่เฟรมละครั้ง (กันหน่วงตอนไฟล์เยอะ)
let _selCountRAF = null;
function scheduleSelCount() {
  if (_selCountRAF) return;
  _selCountRAF = requestAnimationFrame(() => { _selCountRAF = null; updateSelectedCount(); });
}

// เลือกไฟล์ N อันแรก (บนสุด) ตามจำนวนที่กรอก เช่น มี 2000 กรอก 1000 = ติ๊ก 1000 อันแรก
function selectFirstN() {
  const inp = document.getElementById('selectNInput');
  const boxes = Array.from(document.querySelectorAll('.file-check'));   // เรียงตามลำดับที่แสดง (บน→ล่าง)
  if (!boxes.length) { toast('โฟลเดอร์นี้ไม่มีไฟล์ให้เลือก', 'info'); return; }
  let n = parseInt(inp && inp.value, 10);
  if (isNaN(n) || n < 1) { toast('กรอกจำนวนไฟล์ที่ต้องการเลือก (ตัวเลข)', 'info'); if (inp) inp.focus(); return; }
  const capped = Math.min(n, boxes.length);
  boxes.forEach((cb, i) => cb.checked = i < capped);   // เลือก N อันแรก, ที่เหลือไม่เลือก
  _lastCheckIndex = null;
  updateSelectedCount();
  toast('เลือก ' + capped + ' ไฟล์แรกแล้ว' + (n > boxes.length ? ` (มีทั้งหมด ${boxes.length})` : ''), 'success');
}

// ═══════════════════════════════════════════════════════════
//  DRAG-TO-SELECT — ลากคลุมเลือกหลายไฟล์ทีเดียว + Shift+คลิกเลือกช่วง
// ═══════════════════════════════════════════════════════════
let _drag = { pending: false, active: false, anchor: 0, lastCur: 0, target: true, startY: 0, snap: null };
let _lastCheckIndex = null;

function _setCheck(i, state) {
  const cb = document.querySelector('.file-check[data-index="' + i + '"]');
  if (cb) cb.checked = state;
}
function _applyRange(a, b, state) {
  const lo = Math.min(a, b), hi = Math.max(a, b);
  for (let i = lo; i <= hi; i++) _setCheck(i, state);
}
function _restoreRange(a, b) {   // คืนค่าช่วงกลับเป็นสถานะก่อนเริ่มลาก (ตอนลากถอยหลัง)
  const lo = Math.min(a, b), hi = Math.max(a, b);
  for (let i = lo; i <= hi; i++) _setCheck(i, _drag.snap ? _drag.snap.has(i) : false);
}
function _rowIndexFromPoint(x, y) {
  const el = document.elementFromPoint(x, y);
  const tr = (el && el.closest) ? el.closest('tr[data-row]') : null;
  return tr ? parseInt(tr.dataset.row) : null;
}

function initDragSelect() {
  document.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    const tr = e.target.closest && e.target.closest('tr[data-row]');
    if (!tr) return;
    // ปล่อยให้ช่องติ๊ก / ปุ่ม action ทำงานปกติ (ไม่เริ่มลากจากตรงนั้น)
    if (e.target.closest('.file-actions') || e.target.closest('input, button, a, select')) return;
    _drag.pending = true;
    _drag.active = false;
    _drag.anchor = parseInt(tr.dataset.row);
    _drag.startY = e.clientY;
    e.preventDefault();   // กันการลากเลือกข้อความ (dblclick เปิดโฟลเดอร์ยังทำงาน)
  });

  document.addEventListener('mousemove', (e) => {
    if (!_drag.pending) return;
    if (!_drag.active) {
      if (Math.abs(e.clientY - _drag.startY) < 4) return;   // ต้องขยับเมาส์ก่อนถึงเริ่มลาก
      _drag.active = true;
      _drag.snap = new Set();
      document.querySelectorAll('.file-check:checked').forEach(cb => _drag.snap.add(parseInt(cb.dataset.index)));
      _drag.target = !_drag.snap.has(_drag.anchor);   // เริ่มบนแถวที่ยังไม่ติ๊ก=เลือก, ติ๊กอยู่=ยกเลิก
      _drag.lastCur = _drag.anchor;
      document.body.classList.add('no-select');
      _setCheck(_drag.anchor, _drag.target);
      scheduleSelCount();
    }
    const cur = _rowIndexFromPoint(e.clientX, e.clientY);
    if (cur == null || cur === _drag.lastCur) return;
    _restoreRange(_drag.anchor, _drag.lastCur);     // ล้างช่วงเดิม
    _applyRange(_drag.anchor, cur, _drag.target);    // ทาช่วงใหม่
    _drag.lastCur = cur;
    scheduleSelCount();
  });

  document.addEventListener('mouseup', () => {
    if (_drag.active) _lastCheckIndex = _drag.lastCur;
    _drag.pending = false;
    _drag.active = false;
    _drag.snap = null;
    document.body.classList.remove('no-select');
    updateSelectedCount();
  });

  // Shift+คลิกที่ช่องติ๊ก = เลือกต่อเนื่องจากอันที่ติ๊กล่าสุด
  document.addEventListener('click', (e) => {
    const cb = e.target.closest && e.target.closest('.file-check');
    if (!cb) return;
    const idx = parseInt(cb.dataset.index);
    if (e.shiftKey && _lastCheckIndex != null) _applyRange(_lastCheckIndex, idx, cb.checked);
    _lastCheckIndex = idx;
    updateSelectedCount();
  });
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
        let msg = `ลบแล้ว ${resp.deleted} รายการ`;
        if (resp.failed) {
          msg += `, ล้มเหลว ${resp.failed}`;
          if (resp.errors && resp.errors.length) msg += ' — ' + resp.errors[0];
        }
        toast(msg, resp.failed ? 'error' : 'success');
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
//  TRANSFER — ย้าย/คัดลอกไฟล์ ข้ามเครื่อง (เครื่อง 1 → เครื่อง 2)
//  หลักการ: ดึงไฟล์จากเครื่องต้นทาง → ส่งขึ้นเครื่องปลายทาง
//           (ปลายทาง resolve โฟลเดอร์เองจาก base_match/subpath) → ลบต้นทาง (ถ้าเลือกย้าย)
// ═══════════════════════════════════════════════════════════
let _txFiles = [];      // ไฟล์ที่เลือกไว้ย้าย
let _txSource = null;   // agent_id ต้นทาง
let _txBusy = false;
let _txCancel = false;  // ตั้ง true เพื่อหยุดกลางคัน
let _txDone = 0, _txTotal = 0;  // ความคืบหน้าปัจจุบัน (ใช้ในข้อความเตือน)

function txAgentLabel(agentId) {
  const a = (agentsData || []).find(x => x.agent_id === agentId);
  return a ? (a.name || a.hostname || a.agent_id) : agentId;
}

// โฟลเดอร์โปรเจกต์ (allowed_path) ที่ครอบ currentPath อยู่
function currentProjectRoot() {
  const agent = agentsData.find(a => a.agent_id === currentAgent);
  const allowed = (agent && agent.allowed_paths) || [];
  return allowed.find(p => sameRoot(currentPath, p)) || null;
}

function openTransfer() {
  const files = getSelectedFiles().filter(f => !f.is_dir);
  if (!files.length) { toast('เลือกไฟล์ (ไม่ใช่โฟลเดอร์) ที่จะย้ายก่อน', 'info'); return; }

  const others = (agentsData || []).filter(a => a.agent_id !== currentAgent);
  if (!others.length) { toast('ไม่มีเครื่องปลายทางอื่นที่ออนไลน์', 'error'); return; }

  _txFiles = files;
  _txSource = currentAgent;

  // เดาโฟลเดอร์ปลายทางจากตำแหน่งปัจจุบัน → base_match (ชื่อโปรเจกต์) + subpath (โฟลเดอร์ย่อย)
  const root = currentProjectRoot();
  const baseMatch = root ? baseName(root) : '';
  let subpath = '';
  if (root) {
    const np = currentPath.replace(/\\/g, '/');
    const nr = root.replace(/\\/g, '/');
    subpath = np.slice(nr.length).replace(/^\/+/, '');
  }

  document.getElementById('transferSummary').innerHTML =
    `จะส่ง <b>${files.length}</b> ไฟล์ จากเครื่อง <b>${escHtml(txAgentLabel(currentAgent))}</b> ไปเครื่องที่เลือก`;
  document.getElementById('transferDest').innerHTML =
    others.map(a => `<option value="${escAttr(a.agent_id)}">🖥️ ${escHtml(a.name || a.hostname || a.agent_id)}</option>`).join('');
  document.getElementById('transferBase').value = baseMatch;
  document.getElementById('transferSub').value = subpath;
  document.getElementById('transferDelete').checked = true;
  document.getElementById('transferStartBtn').textContent = '🚀 เริ่มย้าย';
  document.getElementById('transferStartBtn').disabled = false;
  document.getElementById('transferProgress').innerHTML = '';
  document.getElementById('transferStatus').style.display = 'none';
  document.getElementById('transferCloseBtn').textContent = 'ปิด';
  _txCancel = false;
  document.getElementById('transferModal').classList.add('show');
}

// ปุ่มมุมซ้ายล่าง: ระหว่างย้าย = หยุด (เตือนก่อน), ตอนไม่ย้าย = ปิดหน้าต่าง
function transferCloseOrCancel() {
  if (_txBusy) {
    if (!confirm(`⏳ กำลังย้ายไฟล์อยู่ (${_txDone}/${_txTotal})\n\nต้องการหยุดการย้ายหรือไม่?\n(ไฟล์ที่ย้ายไปแล้วจะไม่ถูกยกเลิก)`)) return;
    _txCancel = true;
    document.getElementById('transferCloseBtn').textContent = '⏳ กำลังหยุด...';
    return;
  }
  closeModal('transferModal');
}

// Uint8Array → base64 (ทำเป็นก้อนกัน call stack ล้นตอนไฟล์ใหญ่)
function bytesToBase64(bytes) {
  let bin = '';
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  }
  return btoa(bin);
}

// ── ถอด base64 หนึ่ง chunk เป็น bytes ──
function b64ToBytes(b64) {
  const bin = atob(b64 || '');
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}
// ── ต่อ Uint8Array หลายก้อนเป็นก้อนเดียว ──
function concatBytes(parts) {
  let total = 0;
  for (const p of parts) total += p.length;
  const out = new Uint8Array(total);
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

// ดึง bytes ของไฟล์จากเครื่องที่ระบุ (เรียกทีละไฟล์เท่านั้น)
function fetchFileBytesFrom(agentId, filePath) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (fn, val) => { if (!settled) { settled = true; fn(val); } };
    const onSent = (data) => {
      const rid = data.request_id;
      let chunks = [];
      const onChunk = (chunk) => {
        if (chunk.error) { socket.off('file_chunk_' + rid, onChunk); finish(reject, new Error(chunk.error)); return; }
        // ต้องถอด base64 ทีละ chunk — ห้าม join ก่อน เพราะ CHUNK_SIZE (512KB) ไม่หารด้วย 3 ลงตัว
        // แต่ละ chunk เลยมี '=' ปิดท้าย พอต่อกันจะกลายเป็น base64 ที่มี '=' อยู่กลาง = atob พัง
        try {
          chunks.push(b64ToBytes(chunk.data));
        } catch (e) {
          socket.off('file_chunk_' + rid, onChunk);
          finish(reject, new Error('ถอดรหัสไฟล์ไม่ได้: ' + (e.message || e)));
          return;
        }
        if (chunk.is_last) {
          socket.off('file_chunk_' + rid, onChunk);
          finish(resolve, concatBytes(chunks));
        }
      };
      socket.on('file_chunk_' + rid, onChunk);
      socket.once('response_' + rid, (resp) => {
        if (resp && resp.error) { socket.off('file_chunk_' + rid, onChunk); finish(reject, new Error(resp.error)); }
      });
    };
    socket.once('request_sent', onSent);
    setTimeout(() => { socket.off('request_sent', onSent); finish(reject, new Error('หมดเวลาดึงไฟล์จากต้นทาง')); }, 120000);
    socket.emit('request_download', { agent_id: agentId, path: filePath });
  });
}

// ส่ง base64 ขึ้นเครื่องปลายทาง (ให้ agent resolve โฟลเดอร์เองจาก base_match/subpath)
function uploadBase64ToAgent(agentId, opts) {
  return new Promise((resolve, reject) => {
    const uploadId = 'tx_' + Math.random().toString(36).slice(2, 8);
    const CHUNK = 512 * 1024;
    const b64 = opts.base64;
    const total = Math.ceil(b64.length / CHUNK) || 1;
    let settled = false;
    const onReady = (info) => {
      if (info.upload_id !== uploadId) return;
      socket.off('upload_ready', onReady);
      const rid = info.request_id;
      socket.once('response_' + rid, (resp) => {
        settled = true;
        if (resp && resp.error) reject(new Error(resp.error));
        else resolve(resp);
      });
      for (let i = 0; i < total; i++) {
        socket.emit('request_upload_chunk', {
          agent_id: agentId,
          request_id: rid,
          data: b64.slice(i * CHUNK, (i + 1) * CHUNK),
          is_last: (i === total - 1),
        });
      }
    };
    socket.on('upload_ready', onReady);
    const startMsg = {
      agent_id: agentId,
      upload_id: uploadId,
      dest_path: opts.filename,          // fallback; ถ้ามี base_match/subpath ปลายทางจะ override
      filename: opts.filename,
      file_size: opts.fileSize,
    };
    if (opts.baseMatch) startMsg.base_match = opts.baseMatch;
    if (opts.subpath != null && opts.subpath !== '') startMsg.subpath = opts.subpath;
    socket.emit('request_upload_start', startMsg);
    setTimeout(() => {
      if (!settled) { socket.off('upload_ready', onReady); reject(new Error('หมดเวลา (ปลายทางไม่ตอบ)')); }
    }, 120000);
  });
}

// ลบไฟล์เดียวในเครื่องที่ระบุ (คืน resp เสมอ ไม่ throw เพื่อไม่ให้ลูปสะดุด)
function deleteFileOn(agentId, filePath) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (!settled) { settled = true; resolve(v); } };
    const onSent = (data) => {
      socket.once('response_' + data.request_id, (resp) => done(resp || {}));
    };
    socket.once('request_sent', onSent);
    setTimeout(() => { socket.off('request_sent', onSent); done({ error: 'หมดเวลาลบต้นทาง' }); }, 30000);
    socket.emit('request_delete', { agent_id: agentId, path: filePath });
  });
}

async function startTransfer() {
  if (_txBusy) return;
  const destAgent = document.getElementById('transferDest').value;
  const baseMatch = document.getElementById('transferBase').value.trim();
  const subpath = document.getElementById('transferSub').value.trim();
  const deleteSource = document.getElementById('transferDelete').checked;

  if (!destAgent) { toast('เลือกเครื่องปลายทางก่อน', 'info'); return; }
  if (destAgent === _txSource) { toast('ต้นทางกับปลายทางเป็นเครื่องเดียวกัน', 'error'); return; }
  if (!baseMatch && !subpath) {
    if (!confirm('ไม่ได้ระบุโฟลเดอร์ปลายทาง — ไฟล์จะไปที่ Desktop ของเครื่องปลายทาง\nต้องการดำเนินการต่อหรือไม่?')) return;
  }

  _txBusy = true;
  _txCancel = false;
  const verb = deleteSource ? 'ย้าย' : 'คัดลอก';
  const startBtn = document.getElementById('transferStartBtn');
  const closeBtn = document.getElementById('transferCloseBtn');
  startBtn.disabled = true;
  startBtn.textContent = '⏳ กำลัง' + verb + '...';
  closeBtn.textContent = '⏹️ หยุด';

  const statusWrap = document.getElementById('transferStatus');
  const statusText = document.getElementById('txStatusText');
  const statusCount = document.getElementById('txStatusCount');
  const statusBar = document.getElementById('txStatusBar');
  const statusCur = document.getElementById('txStatusCur');
  const logEl = document.getElementById('transferProgress');
  statusWrap.style.display = 'block';
  statusBar.style.background = '';
  logEl.innerHTML = '';

  const N = _txFiles.length;
  _txTotal = N; _txDone = 0;
  const logLines = [];
  const addLog = (html) => {
    logLines.push(html);
    if (logLines.length > 40) logLines.shift();   // เก็บแค่ ~40 บรรทัดล่าสุด (กันรก/หน่วงตอนไฟล์เยอะ)
    logEl.innerHTML = logLines.join('');
    logEl.scrollTop = logEl.scrollHeight;
  };
  const updateStatus = (done, curName) => {
    _txDone = done;
    const pct = N ? Math.round((done / N) * 100) : 100;
    statusBar.style.width = pct + '%';
    statusText.textContent = `⏳ กำลัง${verb} ${done}/${N} (${pct}%)`;
    statusCount.innerHTML = `<span style="color:#22c55e">✅ ${ok}</span>` + (fail ? ` · <span style="color:var(--danger)">❌ ${fail}</span>` : '');
    statusCur.textContent = curName ? ('กำลังทำ: ' + curName) : '';
  };

  let ok = 0, fail = 0;
  updateStatus(0, '');
  for (let i = 0; i < N; i++) {
    if (_txCancel) { addLog('<div style="color:#f59e0b">⏹️ หยุดโดยผู้ใช้</div>'); break; }
    const f = _txFiles[i];
    updateStatus(i, f.name);
    try {
      const bytes = await fetchFileBytesFrom(_txSource, f.full_path);
      await uploadBase64ToAgent(destAgent, {
        filename: f.name, base64: bytesToBase64(bytes), fileSize: bytes.length,
        baseMatch: baseMatch, subpath: subpath,
      });
      if (deleteSource) {
        const dresp = await deleteFileOn(_txSource, f.full_path);
        if (dresp && dresp.error) {
          addLog(`<div style="color:#f59e0b">⚠️ ${escHtml(f.name)} — ส่งแล้วแต่ลบต้นทางไม่ได้</div>`);
          ok++; updateStatus(i + 1, ''); continue;
        }
      }
      ok++;
      if (i < 3 || (i + 1) % 20 === 0) addLog(`<div style="color:#22c55e">✅ ${escHtml(f.name)}</div>`);  // log เป็นช่วง กันรก
    } catch (e) {
      fail++;
      addLog(`<div style="color:var(--danger)">❌ ${escHtml(f.name)} — ${escHtml(e.message || 'ล้มเหลว')}</div>`);
    }
    updateStatus(i + 1, '');
  }

  const done = ok + fail;
  _txBusy = false;
  _txCancel = false;
  startBtn.disabled = false;
  startBtn.textContent = '🚀 เริ่ม' + verb;
  closeBtn.textContent = 'ปิด';
  statusBar.style.width = '100%';
  statusBar.style.background = fail ? 'var(--danger)' : 'var(--success)';
  statusText.textContent = (done < N ? `⏹️ หยุดแล้ว — ${verb}ไป ${done}/${N}` : `✅ ${verb}เสร็จ ${ok}/${N}`);
  addLog(`<div style="font-weight:700; margin-top:8px; color:var(--text-primary)">สรุป: สำเร็จ ${ok}` + (fail ? `, ล้มเหลว ${fail}` : '') + (done < N ? `, ยังไม่ทำ ${N - done}` : '') + `</div>`);
  toast(`${verb}เสร็จ: สำเร็จ ${ok}` + (fail ? `, ล้มเหลว ${fail}` : ''), fail ? 'error' : 'success');
  if (deleteSource && ok) loadDir(currentPath);   // รีเฟรชต้นทาง (ตัวนับไฟล์อัปเดตตาม)
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
  initDragSelect();

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

  // Close modal on overlay click (แต่ถ้ากำลังย้ายไฟล์อยู่ ให้เตือน ไม่ปิด)
  document.querySelectorAll('.modal-overlay').forEach(m => {
    m.addEventListener('click', (e) => {
      if (e.target !== m) return;
      if (m.id === 'transferModal' && _txBusy) {
        alert(`⏳ กำลังย้ายไฟล์อยู่ (${_txDone}/${_txTotal})\n\nกรุณารอจนเสร็จ หรือกดปุ่ม "หยุด" เพื่อยกเลิกก่อน`);
        return;   // ไม่ปิดหน้าต่างระหว่างย้าย
      }
      m.classList.remove('show');
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
