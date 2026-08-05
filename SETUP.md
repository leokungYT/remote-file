# คู่มือติดตั้ง Remote File Manager (รองรับ 30 เครื่อง)

ระบบมี 2 ฝั่ง:
- **Server** (เครื่องหลัก 1 เครื่อง) — รัน `server.py` เปิดหน้าเว็บ dashboard + รับการเชื่อมต่อจากเครื่องลูก
- **Agent** (เครื่องลูก N เครื่อง) — รัน `agent.py` ให้เครื่องหลักสั่งงาน/ดูไฟล์/ดูจอได้

เชื่อมกันผ่าน **Tailscale VPN** (IP คงที่ 100.x.x.x) — เสถียร ไม่ต้องแก้ SERVER_URL บ่อย

---

## สิ่งที่ต้องเตรียม (ทุกเครื่อง)
1. **Windows 10/11**
2. **Python 3.11** — โหลด https://www.python.org/downloads/ ตอนติดตั้ง **ติ๊ก "Add Python to PATH"** ด้วย
   - หรือติดตั้งเร็ว ๆ ด้วย winget: `winget install Python.Python.3.11`
3. **บัญชี Tailscale** (ฟรี) — https://login.tailscale.com (รองรับ 100 เครื่อง)

---

## ส่วนที่ 1 — ตั้งค่า Tailscale (ทำก่อน)

### 1.1 สร้าง Auth Key (ทำครั้งเดียว)
1. เข้า https://login.tailscale.com/admin/settings/keys
2. กด **Generate auth key**
3. ติ๊ก **Reusable** (ใช้ซ้ำได้หลายเครื่อง) + ตั้งวันหมดอายุ (เช่น 90 วัน)
4. ก็อป key ที่ได้ (`tskey-auth-xxxxx`) เก็บไว้ — จะใช้กับทุกเครื่อง

### 1.2 ลง Tailscale ที่เครื่อง Server
1. โหลด + ติดตั้ง Tailscale จาก https://tailscale.com/download/windows
2. ล็อกอินบัญชีเดียวกัน
3. เปิด CMD พิมพ์ `tailscale ip -4` → **จดเลข IP ไว้** (เช่น `100.101.102.103`) — นี่คือที่อยู่ server ที่เครื่องลูกจะเชื่อม

---

## ส่วนที่ 2 — ตั้งค่าเครื่อง Server

### 2.1 วางไฟล์
ก็อปโฟลเดอร์โปรเจกต์ทั้งหมด (server.py, agent.py, config.json, *.bat ...) ไว้ที่เครื่อง server เช่น `C:\RemoteFileManager\`

### 2.2 ติดตั้ง dependencies
เปิด CMD ในโฟลเดอร์นั้น แล้วรัน:
```
pip install -r requirements.txt
```

### 2.3 เปิด server
ดับเบิลคลิก **`start_server.bat`**
- server รันที่พอร์ต `5000` → เปิดเว็บที่ `http://localhost:5000` (เครื่อง server เอง)
- เครื่องอื่นเข้าผ่าน `http://<tailscale-ip-ของ-server>:5000`

> 💡 server เสิร์ฟ `agent.py` ตัวล่าสุดที่ `/agent.py` ให้เครื่องลูกดึงไปอัปเดตอัตโนมัติ — เพราะฉะนั้น **แก้โค้ดที่ server ที่เดียว** เครื่องลูกได้ตัวใหม่ตอนรีสตาร์ท

---

## ส่วนที่ 3 — ติดตั้ง Agent (เครื่องลูก ทีละเครื่อง)

### 3.1 เตรียม "ชุดติดตั้ง" (ทำครั้งเดียว แล้วก็อปไปทุกเครื่อง)
สร้างโฟลเดอร์ที่มีไฟล์เหล่านี้:
- `agent.py`
- `start_agent.bat`
- `install-tailscale.bat`
- `config.json`
- `requirements.txt`
- (ถ้าจะให้เปิดตอน boot) `install-autostart.bat`, `start_agent_hidden.vbs`

**แก้ `install-tailscale.bat`** — ใส่ auth key จากข้อ 1.1:
```
set "AUTHKEY=tskey-auth-xxxxx"
```

