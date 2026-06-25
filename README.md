# Service Report API — Backend

> **ระบบ Backend สำหรับสร้าง PDF รายงานการปฏิบัติงาน ส่งอีเมลอัตโนมัติ และบันทึกข้อมูลลง Google Sheets**

---

## ภาพรวมระบบ

Backend นี้ทำหน้าที่เป็นศูนย์กลางประมวลผลของระบบ Service Report โดยรับข้อมูลจาก Frontend (React) แล้วดำเนินการ 3 อย่างพร้อมกัน:

1. **สร้างไฟล์ PDF** จาก HTML ที่ส่งมาจาก Frontend โดยใช้ Playwright (Headless Chromium) รองรับฟอนต์ภาษาไทยเต็มรูปแบบ
2. **ส่งอีเมลพร้อมแนบ PDF** ไปยังผู้รับหลายคนพร้อมกัน ผ่าน SMTP (Gmail)
3. **บันทึกข้อมูลลง Google Sheets** ผ่าน Google Apps Script Webhook

ระบบมี Authentication ด้วย **Supabase** ทุก Endpoint ที่สำคัญต้องมี Access Token ที่ถูกต้องจึงจะใช้งานได้

---

## Tech Stack

| ส่วนประกอบ | เทคโนโลยี |
|---|---|
| Framework | FastAPI |
| Runtime | Python 3.11 |
| ASGI Server | Uvicorn |
| PDF Generation | Playwright (Headless Chromium) |
| Authentication | Supabase Auth (JWT Bearer Token) |
| Email | smtplib + SMTP_SSL (Gmail) |
| Google Sheets | Google Apps Script Webhook (HTTP POST) |
| Config | python-dotenv |
| Containerization | Docker |

### Python Libraries หลัก (`requirements.txt`)

| Library | หน้าที่ |
|---|---|
| `fastapi` | Web framework หลัก สร้าง REST API |
| `uvicorn` | ASGI server สำหรับรัน FastAPI |
| `pydantic` | Data validation และ Schema definition |
| `playwright` | Headless browser สำหรับ render HTML → PDF |
| `python-dotenv` | โหลดค่า Environment Variables จากไฟล์ `.env` |
| `python-multipart` | รองรับ multipart form data |
| `supabase` | Supabase Python client สำหรับ Authentication |

---

## โครงสร้างโปรเจกต์

```
report-service-react-backend/
│
├── main.py               # โค้ดหลักทั้งหมด — API endpoints, PDF, Email, Sheet
├── requirements.txt      # รายชื่อ Python library ที่ต้องติดตั้ง
├── Dockerfile            # คำสั่งสร้าง Docker Image (ใช้ Deploy)
├── .env                  # Environment Variables (ไม่ commit ขึ้น Git)
├── .gitignore            # ไฟล์ที่ไม่ต้องการให้ Git ติดตาม
├── .dockerignore         # ไฟล์ที่ไม่ต้องการให้ copy เข้า Docker image
└── reports/              # โฟลเดอร์เก็บตัวอย่างไฟล์ PDF (sample output)
    ├── Dockerfile        # (ตัวอย่าง — ไม่ได้ใช้ใน production)
    ├── ServiceReport_form1_20260329212511.pdf
    └── ServiceReport_form2_20260329212848.pdf
```

### คำอธิบายไฟล์สำคัญ

- **`main.py`** — ไฟล์เดียวที่มีโค้ดทั้งหมด ประกอบด้วย:
  - การตั้งค่า FastAPI app และ CORS middleware
  - การเชื่อมต่อ Supabase และ middleware ตรวจสอบ Token
  - Auth endpoints (signup, login, me)
  - ฟังก์ชัน `generate_pdf()` — สร้าง PDF ด้วย Playwright
  - ฟังก์ชัน `send_email_with_pdf()` — ส่งอีเมลผ่าน SMTP SSL
  - ฟังก์ชัน `save_to_google_sheet()` — ส่งข้อมูลไป Google Sheets ผ่าน Webhook
  - Main endpoint `POST /api/submit-report` — รวมทุกอย่างไว้ด้วยกัน

- **`Dockerfile`** — ใช้ `python:3.11-slim` เป็น base, ติดตั้ง Thai fonts, Playwright + Chromium และ Python dependencies

---

## API Endpoints

### Authentication

