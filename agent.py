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
import sys
import json
import time
import base64
import socket
import shutil
import platform
import logging
from pathlib import Path
from datetime import datetime

import socketio

# ─── CONFIG ───────────────────────────────────────────────
# แก้ค่าเหล่านี้ให้ตรงกับระบบของคุณ
SERVER_URL = os.environ.get("SERVER_URL", "http://YOUR_SERVER_IP:5000")
AGENT_SECRET = os.environ.get("AGENT_SECRET", "my-agent-secret-2024")
AGENT_ID = os.environ.get("AGENT_ID", "")  # ปล่อยว่างจะใช้ชื่อเครื่อง
ALLOWED_PATHS = os.environ.get("ALLOWED_PATHS", "").split(";")  # เช่น "C:\Users;D:\Data"
CHUNK_SIZE = 512 * 1024  # 512KB per chunk
RECONNECT_DELAY = 5  # seconds

# ถ้าไม่กำหนด allowed paths จะเข้าถึงได้ทุก drive
if ALLOWED_PATHS == ['']:
    ALLOWED_PATHS = []

# ─── LOGGING ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ─── SOCKET.IO CLIENT ────────────────────────────────────
sio = socketio.Client(
    reconnection=True,
    reconnection_delay=RECONNECT_DELAY,
    reconnection_delay_max=30,
    logger=False,
)

# เก็บสถานะการอัปโหลดแบบแบ่ง chunk (req_id -> {file, path, received})
upload_sessions = {}


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

@sio.event
def connect():
    logger.info("✅ Connected to server!")
    agent_id = AGENT_ID if AGENT_ID else get_hostname()
    sio.emit("agent_register", {
        "secret": AGENT_SECRET,
        "agent_id": agent_id,
        "hostname": get_hostname(),
        "os_info": get_os_info(),
        "ip": get_local_ip(),
        "allowed_paths": ALLOWED_PATHS,
    })


@sio.event
def disconnect():
    logger.warning("❌ Disconnected from server")


@sio.on("registered")
def on_registered(data):
    logger.info(f"📋 Registered as: {data.get('agent_id')}")


@sio.on("auth_failed")
def on_auth_failed(data):
    logger.error(f"🔒 Authentication failed: {data.get('message')}")
    logger.error("ตรวจสอบ AGENT_SECRET ว่าตรงกับ server หรือไม่")


@sio.on("command")
def on_command(data):
    """รับคำสั่งจาก server"""
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
        elif action == "rename_file":
            handle_rename(req_id, payload)
        elif action == "move_file":
            handle_move(req_id, payload)
        else:
            send_response(req_id, {"error": f"Unknown action: {action}"})
    except Exception as e:
        logger.error(f"Error handling {action}: {e}")
        send_response(req_id, {"error": str(e)})


def send_response(req_id, data):
    data["request_id"] = req_id
    sio.emit("agent_response", data)


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

    try:
        file_size = os.path.getsize(path)
        logger.info(f"  Sending file: {path} ({file_size} bytes)")

        with open(path, "rb") as f:
            chunk_index = 0
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                is_last = f.tell() >= file_size

                sio.emit("file_chunk", {
                    "request_id": req_id,
                    "data": base64.b64encode(chunk).decode("ascii"),
                    "chunk_index": chunk_index,
                    "is_last": is_last,
                    "total_size": file_size,
                })
                chunk_index += 1
                time.sleep(0.01)  # ป้องกัน overwhelm

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

    # ถ้า dest_path เป็นแค่ชื่อไฟล์ ให้วางที่ Desktop
    if not os.path.dirname(dest_path):
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        base = desktop if os.path.exists(desktop) else os.path.expanduser("~")
        dest_path = os.path.join(base, filename)

    dest_dir = os.path.dirname(dest_path)
    if not is_path_allowed(dest_dir):
        send_response(req_id, {"error": f"ไม่มีสิทธิ์เขียนที่: {dest_dir}"})
        return

    try:
        os.makedirs(dest_dir, exist_ok=True)
        f = open(dest_path, "wb")
        upload_sessions[req_id] = {"file": f, "path": dest_path, "received": 0}
        logger.info(f"  Upload start: {dest_path}")
        # ยังไม่ตอบกลับ รอ chunk สุดท้าย
    except Exception as e:
        send_response(req_id, {"error": str(e)})


def handle_upload_chunk(req_id, data):
    """รับ chunk เขียนต่อท้ายไฟล์ และแตก zip อัตโนมัติเมื่อรับครบ"""
    sess = upload_sessions.get(req_id)
    if not sess:
        send_response(req_id, {"error": "ไม่พบ session อัปโหลด (upload_start หาย)"})
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

            # ── แตกไฟล์ zip อัตโนมัติ ──
            if path.lower().endswith(".zip"):
                import zipfile
                extract_dir = path[:-4]  # ตัด .zip ออก → โฟลเดอร์ปลายทาง
                try:
                    with zipfile.ZipFile(path, "r") as zf:
                        zf.extractall(extract_dir)
                    logger.info(f"  Auto-extracted zip -> {extract_dir}")
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


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════
def main():
    agent_id = AGENT_ID if AGENT_ID else get_hostname()

    print("=" * 55)
    print("  Remote File Manager - Agent (เครื่องลูก)")
    print("=" * 55)
    print(f"  Agent ID    : {agent_id}")
    print(f"  Hostname    : {get_hostname()}")
    print(f"  OS          : {get_os_info()}")
    print(f"  IP          : {get_local_ip()}")
    print(f"  Server      : {SERVER_URL}")
    if ALLOWED_PATHS:
        print(f"  Allowed     : {', '.join(ALLOWED_PATHS)}")
    else:
        print(f"  Allowed     : ทุกตำแหน่ง (ไม่จำกัด)")
    print("=" * 55)

    if SERVER_URL == "http://YOUR_SERVER_IP:5000":
        print("\n⚠️  กรุณาตั้งค่า SERVER_URL ก่อน!")
        print("   แก้ในไฟล์นี้ หรือตั้ง environment variable:")
        print('   set SERVER_URL=http://192.168.1.100:5000')
        print()
        sys.exit(1)

    while True:
        try:
            logger.info(f"🔗 Connecting to {SERVER_URL}...")
            sio.connect(SERVER_URL, transports=["websocket", "polling"])
            sio.wait()
        except socketio.exceptions.ConnectionError as e:
            logger.warning(f"Connection failed: {e}")
            logger.info(f"Retrying in {RECONNECT_DELAY}s...")
            time.sleep(RECONNECT_DELAY)
        except KeyboardInterrupt:
            logger.info("Agent stopped by user")
            if sio.connected:
                sio.disconnect()
            break
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    main()