### 3.2 ทำที่เครื่องลูกแต่ละเครื่อง
1. **ก็อปโฟลเดอร์ชุดติดตั้ง** ไปวางที่เครื่องลูก (เช่น `C:\agent\`)
2. **แก้ `config.json`** ให้ 2 ค่านี้ถูกต้อง:
   ```json
   {
     "name": "pc_1",                                  // ⚠️ ตั้งไม่ซ้ำกันทุกเครื่อง (pc_1, pc_2, ... pc_30)
     "server_url": "http://100.101.102.103:5000",     // IP tailscale ของ server (จากข้อ 1.2)
     "agent_secret": "2ec990f60382a004d664f06f99a3e7f5",
     "agent_id": "",
     "allowed_paths": [
       "Desktop/pes",
       "Desktop/cookie-run"
     ],
     "cookie_id_path": "Desktop/cookie-run/id-found"
   }
   ```
   - **`name` ต้องไม่ซ้ำ** — เพราะระบบใช้ชื่อนี้แยกเครื่อง (ถ้าซ้ำจะชนกัน)
   - `allowed_paths` / `cookie_id_path` เขียนสั้นได้ (`Desktop/...`) agent เติม `C:\Users\<ชื่อเครื่อง>\` ให้เอง → ใช้ config เดียวกันได้เกือบทุกเครื่อง แก้แค่ `name`
3. **แก้ `SERVER_URL` ใน `start_agent.bat`** ให้ตรงกับ server ด้วย:
   ```
   set SERVER_URL=http://100.101.102.103:5000
   ```
4. **รัน `install-tailscale.bat`** (คลิกขวา → Run as administrator) → เครื่องเข้า VPN อัตโนมัติ
5. **รัน `start_agent.bat`** → ลง deps + ดึง agent.py ล่าสุด + เชื่อม server (มีหน้าต่างสถานะขึ้นมา)
6. เช็กที่เว็บ dashboard → ควรเห็นเครื่องนี้ขึ้นในรายการ

---

## ส่วนที่ 4 — ตั้งให้เปิดอัตโนมัติตอน boot (แนะนำ)
ที่เครื่องลูก รัน **`install-autostart.bat`** (Run as administrator)
- จะสร้าง scheduled task เปิด agent ทุกครั้งที่ล็อกอิน (แบบซ่อน)
- เปิดเครื่อง = agent ทำงานเอง + ดึงโค้ดล่าสุดจาก server อัตโนมัติ

---

## ส่วนที่ 5 — ตรวจสอบ & ใช้งาน
1. เปิดเว็บ `http://<server-tailscale-ip>:5000`
2. แถบซ้ายเห็นเครื่องลูกครบ (เรียงตามเลข)
3. เมนูบน:
   - **Dashboard PES / Cookie-Run** — ดูสถิติ id
   - **📤 ส่งเข้า input-id** — อัปไฟล์ (ส่งเหมือนกัน / ส่งตามลำดับ) + Clear + อัปเดต agent
   - **🖥️ Live View** — ดูจอเครื่องลูกสด ๆ

---

## เทคนิคทำ 30 เครื่องให้เร็ว
- ทำ "ชุดติดตั้ง" (ข้อ 3.1) ให้เสร็จสมบูรณ์ 1 ชุด (ใส่ authkey + server_url เรียบร้อย)
- ก็อปไปทุกเครื่อง → **แก้แค่ `name` ให้ไม่ซ้ำ** → รัน 2 ไฟล์ (`install-tailscale.bat` ครั้งเดียว, `start_agent.bat`)
- ถ้าเครื่องเป็น VM โคลน: ตั้งค่าใน VM ต้นแบบให้ครบ (ยกเว้น name) แล้วค่อยโคลน จากนั้นแก้ name ทีละตัว

---

## แก้ปัญหาที่พบบ่อย (Troubleshooting)
| อาการ | สาเหตุ / วิธีแก้ |
|---|---|
| เครื่องลูกไม่ขึ้นใน dashboard | เช็ก Tailscale เชื่อมไหม (`tailscale status`), SERVER_URL ถูกไหม, agent รันอยู่ไหม (ไอคอน tray/หน้าต่างสถานะ) |
| เครื่องซ้อน/ขึ้นตัวเดียวทั้งที่มีหลายเครื่อง | `name` ใน config ซ้ำกัน → ตั้งให้ไม่ซ้ำ |
| Live View ขึ้น "Unknown action: screenshot" | agent เก่า → กดปุ่ม ⬆️ อัปเดต agent หรือรีสตาร์ท agent |
| Clear/อัปโหลด timeout | server ยังไม่รีสตาร์ทหลังอัปเดตโค้ด, หรือ agent เครื่องนั้นไม่ได้รันจริง |
| อัปโหลดหลายไฟล์แล้วบางเครื่อง "session หาย" | อัปเดต agent ให้เป็นตัวล่าสุด (มีตัวกัน race แล้ว) |

---

## หมายเหตุเรื่อง scale (30+ เครื่อง)
- Tailscale ฟรีรองรับ 100 เครื่อง — 30 สบาย
- server ปัจจุบันรันแบบ threading (dev server) — 30 เครื่องไหว แต่ถ้าจะไป 100+ ควรเปลี่ยนเป็น eventlet/gevent
- โหลด Dashboard กับเครื่องเยอะ ๆ จะช้าหน่อย (สแกนทีละเครื่อง) — ปรับให้สแกนขนานได้ถ้าต้องการ