#### `POST /api/auth/signup`
สมัครสมาชิกใหม่

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "your_password"
}
```

**Response:**
```json
{
  "status": "success",
  "message": "สมัครสมาชิกสำเร็จ"
}
```

---

#### `POST /api/auth/login`
ล็อกอินเพื่อรับ Access Token

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "your_password"
}
```

**Response:**
```json
{
  "status": "success",
  "access_token": "<JWT_TOKEN>",
  "user": {
    "email": "user@example.com"
  }
}
```

---

#### `GET /api/auth/me`
ตรวจสอบ session ปัจจุบัน (ต้องส่ง Token)

**Headers:**
```
Authorization: Bearer <JWT_TOKEN>
```

**Response:**
```json
{
  "status": "success",
  "user": {
    "email": "user@example.com"
  }
}
```

---

### Report

#### `POST /api/submit-report` 🔒
ส่งรายงาน — สร้าง PDF, ส่งอีเมล, บันทึก Google Sheets

> **ต้องมี Authorization Header** — Bearer Token จาก `/api/auth/login`

**Headers:**
```
Authorization: Bearer <JWT_TOKEN>
Content-Type: application/json
```

**Request Body:**
```json
{
  "formType": "Form1",
  "htmlContent": "<html>...</html>",
  "recipients": ["client@example.com", "manager@example.com"],
  "inspectionType": "PM",
  "generalInfo": {
    "reportDate": "2026-03-29T21:25:00",
    "projectName": "โครงการ ABC",
    "address": "123 ถ.สุขุมวิท กรุงเทพ",
    "contactName": "คุณสมชาย",
    "phone": "081-234-5678",
    "email": "client@example.com",
    "lineId": "@abc",
    "operatedBy": "ช่างสมศักดิ์"
  },
  "overallStatus": {
    "status": "ปกติ"
  }
}
```

**Response (Success):**
```json
{
  "status": "success",
  "message": "Report processed.",
  "pdf_saved": "Report_PM_โครงการ_ABC_2026-03-29.pdf",
  "details": {
    "email": { "ok": true, "msg": "Email sent successfully to 2 recipient(s)" },
    "sheet": { "ok": true, "msg": "Success" }
  },
  "errors": []
}
```

**Response (Partial Failure):**
```json
{
  "status": "success",
  "message": "Report processed. Some tasks failed (Email/Sheet).",
  "pdf_saved": "Report_PM_โครงการ_ABC_2026-03-29.pdf",
  "details": {
    "email": { "ok": false, "msg": "SMTP connection error: ..." },
    "sheet": { "ok": true, "msg": "Success" }
  },
  "errors": ["SMTP connection error: ..."]
}
```

> **หมายเหตุ:** ไฟล์ PDF เป็นชั่วคราว — ระบบจะลบออกจาก server โดยอัตโนมัติหลังส่งอีเมลเสร็จ

---

## Environment Variables

สร้างไฟล์ `.env` ที่ root ของโปรเจกต์ โดยอ้างอิงจากตัวแปรต่อไปนี้:

```dotenv
# ── Supabase Authentication ──────────────────────────
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_KEY=your_supabase_anon_key

# ── Brevo Transactional Email API ────────────────────
BREVO_API_KEY=your_brevo_api_key
BREVO_SENDER_EMAIL=your_verified_sender@example.com
BREVO_SENDER_NAME=Service Report

# ── Google Sheets (Apps Script Webhook) ──────────────
GOOGLE_SHEET_WEBHOOK_URL=https://script.google.com/macros/s/YOUR_SCRIPT_ID/exec
```

### วิธีตั้งค่า Brevo
1. สร้าง API key ที่เมนู **SMTP & API → API Keys**
2. เพิ่มและยืนยันอีเมลผู้ส่งที่เมนู **Senders & IP**
3. นำ API key และอีเมลผู้ส่งไปตั้งค่าใน Render

---

## วิธีติดตั้งและรันในเครื่อง (Local Development)

### ข้อกำหนดเบื้องต้น
- Python 3.11+
- pip

### ขั้นตอน

```bash
# 1. Clone โปรเจกต์
git clone <repository_url>
cd report-service-react-backend

# 2. สร้าง Virtual Environment
python -m venv venv

# 3. Activate Virtual Environment
# Windows (cmd):
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate

# 4. ติดตั้ง dependencies
pip install -r requirements.txt

# 5. ติดตั้ง Playwright browser (Chromium)
playwright install chromium

# 6. สร้างไฟล์ .env และกรอกค่าที่จำเป็น (ดูหัวข้อ Environment Variables)
copy .env.example .env   # หรือสร้างไฟล์ .env ใหม่เอง

# 7. รัน server
python main.py
```

