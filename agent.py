"""
=============================================================
  Remote File Manager - Agent (เครื่องลูก)
  รันบนเครื่องลูกแต่ละเครื่อง เพื่อให้เครื่องหลักเข้าถึงไฟล์ได้
=============================================================
  วิธีใช้:
    python agent.py
    
  ตั้งค่าผ่าน Environment Variables หรือแก้ใน CONFIG ด้านล่าง
=============================================================
"""

import os
import re
import sys
import json
import time
import base64
import socket
import shutil
import stat
import platform
import logging
import threading
import subprocess
import queue
from pathlib import Path
from datetime import datetime

import socketio

# ─── CONFIG ───────────────────────────────────────────────
# แก้ค่าได้ง่ายๆ ในไฟล์ config.json (วางไว้โฟลเดอร์เดียวกับ agent.py)
# ลำดับความสำคัญ: environment variable > config.json > ค่าเริ่มต้นด้านล่าง

def _load_config():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(p):
        try:
            # utf-8-sig รองรับทั้งไฟล์ที่มี BOM และไม่มี (เช่นถูกเขียนโดย PowerShell)
            with open(p, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] อ่าน config.json ไม่ได้ ใช้ค่าเริ่มต้นแทน: {e}")
    return {}

_cfg = _load_config()


def _parse_server_urls():
    """รวมรายชื่อ server ที่จะเชื่อมต่อ (รองรับหลายเครื่องพร้อมกัน เช่น server + nuuboy)
       ลำดับความสำคัญ:
         env SERVER_URLS (คั่นด้วย , หรือ ;) > env SERVER_URL >
         config 'server_urls' (list หรือ string) > config 'server_url' > ค่าเริ่มต้น"""
    raw = []
    env_multi = os.environ.get("SERVER_URLS", "").strip()
    env_single = os.environ.get("SERVER_URL", "").strip()
    if env_multi:
        raw = env_multi.replace(";", ",").split(",")
    elif env_single:
        raw = [env_single]
    else:
        cfg_multi = _cfg.get("server_urls")
        cfg_single = _cfg.get("server_url")
        if isinstance(cfg_multi, list) and cfg_multi:
            raw = [str(u) for u in cfg_multi]
        elif isinstance(cfg_multi, str) and cfg_multi.strip():
            raw = cfg_multi.replace(";", ",").split(",")
        elif cfg_single:
            raw = [str(cfg_single)]
    # ทำความสะอาด + ตัด url ซ้ำ (คงลำดับเดิม)
    seen, urls = set(), []
    for u in raw:
        u = u.strip().rstrip("/")
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls or ["http://YOUR_SERVER_IP:5000"]


SERVER_URLS = _parse_server_urls()
SERVER_URL = SERVER_URLS[0]   # server หลัก (ใช้ตอนแสดงผล/auto-update)
AGENT_SECRET = os.environ.get("AGENT_SECRET") or _cfg.get("agent_secret") or "my-agent-secret-2024"
AGENT_ID = os.environ.get("AGENT_ID") or _cfg.get("agent_id") or ""  # ปล่อยว่าง = ใช้ชื่อเครื่อง
AGENT_NAME = os.environ.get("AGENT_NAME") or _cfg.get("name") or ""  # ชื่อที่แสดงในเว็บ (ปล่อยว่าง = ใช้ hostname)

# โฟลเดอร์ที่อนุญาต: env ALLOWED_PATHS (คั่น ;) > config.json "allowed_paths" (list หรือ string) > ค่าเริ่มต้น
# เขียนแบบสั้นได้ เช่น "Desktop/cookie-run" → agent จะเติมโฟลเดอร์ home (C:\Users\<user>\) ให้เอง
# ทำให้ config ก้อนเดียวใช้ได้ทุกเครื่อง ไม่ต้องแก้ username

def _norm_path(p):
    """แปลงเป็น absolute path: ถ้าเป็น relative จะอิงจาก home ของ user เครื่องนั้น
       เช่น 'Desktop/cookie-run' -> C:\\Users\\<user>\\Desktop\\cookie-run"""
    p = os.path.expanduser(str(p).strip())
    if not os.path.isabs(p):
        p = os.path.join(os.path.expanduser("~"), p)
    return os.path.abspath(p)

# โปรเจกต์/เกมหลักที่ต้องเข้าถึงได้ทุกเครื่อง (เพิ่มเกมใหม่ตรงนี้ → อัปเดต agent.py = ได้ทุกเครื่อง)
DEFAULT_ALLOWED_PATHS = [
    "Desktop/pes",
    "Desktop/cookie-run",
    "Desktop/main",        # เกม Line Ranger
]
_env_allowed = os.environ.get("ALLOWED_PATHS", "").strip()
_cfg_allowed = _cfg.get("allowed_paths")
if _env_allowed:
    _raw_allowed = [p for p in _env_allowed.split(";") if p.strip()]
elif isinstance(_cfg_allowed, list) and _cfg_allowed:
    _raw_allowed = [str(p) for p in _cfg_allowed if str(p).strip()]
elif isinstance(_cfg_allowed, str) and _cfg_allowed.strip():
    _raw_allowed = [p for p in _cfg_allowed.split(";") if p.strip()]
else:
    _raw_allowed = list(DEFAULT_ALLOWED_PATHS)
ALLOWED_PATHS = [_norm_path(p) for p in _raw_allowed]

# ถ้าไม่ได้ override ด้วย env ALLOWED_PATHS → รวมโปรเจกต์หลัก (pes, cookie-run, main) เข้าไปเสมอ
# ทำให้เพิ่มโปรเจกต์ใหม่ให้ทุกเครื่องได้ผ่านการอัปเดต agent.py โดยไม่ต้องแก้ config.json ทีละเครื่อง
if not _env_allowed:
    for _d in DEFAULT_ALLOWED_PATHS:
        _dn = _norm_path(_d)
        if _dn not in ALLOWED_PATHS:
            ALLOWED_PATHS.append(_dn)

# โฟลเดอร์ id ของ dashboard cookie-run (กำหนดเองได้)
# ลำดับ: env COOKIE_ID_PATH > config.json "cookie_id_path" > ปล่อยว่าง (ใช้วิธีเดาจาก allowed_paths + base_match)
COOKIE_ID_PATH = (os.environ.get("COOKIE_ID_PATH") or _cfg.get("cookie_id_path") or "").strip()

# โฟลเดอร์ input-id (ที่รับไฟล์จากปุ่ม broadcast + ปุ่ม clear) กำหนดเองได้ ปล่อยว่าง = ใช้ base_match + subpath
COOKIE_INPUT_PATH = (os.environ.get("COOKIE_INPUT_PATH") or _cfg.get("cookie_input_path") or "").strip()

# MuMu Player 12: path ของ MuMuManager.exe (ปล่อยว่าง = ค้นหาอัตโนมัติจาก path มาตรฐาน)
MUMU_MANAGER_PATH = (os.environ.get("MUMU_MANAGER_PATH") or _cfg.get("mumu_manager_path") or "").strip()

CHUNK_SIZE = 512 * 1024  # 512KB per chunk
RECONNECT_DELAY = 5  # seconds

# ─── LOGGING ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ─── SOCKET.IO CLIENTS (หลาย server พร้อมกัน) ─────────────
# Agent เชื่อมต่อได้หลาย server พร้อมกัน (เช่น "server" และ "nuuboy")
# ทุก server เห็นเครื่องนี้ออนไลน์ และ ดู/ดาวน์โหลด/อัปโหลด/แก้ไข/ลบ ได้เท่ากันทั้งหมด
_clients = {}                 # url -> socketio.Client
_client_connected = {}        # url -> bool (เชื่อมต่ออยู่ไหม)
_local = threading.local()    # เก็บ client ของ thread ที่กำลังจัดการคำสั่ง (ตอบกลับให้ถูก server)


def _current_client():
    """client ของ server ที่ส่งคำสั่งนี้เข้ามา — ใช้ตอบกลับให้กลับไปเครื่องที่สั่ง"""
    return getattr(_local, "client", None)


def _any_connected():
    return any(c.connected for c in _clients.values())


def _connected_count():
    return sum(1 for c in _clients.values() if c.connected)


def _disconnect_all():
    for c in list(_clients.values()):
        try:
            if c.connected:
                c.disconnect()
        except Exception:
            pass


def _spawn_with_client(target):
    """เปิด thread ใหม่โดยพก client ปัจจุบันติดไปด้วย
       (thread ใหม่ไม่สืบทอด thread-local เอง จึงต้อง set ให้)"""
    client = _current_client()

    def _wrapped():
        _local.client = client
        target()

    threading.Thread(target=_wrapped, daemon=True).start()

# เก็บสถานะการอัปโหลดแบบแบ่ง chunk (req_id -> {file, path, received})
upload_sessions = {}
upload_errors = {}   # req_id -> เหตุผลที่ upload_start ล้มเหลว (ให้ chunk รายงานเหตุผลจริง ไม่ใช่ "session หาย")

_single_instance_handle = None  # เก็บ handle ของ mutex กัน agent เปิดซ้ำ

# ── สำหรับหน้าต่างสถานะ (status window) ──
_log_queue = queue.Queue(maxsize=2000)          # log ที่จะโชว์ในหน้าต่าง
_ui_state = {"connected": False, "show_request": False}


class _QueueLogHandler(logging.Handler):
    """ส่ง log แต่ละบรรทัดเข้า queue เพื่อให้หน้าต่างสถานะดึงไปแสดง"""
    def emit(self, record):
        try:
            _log_queue.put_nowait(self.format(record))
        except Exception:
            pass


def _attach_ui_log_handler():
    h = _QueueLogHandler()
    h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
    logging.getLogger().addHandler(h)


# ═══════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

def get_hostname():
    return platform.node() or socket.gethostname()


