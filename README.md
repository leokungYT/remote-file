# 📁 Remote File Manager
# ระบบดูและจัดการไฟล์จากเครื่องลูกผ่านเครื่องหลัก

## สถาปัตยกรรม

```
เครื่องลูก A ──┐                    
เครื่องลูก B ──┼── Internet ──► เครื่องหลัก (Server + Web UI)
เครื่องลูก C ──┘                 http://IP:5000
```

---

## ⚡ วิธีติดตั้ง (ทำครั้งเดียว)

### ทุกเครื่อง (ทั้งหลักและลูก)

```bash
pip install -r requirements.txt
```

---

## 🖥️ เครื่องหลัก (Server)

### 1. ตั้งค่า (ไม่บังคับ แต่แนะนำ)

เปิดไฟล์ `server.py` แก้ค่าเหล่านี้:

```python
SECRET_KEY = "เปลี่ยนเป็น-key-ที่ยาวและซับซ้อน"
AGENT_SECRET = "รหัสลับ-ต้องตรงกับเครื่องลูก"
SERVER_PORT = 5000
```

หรือตั้งผ่าน Environment Variable:

```cmd
set SECRET_KEY=my-super-secret
set AGENT_SECRET=office-secret-2024
set SERVER_PORT=5000
```

### 2. รัน Server

```cmd
python server.py
```

### 3. เปิดเว็บเบราว์เซอร์

```
http://localhost:5000
```

### 4. เปิดพอร์ต Firewall (สำคัญ!)

```cmd
netsh advfirewall firewall add rule name="FileManager" dir=in action=allow protocol=TCP localport=5000
```

---

## 📡 เครื่องลูก (Agent)

### 1. ตั้งค่า

เปิดไฟล์ `agent.py` แก้ค่า `SERVER_URL`:

```python
SERVER_URL = "http://IP-เครื่องหลัก:5000"
AGENT_SECRET = "รหัสลับ-ต้องตรงกับ-server"
AGENT_ID = ""  # ปล่อยว่าง = ใช้ชื่อเครื่อง
```

หรือตั้งผ่าน Environment Variable:

```cmd
set SERVER_URL=http://192.168.1.100:5000
set AGENT_SECRET=office-secret-2024
set AGENT_ID=สำนักงาน-กทม
python agent.py
```

### 2. จำกัดโฟลเดอร์ที่เข้าถึงได้ (แนะนำ)

```cmd
set ALLOWED_PATHS=C:\Users\myuser\Documents;D:\SharedData
python agent.py
```

### 3. รัน Agent

```cmd
python agent.py
```

---

## 🔧 ฟีเจอร์ทั้งหมด

| ฟีเจอร์ | คำอธิบาย |
|---------|----------|
| 📂 ดูไฟล์ | เรียกดูไฟล์/โฟลเดอร์ของเครื่องลูกทุกเครื่อง |
| 💾 ดาวน์โหลด | ดึงไฟล์จากเครื่องลูกมาเครื่องหลัก |
| 📤 อัปโหลด | ส่งไฟล์จากเครื่องหลักไปเครื่องลูก |
| ✏️ เปลี่ยนชื่อ | เปลี่ยนชื่อไฟล์/โฟลเดอร์ในเครื่องลูก |
| 🗑️ ลบ | ลบไฟล์/โฟลเดอร์ในเครื่องลูก |
| 📦 ย้าย | ย้ายไฟล์ภายในเครื่องลูก |
| 🟢 สถานะ | เห็นว่าเครื่องลูกออนไลน์/ออฟไลน์ |
| 🔄 Reconnect | เครื่องลูกเชื่อมต่อใหม่อัตโนมัติเมื่อหลุด |

---

## 🌐 การใช้งานผ่าน Internet (คนละที่)

ถ้าเครื่องหลักกับเครื่องลูกอยู่คนละเครือข่าย มีตัวเลือก:

### ตัวเลือก 1: Port Forwarding (ง่ายสุด)
1. เข้า Router ของเครื่องหลัก
2. Forward port 5000 ไปที่ IP เครื่องหลัก
3. เครื่องลูกเชื่อมด้วย Public IP ของ Router

### ตัวเลือก 2: Ngrok (ฟรี, ไม่ต้องตั้ง router)
```cmd
:: ติดตั้ง ngrok จาก https://ngrok.com
ngrok http 5000
```
จะได้ URL เช่น `https://abc123.ngrok.io` ให้เครื่องลูกเชื่อม URL นี้

### ตัวเลือก 3: Tailscale / ZeroTier (แนะนำ)
- ติดตั้ง Tailscale ทุกเครื่อง
- ทุกเครื่องจะอยู่ในเครือข่ายเดียวกันอัตโนมัติ
- ปลอดภัย ฟรี ไม่ต้อง port forward

---

## 🔒 ความปลอดภัย

1. **เปลี่ยน AGENT_SECRET** - ค่าเริ่มต้นไม่ปลอดภัย
2. **จำกัด ALLOWED_PATHS** - อย่าเปิดให้เข้าถึงทุกโฟลเดอร์
3. **ใช้ HTTPS** - ถ้าผ่าน Internet ควรใช้ ngrok หรือ reverse proxy ที่มี SSL
4. **Firewall** - เปิดเฉพาะพอร์ตที่จำเป็น

---

## 🐛 แก้ปัญหา

| ปัญหา | วิธีแก้ |
|-------|--------|
| Agent เชื่อมไม่ได้ | ตรวจ SERVER_URL, เช็ค firewall, เช็ค AGENT_SECRET |
| เปิดเว็บไม่ได้ | ตรวจว่า server.py รันอยู่ ลอง http://localhost:5000 |
| ดูไฟล์ไม่ได้ | ตรวจสิทธิ์ ALLOWED_PATHS |
| ส่งไฟล์ช้า | ปกติสำหรับไฟล์ใหญ่ผ่าน Internet |

---

## 📂 โครงสร้างโปรเจค

```
file-manager/
├── server.py          ← รันที่เครื่องหลัก
├── agent.py           ← รันที่เครื่องลูกแต่ละเครื่อง
├── requirements.txt   ← dependencies
└── README.md          ← ไฟล์นี้
```