Server จะรันที่ `http://localhost:8000`

ดู API documentation แบบ interactive ได้ที่:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## วิธี Deploy บน AWS EC2

### ขั้นที่ 1: เตรียม EC2 Instance

1. เข้า AWS Console → EC2 → Launch Instance
2. เลือก **Ubuntu Server 22.04 LTS** (แนะนำ)
3. Instance type: `t3.small` ขึ้นไป (Playwright ต้องการ RAM พอสมควร)
4. Security Group — เปิด Inbound rules:
   - `SSH` port 22 (จาก IP ตัวเอง)
   - `Custom TCP` port 8000 (จาก Anywhere หรือเฉพาะ Frontend server)
5. สร้างหรือเลือก Key Pair (.pem) แล้ว Download ไว้

### ขั้นที่ 2: เชื่อมต่อ EC2

```bash
chmod 400 your-key.pem
ssh -i your-key.pem ubuntu@<EC2_PUBLIC_IP>
```

### ขั้นที่ 3: ติดตั้ง Docker บน EC2

```bash
# อัปเดต package list
sudo apt-get update

# ติดตั้ง Docker
sudo apt-get install -y docker.io

# เริ่ม Docker service
sudo systemctl start docker
sudo systemctl enable docker

# ให้ user "ubuntu" ใช้ Docker ได้โดยไม่ต้อง sudo
sudo usermod -aG docker ubuntu

# Logout แล้ว SSH กลับเข้ามาใหม่เพื่อให้ group มีผล
exit
```

### ขั้นที่ 4: โอนไฟล์โปรเจกต์ขึ้น EC2

**วิธีที่ 1 — ใช้ Git (แนะนำ):**
```bash
git clone <repository_url>
cd report-service-react-backend
```

**วิธีที่ 2 — ใช้ scp:**
```bash
# รันบนเครื่อง local
scp -i your-key.pem -r ./report-service-react-backend ubuntu@<EC2_PUBLIC_IP>:~/
```

### ขั้นที่ 5: สร้างไฟล์ .env บน Server

```bash
cd report-service-react-backend
nano .env
```

วางค่าต่อไปนี้ลงในไฟล์ แล้วแก้ไขให้ตรงกับของจริง:

```dotenv
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_KEY=your_supabase_anon_key
BREVO_API_KEY=your_brevo_api_key
BREVO_SENDER_EMAIL=your_verified_sender@example.com
BREVO_SENDER_NAME=Service Report
GOOGLE_SHEET_WEBHOOK_URL=https://script.google.com/macros/s/YOUR_SCRIPT_ID/exec
```

บันทึกไฟล์: `Ctrl+O` → Enter → `Ctrl+X`

### ขั้นที่ 6: Build Docker Image

```bash
docker build -t service-report-api .
```

> ขั้นตอนนี้ใช้เวลาประมาณ 3–5 นาที (โหลด Chromium สักครู่)

### ขั้นที่ 7: รัน Docker Container

```bash
docker run -d \
  --name service-report-api \
  --env-file .env \
  -p 8000:8000 \
  --restart unless-stopped \
  service-report-api
```

ตรวจสอบว่า container รันอยู่:

```bash
docker ps
docker logs service-report-api
```

### ขั้นที่ 8: ทดสอบ API

```bash
curl http://<EC2_PUBLIC_IP>:8000/docs
```

หรือเปิด browser ไปที่ `http://<EC2_PUBLIC_IP>:8000/docs`

---

## คำสั่ง Docker ที่ใช้บ่อย

```bash
# ดู log แบบ realtime
docker logs -f service-report-api

# หยุด container
docker stop service-report-api

# ลบ container
docker rm service-report-api

# Deploy version ใหม่ (build + restart)
docker build -t service-report-api . && \
docker stop service-report-api && \
docker rm service-report-api && \
docker run -d --name service-report-api --env-file .env -p 8000:8000 --restart unless-stopped service-report-api
```

---

## หมายเหตุด้านความปลอดภัย

- ไฟล์ `.env` ต้องไม่ถูก commit ขึ้น Git เด็ดขาด (มี `.gitignore` ป้องกันไว้แล้ว)
- ทุก request ไปยัง `/api/submit-report` ต้องผ่าน Supabase JWT Authentication
- ควรใช้ HTTPS (ติดตั้ง Nginx + SSL Certificate) ใน production environment

---

*พัฒนาโดย Test True Company Limited*