def get_local_ip():
    """หา local IP address"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def get_os_info():
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


def is_path_allowed(path):
    """ตรวจสอบว่า path อยู่ใน allowed paths หรือไม่"""
    if not ALLOWED_PATHS:
        return True  # ถ้าไม่กำหนด = เข้าถึงได้ทุกที่

    path = os.path.abspath(path)
    for allowed in ALLOWED_PATHS:
        allowed = os.path.abspath(allowed.strip())
        if path.startswith(allowed):
            return True
    return False


def get_windows_drives():
    """หา drive letters ที่มีอยู่ใน Windows"""
    drives = []
    if platform.system() == "Windows":
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            if bitmask & 1:
                drive = f"{letter}:\\"
                try:
                    # ตรวจว่า drive เข้าถึงได้
                    os.listdir(drive)
                    drives.append(drive)
                except (PermissionError, OSError):
                    drives.append(drive)  # ยังแสดงแม้เข้าถึงไม่ได้
            bitmask >>= 1
    return drives


def format_file_info(path, name):
    """สร้าง dict ข้อมูลไฟล์"""
    full_path = os.path.join(path, name)
    try:
        stat = os.stat(full_path)
        is_dir = os.path.isdir(full_path)
        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        return {
            "name": name,
            "full_path": full_path,
            "is_dir": is_dir,
            "size": 0 if is_dir else stat.st_size,
            "modified": modified,
        }
    except (PermissionError, OSError) as e:
        return {
            "name": name,
            "full_path": full_path,
            "is_dir": os.path.isdir(full_path) if os.path.exists(full_path) else False,
            "size": 0,
            "modified": "-",
            "error": str(e),
        }


# ═══════════════════════════════════════════════════════════
#  SOCKET.IO EVENT HANDLERS
# ═══════════════════════════════════════════════════════════

def _dispatch_command(data):
    """แยกคำสั่งจาก server ไปยัง handler (client ปัจจุบันถูก set ไว้ใน _local แล้ว)"""
    req_id = data.get("request_id")
    action = data.get("action")
    payload = data.get("data", {})

    logger.info(f"📥 Command: {action} (req: {req_id})")

    try:
        if action == "list_dir":
            handle_list_dir(req_id, payload)
        elif action == "download_file":
            handle_download(req_id, payload)
        elif action == "upload_file":
            handle_upload(req_id, payload)
        elif action == "upload_start":
            handle_upload_start(req_id, payload)
        elif action == "upload_chunk":
            handle_upload_chunk(req_id, payload)
        elif action == "delete_file":
            handle_delete(req_id, payload)
        elif action == "delete_many":
            handle_delete_many(req_id, payload)
        elif action == "count_heroes":
            handle_count_heroes(req_id, payload)
        elif action == "count_prefix_ids":
            handle_count_prefix_ids(req_id, payload)
        elif action == "export_folder":
            handle_export_folder(req_id, payload)
        elif action == "list_ids":
            handle_list_ids(req_id, payload)
        elif action == "rename_file":
            handle_rename(req_id, payload)
        elif action == "move_file":
            handle_move(req_id, payload)
        elif action == "shutdown":
            handle_shutdown(req_id, payload)
        elif action == "clear_input":
            handle_clear_input(req_id, payload)
        elif action == "self_update":
            handle_self_update(req_id, payload)
        elif action == "screenshot":
            handle_screenshot(req_id, payload)
        elif action == "mumu_control":
            handle_mumu_control(req_id, payload)
        elif action == "mumu_clone":
            handle_mumu_clone(req_id, payload)
        elif action == "run_file":
            handle_run_file(req_id, payload)
        else:
            send_response(req_id, {"error": f"Unknown action: {action}"})
    except Exception as e:
        logger.error(f"Error handling {action}: {e}")
        send_response(req_id, {"error": str(e)})


def send_response(req_id, data):
    """ตอบกลับไปยัง server ที่ส่งคำสั่งนี้เข้ามา (ไม่ใช่ทุก server)"""
    data["request_id"] = req_id
    client = _current_client()
    if client is None:
        logger.warning("ไม่มี client ปัจจุบัน — ตอบกลับไม่ได้")
        return
    try:
        client.emit("agent_response", data)
    except Exception as e:
        logger.warning(f"ส่ง response ไม่สำเร็จ: {e}")


def _make_client(url):
    """สร้าง socketio client 1 ตัวต่อ server 1 เครื่อง พร้อมผูก event handler ครบชุด"""
    client = socketio.Client(
        reconnection=True,
        reconnection_delay=RECONNECT_DELAY,
        reconnection_delay_max=30,
        logger=False,
    )

    @client.event
    def connect():
        _local.client = client
        _client_connected[url] = True
        _ui_state["connected"] = True
        logger.info(f"✅ Connected to server: {url}")
        agent_id = AGENT_ID or AGENT_NAME or get_hostname()  # ใช้ name ถ้ามี กัน hostname ซ้ำชนกัน
        client.emit("agent_register", {
            "secret": AGENT_SECRET,
            "agent_id": agent_id,
            "name": AGENT_NAME,
            "hostname": get_hostname(),
            "os_info": get_os_info(),
            "ip": get_local_ip(),
            "allowed_paths": ALLOWED_PATHS,
            "ips": get_all_ips(),        # ทุกวง (LAN + Tailscale) ให้เพื่อนเลือกทางเร็วสุด
            "peer_port": PEER_PORT,
            "backups": _peer_cached_files(),
        })

    @client.event
    def disconnect():
        _client_connected[url] = False
        _ui_state["connected"] = _any_connected()
        logger.warning(f"❌ Disconnected from server: {url}")

    @client.on("registered")
    def on_registered(data):
        logger.info(f"📋 Registered as: {data.get('agent_id')} @ {url}")

    @client.on("auth_failed")
    def on_auth_failed(data):
        logger.error(f"🔒 Authentication failed @ {url}: {data.get('message')}")
        logger.error("ตรวจสอบ AGENT_SECRET ว่าตรงกับ server หรือไม่")

    @client.on("command")
    def on_command(data):
        _local.client = client   # ผูก response ให้กลับไปที่ server ที่สั่งมา
        _dispatch_command(data)

    return client


# ═══════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════

def handle_list_dir(req_id, data):
    """แสดงรายการไฟล์ในโฟลเดอร์"""
    path = data.get("path", "")

    # ถ้าไม่ระบุ path → แสดงจุดเริ่มต้น
    if not path:
        # ถ้ากำหนด ALLOWED_PATHS ไว้ → แสดงเฉพาะโฟลเดอร์ที่อนุญาต (ไม่แสดงทุกไดรฟ์)
        if ALLOWED_PATHS:
            files = [{
                "name": os.path.abspath(a.strip()),
                "full_path": os.path.abspath(a.strip()),
                "is_dir": True,
                "size": 0,
                "modified": "-",
            } for a in ALLOWED_PATHS if a.strip()]
            send_response(req_id, {"files": files, "path": ""})
            return
        if platform.system() == "Windows":
            drives = get_windows_drives()
            files = [{
                "name": d.rstrip("\\"),
                "full_path": d,
                "is_dir": True,
                "size": 0,
                "modified": "-",
            } for d in drives]
            send_response(req_id, {"files": files, "path": ""})
            return
        else:
            path = "/"

    # ตรวจสอบ permission
    if not is_path_allowed(path):
        send_response(req_id, {"error": f"ไม่มีสิทธิ์เข้าถึง: {path}"})
        return

    if not os.path.exists(path):
        send_response(req_id, {"error": f"ไม่พบ path: {path}"})
        return

    if not os.path.isdir(path):
        send_response(req_id, {"error": f"ไม่ใช่โฟลเดอร์: {path}"})
        return

    try:
        entries = os.listdir(path)
        files = []
        for name in entries:
            # ข้ามไฟล์ระบบที่ซ่อน
            if name.startswith('.') or name in ('$Recycle.Bin', 'System Volume Information'):
                continue
            info = format_file_info(path, name)
            files.append(info)

        # เพิ่มปุ่มย้อนกลับ (ไม่ให้ย้อนเกินขอบเขตที่อนุญาต)
        parent = os.path.dirname(path.rstrip("\\/"))
        if parent and parent != path and is_path_allowed(parent):
            files.insert(0, {
                "name": "..",
                "full_path": parent,
                "is_dir": True,
                "size": 0,
                "modified": "-",
            })

        send_response(req_id, {"files": files, "path": path})
        logger.info(f"  Listed {len(files)} items in {path}")

    except PermissionError:
        send_response(req_id, {"error": f"ไม่มีสิทธิ์เข้าถึง: {path}"})
    except Exception as e:
        send_response(req_id, {"error": str(e)})


def handle_download(req_id, data):
    """ส่งไฟล์ไป server (แบ่งเป็น chunks)"""
    path = data.get("path", "")

    if not is_path_allowed(path):
        send_response(req_id, {"error": "ไม่มีสิทธิ์เข้าถึงไฟล์นี้"})
        return

    if not os.path.isfile(path):
        send_response(req_id, {"error": f"ไม่พบไฟล์: {path}"})
        return

    client = _current_client()
    if client is None:
        logger.warning("ไม่มี client ปัจจุบัน — ส่งไฟล์ไม่ได้")
        send_response(req_id, {"error": "agent ไม่ได้เชื่อมต่อ server อยู่"})
        return

    try:
        file_size = os.path.getsize(path)
        logger.info(f"  Sending file: {path} ({file_size} bytes)")

        with open(path, "rb") as f:
            chunk_index = 0
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                # อ่านล่วงหน้า 1 ไบต์เพื่อรู้ว่าหมดไฟล์จริงไหม (ไฟล์อาจโตขึ้นระหว่างอ่าน
                # แล้ว f.tell() >= file_size จะไม่มีวันจริง → ไม่มี chunk ไหนถูกมาร์ก is_last
                # → ฝั่งเว็บรอจน timeout)
                pos = f.tell()
                is_last = not f.read(1)
                f.seek(pos)

                client.emit("file_chunk", {
                    "request_id": req_id,
                    "data": base64.b64encode(chunk).decode("ascii"),
                    "chunk_index": chunk_index,
                    "is_last": is_last,
                    "total_size": file_size,
                })
                chunk_index += 1
                time.sleep(0.01)  # ป้องกัน overwhelm

            # ไฟล์ขนาด 0 ไบต์: ลูปข้างบนไม่ส่ง chunk เลย ฝั่งเว็บเลยไม่เคยได้ is_last
            # ต้องส่ง chunk ว่างปิดท้ายให้ ไม่งั้นค้างรอจนหมดเวลา
            if chunk_index == 0:
                client.emit("file_chunk", {
                    "request_id": req_id, "data": "", "chunk_index": 0,
                    "is_last": True, "total_size": 0,
                })
                chunk_index = 1

        logger.info(f"  File sent: {chunk_index} chunks")

    except PermissionError:
        send_response(req_id, {"error": "ไม่มีสิทธิ์อ่านไฟล์นี้"})
    except Exception as e:
        send_response(req_id, {"error": str(e)})


def handle_upload(req_id, data):
    """รับไฟล์จาก server และบันทึก"""
    dest_path = data.get("path", "")
    filename = data.get("filename", "uploaded_file")
    file_data = data.get("file_data", "")

    # ถ้า dest_path เป็นแค่ชื่อไฟล์ ให้วางที่ Desktop
    if not os.path.dirname(dest_path):
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if os.path.exists(desktop):
            dest_path = os.path.join(desktop, filename)
        else:
            dest_path = os.path.join(os.path.expanduser("~"), filename)

    # ตรวจสอบ permission
    dest_dir = os.path.dirname(dest_path)
    if not is_path_allowed(dest_dir):
        send_response(req_id, {"error": f"ไม่มีสิทธิ์เขียนที่: {dest_dir}"})
        return

    try:
        os.makedirs(dest_dir, exist_ok=True)
        file_bytes = base64.b64decode(file_data)

        with open(dest_path, "wb") as f:
            f.write(file_bytes)

        logger.info(f"  File saved: {dest_path} ({len(file_bytes)} bytes)")
        send_response(req_id, {"success": True, "path": dest_path})

    except Exception as e:
        send_response(req_id, {"error": str(e)})


def handle_upload_start(req_id, data):
    """เริ่มรับไฟล์แบบแบ่ง chunk (เปิดไฟล์รอเขียน)"""
    dest_path = data.get("path", "")
    filename = data.get("filename", "uploaded_file")
    match = (data.get("base_match") or "").strip().lower()
    subpath = data.get("subpath")

    # โหมด broadcast: ระบุ base_match/subpath → วางไฟล์ในโฟลเดอร์ input-id ที่ resolve เอง
    if match or subpath:
        folder = _resolve_input_folder(match, subpath or "input-id")
        if not folder:
            upload_errors[req_id] = f"หาโฟลเดอร์ '{subpath or 'input-id'}' ของ '{match}' ไม่เจอ (ไม่มีใน allowed_paths / agent เก่า)"
            send_response(req_id, {"error": upload_errors[req_id]})
            return
        os.makedirs(folder, exist_ok=True)
        dest_path = os.path.join(folder, filename)

    # ถ้า dest_path เป็นแค่ชื่อไฟล์ ให้วางที่ Desktop
    if not os.path.dirname(dest_path):
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        base = desktop if os.path.exists(desktop) else os.path.expanduser("~")
        dest_path = os.path.join(base, filename)

    dest_dir = os.path.dirname(dest_path)
    if not is_path_allowed(dest_dir):
        upload_errors[req_id] = f"ไม่มีสิทธิ์เขียนที่: {dest_dir}"
        send_response(req_id, {"error": upload_errors[req_id]})
        return

    try:
        os.makedirs(dest_dir, exist_ok=True)
        f = open(dest_path, "wb")
        upload_sessions[req_id] = {"file": f, "path": dest_path, "received": 0}
        upload_errors.pop(req_id, None)   # start สำเร็จ ล้าง error เก่า (ถ้ามี)
        logger.info(f"  Upload start: {dest_path}")
        # ยังไม่ตอบกลับ รอ chunk สุดท้าย
    except Exception as e:
        upload_errors[req_id] = str(e)
        send_response(req_id, {"error": str(e)})


def handle_upload_chunk(req_id, data):
    """รับ chunk เขียนต่อท้ายไฟล์ และแตก zip อัตโนมัติเมื่อรับครบ"""
    # ถ้า upload_start ของ req นี้ล้มเหลวไปแล้ว → ตอบเหตุผลจริงทันที (fail เร็ว ทุก chunk ไม่ต้อง sleep)
    if req_id in upload_errors:
        reason = upload_errors.pop(req_id) if data.get("is_last") else upload_errors.get(req_id)
        send_response(req_id, {"error": reason})
        return

    sess = upload_sessions.get(req_id)
    if not sess:
        # chunk อาจมาก่อน upload_start ประมวลผลเสร็จ (ตอนอัปหลายไฟล์พร้อมกัน) → รอ session สักครู่
        for _ in range(40):
            time.sleep(0.05)
            if req_id in upload_errors:   # ระหว่างรอ ถ้า start ล้มเหลว → ตอบเหตุผลจริง
                send_response(req_id, {"error": upload_errors.get(req_id)})
                return
            sess = upload_sessions.get(req_id)
            if sess:
                break
    if not sess:
        send_response(req_id, {"error": "ไม่พบ session อัปโหลด (upload_start หาย/ล้มเหลว)"})
        return

    try:
        chunk_b64 = data.get("data", "")
        if chunk_b64:
            chunk = base64.b64decode(chunk_b64)
            sess["file"].write(chunk)
            sess["received"] += len(chunk)

        if data.get("is_last"):
            sess["file"].close()
            path = sess["path"]
            received = sess["received"]
            upload_sessions.pop(req_id, None)
            logger.info(f"  Upload complete: {path} ({received} bytes)")

            # ── แตกไฟล์ zip ลงโฟลเดอร์เดียวกับ zip ตรงๆ (ตัดโฟลเดอร์ครอบชั้นเดียวออก) ──
            if path.lower().endswith(".zip"):
                import zipfile
                try:
                    extract_dir = os.path.dirname(path)  # เช่น backup-id
                    dest_abs = os.path.abspath(extract_dir)
                    with zipfile.ZipFile(path, "r") as zf:
                        norm = [n.replace("\\", "/").lstrip("/") for n in zf.namelist() if n.strip()]
                        tops = set(n.split("/")[0] for n in norm)
                        # ถ้ามีโฟลเดอร์ครอบชั้นเดียว → ตัดชื่อโฟลเดอร์นั้นออก ให้ไฟล์ลง extract_dir ตรงๆ
                        strip = (list(tops)[0] + "/") if (len(tops) == 1 and all("/" in n for n in norm)) else ""
                        for info in zf.infolist():
                            name = info.filename.replace("\\", "/").lstrip("/")
                            if strip and name.startswith(strip):
                                name = name[len(strip):]
                            if not name:
                                continue
                            target = os.path.join(extract_dir, *name.split("/"))
                            target_abs = os.path.abspath(target)
                            if not (target_abs == dest_abs or target_abs.startswith(dest_abs + os.sep)):
                                continue  # กัน zip-slip
                            if name.endswith("/"):
                                os.makedirs(target, exist_ok=True)
                            else:
                                os.makedirs(os.path.dirname(target), exist_ok=True)
                                with zf.open(info) as src, open(target, "wb") as dst:
                                    shutil.copyfileobj(src, dst)
                    logger.info(f"  Auto-extracted zip (flat) -> {extract_dir}")
                    send_response(req_id, {"success": True, "path": path,
                                           "extracted": True, "extract_dir": extract_dir})
                except Exception as ze:
                    logger.error(f"  Extract failed: {ze}")
                    send_response(req_id, {"success": True, "path": path,
                                           "extracted": False, "extract_error": str(ze)})
            else:
                send_response(req_id, {"success": True, "path": path, "extracted": False})

    except Exception as e:
        sess = upload_sessions.pop(req_id, None)
        if sess:
            try:
                sess["file"].close()
            except Exception:
                pass
        send_response(req_id, {"error": str(e)})


def handle_delete(req_id, data):
    """ลบไฟล์หรือโฟลเดอร์"""
    path = data.get("path", "")

    if not is_path_allowed(path):
        send_response(req_id, {"error": "ไม่มีสิทธิ์ลบ"})
        return

    if not os.path.exists(path):
        send_response(req_id, {"error": f"ไม่พบ: {path}"})
        return

    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
            logger.info(f"  Deleted folder: {path}")
        else:
            os.remove(path)
            logger.info(f"  Deleted file: {path}")
        send_response(req_id, {"success": True})

    except PermissionError:
        send_response(req_id, {"error": "ไม่มีสิทธิ์ลบ"})
    except Exception as e:
        send_response(req_id, {"error": str(e)})


def _force_remove(path):
    """ลบไฟล์/โฟลเดอร์ให้ได้แม้เป็น read-only (เคลียร์ attribute ก่อนลบ)
       ไม่สามารถลบไฟล์ที่ถูกโปรแกรมอื่นเปิดค้างอยู่ได้ (จะโยน PermissionError)"""
    def _onerror(func, p, exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=_onerror)
    else:
        try:
            os.remove(path)
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)


def handle_delete_many(req_id, data):
    """ลบหลายไฟล์/โฟลเดอร์ในคำสั่งเดียว (เร็วกว่าลบทีละไฟล์มาก) + บอกเหตุผลรายไฟล์"""
    paths = data.get("paths", [])
    deleted = 0
    failed = 0
    errors = []
    for p in paths:
        name = os.path.basename(p.rstrip("\\/")) or p
        try:
            if not is_path_allowed(p):
                raise PermissionError("ไม่อยู่ในโฟลเดอร์ที่อนุญาต")
            if not os.path.exists(p):
                raise FileNotFoundError("ไม่พบไฟล์/โฟลเดอร์")
            _force_remove(p)
            deleted += 1
        except Exception as e:
            failed += 1
            msg = f"{name}: {e}"
            logger.warning(f"  delete fail - {msg}")
            if len(errors) < 8:
                errors.append(msg)
    logger.info(f"  Bulk delete: {deleted} deleted, {failed} failed ({len(paths)} requested)")
    send_response(req_id, {"success": True, "deleted": deleted, "failed": failed, "errors": errors})


def handle_rename(req_id, data):
    """เปลี่ยนชื่อไฟล์/โฟลเดอร์"""
    old_path = data.get("old_path", "")
    new_name = data.get("new_name", "")

    if not is_path_allowed(old_path):
        send_response(req_id, {"error": "ไม่มีสิทธิ์"})
        return

    if not os.path.exists(old_path):
        send_response(req_id, {"error": f"ไม่พบ: {old_path}"})
        return

    if not new_name or '/' in new_name or '\\' in new_name:
        send_response(req_id, {"error": "ชื่อไม่ถูกต้อง"})
        return

    try:
        parent = os.path.dirname(old_path)
        new_path = os.path.join(parent, new_name)

        if os.path.exists(new_path):
            send_response(req_id, {"error": f"มีไฟล์ชื่อนี้อยู่แล้ว: {new_name}"})
            return

        os.rename(old_path, new_path)
        logger.info(f"  Renamed: {old_path} → {new_path}")
        send_response(req_id, {"success": True, "new_path": new_path})

    except Exception as e:
        send_response(req_id, {"error": str(e)})


def handle_move(req_id, data):
    """ย้ายไฟล์/โฟลเดอร์"""
    src = data.get("src_path", "")
    dest = data.get("dest_path", "")

    if not is_path_allowed(src) or not is_path_allowed(dest):
        send_response(req_id, {"error": "ไม่มีสิทธิ์"})
        return

    if not os.path.exists(src):
        send_response(req_id, {"error": f"ไม่พบ: {src}"})
        return

    try:
        shutil.move(src, dest)
        logger.info(f"  Moved: {src} → {dest}")
        send_response(req_id, {"success": True})
    except Exception as e:
        send_response(req_id, {"error": str(e)})


_ID_SEG = re.compile(r"\d{4,}")          # โค้ดท้ายไฟล์อย่าง ASCV188565062-[10] มีตัวเลขยาว ชื่อคนไม่มี


def _read_hero_list(base):
    """อ่านรายชื่อฮีโร่จาก list_find_hero ในไฟล์ config ของเกม (เช่น pes\\config.py)
       คืน [] ถ้าไม่เจอ — ผู้เรียกจะ fallback ไปใช้ลิสต์ที่ server ส่งมาแทน"""
    for fn in ("config.py", "config.json", "configmain.json"):
        p = os.path.join(base, fn)
        if not os.path.isfile(p):
            continue
        try:
            txt = io_open_text(p)
        except Exception:
            continue
        m = re.search(r"list_find_hero\s*=\s*\[(.*?)\]", txt, re.S)
        if not m:
            continue
        names = re.findall(r'["\']([^"\']+)["\']', m.group(1))
        if names:
            logger.info(f"  อ่าน list_find_hero จาก {fn}: {len(names)} ชื่อ")
            return names
    return []


def io_open_text(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _hero_combo(filename, names_map):
    """แกะชื่อฮีโร่จากชื่อไฟล์ เช่น
         Lionel Messi+ASCV188565062-[10].dat        -> "Lionel Messi"
         Kevin De Bruyne+Lionel Messi+ASCV1885.dat  -> "Kevin De Bruyne+Lionel Messi"
       ตัดเฉพาะท่อนที่เป็นโค้ด (มีเลขติดกัน 4 ตัวขึ้นไป) ที่เหลือถือเป็นชื่อฮีโร่ทั้งหมด
       ชื่อที่อยู่ในลิสต์ config จะถูกแก้ตัวพิมพ์ให้ตรงลิสต์ ส่วนชื่อที่ไม่มีในลิสต์ก็ยังนับให้"""
    stem = os.path.splitext(filename)[0]
    out = []
    for p in stem.split("+"):
        p = p.strip()
        if not p or _ID_SEG.search(p):
            continue
        out.append(names_map.get(p.lower(), p))
    return "+".join(out) if out else None


def handle_count_heroes(req_id, data):
    """นับจำนวนไฟล์ตามชื่อฮีโร่ในโฟลเดอร์ (เช่น found-hero) — เดินเข้าโฟลเดอร์ย่อยด้วย"""
    names = data.get("names", [])
    subpath = data.get("subpath", "found-hero")
    match = (data.get("base_match") or "").strip().lower()

    base = _resolve_game_base(match) if match else None
    if base is None:
        base = (os.path.abspath(ALLOWED_PATHS[0].strip()) if ALLOWED_PATHS
                else os.path.join(os.path.expanduser("~"), "Desktop", "pes"))
    folder = os.path.join(base, subpath)

    # รายชื่อฮีโร่: ยึด list_find_hero ในไฟล์ config ของเครื่องนั้นเป็นหลัก
    # ถ้าอ่านไม่ได้ค่อยใช้ลิสต์ที่ server ส่งมา
    cfg_names = _read_hero_list(base)
    if cfg_names:
        names = cfg_names

    # map ชื่อฮีโร่ (ตัวเล็ก) -> ชื่อจริงตามลิสต์ ไว้แก้ตัวพิมพ์ให้ตรงกัน
    names_map = {n.strip().lower(): n.strip() for n in names if n and n.strip()}
    known = set(names_map.values())
    combos = {}            # "hero1+hero2" -> จำนวนไฟล์ (1 ไฟล์ = 1 id)
    groups = {}            # โฟลเดอร์ย่อยชั้นแรก (hero1/hero2) -> {combo: จำนวน}
    group_totals = {}
    total_files = matched_files = 0
    exists = os.path.isdir(folder)
    if exists:
        try:
            for root, dirs, filenames in os.walk(folder):
                rel = os.path.relpath(root, folder)
                top = "" if rel == "." else rel.replace("\\", "/").split("/")[0]
                for fn in filenames:
                    total_files += 1
                    group_totals[top] = group_totals.get(top, 0) + 1
                    combo = _hero_combo(fn, names_map)
                    if combo:
                        matched_files += 1
                        combos[combo] = combos.get(combo, 0) + 1
                        g = groups.setdefault(top, {})
                        g[combo] = g.get(combo, 0) + 1
        except Exception as e:
            send_response(req_id, {"error": str(e)})
            return

    logger.info(f"  count_heroes: {total_files} files, {matched_files} matched, "
                f"{len(combos)} combos, {len(group_totals)} groups in {folder} (exists={exists})")
    send_response(req_id, {"success": True, "combos": combos, "groups": groups,
                           "group_totals": group_totals, "known": sorted(known),
                           "total_files": total_files, "matched_files": matched_files,
                           "folder": folder, "exists": exists})


def _prefix_combo(filename, rel_dir):
    """แกะชื่อฮีโร่ (combo) ออกจากชื่อไฟล์แบบ Line Ranger

    รูปแบบไฟล์: "<prefix>-<ชื่อไฟล์เดิม>.xml" โดย prefix = ชื่อฮีโร่ที่ต่อกันด้วย '+'
      kikoruU+-RB136_TK24_norandom408dd2d9_LINE_COCOS_PREF_KEY.xml -> "kikoruU"
      kikoru+Kafka+-RB136_..._LINE_COCOS_PREF_KEY.xml              -> "kikoru+Kafka"
    ถ้าไฟล์ไม่มี prefix แต่อยู่ในโฟลเดอร์ย่อย (backup-id/<name1+name2>/) ใช้ชื่อโฟลเดอร์แทน
    คืน None ถ้าหาชื่อไม่ได้ (เช่นไฟล์เดิมล้วนๆ ที่ไม่มีฮีโร่)"""
    stem = os.path.splitext(filename)[0]
    if "-" in stem:
        head = stem.split("-", 1)[0].strip()
        # ชื่อไฟล์เดิมมี '_' เสมอ (RB136_TK24_...) ส่วน prefix ฮีโร่ไม่มี → ใช้กันจับผิดตัว
        if head and "_" not in head:
            parts = [p.strip() for p in head.split("+") if p.strip()]
            if parts:
                return "+".join(parts)
    # โฟลเดอร์ย่อยจะถือเป็นชื่อฮีโร่ ก็ต่อเมื่อชื่อมี '+' (login.py ตั้งชื่อแบบ hero1+hero2)
    # โฟลเดอร์แบ่งชุดอย่าง ranger / ranger(2) ไม่มี '+' → เป็นแค่กล่องแบ่งชุด ไม่ใช่ชื่อตัว
    # ไล่จากโฟลเดอร์ชั้นในสุดออกมา รองรับทั้ง backup-id/<combo>/ และ backup-id/ranger(3)/<combo>/
    if rel_dir:
        for seg in reversed(rel_dir.replace("\\", "/").split("/")):
            seg = seg.strip()
            if "+" in seg:
                parts = [p.strip() for p in seg.split("+") if p.strip()]
                if parts:
                    return "+".join(parts)
    return None


def handle_count_prefix_ids(req_id, data):
    """นับ id ตามชื่อฮีโร่ที่อยู่หน้าชื่อไฟล์ (Dashboard Line Ranger — โฟลเดอร์ backup-id)

    1 ไฟล์ = 1 id; ไฟล์ที่มี 2 ชื่อจะนับเป็น combo เดียว เช่น "kikoru+Kafka"
    """
    subpath = data.get("subpath", "backup-id")
    match = (data.get("base_match") or "").strip().lower()
    exts = [str(e).lower() for e in (data.get("exts") or []) if str(e).strip()]
    # by="filename" (ค่าเริ่มต้น) อ่านชื่อตัวจาก prefix หน้าชื่อไฟล์
    # by="folder"   อ่านชื่อตัวจากชื่อโฟลเดอร์ที่อยู่ใต้ชุด เช่น backup-id\ranger\kikoru+Kafka\*.xml
    by_folder = str(data.get("by") or "filename").strip().lower() == "folder"

    base = _resolve_game_base(match)
    if base is None:
        send_response(req_id, {"success": True, "combos": {}, "total_files": 0,
                               "matched_files": 0, "folder": "", "exists": False})
        return

    folder = os.path.join(base, subpath)
    combos = {}            # รวมทุกชุด (ของเดิม — Dashboard Line Ranger ใช้อยู่)
    groups = {}            # แยกตามโฟลเดอร์ย่อยชั้นแรก: ranger / ranger(2) / ... ("" = ไฟล์ที่วางไว้ชั้นนอก)
    group_totals = {}      # จำนวนไฟล์ทั้งหมดของแต่ละชุด (รวมไฟล์ที่แกะชื่อไม่ได้)
    total_files = 0
    matched_files = 0
    exists = os.path.isdir(folder)
    if exists:
        try:
            for root, dirs, filenames in os.walk(folder):
                rel_dir = os.path.relpath(root, folder)
                if rel_dir == ".":
                    rel_dir = ""
                segs = [s for s in rel_dir.replace("\\", "/").split("/") if s] if rel_dir else []
                top = segs[0] if segs else ""
                # โหมด folder: ชื่อตัว = โฟลเดอร์ที่อยู่ใต้ชุดอีกชั้น (backup-id\<ชุด>\<ชื่อตัว>\ไฟล์)
                dir_combo = None
                if by_folder and len(segs) >= 2:
                    parts = [p.strip() for p in segs[1].split("+") if p.strip()]
                    if parts:
                        dir_combo = "+".join(parts)
                for fn in filenames:
                    if exts and os.path.splitext(fn)[1].lower() not in exts:
                        continue
                    total_files += 1
                    group_totals[top] = group_totals.get(top, 0) + 1
                    combo = dir_combo if by_folder else _prefix_combo(fn, rel_dir)
                    if combo:
                        matched_files += 1
                        combos[combo] = combos.get(combo, 0) + 1
                        g = groups.setdefault(top, {})
                        g[combo] = g.get(combo, 0) + 1
        except Exception as e:
            send_response(req_id, {"error": str(e)})
            return

    logger.info(f"  count_prefix_ids: {total_files} files, {matched_files} matched, "
                f"{len(combos)} combos, {len(group_totals)} groups in {folder} (exists={exists})")
    send_response(req_id, {"success": True, "combos": combos, "groups": groups,
                           "group_totals": group_totals, "total_files": total_files,
                           "matched_files": matched_files, "folder": folder, "exists": exists})


def _export_match(folder_name, mode, key, names=None, match="only"):
    """โฟลเดอร์ชื่อนี้ตรงกับที่ขอไหม

    โหมดเจาะจงตัวเดียว (จากการ์ดในหน้าเว็บ):
       mode=combo : ต้องเป็นชุดเดียวกันเป๊ะ (kikoru+Kafka)
       mode=name  : ขอแค่มีชื่อนั้นอยู่ในชุด (kikoru อยู่ใน kikoru+Kafka ก็เอา)
    โหมดหลายชื่อ (ปุ่มโหลดทั้งหมด) — names ว่าง = เอาทุกโฟลเดอร์:
       match=only : ทุกชื่อในโฟลเดอร์ต้องอยู่ในรายการที่เลือก
       match=all  : ต้องมีชื่อที่เลือกครบทุกตัว
       match=any  : มีตัวใดตัวหนึ่งก็พอ
    """
    parts = [p.strip() for p in folder_name.split("+") if p.strip()]
    if not parts:
        return False
    if mode == "multi":
        if not names:
            return True
        if match == "all":
            return all(n in parts for n in names)
        if match == "any":
            return any(p in names for p in parts)
        return all(p in names for p in parts)      # only
    if mode == "name":
        return key in parts
    return "+".join(parts) == key


def handle_export_folder(req_id, data):
    """zip โฟลเดอร์ที่ตรงกับที่ขอ (backup-id/<ชุด>/<ชื่อตัว>/) แล้วอัปขึ้น server
       ตั้งชื่อไฟล์ใน zip เป็น <ชุด>/<ชื่อตัว>/<ไฟล์> ตามโครงเดิม
       move=True จะลบต้นทางหลังอัปสำเร็จเท่านั้น (อัปไม่ผ่าน = ไม่ลบ)"""
    import zipfile
    import tempfile

    subpath = data.get("subpath", "backup-id")
    match = (data.get("base_match") or "main").strip().lower()
    group = data.get("group") or "ALL"
    mode = (data.get("mode") or "combo").strip().lower()
    key = (data.get("key") or "").strip()
    job = str(data.get("job") or "").strip()
    move = bool(data.get("move"))
    upload_url = (data.get("upload_url") or "").strip()
    # โหมดหลายชื่อ/หลายชุด (ปุ่มโหลดทั้งหมด)
    groups = data.get("groups")                 # list ของชื่อชุด (None/ว่าง = ทุกชุด)
    names = [str(n).strip() for n in (data.get("names") or []) if str(n).strip()]
    # ห้ามตั้งชื่อ match — ชนกับ match ที่ใช้หาโฟลเดอร์เกมด้านบน (base_match)
    name_match = (data.get("match") or "only").strip().lower()

    if not job or (mode not in ("multi", "flat") and not key):
        send_response(req_id, {"error": "ข้อมูลไม่ครบ (key/job)"})
        return

    base = _resolve_game_base(match)
    root = os.path.join(base, subpath) if base else None
    if not root or not os.path.isdir(root):
        send_response(req_id, {"success": True, "files": 0, "bytes": 0, "exists": False})
        return

    # หาโฟลเดอร์เป้าหมายทั้งหมด
    targets = []          # (ชื่อชุด, ชื่อโฟลเดอร์, path เต็ม) — ว่างทั้งคู่ = เอาทั้งโฟลเดอร์
    file_targets = []     # (ชื่อใน zip, path จริง) — ใช้ตอนคัดเป็นรายไฟล์ (found-hero)
    if mode == "file":
        # ชื่อฮีโร่อยู่ใน "ชื่อไฟล์" ไม่ใช่ชื่อโฟลเดอร์ (pes/found-hero/hero1/<ชื่อ>+ASCV...dat)
        nmap = {n.strip().lower(): n.strip() for n in _read_hero_list(base) if n and n.strip()}
        submode = (data.get("submode") or "combo").strip().lower()
        try:
            for cur, _dirs, fns in os.walk(root):
                rel = os.path.relpath(cur, root)
                for fn in fns:
                    combo = _hero_combo(fn, nmap)
                    if not combo:
                        continue
                    hit = (key in combo.split("+")) if submode == "name" else (combo == key)
                    if hit:
                        arc = fn if rel == "." else rel.replace("\\", "/") + "/" + fn
                        file_targets.append((arc, os.path.join(cur, fn)))
        except Exception as e:
            send_response(req_id, {"error": str(e)})
            return
    elif mode == "flat":
        if groups:
            # เลือกเฉพาะโฟลเดอร์ย่อยที่ติ๊กมา (เช่น found-hero/hero1, hero2) เก็บชื่อโฟลเดอร์ใน zip ด้วย
            targets = [(g, "", os.path.join(root, g)) for g in groups
                       if os.path.isdir(os.path.join(root, g))]
        else:
            # โฟลเดอร์แบนๆ (fast-random / input-id / backup) เอาทุกไฟล์ในนั้นเลย
            targets = [("", "", root)]
    else:
        try:
            for set_name in sorted(os.listdir(root)):
                set_dir = os.path.join(root, set_name)
                if not os.path.isdir(set_dir):
                    continue
                if groups:                                   # เลือกหลายชุด (ปุ่มโหลดทั้งหมด)
                    if set_name not in groups:
                        continue
                elif group != "ALL" and set_name != group:   # เลือกชุดเดียว (จากการ์ด)
                    continue
                for fld in sorted(os.listdir(set_dir)):
                    d = os.path.join(set_dir, fld)
                    if os.path.isdir(d) and _export_match(fld, mode, key, names, name_match):
                        targets.append((set_name, fld, d))
        except Exception as e:
            send_response(req_id, {"error": str(e)})
            return

    if not targets and not file_targets:
        send_response(req_id, {"success": True, "files": 0, "bytes": 0, "exists": True})
        return

    tmp = tempfile.NamedTemporaryFile(prefix="export_", suffix=".zip", delete=False)
    tmp.close()
    packed = []           # path จริงที่ใส่ zip แล้ว (ไว้ลบตอน move)
    n_files = 0
    try:
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            for arc, full in file_targets:          # โหมดคัดรายไฟล์
                zf.write(full, arc)
                packed.append(full)
                n_files += 1
            for set_name, fld, d in targets:
                for cur, _dirs, files in os.walk(d):
                    rel_in = os.path.relpath(cur, d)
                    for fn in files:
                        full = os.path.join(cur, fn)
                        arc_parts = [p for p in (set_name, fld) if p]   # flat = ไม่มีชั้นชุด/ชื่อตัว
                        if rel_in != ".":
                            arc_parts += rel_in.replace("\\", "/").split("/")
                        arc_parts.append(fn)
                        zf.write(full, "/".join(arc_parts))
                        packed.append(full)
                        n_files += 1
        size = os.path.getsize(tmp.name)

        if n_files == 0:
            os.remove(tmp.name)
            send_response(req_id, {"success": True, "files": 0, "bytes": 0, "exists": True})
            return

        # อัปขึ้น server (ลองทุก URL จนกว่าจะสำเร็จ)
        import requests
        urls = [upload_url] if upload_url else [u.rstrip("/") + "/export-upload" for u in SERVER_URLS]
        last_err = None
        ok = False
        for u in urls:
            try:
                with open(tmp.name, "rb") as f:
                    r = requests.post(u, params={"job": job, "agent": AGENT_NAME, "secret": AGENT_SECRET},
                                      data=f, headers={"Content-Type": "application/zip"}, timeout=600)
                r.raise_for_status()
                ok = True
                break
            except Exception as e:
                last_err = e
        if not ok:
            send_response(req_id, {"error": f"อัปโหลด zip ไม่สำเร็จ: {last_err}"})
            return

        # ลบต้นทางเฉพาะตอนสั่ง "ย้าย" และอัปสำเร็จแล้วเท่านั้น
        deleted = 0
        if move:
            for full in packed:
                try:
                    os.remove(full)
                    deleted += 1
                except Exception:
                    pass
            for _set_name, _fld, d in targets:       # เก็บกวาดโฟลเดอร์ที่ว่างแล้ว
                for cur, dirs, files in os.walk(d, topdown=False):
                    if not os.listdir(cur):
                        try:
                            os.rmdir(cur)
                        except Exception:
                            pass

        logger.info(f"  export_folder: {n_files} ไฟล์ ({size} bytes) จาก {len(targets)} โฟลเดอร์"
                    + (f" · ลบต้นทาง {deleted}" if move else ""))
        send_response(req_id, {"success": True, "files": n_files, "bytes": size,
                               "folders": len(targets), "deleted": deleted, "exists": True})
    except Exception as e:
        send_response(req_id, {"error": str(e)})
    finally:
        try:
            os.remove(tmp.name)
        except Exception:
            pass


def _reply_ids(req_id, folder):
    """สแกนโฟลเดอร์ folder แล้วส่งรายชื่อ id + full path จริงกลับ
       (ids = ชื่อโชว์บน dashboard, entries = path จริงไว้ใช้ลบแบบปกติ)"""
    exists = os.path.isdir(folder)
    ids = []
    entries = []
    if exists:
        try:
            for name in sorted(os.listdir(folder)):
                full = os.path.join(folder, name)
                entries.append(full)                       # path จริงทุกไฟล์/โฟลเดอร์ (ไว้ลบ)
                if name.startswith("."):
                    continue
                stem = name if os.path.isdir(full) else os.path.splitext(name)[0]
                if stem:
                    ids.append(stem)
        except Exception as e:
            send_response(req_id, {"error": str(e)})
            return
    logger.info(f"  list_ids: {len(ids)} ids in {folder} (exists={exists})")
    send_response(req_id, {"success": True, "ids": ids, "total": len(ids),
                           "entries": entries, "folder": folder, "exists": exists})


def _resolve_game_base(match):
    """หา base folder ของเกม (เช่น pes / ro / cookie-run) จาก allowed_paths ตาม base_match
       - เทียบชื่อโฟลเดอร์ (basename) ตรงเป๊ะก่อน แล้วค่อย fallback เป็น substring
       คืน None ถ้าระบุ match แต่หาไม่เจอ"""
    if ALLOWED_PATHS:
        if match:
            for p in ALLOWED_PATHS:  # 1) ชื่อโฟลเดอร์ตรงเป๊ะ
                ap = os.path.abspath(p.strip())
                if os.path.basename(ap).lower() == match:
                    return ap
            for p in ALLOWED_PATHS:  # 2) fallback: มีคำนั้นอยู่ในพาธ
                ap = os.path.abspath(p.strip())
                if match in ap.lower():
                    return ap
            return None
        return os.path.abspath(ALLOWED_PATHS[0].strip())
    if not match:
        return os.path.join(os.path.expanduser("~"), "Desktop", "cookie-run")
    return None


# ชื่อเดิม (คงไว้ให้ handle_list_ids ใช้)
_resolve_cookie_base = _resolve_game_base


def _resolve_input_folder(match, subpath="input-id"):
    """หาโฟลเดอร์ input-id ของเกม: base_match + subpath
       (COOKIE_INPUT_PATH ใช้ override เฉพาะ cookie-run เท่านั้น ไม่ทับเกมอื่น)"""
    if COOKIE_INPUT_PATH and (not match or match == "cookie-run"):
        return _norm_path(COOKIE_INPUT_PATH)
    base = _resolve_game_base(match)
    return os.path.join(base, subpath) if base else None


def handle_list_ids(req_id, data):
    """ดึงรายชื่อ id + path จริงในโฟลเดอร์ (dashboard=id-found, clear=input-id)"""
    subpath = data.get("subpath", "id-found")
    match = (data.get("base_match") or "").strip().lower()

    # cookie_id_path override ใช้เฉพาะ dashboard cookie-run (id-found) เท่านั้น
    # ห้ามไปทับตอนถาม input-id (clear) หรือเกมอื่น ไม่งั้น clear จะลบผิดโฟลเดอร์
    if COOKIE_ID_PATH and subpath == "id-found" and (not match or match == "cookie-run"):
        _reply_ids(req_id, _norm_path(COOKIE_ID_PATH))
        return

    base = _resolve_cookie_base(match)
    if base is None:
        # ระบุ base_match แต่หาโฟลเดอร์ที่อนุญาตไม่เจอ
        send_response(req_id, {"success": True, "ids": [], "total": 0,
                               "entries": [], "folder": "", "exists": False})
        return

    _reply_ids(req_id, os.path.join(base, subpath))


def handle_clear_input(req_id, data):
    """ลบไฟล์/โฟลเดอร์ทั้งหมดในโฟลเดอร์ input-id (เคลียร์ข้อมูล input)"""
    subpath = data.get("subpath", "input-id")
    match = (data.get("base_match") or "").strip().lower()
    folder = _resolve_input_folder(match, subpath)

    if not folder or not os.path.isdir(folder):
        send_response(req_id, {"success": True, "deleted": 0, "exists": False,
                               "folder": folder or ""})
        return
    if not is_path_allowed(folder):
        send_response(req_id, {"error": f"ไม่มีสิทธิ์ลบใน: {folder}"})
        return

    deleted, errors = 0, []
    for name in os.listdir(folder):
        full = os.path.join(folder, name)
        try:
            if os.path.isdir(full):
                shutil.rmtree(full)
            else:
                os.remove(full)
            deleted += 1
        except Exception as e:
            errors.append(f"{name}: {e}")

    logger.info(f"  clear_input: ลบ {deleted} รายการใน {folder}")
    send_response(req_id, {"success": True, "deleted": deleted, "exists": True,
                           "folder": folder, "errors": errors})


# ═══════════════════════════════════════════════════════════
#  RUN FILE — สั่งรันไฟล์ .bat/.cmd/.exe/.py ในโฟลเดอร์โปรเจกต์จากเว็บ
#  (เช่น pes\login.bat) เปิดเป็นหน้าต่างใหม่ให้เห็นเหมือนดับเบิลคลิกเอง
#  จำกัดเฉพาะไฟล์ที่อยู่ใน ALLOWED_PATHS เท่านั้น
# ═══════════════════════════════════════════════════════════

_RUN_EXTS = (".bat", ".cmd", ".exe", ".py")
_RUN_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_run_state.json")
_run_lock = threading.Lock()
_run_jobs = {}   # key (path ตัวพิมพ์เล็ก) -> {"path","name","base","pid","started"}


def _pid_alive(pid):
    """เช็คว่า pid ยังทำงานอยู่ไหม
       (ห้ามใช้ os.kill(pid, 0) — บน Windows มันเรียก TerminateProcess = ฆ่าจริง)"""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == 259                  # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    except Exception:
        return False


def _run_state_load():
    """อ่านงานที่ค้างจากไฟล์ — ให้สถานะไม่หายเวลา agent ถูกรีสตาร์ท"""
    try:
        with open(_RUN_STATE_FILE, "r", encoding="utf-8") as f:
            for job in json.load(f).get("jobs", []):
                if job.get("pid") and _pid_alive(job["pid"]):
                    _run_jobs[str(job.get("path", "")).lower()] = job
    except Exception:
        pass


def _run_state_save():
    try:
        with open(_RUN_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"jobs": list(_run_jobs.values())}, f, ensure_ascii=False)
    except Exception:
        pass


def _run_resolve(data):
    """หา path เต็มของไฟล์ที่จะรัน จาก base_match (เช่น pes) + name (เช่น login.bat)
       คืน (path, base, error)"""
    base = _resolve_game_base((data.get("base_match") or "").strip().lower())
    if not base or not os.path.isdir(base):
        return None, None, f"ไม่พบโฟลเดอร์โปรเจกต์ '{data.get('base_match')}' ที่เครื่องนี้"
    name = str(data.get("name") or "").strip().replace("/", os.sep).replace("\\", os.sep)
    if not name:
        return None, base, "ยังไม่ได้เลือกไฟล์"
    full = os.path.abspath(os.path.join(base, os.path.normpath(name)))
    # กัน ../ ออกนอกโฟลเดอร์โปรเจกต์ และกันสั่งรันไฟล์นอก allowed_paths
    if not full.lower().startswith(base.lower() + os.sep) or not is_path_allowed(full):
        return None, base, "ไฟล์อยู่นอกโฟลเดอร์ที่อนุญาต"
    if os.path.splitext(full)[1].lower() not in _RUN_EXTS:
        return None, base, f"รันได้เฉพาะไฟล์ {', '.join(_RUN_EXTS)}"
    if not os.path.isfile(full):
        return None, base, f"ไม่พบไฟล์ {name} ที่เครื่องนี้"
    return full, base, None


def _run_list(data):
    """รายชื่อไฟล์ที่รันได้ในโฟลเดอร์โปรเจกต์"""
    base = _resolve_game_base((data.get("base_match") or "").strip().lower())
    if not base or not os.path.isdir(base):
        return {"success": True, "files": [], "base": base or "", "exists": False}
    files = []
    try:
        for entry in sorted(os.listdir(base)):
            full = os.path.join(base, entry)
            if os.path.isfile(full) and os.path.splitext(entry)[1].lower() in _RUN_EXTS:
                files.append(entry)
    except Exception as e:
        return {"error": f"อ่านโฟลเดอร์ไม่ได้: {e}"}
    return {"success": True, "files": files, "base": base, "exists": True}


def _run_start(data):
    """เปิดไฟล์ให้ทำงาน — .bat/.cmd ผ่าน cmd /c, .py ผ่าน py, .exe รันตรง
       ตั้ง cwd เป็นโฟลเดอร์ของไฟล์ (สคริปต์ส่วนใหญ่อ้าง path แบบ relative)"""
    full, base, err = _run_resolve(data)
    if err:
        return {"error": err}

    key = full.lower()
    with _run_lock:
        old = _run_jobs.get(key)
        if old and old.get("pid") and _pid_alive(old["pid"]) and not data.get("force"):
            return {"error": f"{os.path.basename(full)} กำลังทำงานอยู่แล้ว (PID {old['pid']})",
                    "already_running": True, "pid": old["pid"]}

    ext = os.path.splitext(full)[1].lower()
    if ext in (".bat", ".cmd"):
        args = [os.environ.get("COMSPEC", "cmd.exe"), "/c", full]
    elif ext == ".py":
        args = [shutil.which("py") or sys.executable, full]
    else:
        args = [full]

    hidden = bool(data.get("hidden"))
    flags = 0x00000008    # DETACHED_PROCESS — ไม่ตายตาม agent
    if not hidden:
        flags = 0x00000010   # CREATE_NEW_CONSOLE — โผล่หน้าต่างให้เห็นเหมือนดับเบิลคลิก
    flags |= 0x00000200      # CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(args, cwd=os.path.dirname(full), creationflags=flags,
                                close_fds=True)
    except Exception as e:
        return {"error": f"เปิดไฟล์ไม่สำเร็จ: {e}"}

    job = {"path": full, "name": os.path.relpath(full, base), "base": base,
           "pid": proc.pid, "started": datetime.now().strftime("%H:%M:%S")}
    with _run_lock:
        _run_jobs[key] = job
        _run_state_save()
    logger.info(f"  run_file: เปิด {job['name']} (PID {proc.pid}){' แบบซ่อนหน้าต่าง' if hidden else ''}")
    return {"success": True, "started": True, "pid": proc.pid,
            "name": job["name"], "path": full}


def _run_status():
    """สถานะงานที่เคยสั่งรันจากเว็บ (ตัดงานที่จบไปแล้วออกจากรายการ)"""
    with _run_lock:
        jobs, dead = [], []
        for key, job in _run_jobs.items():
            alive = bool(job.get("pid")) and _pid_alive(job["pid"])
            if alive:
                jobs.append({"name": job.get("name"), "pid": job.get("pid"),
                             "started": job.get("started"), "path": job.get("path")})
            else:
                dead.append(key)
        for key in dead:
            _run_jobs.pop(key, None)
        if dead:
            _run_state_save()
    return {"success": True, "jobs": jobs, "running": len(jobs)}


def _procs_in_folder(folder):
    """คืน [(pid, cmdline)] ของ process ที่ "กำลังรันไฟล์ .bat/.py ในโฟลเดอร์โปรเจกต์นี้จริงๆ"

    ใช้ตอนสั่ง stop โดยที่ agent ไม่ได้เป็นคนเปิดงานนั้นเอง (ไม่มี PID ในมือ) เช่นบอทถูกเปิด
    จาก Task Scheduler / login.bat รีสตาร์ทตัวเอง / force-update.bat

    เงื่อนไขต้องครบ 3 ข้อถึงจะนับ (กันฆ่าผิดตัว):
      1. ชื่อโปรเซสเป็น cmd.exe / python.exe / pythonw.exe / py.exe เท่านั้น
         (bash, explorer, cloudflared ฯลฯ ที่บังเอิญมีพาธนี้ใน command line จะไม่โดน)
      2. command line มี "พาธโฟลเดอร์โปรเจกต์ + ชื่อไฟล์ .bat/.cmd/.py" ต่อกันจริงๆ
         ไม่ใช่แค่เอ่ยถึงพาธเฉยๆ
      3. ไม่ใช่ตัว agent เอง (agent.py อยู่คนละโฟลเดอร์อยู่แล้ว + กัน pid ตัวเองไว้อีกชั้น)
    """
    if not folder:
        return []
    import re
    try:
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine } | "
              "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress")
        out, _rc = _run_hidden(["powershell", "-NoProfile", "-Command", ps], timeout=30)
        data = json.loads(out) if out.strip() else []
        if isinstance(data, dict):
            data = [data]
    except Exception as e:
        logger.warning(f"  run_file: อ่านรายชื่อ process ไม่ได้: {e}")
        return []

    me = os.getpid()
    allow = {"cmd.exe", "python.exe", "pythonw.exe", "py.exe"}
    key = os.path.normcase(os.path.abspath(folder)).rstrip("\/")
    pat = re.compile(re.escape(key) + r"[\\/][^\"'<>|]*?\.(?:bat|cmd|py)")

    hits = []
    for it in data:
        try:
            pid = int(it.get("ProcessId") or 0)
            name = str(it.get("Name") or "").lower()
            cmd = str(it.get("CommandLine") or "")
        except Exception:
            continue
        if not pid or pid == me or name not in allow:
            continue
        if pat.search(os.path.normcase(cmd)):
            hits.append((pid, cmd))
    return hits


def _run_stop(data):
    """หยุดงาน: ปิดทั้ง process tree (cmd + py ที่มันเปิดต่อ) ด้วย taskkill /T /F"""
    name = str(data.get("name") or "").strip().lower()
    with _run_lock:
        targets = [dict(j) for j in _run_jobs.values()
                   if not name or str(j.get("name", "")).lower() == name]
    stopped, errors = [], []
    killed_pids = set()
    for job in targets:
        pid = job.get("pid")
        if not pid or not _pid_alive(pid):
            continue
        try:
            _run_hidden(["taskkill", "/T", "/F", "/PID", str(pid)], timeout=20)
            killed_pids.add(pid)
            stopped.append(job.get("name"))
        except Exception as e:
            errors.append(f"{job.get('name')}: {e}")

    # ── กวาดซ้ำ: ฆ่า process จริงที่ยังรันอยู่ในโฟลเดอร์โปรเจกต์ ──────────────
    #    เคสที่เจอบ่อย: บอทถูกเปิดจากทางอื่น (Task Scheduler / login.bat รีสตาร์ทตัวเอง /
    #    force-update.bat) agent เลยไม่มี PID ในมือ กด "หยุด" แล้วไม่มีอะไรเกิดขึ้น
    base = _resolve_game_base((data.get("base_match") or "").strip().lower())
    if base:
        for pid, cmd in _procs_in_folder(base):
            if pid in killed_pids or not _pid_alive(pid):
                continue
            try:
                _run_hidden(["taskkill", "/T", "/F", "/PID", str(pid)], timeout=20)
                killed_pids.add(pid)
                stopped.append(os.path.basename(cmd.strip().strip('"').split('"')[0]) or f"pid {pid}")
            except Exception as e:
                errors.append(f"pid {pid}: {e}")

    with _run_lock:
        for key in [k for k, j in _run_jobs.items() if not j.get("pid") or not _pid_alive(j["pid"])]:
            _run_jobs.pop(key, None)
        _run_state_save()
    logger.info(f"  run_file: หยุด {len(stopped)} งาน")
    return {"success": True, "stopped": stopped, "count": len(stopped), "errors": errors}


def handle_run_file(req_id, data):
    """สั่งรัน/หยุด/ดูสถานะไฟล์ในโฟลเดอร์โปรเจกต์ (รันใน thread กันบล็อก socket)"""
    sub = data.get("sub")

    def _go():
        try:
            if sub == "list":
                res = _run_list(data)
            elif sub == "start":
                res = _run_start(data)
            elif sub == "status":
                res = _run_status()
            elif sub == "stop":
                res = _run_stop(data)
            else:
                res = {"error": f"คำสั่ง run ไม่รู้จัก: {sub}"}
        except Exception as e:
            res = {"error": str(e)[:200]}
        send_response(req_id, res)

    _spawn_with_client(_go)


# ═══════════════════════════════════════════════════════════
#  MuMu Player 12 control (เปิด/ปิด instance รายเครื่อง)
# ═══════════════════════════════════════════════════════════

_MUMU_SUBS = ("shell", "nx_main", "nx_device", "")
_MUMU_PRODS = ("MuMuPlayer-12.0", "MuMuPlayerGlobal-12.0", "MuMu Player 12",
               "MuMuPlayer", "MuMuPlayerGlobal", "MuMuNebula")
_MUMU_DRIVES = ("C:", "D:", "E:", "F:")
_MUMU_PFS = ("Program Files", "Program Files (x86)")


def _mumu_reg_locations():
    """อ่าน InstallLocation ของ MuMu จาก registry (Uninstall keys)"""
    locs = []
    try:
        import winreg
    except Exception:
        return locs
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in roots:
        try:
            key = winreg.OpenKey(hive, path)
            count = winreg.QueryInfoKey(key)[0]
        except Exception:
            continue
        for i in range(count):
            try:
                sk = winreg.OpenKey(key, winreg.EnumKey(key, i))
                name = str(winreg.QueryValueEx(sk, "DisplayName")[0])
            except Exception:
                continue
            if "mumu" in name.lower():
                try:
                    loc = winreg.QueryValueEx(sk, "InstallLocation")[0]
                    if loc and os.path.isdir(loc):
                        locs.append(loc)
                except Exception:
                    pass
    return locs


def _mumu_manager_path():
    """หา MuMuManager.exe: config > path มาตรฐาน > registry > ค้นใต้โฟลเดอร์ Netease"""
    import glob
    if MUMU_MANAGER_PATH and os.path.isfile(MUMU_MANAGER_PATH):
        return MUMU_MANAGER_PATH
    # 1) path มาตรฐานหลายแบบ
    for drive in _MUMU_DRIVES:
        for pf in _MUMU_PFS:
            for prod in _MUMU_PRODS:
                for sub in _MUMU_SUBS:
                    c = os.path.join(f"{drive}\\", pf, "Netease", prod, sub, "MuMuManager.exe")
                    if os.path.isfile(c):
                        return c
    # 2) install location จาก registry
    for loc in _mumu_reg_locations():
        for sub in _MUMU_SUBS:
            c = os.path.join(loc, sub, "MuMuManager.exe")
            if os.path.isfile(c):
                return c
        try:
            hits = glob.glob(os.path.join(loc, "**", "MuMuManager.exe"), recursive=True)
            if hits:
                return hits[0]
        except Exception:
            pass
    # 3) ค้นใต้โฟลเดอร์ Netease (จำกัดวงแค่โฟลเดอร์ Netease จึงเร็ว)
    for drive in _MUMU_DRIVES:
        for pf in _MUMU_PFS:
            base = os.path.join(f"{drive}\\", pf, "Netease")
            if os.path.isdir(base):
                try:
                    hits = glob.glob(os.path.join(base, "**", "MuMuManager.exe"), recursive=True)
                    if hits:
                        return hits[0]
                except Exception:
                    pass
    return None


def _mumu_hint():
    """บอกใบ้ว่าเจอโฟลเดอร์ Netease/MuMu ที่ไหนบ้าง เผื่อ user เอาไปตั้ง path"""
    found = []
    for drive in _MUMU_DRIVES:
        for pf in _MUMU_PFS:
            base = os.path.join(f"{drive}\\", pf, "Netease")
            if os.path.isdir(base):
                try:
                    for d in os.listdir(base):
                        found.append(os.path.join(base, d))
                except Exception:
                    pass
    if found:
        return " | เจอโฟลเดอร์: " + ", ".join(found[:6])
    return " | ไม่เจอโฟลเดอร์ Netease เลย (MuMu ติดตั้งไว้ไดรฟ์/โฟลเดอร์ไหน?)"


def _run_hidden(args, timeout=30):
    """รันคำสั่งแบบไม่โผล่หน้าต่าง คืน (stdout_text, returncode)"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=flags)
    out = (r.stdout or b"").decode("utf-8", "ignore")
    if not out.strip():
        out = (r.stderr or b"").decode("utf-8", "ignore")
    return out, r.returncode


def _mumu_kill_all():
    """taskkill ทุก process ที่ชื่อมี MuMu หรือ Nemu (ปิด MuMu ทั้งหมด)"""
    killed = []
    try:
        out, _ = _run_hidden(["tasklist", "/fo", "csv", "/nh"], timeout=15)
        for line in out.splitlines():
            parts = [x.strip().strip('"') for x in line.split('","')]
            if not parts or not parts[0]:
                continue
            name = parts[0].strip('"')
            low = name.lower()
            if "mumu" in low or "nemu" in low:
                pid = parts[1] if len(parts) > 1 else ""
                if pid.isdigit():
                    try:
                        _run_hidden(["taskkill", "/F", "/PID", pid], timeout=10)
                        killed.append(name)
                    except Exception:
                        pass
    except Exception as e:
        return {"error": f"taskkill ล้มเหลว: {e}"}
    logger.info(f"  MuMu kill: ปิด {len(killed)} process")
    return {"success": True, "killed": killed, "count": len(killed)}


_MUMU_VFLAGS = ("-v", "--vmindex")
_mumu_vflag = {"ok": None}   # จำ flag ที่ใช้ได้กับเครื่องนี้ ครั้งต่อไปจะได้ยิงถูกตั้งแต่ครั้งแรก


def _mumu_is_help(out):
    """MuMuManager พ่นหน้าช่วยเหลือ/ข้อความ error ออกมาแทนที่จะทำงานให้หรือเปล่า"""
    t = out or ""
    return ("OVERVIEW:" in t and "USAGE:" in t) or "error !!!" in t


def _mumu_vflag_order():
    ok = _mumu_vflag["ok"]
    if ok:
        return (ok,) + tuple(f for f in _MUMU_VFLAGS if f != ok)
    return _MUMU_VFLAGS


def _mumu_run_v(mgr, subcmd, vvalue, *rest, timeout=60):
    """เรียกคำสั่งที่ต้องระบุเลขจอ โดยลองทั้ง -v และ --vmindex
       MuMuManager บางเวอร์ชันไม่รู้จัก flag ตัวย่อ -v พอใส่ไปจะพ่นหน้า help ออกมาเฉยๆ
       แล้วคำสั่งไม่ทำงาน (เดิมเราไม่ได้เช็ก เลยนึกว่าสั่งสำเร็จ)
       คืน (out, rc, flag ที่ใช้ได้ หรือ None ถ้าไม่มีอันไหนผ่าน)"""
    last_out, last_rc = "", -1
    for flag in _mumu_vflag_order():
        out, rc = _run_hidden([mgr, subcmd, flag, str(vvalue), *rest], timeout=timeout)
        if not _mumu_is_help(out):
            _mumu_vflag["ok"] = flag
            return out, rc, flag
        last_out, last_rc = out, rc
    return last_out, last_rc, None


_MUMU_SCAN_MAX = 64   # ไล่ถามเลขจอ 0..63 ตอนที่ถามแบบ 'all' ไม่ได้


def _mumu_info_raw(mgr, timeout=20):
    """ดึง info ของทุกจอ คืน (data, flag, raw ล่าสุด)
       เครื่องที่ยังไม่มี instance สักจอ MuMuManager จะไม่รับคำว่า 'all'
       แล้วพ่นหน้า help ออกมาแทน (ไม่ใช่ {} ว่างๆ) — กรณีนั้นถอยไปไล่ถามทีละเลข
       แล้วคัดเฉพาะจอที่มีอยู่จริง (จอที่ไม่มีจะตอบ errcode -200 มา)"""
    last = ""
    for flag in _mumu_vflag_order():
        try:
            out, _rc = _run_hidden([mgr, "info", flag, "all"], timeout=timeout)
        except Exception as e:
            last = f"เรียกไม่สำเร็จ: {e}"
            continue
        data = _mumu_json(out)
        if data is not None:
            _mumu_vflag["ok"] = flag
            return data, flag, out
        last = out

    idx_list = ",".join(str(i) for i in range(_MUMU_SCAN_MAX))
    for flag in _mumu_vflag_order():
        try:
            out, _rc = _run_hidden([mgr, "info", flag, idx_list], timeout=max(timeout, 60))
        except Exception:
            continue
        data = _mumu_json(out)
        if isinstance(data, dict):
            _mumu_vflag["ok"] = flag
            return {k: v for k, v in data.items() if _mumu_entry_ok(v)}, flag, out
    return None, None, last


def _mumu_entry_ok(v):
    """จอนี้มีอยู่จริงไหม — จอที่ไม่มีจะตอบ {"errcode": -200, "errmsg": "unknown error"}
       (คนละคีย์กับ error_code ของจอจริง)"""
    return isinstance(v, dict) and not v.get("errcode") and not v.get("errmsg")


def _mumu_json(out):
    """แกะ JSON ออกจากผลลัพธ์ของ MuMuManager
       บางเครื่องพ่นบรรทัดว่าง/ข้อความเตือนนำหน้าหรือต่อท้าย JSON มาด้วย
       (json.loads ตรงๆ จะพังด้วย 'Expecting value: line 3 column 1')
       คืน None ถ้าหา JSON ไม่เจอเลย"""
    if not out:
        return None
    text = out.strip().lstrip("﻿").strip("\x00")
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return None
    start = min(starts)
    for end in sorted((text.rfind("}"), text.rfind("]")), reverse=True):
        if end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                continue
    return None


def _mumu_list():
    """ดึงรายชื่อ instance ทั้งหมดของ MuMu 12 (MuMuManager info -v all)"""
    mgr = _mumu_manager_path()
    if not mgr:
        return {"error": "หา MuMuManager.exe ไม่เจอ — ตั้ง 'mumu_manager_path' ใน config.json" + _mumu_hint()}
    try:
        data, vflag, out = _mumu_info_raw(mgr)
    except Exception as e:
        return {"error": f"เรียก MuMuManager ไม่สำเร็จ: {e}", "manager": mgr}

    if data is None:
        # ไม่มี JSON เลย — เอาข้อความจริงที่ MuMuManager ตอบกลับไปโชว์ จะได้รู้ว่าเครื่องนั้นติดอะไร
        snippet = " ".join((out or "").split())[:160] or "(ไม่มีข้อความตอบกลับ)"
        return {"error": f"MuMuManager ไม่ได้ตอบเป็น JSON — ตอบว่า: {snippet}",
                "manager": mgr, "raw": (out or "")[:400]}

    # รูปแบบอาจเป็น {"0":{...},"1":{...}} หรือ instance เดียว {...}
    if isinstance(data, dict) and ("index" in data or "name" in data) and not any(k.isdigit() for k in data.keys()):
        items = {str(data.get("index", 0)): data}
    elif isinstance(data, dict):
        items = data
    else:
        return {"error": "รูปแบบข้อมูล MuMuManager ไม่รู้จัก"}

    instances = []
    for k, v in items.items():
        if not _mumu_entry_ok(v):     # ข้ามจอที่ไม่มีอยู่จริง (errcode -200) ไม่ให้กลายเป็นจอผี
            continue
        idx = v.get("index", k)
        try:
            idx = int(idx)
        except Exception:
            pass
        running = bool(v.get("is_process_started") or v.get("is_android_started")
                       or str(v.get("player_state", "")).lower() in ("start_finished", "starting", "running"))
        instances.append({"index": idx, "name": v.get("name") or f"MuMu-{idx}", "running": running})
    instances.sort(key=lambda x: (isinstance(x["index"], str), x["index"]))
    return {"success": True, "instances": instances, "manager": mgr}


def _mumu_open(indices):
    """เปิด instance ตามเลขที่เลือก (MuMuManager control -v <idx> launch)"""
    mgr = _mumu_manager_path()
    if not mgr:
        return {"error": "หา MuMuManager.exe ไม่เจอ — ตั้ง 'mumu_manager_path' ใน config.json" + _mumu_hint()}
    opened, errors = [], []
    for idx in indices:
        try:
            out, _rc, flag = _mumu_run_v(mgr, "control", idx, "launch", timeout=40)
            if flag is None:
                errors.append(f"จอ {idx}: MuMuManager ไม่รับคำสั่ง ({' '.join((out or '').split())[:80]})")
            else:
                opened.append(idx)
        except Exception as e:
            errors.append(f"จอ {idx}: {e}")
    logger.info(f"  MuMu open: เปิด {opened}")
    return {"success": True, "opened": opened, "errors": errors}


def _mumu_open_all():
    """เปิด instance ทุกจอในคำสั่งเดียว (control -v all launch)"""
    mgr = _mumu_manager_path()
    if not mgr:
        return {"error": "หา MuMuManager.exe ไม่เจอ — ตั้ง 'mumu_manager_path' ใน config.json" + _mumu_hint()}
    res = _mumu_list()
    if res.get("error"):
        return res
    idxs = [str(x["index"]) for x in res.get("instances", [])]
    if not idxs:
        return {"error": "ไม่มี instance ให้เปิด (0 จอ)"}
    try:
        out, _rc, flag = _mumu_run_v(mgr, "control", "all", "launch", timeout=180)
        if flag is None:
            # เวอร์ชันที่ไม่รับคำว่า all — ไล่ส่งเลขจอไปตรงๆ แทน
            out, _rc, flag = _mumu_run_v(mgr, "control", ",".join(idxs), "launch", timeout=180)
        if flag is None:
            return {"error": f"MuMuManager ไม่รับคำสั่งเปิดจอ: {' '.join((out or '').split())[:120]}"}
    except Exception as e:
        return {"error": f"เปิดจอไม่สำเร็จ: {e}"}
    logger.info(f"  MuMu open_all: สั่งเปิดทั้งหมด {len(idxs)} จอ")
    return {"success": True, "count": len(idxs)}


def _mumu_delete_all():
    """ลบ instance ทุกจอ: ปิด process MuMu ทั้งหมดก่อน แล้วค่อย delete -v all"""
    mgr = _mumu_manager_path()
    if not mgr:
        return {"error": "หา MuMuManager.exe ไม่เจอ — ตั้ง 'mumu_manager_path' ใน config.json" + _mumu_hint()}
    before = _mumu_list()
    if before.get("error"):
        return before
    idxs = [str(x["index"]) for x in before.get("instances", [])]
    n_before = len(idxs)
    if not n_before:
        return {"success": True, "deleted": 0, "remain": 0}
    _mumu_kill_all()          # จอที่กำลังเปิดอยู่จะลบไม่ได้ ต้องปิดก่อน
    time.sleep(2)
    try:
        out, _rc, flag = _mumu_run_v(mgr, "delete", "all", timeout=600)
        if flag is None:
            # เวอร์ชันที่ไม่รับคำว่า all — ไล่ส่งเลขจอไปตรงๆ แทน
            out, _rc, flag = _mumu_run_v(mgr, "delete", ",".join(idxs), timeout=600)
        if flag is None:
            return {"error": f"MuMuManager ไม่รับคำสั่งลบจอ: {' '.join((out or '').split())[:120]}"}
    except Exception as e:
        return {"error": f"ลบไม่สำเร็จ: {e}"}
    after = _mumu_list()
    remain = len(after.get("instances", [])) if after.get("success") else 0
    deleted = max(0, n_before - remain)
    logger.info(f"  MuMu delete_all: ลบ {deleted} จอ เหลือ {remain}")
    res = {"success": True, "deleted": deleted, "remain": remain}
    if remain:
        res["warning"] = f"ยังเหลือ {remain} จอที่ลบไม่ได้: {(out or '').strip()[:120]}"
    return res


def _mumu_probe():
    """ตรวจสภาพ MuMuManager ของเครื่องนี้ — เวอร์ชันอะไร และรับคำสั่ง info รูปแบบไหนได้บ้าง
       (ไว้ไล่ปัญหาเครื่องที่อ่านรายชื่อจอไม่ได้ รันแต่ MuMuManager เท่านั้น)"""
    mgr = _mumu_manager_path()
    if not mgr:
        return {"error": "หา MuMuManager.exe ไม่เจอ" + _mumu_hint()}
    forms = [
        ["version"],
        ["info", "-v", "all"],
        ["info", "--vmindex", "all"],
        ["info", "-v", "0"],
        ["info", "-v", ",".join(str(i) for i in range(10))],
    ]
    out_list = []
    for form in forms:
        try:
            out, rc = _run_hidden([mgr, *form], timeout=25)
        except Exception as e:
            out_list.append({"cmd": " ".join(form), "error": str(e)[:120]})
            continue
        parsed = _mumu_json(out)
        out_list.append({
            "cmd": " ".join(form),
            "rc": rc,
            "json": parsed is not None,
            "keys": sorted(parsed.keys())[:20] if isinstance(parsed, dict) else None,
            "head": " ".join((out or "").split())[:150],
        })
    return {"success": True, "manager": mgr, "results": out_list}


def handle_mumu_control(req_id, data):
    """เปิด/ปิด/ลบ/ดึงรายชื่อ MuMu instance (รันใน thread กันบล็อก socket)"""
    sub = data.get("sub")

    def _go():
        try:
            if sub == "list":
                res = _mumu_list()
            elif sub == "open":
                res = _mumu_open(data.get("indices", []))
            elif sub == "open_all":
                res = _mumu_open_all()
            elif sub == "close":
                res = _mumu_kill_all()
            elif sub == "delete_all":
                res = _mumu_delete_all()
            elif sub == "probe":
                res = _mumu_probe()
            else:
                res = {"error": f"คำสั่ง mumu ไม่รู้จัก: {sub}"}
        except Exception as e:
            res = {"error": str(e)}
        send_response(req_id, res)

    _spawn_with_client(_go)


# ═══════════════════════════════════════════════════════════
#  MuMu Clone — วางลิงก์ Google Drive -> โหลดไฟล์ .mumudata -> restore หลายจออัตโนมัติ
#  งานรันเบื้องหลังทีละเครื่อง เว็บโพลความคืบหน้าด้วย sub=status
# ═══════════════════════════════════════════════════════════

_CLONE_CHUNK = 1024 * 1024
_clone_lock = threading.Lock()
_clone_cancel = threading.Event()
_clone_state = {
    "status": "idle",      # idle | downloading | restoring | launching | done | failed | cancelled
    "message": "",
    "folder": "",          # โฟลเดอร์ที่เก็บไฟล์ที่โหลดมา (ไว้บอกผู้ใช้ว่าไปลบตรงไหน)
    "filename": "",
    "downloaded": 0,       # ไบต์ที่โหลดแล้ว
    "total": 0,            # ขนาดไฟล์ทั้งหมด (0 = ไม่รู้)
    "done": 0,             # จอที่ restore เสร็จแล้ว
    "count": 0,            # จอทั้งหมดที่สั่ง
    "errors": [],
    "new_indexes": [],     # index ของจอใหม่ที่สร้างสำเร็จ
}


class _CloneError(Exception):
    pass


def _clone_update(**kw):
    with _clone_lock:
        _clone_state.update(kw)


def _clone_snapshot():
    with _clone_lock:
        snap = dict(_clone_state)
        snap["errors"] = list(_clone_state["errors"])[:5]
        snap["new_indexes"] = list(_clone_state["new_indexes"])
    return snap


def _clone_backup_dir():
    """ที่เก็บไฟล์ backup ที่โหลดมา — ตั้งชื่อคนละอันกับ mumu-backup ของ server
       เผื่อมีการรัน agent บนเครื่อง server ในโฟลเดอร์เดียวกัน จะได้ไม่เขียนทับไฟล์ต้นฉบับ"""
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mumu-cache")
    os.makedirs(d, exist_ok=True)
    return d


def _gdrive_file_id(url):
    """ดึง file id จากลิงก์ Google Drive รูปแบบต่างๆ"""
    for pat in (r"/file/d/([\w-]{20,})", r"[?&]id=([\w-]{20,})", r"^([\w-]{20,})$"):
        m = re.search(pat, url.strip())
        if m:
            return m.group(1)
    raise _CloneError("ลิงก์ไม่ถูกต้อง (ต้องเป็นลิงก์ไฟล์ Google Drive)")


def _gd_check(r):
    if r.status_code == 404:
        raise _CloneError("ไม่พบไฟล์ ลิงก์อาจผิดหรือไฟล์ถูกลบไปแล้ว")
    if r.status_code == 403:
        raise _CloneError('ไม่มีสิทธิ์เข้าถึง ตรวจสอบว่าแชร์แบบ "ทุกคนที่มีลิงก์"')
    r.raise_for_status()


def _gd_filename(resp, default="backup.mumudata"):
    from urllib.parse import unquote
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd)
    if m:
        return unquote(m.group(1)).strip('" ')
    m = re.search(r'filename="?([^";]+)', cd)
    if m:
        return m.group(1).strip()
    return default


def _extract_mumudata(dest, dest_dir):
    """ถ้าไฟล์ที่โหลดมาเป็น zip -> แตกเอาเฉพาะ .mumudata ออกมา"""
    if not dest.lower().endswith(".zip"):
        return dest
    import zipfile
    found = []
    with zipfile.ZipFile(dest) as z:
        for name in z.namelist():
            if name.lower().endswith(".mumudata"):
                out = os.path.join(dest_dir, os.path.basename(name))
                with z.open(name) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst, _CLONE_CHUNK)
                found.append(out)
    os.remove(dest)
    if not found:
        raise _CloneError("ใน zip ไม่มีไฟล์ .mumudata")
    return found[0]


def _stream_to_file(resp, dest_dir, filename, total, resume_from=0):
    """เขียน response ลงไฟล์พร้อมรายงานความคืบหน้า
       - มีไฟล์เดิมขนาดตรงกันเป๊ะแล้ว → ใช้เลย ไม่โหลดซ้ำหลาย GB
       - สายหลุดกลางคัน → เก็บไฟล์ .part ไว้ ไม่ลบทิ้ง รอบหน้าจะโหลดต่อจากจุดเดิมได้"""
    dest = os.path.join(dest_dir, filename)
    if total and os.path.isfile(dest) and os.path.getsize(dest) == total:
        resp.close()
        _clone_update(filename=filename, downloaded=total, total=total,
                      message=f"ใช้ไฟล์ {filename} ที่โหลดไว้แล้ว (ขนาดตรงกัน)")
        return dest

    tmp = dest + ".part"
    downloaded = last = resume_from
    _clone_update(filename=filename, downloaded=downloaded, total=total,
                  message=(f"โหลดต่อจาก {downloaded // 1048576} MB ..." if resume_from
                           else f"กำลังดาวน์โหลด {filename} ..."))
    try:
        with open(tmp, "ab" if resume_from else "wb") as f:
            for chunk in resp.iter_content(_CLONE_CHUNK):
                if _clone_cancel.is_set():
                    raise _CloneError("ยกเลิกการดาวน์โหลด")
                f.write(chunk)
                downloaded += len(chunk)
                if downloaded - last >= 5 * _CLONE_CHUNK or downloaded == total:
                    last = downloaded
                    _clone_update(downloaded=downloaded, total=total)
    except BaseException:
        if _clone_cancel.is_set():        # ยกเลิกเองถึงจะลบทิ้ง
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    if total and downloaded < total:      # สายหลุดเงียบๆ ยังไม่ครบ ไม่ควรเอาไปใช้
        raise ConnectionError(f"ได้ไม่ครบ ({downloaded}/{total} bytes)")
    os.replace(tmp, dest)
    _clone_update(downloaded=downloaded, total=total or downloaded)
    return dest


class _CloneBusy(Exception):
    """server บอกว่าคิวโหลดเต็ม ให้รอแล้วค่อยมาใหม่ (ไม่ใช่ความผิดพลาด)"""

    def __init__(self, wait):
        super().__init__("คิวเต็ม")
        self.wait = wait


def _direct_download(url):
    """โหลดไฟล์จาก URL ตรงๆ (server ของเราเอง / Dropbox?dl=1 / OneDrive / R2 ฯลฯ)"""
    import requests
    from urllib.parse import unquote, urlparse
    dest_dir = _clone_backup_dir()

    # มีไฟล์ .part ค้างจากรอบก่อนไหม — ถ้ามีให้ขอโหลดต่อจากจุดนั้น (Range)
    guess = os.path.basename(unquote(urlparse(url).path)) or ""
    part = os.path.join(dest_dir, guess + ".part") if guess else ""
    have = os.path.getsize(part) if part and os.path.isfile(part) else 0
    headers = {"Range": f"bytes={have}-"} if have else {}

    # มีไฟล์ครบอยู่ในเครื่องแล้วไหม — ถามขนาดก่อน (HEAD) จะได้ไม่ต้องเปิดสายโหลดใหม่ให้เปลืองเน็ต
    if guess and not have:
        done = os.path.join(dest_dir, guess)
        if os.path.isfile(done):
            try:
                h = requests.head(url, timeout=(10, 30), allow_redirects=True)
                remote = int(h.headers.get("Content-Length") or 0)
            except Exception:
                remote = 0
            if remote and os.path.getsize(done) == remote:
                _clone_update(filename=guess, downloaded=remote, total=remote,
                              folder=dest_dir,
                              message=f"มีไฟล์ {guess} อยู่ในเครื่องแล้ว ({_gb(remote)}) — ไม่ต้องโหลดใหม่")
                logger.info(f"  ใช้ไฟล์เดิมที่ {done} (ไม่โหลดซ้ำ)")
                return _extract_mumudata(done, dest_dir)

    resp = requests.get(url, stream=True, timeout=(10, 120), headers=headers)
    if resp.status_code == 416:        # ขอเกินขนาดไฟล์ = .part เพี้ยน เริ่มใหม่
        resp.close()
        try:
            os.remove(part)
        except OSError:
            pass
        have, headers = 0, {}
        resp = requests.get(url, stream=True, timeout=(10, 120))
    if resp.status_code == 503:
        wait = 30
        try:
            wait = max(5, min(120, int(resp.headers.get("Retry-After", 30))))
        except (TypeError, ValueError):
            pass
        resp.close()
        raise _CloneBusy(wait)
    if resp.status_code == 403:
        detail = ""
        try:
            detail = str((resp.json() or {}).get("message") or "")[:160]
        except Exception:
            pass
        resp.close()
        if "hotlink" in detail.lower() or "rate" in detail.lower():
            raise _CloneError(f"เว็บต้นทางบล็อกการโหลดอัตโนมัติแล้ว (403): {detail} "
                              "— ใช้ 'ไฟล์บน server' หรือดูดจากเครื่องเพื่อนแทน")
        raise _CloneError("เข้าถึงไฟล์ไม่ได้ (403)"
                          + (f": {detail}" if detail else
                             " — ถ้าโหลดจาก server ให้เช็กว่า agent_secret ตรงกัน"))
    if resp.status_code == 404:
        raise _CloneError("ไม่พบไฟล์บน server (404) — ไฟล์อาจถูกลบหรือชื่อไม่ตรง")
    resp.raise_for_status()
    if "text/html" in resp.headers.get("Content-Type", ""):
        resp.close()
        raise _CloneError("ลิงก์นี้ตอบกลับมาเป็นหน้าเว็บ ไม่ใช่ไฟล์ — ต้องเป็นลิงก์ดาวน์โหลดตรง")

    filename = _gd_filename(resp, "") or guess or "backup.mumudata"
    total = int(resp.headers["Content-Length"]) if "Content-Length" in resp.headers else 0
    resume_from = 0
    if resp.status_code == 206:        # server ยอมให้โหลดต่อ
        resume_from = have
        m = re.search(r"bytes\s+\d+-\d+/(\d+)", resp.headers.get("Content-Range", ""))
        total = int(m.group(1)) if m else (total + have)
        logger.info(f"  โหลดต่อจาก {have // 1048576} MB / {total // 1048576} MB")
    dest = _stream_to_file(resp, dest_dir, filename, total, resume_from)
    return _extract_mumudata(dest, dest_dir)


# เว็บฝากไฟล์ที่ลิงก์แชร์เป็น "หน้าเว็บ" ไม่ใช่ไฟล์ ต้องกดผ่านหน้าเว็บถึงจะได้ไฟล์
_PAGE_HOSTS = ("gofile.io", "mega.nz", "mediafire.com", "1fichier.com",
               "terabox", "krakenfiles", "send.cm")


def _link_meta(url):
    """คืน (ชื่อไฟล์, ขนาด) ของลิงก์ — ใช้เช็คว่ามีไฟล์นี้ในเครื่อง/ที่เพื่อนแล้วหรือยัง
       pixeldrain ถาม info API ได้ตลอด แม้ตอนที่การโหลดโดนบล็อกเพราะ hotlink"""
    import requests
    from urllib.parse import unquote, urlparse
    m = re.search(r"pixeldrain\.com/(?:api/file|u)/([A-Za-z0-9_-]+)", url)
    if m:
        try:
            j = requests.get(f"https://pixeldrain.com/api/file/{m.group(1)}/info",
                             timeout=20).json()
            if j.get("name"):
                return j["name"], int(j.get("size") or 0)
        except Exception:
            pass
    try:
        r = requests.head(url, timeout=(10, 30), allow_redirects=True)
        if r.status_code < 400:
            return (_gd_filename(r, "") or os.path.basename(unquote(urlparse(url).path)),
                    int(r.headers.get("Content-Length") or 0))
    except Exception:
        pass
    return "", 0


def _link_filename(url):
    return _link_meta(url)[0]


def _pixeldrain_direct(url):
    """แปลงลิงก์แชร์ pixeldrain เป็นลิงก์ไฟล์ตรง
       /u/<id> = ไฟล์เดียว · /l/<id> = อัลบั้ม (หยิบไฟล์ .mumudata/.zip ให้)"""
    import requests
    m = re.search(r"pixeldrain\.com/(api/file|api/list|u|l)/([A-Za-z0-9_-]+)", url)
    if not m:
        raise _CloneError("ลิงก์ pixeldrain ใช้ไม่ได้ — ต้องเป็นลิงก์ของไฟล์ (.../u/<id>) "
                          "หรืออัลบั้ม (.../l/<id>) · หน้า /user/ เป็นหน้าจัดการไฟล์ ไม่ใช่ลิงก์ไฟล์")
    kind, pid = m.group(1), m.group(2)
    if kind in ("u", "api/file"):
        return f"https://pixeldrain.com/api/file/{pid}?download"

    r = requests.get(f"https://pixeldrain.com/api/list/{pid}", timeout=30)
    if r.status_code != 200:
        raise _CloneError(f"เปิดอัลบั้ม pixeldrain ไม่ได้ (HTTP {r.status_code})")
    files = r.json().get("files") or []
    pick = next((f for f in files
                 if str(f.get("name", "")).lower().endswith((".mumudata", ".zip"))),
                files[0] if files else None)
    if not pick:
        raise _CloneError("ในอัลบั้ม pixeldrain นี้ไม่มีไฟล์")
    logger.info(f"  pixeldrain: เลือกไฟล์ {pick.get('name')}")
    return f"https://pixeldrain.com/api/file/{pick['id']}?download"


def _resolve_link(url):
    """แปลงลิงก์แชร์ให้เป็นลิงก์ที่โหลดได้จริง + ปัดลิงก์ที่เป็นหน้าเว็บล้วนๆ ออกตั้งแต่ต้น"""
    low = (url or "").lower()
    if "pixeldrain.com" in low:
        return _pixeldrain_direct(url)
    for h in _PAGE_HOSTS:
        if h in low:
            raise _CloneError(
                f"ลิงก์ {h} เป็นหน้าเว็บสำหรับกดโหลดเอง ใช้กับระบบอัตโนมัติไม่ได้ — "
                "ให้ใช้ 'ไฟล์บน server' หรือลิงก์ดาวน์โหลดตรง "
                "(เช่น pixeldrain: https://pixeldrain.com/u/<id>)")
    return url


def _clone_download(url):
    """เลือกวิธีโหลดตามชนิดของลิงก์ — Google Drive ต้องคุยหน้า confirm ก่อน ที่เหลือโหลดตรงได้เลย"""
    url = _resolve_link(url)
    low = url.lower()
    if "drive.google.com" in low or "docs.google.com" in low:
        return _gdrive_download(url)
    return _direct_download(url)


def _gdrive_download(url):
    """ดาวน์โหลดไฟล์จาก Google Drive (รองรับหน้า confirm ของไฟล์ใหญ่) คืน path ของ .mumudata
       ถ้าเคยโหลดไฟล์เดิมไว้แล้วขนาดตรงกัน จะใช้ไฟล์เดิมโดยไม่โหลดซ้ำ"""
    import requests
    dest_dir = _clone_backup_dir()
    file_id = _gdrive_file_id(url)
    s = requests.Session()
    resp = s.get(f"https://drive.google.com/uc?export=download&id={file_id}",
                 stream=True, timeout=60)
    _gd_check(resp)

    if "text/html" in resp.headers.get("Content-Type", ""):
        html = resp.text
        resp.close()
        # หน้า confirm ของไฟล์ใหญ่ -> อ่านฟอร์มแล้วยิงตามที่ฟอร์มบอก
        m_action = re.search(r'<form[^>]+id="download-form"[^>]+action="([^"]+)"', html)
        if not m_action:
            if "quota" in html.lower():
                raise _CloneError("ไฟล์นี้ถูกโหลดเกินโควต้าของ Google Drive แล้ว ลองใหม่พรุ่งนี้")
            raise _CloneError('เปิดไฟล์ไม่ได้ ตรวจสอบว่าแชร์แบบ "ทุกคนที่มีลิงก์" (Anyone with the link)')
        params = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', html))
        resp = s.get(m_action.group(1).replace("&amp;", "&"), params=params,
                     stream=True, timeout=60)
        _gd_check(resp)
        if "text/html" in resp.headers.get("Content-Type", ""):
            body = resp.text[:4000].lower()
            resp.close()
            # เจอบ่อยสุดตอนหลายเครื่องรุมโหลดไฟล์ใหญ่ไฟล์เดียวกัน = โดนจำกัดโควต้า
            if "quota" in body or "too many" in body or "can't view or download" in body:
                raise _CloneError("Google Drive จำกัดโควต้าดาวน์โหลดของไฟล์นี้แล้ว "
                                  "(คนโหลดพร้อมกันเยอะเกิน) — ให้เปลี่ยนไปใช้ไฟล์บน server แทน")
            raise _CloneError("Google Drive ไม่ยอมให้ดาวน์โหลด — ถ้าแชร์ถูกต้องแล้วมักแปลว่าโดนจำกัดโควต้า "
                              "ให้เปลี่ยนไปใช้ไฟล์บน server แทน")

    filename = _gd_filename(resp)
    total = int(resp.headers["Content-Length"]) if "Content-Length" in resp.headers else 0
    dest = _stream_to_file(resp, dest_dir, filename, total)
    return _extract_mumudata(dest, dest_dir)


def _mumu_list_indexes(mgr):
    """คืน set ของ index ทุก instance (ใช้เทียบก่อน/หลัง import เพื่อหาจอใหม่)"""
    try:
        data, _flag, _out = _mumu_info_raw(mgr, timeout=30)
    except Exception:
        return set()
    if data is None:
        return set()
    if isinstance(data, dict) and ("index" in data or "name" in data) \
            and not any(k.isdigit() for k in data.keys()):
        return {str(data.get("index", 0))}
    if isinstance(data, dict):
        return {k for k in data.keys() if k.isdigit()}
    return set()


def _mumu_err_text(out):
    """แปลงผลลัพธ์ error ของ MuMuManager เป็นข้อความสั้นๆ ที่อ่านรู้เรื่อง"""
    data = _mumu_json(out)
    msgs = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict) and v.get("errmsg"):
                msgs.append(f"{v['errmsg']} (code {v.get('errcode')})")
            elif k == "errmsg":
                msgs.append(str(v))
    text = "; ".join(dict.fromkeys(msgs))[:150] or " ".join((out or "").split())[:150]
    if "mainnx" in text.lower() or "-502" in text:
        text += " — บริการหลักของ MuMu ไม่ตอบ (มักเกิดตอนสั่งงานรัวเกินไป)"
    return text


def _mumu_wait_new(mgr, before, wait):
    """รอให้จอใหม่โผล่ (บางคำสั่งทำงานต่อเบื้องหลังหลังคืน prompt แล้ว)"""
    deadline = time.time() + wait
    while time.time() < deadline and not _clone_cancel.is_set():
        time.sleep(3)
        new = _mumu_list_indexes(mgr) - before
        if new:
            return sorted(new, key=int)
    return []


def _mumu_run_progress(mgr, args, before, timeout, label):
    """รันคำสั่งที่กินเวลานาน พร้อมรายงานความคืบหน้า
       บอกเวลาที่ผ่านไป + เนื้อที่ว่างด้วย เพราะ MuMu ตอนสร้างจอจะดูเหมือนค้าง
       (Windows ขึ้น 'not responding' ตั้งแต่หน้าต่างไม่ตอบ 5 วิ) จะได้แยกออกว่าค้างจริงหรือแค่ช้า"""
    box = {}

    def _go():
        try:
            box["out"], box["rc"] = _run_hidden([mgr, *args], timeout=timeout)
        except Exception as e:
            box["err"] = str(e)[:150]

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    t0 = time.time()
    last_made, stall_since = -1, time.time()
    while t.is_alive():
        t.join(15)         # ถามห่างๆ ตอน MuMu กำลังทำงานหนัก ไม่ให้ไปแย่ง mainnx
        made = len(_mumu_list_indexes(mgr) - before)
        free = _disk_free(mgr)
        if made != last_made:
            last_made, stall_since = made, time.time()
        mins = int((time.time() - t0) / 60)
        warn = ""
        if free < 3 * 1073741824:
            warn = " ⚠️ ดิสก์ใกล้เต็ม!"
        elif time.time() - stall_since > 900:
            warn = " ⚠️ ไม่คืบหน้ามา 15 นาที (MuMu อาจค้าง — ลองกด Kill MuMu แล้วเริ่มใหม่)"
        _clone_update(done=made,
                      message=f"{label} — สร้างแล้ว {made} จอ · ผ่านไป {mins} นาที · "
                              f"ดิสก์ว่าง {_gb(free)}{warn}")
    return box.get("out", ""), box.get("err")


def _disk_free(path):
    """เนื้อที่ว่างของไดรฟ์ที่ path นั้นอยู่ (bytes) — 0 ถ้าเช็คไม่ได้"""
    try:
        return shutil.disk_usage(os.path.dirname(os.path.abspath(path))).free
    except Exception:
        return 0


def _gb(n):
    return f"{n / 1073741824:.1f} GB"


def _mumu_restore_many(mgr, path, count, close_first=True):
    """สร้างจอใหม่ count จอจากไฟล์ backup
       import ทีละจอรัวๆ จะทำให้ MuMu ตอบ 'mainnx request failed' (errcode -502)
       จึง import แค่จอแรก แล้วใช้ clone ก๊อปที่เหลือ (เร็วกว่าด้วย ไม่ต้องแตกไฟล์ใหม่ทุกจอ)
       คืน (รายการ index ใหม่, รายการ error)"""
    errors = []
    if close_first:
        # ปิด MuMu ให้หมดก่อน แล้วทำงานผ่าน MuMuManager ล้วนๆ
        # หน้าต่าง MuMu ที่เปิดค้างไว้จะแย่งดิสก์/แรมและมักค้างเองตอนสร้างจอ
        _clone_update(message="กำลังปิด MuMu ทั้งหมดก่อนเริ่ม (ทำงานแบบไม่เปิดหน้าต่าง)...")
        _mumu_kill_all()
        time.sleep(5)

    before = _mumu_list_indexes(mgr)

    # เนื้อที่ไม่พอคือสาเหตุที่ MuMu ค้างค้าง "Creating device" แล้วไม่ตอบสนอง
    # เช็คก่อนเลย จะได้ไม่ต้องรอเป็นชั่วโมงแล้วพัง
    need = os.path.getsize(path)
    free = _disk_free(mgr)
    if free and free < need * 1.5:
        raise _CloneError(f"เนื้อที่ไดรฟ์ที่ติดตั้ง MuMu ไม่พอ — เหลือ {_gb(free)} "
                          f"แต่ต้องใช้อย่างน้อย {_gb(need * 1.5)} ต่อ 1 จอ")
    if free and free < need * count:
        errors.append(f"⚠️ เนื้อที่อาจไม่พอ: เหลือ {_gb(free)} แต่ {count} จอต้องใช้ราว "
                      f"{_gb(need * count)} — จอท้ายๆ อาจสร้างไม่สำเร็จ")
        _clone_update(message=errors[-1])
        time.sleep(3)

    out, err = _mumu_run_progress(mgr, ["import", "-p", str(path)], before, 3600,
                                  "กำลัง import จอแรกจากไฟล์ backup")
    if err:
        return [], [f"import จอแรก: {err}"]
    made = sorted(_mumu_list_indexes(mgr) - before, key=int) or _mumu_wait_new(mgr, before, 60)
    if not made:
        return [], [f"import จอแรกไม่สำเร็จ: {_mumu_err_text(out)}"]
    _clone_update(done=len(made))
    if count <= len(made) or _clone_cancel.is_set():
        return made, errors

    # จอแรกเสร็จแล้ว → วัดว่าจอหนึ่งกินดิสก์จริงเท่าไร แล้วคำนวณว่าเหลือที่ทำได้อีกกี่จอ
    # (สำคัญมาก: ถ้าสั่งเกินที่ดิสก์รับไหว MuMu จะค้างที่ "Creating device xx%" แล้วไม่ตอบสนอง)
    base = made[0]
    remain = count - len(made)
    per = 0
    try:
        data, _f, _o = _mumu_info_raw(mgr)
        if isinstance(data, dict):
            per = int((data.get(str(base)) or {}).get("disk_size_bytes") or 0)
    except Exception:
        pass
    per = max(per, need)                       # อย่างน้อยต้องเท่าไฟล์ backup
    free_now = _disk_free(mgr)
    if per and free_now:
        room = int((free_now - 5 * 1073741824) // per)   # กันเนื้อที่ระบบไว้ 5 GB
        if room < remain:
            msg = (f"เนื้อที่พอทำได้อีก {max(0, room)} จอเท่านั้น (สั่งไว้ {count} จอ) — "
                   f"จอละ ~{_gb(per)} เหลือ {_gb(free_now)}")
            errors.append(msg)
            _clone_update(message=msg)
            logger.warning(f"  clone: {msg}")
            remain = max(0, room)
            if remain == 0:
                return made, errors
            count = len(made) + remain
            time.sleep(3)
    # ── ก๊อปทีละจอ ──
    # สั่ง clone -n หลายจอรวดเดียว MuMu จะทำงานหนักจนค้างที่ "Creating device xx%"
    # ทำทีละจอ + เว้นจังหวะให้มันหายใจ + เช็คดิสก์ก่อนทุกจอ ช้ากว่านิดเดียวแต่ไม่ค้าง
    flag = _mumu_vflag_order()[0]
    for n in range(remain):
        if _clone_cancel.is_set():
            break
        free_now = _disk_free(mgr)
        if per and free_now < per + 3 * 1073741824:
            errors.append(f"หยุดที่ {len(made)} จอ — ดิสก์เหลือ {_gb(free_now)} "
                          f"ไม่พอทำจอต่อไป (จอละ ~{_gb(per)})")
            break

        mark = set(_mumu_list_indexes(mgr))
        label = f"กำลังก๊อปจอที่ {len(made) + 1}/{count} จากจอ #{base}"
        out, err = _mumu_run_progress(mgr, ["clone", flag, str(base), "-n", "1"],
                                      mark, 3600, label)
        if _mumu_is_help(out):          # เวอร์ชันที่ไม่รับ flag ตัวย่อ
            alt = [f for f in _MUMU_VFLAGS if f != flag]
            if alt:
                flag = alt[0]
                out, err = _mumu_run_progress(mgr, ["clone", flag, str(base), "-n", "1"],
                                              mark, 3600, label)

        new = sorted(_mumu_list_indexes(mgr) - mark, key=int) or _mumu_wait_new(mgr, mark, 90)
        if not new:
            errors.append(f"จอที่ {len(made) + 1} ไม่สำเร็จ: {err or _mumu_err_text(out)}")
            break                       # พลาดแล้วหยุด ไม่ดันต่อจนเครื่องค้าง
        made.extend(new)
        _clone_update(done=len(made),
                      message=f"เสร็จ {len(made)}/{count} จอ · ดิสก์ว่าง {_gb(_disk_free(mgr))}")
        if n < remain - 1 and not _clone_cancel.is_set():
            time.sleep(8)               # ให้ MuMu เขียนดิสก์ให้จบก่อนสั่งจอถัดไป
    return made[:count], errors


# ═══════════════════════════════════════════════════════════
#  PEER — เครื่องลูกแบ่งไฟล์ backup ให้กันเอง
#  เครื่องแม่ต่อผ่าน Tailscale เน็ตขาออกตัวเดียวรับ 20+ เครื่องไม่ไหว
#  เครื่องไหนโหลดไฟล์เสร็จแล้วจะเปิดให้เพื่อนมาดูดต่อ (เพื่อนวง LAN เดียวกันได้ความเร็ว LAN เต็มๆ)
# ═══════════════════════════════════════════════════════════
PEER_PORT = int(os.environ.get("PEER_PORT") or _cfg.get("peer_port") or 5010)
_peer_sem = threading.Semaphore(4)      # จำกัดคนมาดูดพร้อมกัน ไม่ให้เครื่องตัวเองอืด


def get_all_ips():
    """IPv4 ทุกวงของเครื่องนี้ (LAN + Tailscale) ให้เพื่อนเลือกทางที่เร็วที่สุดเอง"""
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    main = get_local_ip()
    if main and main not in ips and not main.startswith("127."):
        ips.insert(0, main)
    return ips


def _peer_cached_files():
    """ไฟล์ backup ที่เครื่องนี้มีอยู่แล้ว (พร้อมแบ่งให้เพื่อน)"""
    out = []
    try:
        d = _clone_backup_dir()
        for n in sorted(os.listdir(d)):
            full = os.path.join(d, n)
            if os.path.isfile(full) and n.lower().endswith((".mumudata", ".zip")):
                out.append({"name": n, "size": os.path.getsize(full)})
    except Exception:
        pass
    return out


def _peer_report():
    """บอกทุก server ว่าเครื่องนี้มีไฟล์อะไรให้แบ่งบ้าง"""
    payload = {"secret": AGENT_SECRET, "agent_id": AGENT_ID or AGENT_NAME or get_hostname(),
               "ips": get_all_ips(), "port": PEER_PORT, "files": _peer_cached_files()}
    for url, client in list(_clients.items()):
        if _client_connected.get(url):
            try:
                client.emit("agent_have", payload)
            except Exception:
                pass


def _peer_serve():
    """HTTP server เล็กๆ เสิร์ฟไฟล์ใน mumu-cache ให้เพื่อน (ต้องมี secret ตรงกัน)"""
    import hmac
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse, parse_qs, unquote

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass          # ไม่ต้องรก log

        def _check(self):
            u = urlparse(self.path)
            if not u.path.startswith("/peer/"):
                self.send_error(404)
                return None
            if not hmac.compare_digest(
                    (parse_qs(u.query).get("secret") or [""])[0], AGENT_SECRET):
                self.send_error(403)
                return None
            name = os.path.basename(unquote(u.path[len("/peer/"):]))
            full = os.path.join(_clone_backup_dir(), name)
            if not name or not os.path.isfile(full):
                self.send_error(404)
                return None
            return full

        def do_HEAD(self):
            full = self._check()
            if not full:
                return
            self.send_response(200)
            self.send_header("Content-Length", str(os.path.getsize(full)))
            self.end_headers()

        def do_GET(self):
            full = self._check()
            if not full:
                return
            if not _peer_sem.acquire(blocking=False):
                self.send_response(503)
                self.send_header("Retry-After", "20")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            try:
                size = os.path.getsize(full)
                start = 0
                rng = self.headers.get("Range", "")
                if rng.startswith("bytes="):        # เพื่อนขอโหลดต่อจากจุดที่ค้าง
                    try:
                        start = int(rng.split("=", 1)[1].split("-")[0] or 0)
                    except ValueError:
                        start = 0
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(206 if start else 200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size - start))
                if start:
                    self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{os.path.basename(full)}"')
                self.end_headers()
                with open(full, "rb") as f:
                    f.seek(start)
                    shutil.copyfileobj(f, self.wfile, 1024 * 1024)
            except Exception:
                pass          # เพื่อนตัดสาย = เรื่องปกติ
            finally:
                _peer_sem.release()

    try:
        # เปิด firewall ให้พอร์ตนี้ (ถ้าไม่ได้รันแบบ admin จะเงียบๆ ไป เพื่อนในวง LAN อาจต่อไม่ได้)
        _run_hidden(["netsh", "advfirewall", "firewall", "add", "rule",
                     "name=RemoteFileAgentPeer", "dir=in", "action=allow",
                     "protocol=TCP", f"localport={PEER_PORT}"], timeout=15)
    except Exception:
        pass
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", PEER_PORT), Handler)
        srv.daemon_threads = True
        logger.info(f"📤 เปิดพอร์ตแบ่งไฟล์ให้เพื่อนที่ {PEER_PORT}")
        srv.serve_forever()
    except Exception as e:
        logger.warning(f"เปิดพอร์ตแบ่งไฟล์ไม่ได้ ({PEER_PORT}): {e}")


def _scrub_secret(s):
    """ตัด secret ออกจากข้อความก่อนส่งไปโชว์บนหน้าเว็บ"""
    return re.sub(r"(secret=)[^&\s\"']+", r"\1***", str(s))


def _host_of(url):
    from urllib.parse import urlparse
    return urlparse(url).netloc or url


def _server_candidates(prefer=None):
    """เรียงลำดับ server ที่จะลองโหลดไฟล์:
       1) ตัวที่สั่งงานเข้ามา  2) ตัวที่ socket ยังเชื่อมต่ออยู่  3) ที่เหลือ
       (เดิมไล่ตามลำดับใน config ดื้อๆ เลยไปโดน server ที่ติดต่อไม่ได้ก่อน)"""
    def rank(u):
        if prefer and u == prefer:
            return 0
        return 1 if _client_connected.get(u) else 2
    return sorted([u for u in SERVER_URLS if u], key=rank)


def _server_backup_url(name, prefer=None):
    """ประกอบลิงก์โหลดไฟล์ backup จาก server (agent รู้ secret อยู่แล้ว ไม่ต้องส่ง secret ผ่านหน้าเว็บ)"""
    from urllib.parse import quote
    return [f"{u.rstrip('/')}/mumu-backup/{quote(os.path.basename(name))}"
            f"?secret={quote(AGENT_SECRET)}" for u in _server_candidates(prefer)]


def _peer_urls(name, prefer):
    """ถาม server ว่าเครื่องไหนโหลดไฟล์นี้เสร็จแล้วบ้าง แล้วเรียงลำดับ:
       เพื่อนวง LAN เดียวกันก่อน (เร็วสุด) → เพื่อนที่อื่น (ใช้เน็ตเครื่องลูก) → ค่อยเป็นเครื่องแม่"""
    import requests
    from urllib.parse import quote
    my_ips = get_all_ips()
    my_nets = {".".join(ip.split(".")[:3]) for ip in my_ips if ip.count(".") == 3}
    me = (AGENT_ID or AGENT_NAME or get_hostname())
    urls = []
    for base in _server_candidates(prefer):
        try:
            r = requests.get(f"{base.rstrip('/')}/mumu-peers/{quote(os.path.basename(name))}",
                             params={"secret": AGENT_SECRET}, timeout=15)
            if r.status_code != 200:
                continue
            for p in r.json().get("peers", []):
                if p.get("agent_id") == me:
                    continue
                port = p.get("port") or PEER_PORT
                for ip in p.get("ips", []):
                    if ip in my_ips:
                        continue
                    same_lan = ".".join(ip.split(".")[:3]) in my_nets
                    urls.append((0 if same_lan else 1,
                                 f"http://{ip}:{port}/peer/{quote(os.path.basename(name))}"
                                 f"?secret={quote(AGENT_SECRET)}"))
            break        # ถาม server ตัวแรกที่ตอบได้ก็พอ
        except Exception:
            continue
    urls.sort(key=lambda x: x[0])
    return [u for _, u in urls]


def _download_from_servers(name, prefer):
    """โหลดไฟล์ backup — เอาจากเพื่อนที่โหลดเสร็จแล้วก่อน ไม่ได้ค่อยไปเอาจากเครื่องแม่
       (เครื่องแม่ต่อผ่าน Tailscale เน็ตขาออกตัวเดียว รับ 20+ เครื่องพร้อมกันไม่ไหว)"""
    cands = _peer_urls(name, prefer) + _server_backup_url(name, prefer)
    if not cands:
        raise _CloneError("ยังไม่ได้ตั้งค่า server_urls ในเครื่องนี้")
    # ลองหลายรอบได้ เพราะแต่ละรอบโหลด "ต่อ" จากไฟล์ .part เดิม ไม่ได้เริ่มใหม่
    errs, waited = [], 0
    for attempt in range(8):
        for u in cands:
            host = _host_of(u)
            while True:                   # วนเฉพาะตอนติดคิว ไม่นับเป็นความล้มเหลว
                if _clone_cancel.is_set():
                    raise _CloneError("ยกเลิกแล้ว")
                try:
                    return _direct_download(u)
                except _CloneBusy as b:
                    waited += b.wait
                    if waited > 7200:     # รอรวมเกิน 2 ชม. ก็ยอมแพ้
                        errs.append(f"{host}: รอคิวนานเกินไป")
                        break
                    _clone_update(status="downloading",
                                  message=f"เข้าคิวรอโหลดจาก {host} (คิวเต็ม ขอใหม่ใน {b.wait} วิ)")
                    time.sleep(b.wait)
                    # ระหว่างรอ อาจมีเพื่อนโหลดเสร็จแล้ว — ถ้ามีให้ไปดูดจากเพื่อนแทนเลย
                    fresh = _peer_urls(name, prefer)
                    if fresh:
                        cands[:0] = [f for f in fresh if f not in cands]
                        break
                except _CloneError as e:  # 403/404/ไม่ใช่ไฟล์ — ลองใหม่ก็ไม่ช่วย
                    errs.append(f"{host}: {_scrub_secret(e)}")
                    break
                except Exception as e:    # ต่อไม่ติด/หลุดกลางคัน — รอแล้วลองใหม่ได้
                    errs.append(f"{host}: {_scrub_secret(e)[:100]}")
                    break
        if attempt < 7 and not _clone_cancel.is_set():
            wait = min(60, 15 * (attempt + 1))
            _clone_update(message=f"สายหลุด — รอ {wait} วิแล้วโหลดต่อจากจุดเดิม "
                                  f"(ครั้งที่ {attempt + 2}/8)")
            time.sleep(wait)
    raise _CloneError("โหลดไฟล์จาก server ไม่สำเร็จ — " + " | ".join(dict.fromkeys(errs))[:250])


def _clone_cache_info():
    """ไฟล์ backup ที่โหลดเก็บไว้ในเครื่องนี้ + เนื้อที่ว่าง (ไว้โชว์ให้รู้ว่าไฟล์ไปอยู่ไหน)"""
    d = _clone_backup_dir()
    files, total = [], 0
    try:
        for n in sorted(os.listdir(d)):
            full = os.path.join(d, n)
            if os.path.isfile(full):
                sz = os.path.getsize(full)
                total += sz
                files.append({"name": n, "size": sz, "partial": n.endswith(".part")})
    except Exception as e:
        return {"error": f"อ่านโฟลเดอร์ไม่ได้: {e}", "folder": d}
    mgr = _mumu_manager_path()
    return {"success": True, "folder": d, "files": files, "total": total,
            "free": _disk_free(d), "mumu_free": _disk_free(mgr) if mgr else 0}


def _clone_cache_clear(name=""):
    """ลบไฟล์ backup ที่โหลดเก็บไว้ (คืนเนื้อที่) — ระบุชื่อไฟล์ได้ ไม่ระบุ = ลบหมด"""
    d = _clone_backup_dir()
    if _clone_state["status"] in ("downloading", "restoring", "launching"):
        return {"error": "มีงาน clone ทำอยู่ ลบไฟล์ตอนนี้ไม่ได้"}
    want = os.path.basename(name) if name else ""
    freed, deleted, errors = 0, [], []
    try:
        for n in os.listdir(d):
            if want and n != want and n != want + ".part":
                continue
            full = os.path.join(d, n)
            if not os.path.isfile(full):
                continue
            try:
                sz = os.path.getsize(full)
                os.remove(full)
                freed += sz
                deleted.append(n)
            except Exception as e:
                errors.append(f"{n}: {e}")
    except Exception as e:
        return {"error": f"ลบไม่สำเร็จ: {e}", "folder": d}
    if deleted:
        _peer_report()      # ไม่มีไฟล์แล้ว อย่าให้เพื่อนมาดูดต่อ
    logger.info(f"  clone cache: ลบ {len(deleted)} ไฟล์ คืนเนื้อที่ {_gb(freed)}")
    return {"success": True, "folder": d, "deleted": deleted, "freed": freed,
            "errors": errors, "free": _disk_free(d)}


def _clone_netcheck(name, prefer=None):
    """ตรวจว่าเครื่องนี้คุยกับ server ตัวไหนได้บ้าง + โหลดไฟล์ได้ไหม (ไว้ไล่ปัญหา)"""
    import requests
    out = {"success": True, "prefer": prefer, "server_urls": list(SERVER_URLS),
           "connected": [u for u, ok in _client_connected.items() if ok], "tests": []}
    for u in _server_backup_url(name or "test.mumudata", prefer):
        rec, t0 = {"host": _host_of(u)}, time.time()
        try:
            r = requests.get(u, headers={"Range": "bytes=0-1023"}, timeout=20)
            rec["status"] = r.status_code
            rec["bytes"] = len(r.content)
            r.close()
        except Exception as e:
            rec["error"] = _scrub_secret(e)[:160]
        rec["ms"] = int((time.time() - t0) * 1000)
        out["tests"].append(rec)
    return out


def _clone_worker(url, count, launch, source="link", name="", server_url=None,
                  close_first=True):
    """งานเบื้องหลัง: โหลดไฟล์ -> restore ทีละจอจนครบ -> (ถ้าเลือก) เปิดจอใหม่ทั้งหมด"""
    try:
        mgr = _mumu_manager_path()
        if not mgr:
            raise _CloneError("หา MuMuManager.exe ไม่เจอ — ตั้ง 'mumu_manager_path' ใน config.json" + _mumu_hint())

        if source == "server":
            _clone_update(status="downloading", message=f"กำลังหาแหล่งโหลด {name}...")
            path = _download_from_servers(name, server_url)
            _peer_report()      # โหลดเสร็จแล้ว บอกเพื่อนว่ามาดูดต่อจากเราได้
        else:
            _clone_update(status="downloading", message="กำลังเชื่อมต่อแหล่งดาวน์โหลด...")
            url = _resolve_link(url)          # แปลงลิงก์แชร์ให้เป็นลิงก์ตรงก่อน
            path = None
            fname, fsize = _link_meta(url)

            # 1) มีไฟล์ครบอยู่ในเครื่องแล้ว → ใช้เลย ไม่ต้องยุ่งกับเน็ตเลยสักนิด
            if fname:
                local = os.path.join(_clone_backup_dir(), fname)
                if os.path.isfile(local) and (not fsize or os.path.getsize(local) == fsize):
                    path = local
                    _clone_update(filename=fname, downloaded=os.path.getsize(local),
                                  total=os.path.getsize(local), folder=_clone_backup_dir(),
                                  message=f"มีไฟล์ {fname} อยู่ในเครื่องแล้ว — ไม่ต้องโหลดใหม่")
                    logger.info(f"  ใช้ไฟล์เดิมที่ {local}")

            # 2) ไม่มีก็ดูดจากเพื่อนที่โหลดไว้แล้ว (เร็วกว่าและไม่ไปโดนเว็บต้นทางบล็อก)
            if path is None and fname:
                for u in _peer_urls(fname, server_url):
                    try:
                        _clone_update(message=f"เจอไฟล์ที่เพื่อน ({_host_of(u)}) กำลังดูดมา...")
                        path = _direct_download(u)
                        break
                    except Exception:
                        continue

            # 3) สุดท้ายค่อยโหลดจากลิงก์ต้นทาง
            if path is None:
                path = _clone_download(url)
            _peer_report()

        _clone_update(status="restoring",
                      message=f"เริ่ม restore จากไฟล์ {os.path.basename(path)}")
        try:
            new_indexes, errs = _mumu_restore_many(mgr, path, count, close_first)
        except subprocess.TimeoutExpired:
            new_indexes, errs = [], ["หมดเวลา (timeout)"]
        if _clone_cancel.is_set():
            raise _CloneError("ยกเลิกแล้ว")
        with _clone_lock:
            _clone_state["new_indexes"] = list(new_indexes)
            _clone_state["done"] = len(new_indexes)
            _clone_state["errors"].extend(errs)

        if launch and new_indexes:
            _clone_update(status="launching",
                          message=f"กำลังเปิดจอใหม่ {len(new_indexes)} จอ...")
            try:
                _mumu_run_v(mgr, "control", ",".join(str(x) for x in new_indexes),
                            "launch", timeout=300)
            except Exception as e:
                with _clone_lock:
                    _clone_state["errors"].append(f"เปิดจอ: {e}")

        done = _clone_snapshot()["done"]
        if done == count:
            msg = f"เสร็จทั้งหมด {done}/{count} จอ"
            if launch and new_indexes:
                msg += " และสั่งเปิดจอแล้ว"
            _clone_update(status="done", message=msg)
        else:
            _clone_update(status="done", message=f"เสร็จ {done}/{count} จอ (มีบางจอล้มเหลว)")
    except _CloneError as e:
        _clone_update(status="cancelled" if _clone_cancel.is_set() else "failed", message=str(e))
    except Exception as e:
        _clone_update(status="failed", message=str(e)[:200])
    snap = _clone_snapshot()
    logger.info(f"  MuMu clone: จบงาน — {snap['status']}: {snap['message']}")


def handle_mumu_clone(req_id, data):
    """คำสั่งจากเว็บ: start = เริ่มงาน clone / status = ดูความคืบหน้า / cancel = ยกเลิก"""
    sub = data.get("sub")

    if sub == "status":
        send_response(req_id, {"success": True, "state": _clone_snapshot()})
        return
    if sub == "cache_list":
        send_response(req_id, _clone_cache_info())
        return
    if sub == "cache_clear":
        send_response(req_id, _clone_cache_clear(data.get("name", "")))
        return
    if sub == "netcheck":
        def _go():
            cur = _current_client()
            su = next((u for u, c in _clients.items() if c is cur), None)
            try:
                send_response(req_id, _clone_netcheck(data.get("name"), su))
            except Exception as e:
                send_response(req_id, {"error": _scrub_secret(e)[:200]})
        _spawn_with_client(_go)
        return
    if sub == "cancel":
        _clone_cancel.set()
        send_response(req_id, {"success": True, "cancelling": True})
        return
    if sub != "start":
        send_response(req_id, {"error": f"คำสั่ง clone ไม่รู้จัก: {sub}"})
        return

    source = str(data.get("source") or "link").strip()
    url = str(data.get("url") or "").strip()
    name = str(data.get("name") or "").strip()
    if source == "server":
        if not name:
            send_response(req_id, {"error": "ยังไม่ได้เลือกไฟล์ backup บน server"})
            return
    elif not url:
        send_response(req_id, {"error": "ยังไม่ได้ใส่ลิงก์ดาวน์โหลด"})
        return

    # จำไว้ว่าคำสั่งนี้มาจาก server เครื่องไหน จะได้โหลดไฟล์จากเครื่องนั้นก่อน
    cur = _current_client()
    server_url = next((u for u, c in _clients.items() if c is cur), None)

    try:
        count = max(1, min(100, int(data.get("count") or 1)))
    except (TypeError, ValueError):
        count = 1
    launch = bool(data.get("launch"))
    close_first = data.get("close_first", True)   # ปิด MuMu ก่อนเริ่ม (ค่าเริ่มต้น: ปิด)

    with _clone_lock:
        if _clone_state["status"] in ("downloading", "restoring", "launching"):
            send_response(req_id, {"error": "มีงาน clone ทำอยู่ รอให้เสร็จหรือกดยกเลิกก่อน"})
            return
        _clone_cancel.clear()
        _clone_state.update({"status": "downloading", "message": "กำลังเริ่มงาน...",
                             "folder": _clone_backup_dir(),
                             "filename": "", "downloaded": 0, "total": 0,
                             "done": 0, "count": count, "errors": [], "new_indexes": []})

    threading.Thread(target=_clone_worker,
                     args=(url, count, launch, source, name, server_url, bool(close_first)),
                     daemon=True).start()
    logger.info(f"  MuMu clone: เริ่มงาน — {count} จอ, launch={launch}, "
                f"source={source}{(' ' + name) if name else ''}")
    send_response(req_id, {"success": True, "started": True, "count": count})


def handle_screenshot(req_id, data):
    """จับภาพหน้าจอเครื่องนี้ ส่งกลับเป็น JPEG base64 (สำหรับ live view / PC monitor)"""
    try:
        import io
        from PIL import ImageGrab
        max_w = int(data.get("width") or 640)
        quality = int(data.get("quality") or 55)
        img = ImageGrab.grab()
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if w > max_w:
            img = img.resize((max_w, max(1, int(h * max_w / w))))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        send_response(req_id, {"success": True, "image": b64,
                               "w": img.size[0], "h": img.size[1]})
    except Exception as e:
        send_response(req_id, {"error": f"จับภาพหน้าจอไม่ได้: {e}"})


def handle_shutdown(req_id, data):
    """สั่งปิดโปรแกรม agent ที่เครื่องนี้จากระยะไกล (จาก dashboard)"""
    logger.info("🛑 ได้รับคำสั่งปิด agent จาก server — กำลังปิดโปรแกรม...")
    send_response(req_id, {"success": True, "message": "agent shutting down"})

    def _die():
        time.sleep(0.6)  # รอให้ response ถูกส่งกลับไปก่อนค่อยปิด
        _disconnect_all()
        os._exit(0)

    threading.Thread(target=_die, daemon=True).start()


# ── SELF-UPDATE (ดึง agent.py ตัวล่าสุดจาก server แล้วรีสตาร์ทตัวเอง จากปุ่มบน dashboard) ──

def _release_single_instance():
    """ปล่อย mutex กันเปิดซ้ำ เพื่อให้ agent ตัวใหม่เปิดได้หลังรีสตาร์ท"""
    global _single_instance_handle
    try:
        if _single_instance_handle:
            import ctypes
            ctypes.windll.kernel32.ReleaseMutex(_single_instance_handle)
            ctypes.windll.kernel32.CloseHandle(_single_instance_handle)
            _single_instance_handle = None
    except Exception:
        pass


def _download_new_agent():
    """โหลด agent.py ตัวล่าสุดจาก server (/agent.py) มาเขียนทับ ถ้าต่างจากเดิม
       ลองทีละ server จนกว่าจะสำเร็จ (รองรับหลาย server)
       คืน True = มีของใหม่เขียนทับแล้ว, False = เหมือนเดิม (ไม่ต้องรีสตาร์ท)
       - ตรวจไฟล์ก่อนเขียนทับ กันโหลดหน้า error / ไฟล์เพี้ยนมาทำ agent พัง"""
    import requests
    here = os.path.dirname(os.path.abspath(__file__))
    dest = os.path.join(here, "agent.py")
    last_err = None
    for url in SERVER_URLS:
        try:
            r = requests.get(url.rstrip("/") + "/agent.py", timeout=30)
            r.raise_for_status()
            code = r.content
            text = code.decode("utf-8", "ignore")
            if len(code) < 3000 or "RemoteFileManagerAgent_SingleInstance" not in text or "def main()" not in text:
                raise RuntimeError("agent.py ที่โหลดมาไม่ถูกต้อง")
            try:
                with open(dest, "rb") as f:
                    if f.read() == code:
                        return False   # เหมือนเดิม
            except Exception:
                pass
            with open(dest, "wb") as f:
                f.write(code)
            logger.info(f"⬆️ อัปเดต agent.py จาก {url} สำเร็จ ({len(code)} bytes)")
            return True
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"อัปเดตไม่สำเร็จจากทุก server: {last_err}")


def _relaunch_and_exit():
    """เปิด agent ตัวใหม่แบบ detached แล้วปิดตัวเก่า"""
    here = os.path.dirname(os.path.abspath(__file__))
    agent_py = os.path.join(here, "agent.py")
    _release_single_instance()  # ปล่อย mutex ก่อน ไม่งั้นตัวใหม่จะเด้งออก
    try:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pyw if os.path.exists(pyw) else sys.executable
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([exe, agent_py], cwd=here, close_fds=True, creationflags=flags)
        logger.info("🔄 เปิด agent ตัวใหม่แล้ว — กำลังปิดตัวเก่า")
    except Exception as e:
        logger.error(f"relaunch failed: {e}")
    _disconnect_all()
    time.sleep(0.3)
    os._exit(0)


def handle_self_update(req_id, data):
    """สั่งอัปเดต agent จากระยะไกล: ดึง agent.py ล่าสุดจาก server แล้วรีสตาร์ทตัวเอง
       - โหลดสำเร็จ → ตอบ success แล้วรีสตาร์ท
       - โหลดพัง → ตอบ error และ 'ไม่' รีสตาร์ท (คงตัวเดิมไว้ เครื่องไม่หลุด)"""
    logger.info("⬆️ ได้รับคำสั่งอัปเดต agent — กำลังดึง agent.py จาก server...")

    def _go():
        try:
            changed = _download_new_agent()
        except Exception as e:
            logger.error(f"update failed: {e}")
            send_response(req_id, {"error": f"อัปเดตไม่สำเร็จ: {e}"})
            return  # ไม่รีสตาร์ท agent เดิมยังทำงานต่อ
        if not changed:
            logger.info("✅ agent เป็นเวอร์ชันล่าสุดอยู่แล้ว (ไม่ต้องรีสตาร์ท)")
            send_response(req_id, {"success": True, "updated": False,
                                   "message": "already up to date"})
            return
        send_response(req_id, {"success": True, "updated": True,
                               "message": "updated, restarting"})
        time.sleep(0.4)  # ให้ response ส่งถึงก่อนรีสตาร์ท
        _relaunch_and_exit()

    _spawn_with_client(_go)   # พก client ที่สั่งมาไปด้วย จะได้ตอบกลับถูก server


def _startup_autoupdate():
    """ตอนเปิด agent: เช็ก agent.py ใหม่จาก server ถ้ามีของใหม่กว่า → เขียนทับ + รีสตาร์ท
       ครอบคลุมทุกวิธีเปิด (bat / vbs / scheduled task) — ปิดได้ด้วย env AGENT_NO_AUTOUPDATE=1"""
    if os.environ.get("AGENT_NO_AUTOUPDATE"):
        return
    here = os.path.dirname(os.path.abspath(__file__))
    changed = False
    try:
        import requests
        for url in SERVER_URLS:   # ลองทีละ server จนกว่าจะเจอที่ตอบได้
            try:
                r = requests.get(url.rstrip("/") + "/agent.py", timeout=8)
                if r.status_code != 200 or not r.content:
                    continue
                new = r.content
                text = new.decode("utf-8", "ignore")
                # ตรวจว่าเป็น agent.py จริง กันโหลดหน้า error/ไฟล์เพี้ยนมาทับ
                if len(new) < 3000 or "RemoteFileManagerAgent_SingleInstance" not in text or "def main()" not in text:
                    continue
                with open(os.path.join(here, "agent.py"), "rb") as f:
                    if f.read() == new:
                        return   # เหมือนเดิม ไม่ต้องทำอะไร
                with open(os.path.join(here, "agent.py"), "wb") as f:
                    f.write(new)
                logger.info(f"⬆️ พบ agent.py เวอร์ชันใหม่จาก {url} — อัปเดตแล้ว กำลังรีสตาร์ท")
                changed = True
                break
            except Exception:
                continue
    except Exception as e:
        logger.info(f"(ข้ามการเช็กอัปเดตตอนเปิด: {e})")
        return
    if changed:
        _relaunch_and_exit()


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════
def _connect_loop(url, client):
    """ลูปเชื่อมต่อ server หนึ่งเครื่อง + reconnect อัตโนมัติ
       server ที่ตายแล้ว (เช่นตัวสำรองที่เลิกใช้) จะถอยห่างขึ้นเรื่อยๆ สูงสุด 5 นาที
       ไม่งั้น log จะเต็มไปด้วยข้อความ 'เชื่อมต่อไม่ได้' ทุก 5 วิ จนอ่านของจริงไม่เจอ"""
    fails = 0
    while True:
        try:
            if fails == 0:
                logger.info(f"🔗 Connecting to {url}...")
            client.connect(url, transports=["websocket", "polling"])
            fails = 0
            client.wait()
        except socketio.exceptions.ConnectionError as e:
            fails += 1
            delay = min(300, RECONNECT_DELAY * (2 ** min(fails - 1, 6)))
            if fails <= 2 or fails % 10 == 0:      # เตือนแค่ช่วงแรกกับเป็นระยะ
                logger.warning(f"[{url}] เชื่อมต่อไม่ได้ ({fails} ครั้ง): {str(e)[:60]} "
                               f"— ลองใหม่ใน {delay}s")
            time.sleep(delay)
        except Exception as e:
            fails += 1
            logger.error(f"[{url}] ผิดพลาด: {str(e)[:80]}")
            time.sleep(min(300, RECONNECT_DELAY * (2 ** min(fails - 1, 6))))


def agent_loop():
    """เชื่อมต่อทุก server พร้อมกัน (แต่ละเครื่องมี thread เชื่อมต่อของตัวเอง)"""
    for url in SERVER_URLS:
        client = _make_client(url)
        _clients[url] = client
        _client_connected[url] = False
        threading.Thread(target=_connect_loop, args=(url, client),
                         daemon=True, name=f"conn:{url}").start()
    # คง thread นี้ไว้ (thread เชื่อมต่อจริงเป็น daemon แยกต่างหาก)
    while True:
        time.sleep(3600)


def run_tray():
    """แสดงไอคอนใน system tray + คลิกขวาเลือก Exit เพื่อปิด"""
    import pystray
    from PIL import Image, ImageDraw

    def make_icon():
        img = Image.new("RGB", (64, 64), (26, 34, 53))
        d = ImageDraw.Draw(img)
        d.rectangle([10, 18, 30, 26], fill=(59, 130, 246))   # แถบโฟลเดอร์
        d.rectangle([10, 24, 54, 50], fill=(59, 130, 246))   # ตัวโฟลเดอร์
        return img

    def on_show(icon, item):
        _ui_state["show_request"] = True   # หน้าต่างสถานะจะ deiconify เอง

    def on_exit(icon, item):
        _disconnect_all()
        icon.stop()
        os._exit(0)

    def _status_text(item):
        if _any_connected():
            return f"🟢 เชื่อมต่อ {_connected_count()}/{len(SERVER_URLS)} server"
        return "🔴 Offline"

    menu = pystray.Menu(
        pystray.MenuItem(_status_text, None, enabled=False),
        pystray.MenuItem("แสดงหน้าต่างสถานะ", on_show, default=True),
        pystray.MenuItem("Exit", on_exit),
    )
    pystray.Icon("RemoteFileAgent", make_icon(), "Remote File Agent", menu).run()


def run_status_window():
    """หน้าต่างสถานะ: โชว์ 🟢 เชื่อมต่อ/🔴 หลุด + log กิจกรรมสด ๆ (คล้าย cmd)
       ปิดหน้าต่าง = ย่อลง tray (ยังทำงานต่อเบื้องหลัง)"""
    import tkinter as tk
    from tkinter import scrolledtext

    BG, CARD, FG = "#0a0e17", "#111827", "#e8ecf4"
    root = tk.Tk()
    root.title("Remote File Agent — สถานะการทำงาน")
    root.geometry("660x420")
    root.minsize(440, 280)
    root.configure(bg=BG)

    top = tk.Frame(root, bg=BG)
    top.pack(fill="x", padx=14, pady=(14, 6))
    status_var = tk.StringVar(value="⏳ กำลังเชื่อมต่อ...")
    status_lbl = tk.Label(top, textvariable=status_var, bg=BG, fg="#f59e0b",
                          font=("Segoe UI", 14, "bold"), anchor="w")
    status_lbl.pack(fill="x")
    info = f"🖥️ {AGENT_ID or get_hostname()}    ·    🌐 {', '.join(SERVER_URLS)}"
    tk.Label(top, text=info, bg=BG, fg="#8494ad", font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(2, 0))

    txt = scrolledtext.ScrolledText(root, bg=CARD, fg=FG, insertbackground=FG,
                                    font=("Consolas", 9), relief="flat", borderwidth=0, wrap="word")
    txt.pack(fill="both", expand=True, padx=14, pady=(6, 14))
    txt.configure(state="disabled")

    def poll():
        if _any_connected():
            status_var.set(f"🟢 กำลังทำงาน — เชื่อมต่อ {_connected_count()}/{len(SERVER_URLS)} server")
            status_lbl.config(fg="#22c55e")
        else:
            status_var.set("🔴 หลุด / กำลังลองเชื่อมต่อใหม่...")
            status_lbl.config(fg="#ef4444")

        if _ui_state.get("show_request"):
            _ui_state["show_request"] = False
            try:
                root.deiconify(); root.lift(); root.focus_force()
            except Exception:
                pass

        lines = []
        while True:
            try:
                lines.append(_log_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            txt.configure(state="normal")
            txt.insert("end", "\n".join(lines) + "\n")
            total = int(txt.index("end-1c").split(".")[0])
            if total > 500:                       # เก็บแค่ ~500 บรรทัดล่าสุด
                txt.delete("1.0", f"{total - 500}.0")
            txt.see("end")
            txt.configure(state="disabled")
        root.after(400, poll)

    def on_close():
        root.withdraw()                           # ซ่อนไป tray แทนปิดจริง
        logger.info("ซ่อนหน้าต่าง (ยังทำงานเบื้องหลัง — ดับเบิลคลิกไอคอน tray เพื่อเปิดใหม่)")

    root.protocol("WM_DELETE_WINDOW", on_close)
    logger.info("📊 เปิดหน้าต่างสถานะ agent แล้ว")
    root.after(300, poll)
    root.mainloop()


def is_already_running():
    """True = มี agent อีกตัวรันอยู่แล้ว (บังคับให้เปิดได้ตัวเดียว)"""
    global _single_instance_handle
    if platform.system() != "Windows":
        return False
    try:
        import ctypes
        _single_instance_handle = ctypes.windll.kernel32.CreateMutexW(
            None, False, "RemoteFileManagerAgent_SingleInstance")
        return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return False


def main():
    if is_already_running():
        print("Agent already running - exiting this duplicate.")
        sys.exit(0)

    agent_id = AGENT_ID or AGENT_NAME or get_hostname()  # ใช้ name ถ้ามี กัน hostname ซ้ำชนกัน

    print("=" * 55)
    print("  Remote File Manager - Agent (เครื่องลูก)")
    print("=" * 55)
    print(f"  Agent ID    : {agent_id}")
    print(f"  Hostname    : {get_hostname()}")
    print(f"  OS          : {get_os_info()}")
    print(f"  IP          : {get_local_ip()}")
    if len(SERVER_URLS) == 1:
        print(f"  Server      : {SERVER_URLS[0]}")
    else:
        print(f"  Servers     : {len(SERVER_URLS)} เครื่อง")
        for i, u in enumerate(SERVER_URLS, 1):
            print(f"                {i}. {u}")
    if ALLOWED_PATHS:
        print(f"  Allowed     : {', '.join(ALLOWED_PATHS)}")
    else:
        print(f"  Allowed     : ทุกตำแหน่ง (ไม่จำกัด)")
    print("=" * 55)

    if SERVER_URLS == ["http://YOUR_SERVER_IP:5000"]:
        print("\n⚠️  กรุณาตั้งค่า server ก่อน!")
        print("   แก้ในไฟล์ config.json:")
        print('   "server_urls": ["http://server:5000", "http://nuuboy:5000"]')
        print("   หรือตั้ง environment variable:")
        print('   set SERVER_URLS=http://server:5000,http://nuuboy:5000')
        print()
        sys.exit(1)

    _startup_autoupdate()      # เช็ก agent.py ใหม่จาก server ก่อน (ถ้ามี → อัปเดต+รีสตาร์ท)
    _attach_ui_log_handler()   # ให้ log ไหลเข้าหน้าต่างสถานะ
    _run_state_load()          # งาน .bat ที่สั่งไว้ก่อนรีสตาร์ท ยังเห็นสถานะต่อได้

    # socket loop ทำงานเบื้องหลังเสมอ
    threading.Thread(target=agent_loop, daemon=True).start()
    threading.Thread(target=_peer_serve, daemon=True).start()   # แบ่งไฟล์ backup ให้เพื่อน

    has_tray = False
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        has_tray = True
    except ImportError:
        pass
    has_tk = False
    try:
        import tkinter  # noqa: F401
        has_tk = True
    except ImportError:
        pass

    # tray รันใน thread แยก, หน้าต่างสถานะรันบน main thread (tkinter ต้องอยู่ main)
    if has_tray:
        threading.Thread(target=run_tray, daemon=True).start()

    if has_tk:
        run_status_window()          # บล็อกที่นี่จนปิดโปรแกรม
    elif has_tray:
        while True:                  # ไม่มี tk แต่มี tray → คง main thread ไว้
            time.sleep(1)
    else:
        logger.info("(no tray/tk - running console only)")
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
