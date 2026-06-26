from fastapi import FastAPI, Request, HTTPException, Depends, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
import uvicorn, json, datetime, os, sys, asyncio, tempfile, hashlib, secrets, base64
import jwt
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from playwright.async_api import async_playwright
import smtplib
from email.message import EmailMessage
import urllib.request
import urllib.error
from uuid import UUID, uuid4
from ocr_service import ALLOWED_IMAGE_TYPES, scan_document_image

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

load_dotenv()

app = FastAPI(title="Service Report API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── PostgreSQL Authentication & Admin ───────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
JWT_SECRET = os.getenv("JWT_SECRET", "").strip() or secrets.token_urlsafe(48)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "0") or "0")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_NAME = os.getenv("ADMIN_NAME", "System Administrator").strip()

security = HTTPBearer()


class UserAuth(BaseModel):
    email: str
    password: str


class AdminCreateUser(BaseModel):
    email: str
    password: str = Field(min_length=8)
    full_name: str = ""
    role: str = "user"
    is_active: bool = True


class AdminUpdateUser(BaseModel):
    email: str | None = None
    password: str | None = Field(default=None, min_length=8)
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None


class ScanDocumentResponse(BaseModel):
    success: bool
    status: str
    message: str
    data: dict
    ocr_text: str = ""


def get_db_connection():
    if not DATABASE_URL:
        raise HTTPException(
            status_code=503,
            detail="ยังไม่ได้ตั้งค่า DATABASE_URL สำหรับ PostgreSQL",
        )
    try:
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=f"เชื่อมต่อ PostgreSQL ไม่สำเร็จ: {error}",
        )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=64,
    )
    return f"scrypt$16384$8$1${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, n, r, p, salt_value, digest_value = password_hash.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.b64decode(salt_value)
        expected = base64.b64decode(digest_value)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_access_token(user: dict) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "role": user["role"],
        "iat": now,
    }
    if JWT_EXPIRE_HOURS > 0:
        payload["exp"] = now + datetime.timedelta(hours=JWT_EXPIRE_HOURS)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def serialize_user(user: dict) -> dict:
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "full_name": user.get("full_name") or "",
        "role": user.get("role") or "user",
        "is_active": bool(user.get("is_active", True)),
        "created_at": user.get("created_at").isoformat() if user.get("created_at") else "",
        "updated_at": user.get("updated_at").isoformat() if user.get("updated_at") else "",
        "last_sign_in_at": (
            user.get("last_sign_in_at").isoformat()
            if user.get("last_sign_in_at")
            else ""
        ),
    }


def serialize_project(project: dict) -> dict:
    return {
        "id": str(project["id"]),
        "project_name": project["project_name"],
        "address": project.get("address") or "",
        "contact_name": project.get("contact_name") or "",
        "phone": project.get("phone") or "",
        "email": project.get("email") or "",
        "line_id": project.get("line_id") or "",
        "last_used_at": (
            project.get("last_used_at").isoformat()
            if project.get("last_used_at")
            else ""
        ),
    }


def initialize_database():
    if not DATABASE_URL:
        print("--> [Warning] DATABASE_URL is missing (PostgreSQL auth is disabled)")
        return

    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.users') AS users_table")
            if cursor.fetchone()["users_table"] is None:
                cursor.execute(
                    """
                    CREATE TABLE users (
                        id UUID PRIMARY KEY,
                        email VARCHAR(320) NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        full_name VARCHAR(200) NOT NULL DEFAULT '',
                        role VARCHAR(20) NOT NULL DEFAULT 'user'
                            CHECK (role IN ('admin', 'user')),
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        last_sign_in_at TIMESTAMPTZ
                    )
                    """
                )
                cursor.execute(
                    "CREATE INDEX idx_users_email ON users (LOWER(email))"
                )
            cursor.execute("SELECT to_regclass('public.projects') AS projects_table")
            if cursor.fetchone()["projects_table"] is None:
                cursor.execute(
                    """
                    CREATE TABLE projects (
                        id UUID PRIMARY KEY,
                        project_key VARCHAR(320) NOT NULL UNIQUE,
                        project_name VARCHAR(320) NOT NULL,
                        address TEXT NOT NULL DEFAULT '',
                        contact_name VARCHAR(200) NOT NULL DEFAULT '',
                        phone VARCHAR(30) NOT NULL DEFAULT '',
                        email VARCHAR(320) NOT NULL DEFAULT '',
                        line_id VARCHAR(200) NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    "CREATE INDEX idx_projects_last_used ON projects (last_used_at DESC)"
                )
            if ADMIN_EMAIL and ADMIN_PASSWORD:
                cursor.execute(
                    """
                    INSERT INTO users (id, email, password_hash, full_name, role, is_active)
                    VALUES (%s, %s, %s, %s, 'admin', TRUE)
                    ON CONFLICT (email) DO UPDATE SET
                        role = 'admin',
                        is_active = TRUE,
                        full_name = CASE
                            WHEN users.full_name = '' THEN EXCLUDED.full_name
                            ELSE users.full_name
                        END
                    """,
                    (
                        uuid4(),
                        ADMIN_EMAIL,
                        hash_password(ADMIN_PASSWORD),
                        ADMIN_NAME,
                    ),
                )
        connection.commit()
    print("--> [Database] PostgreSQL users table is ready")


@app.on_event("startup")
async def startup_database():
    try:
        initialize_database()
    except Exception as error:
        print(f"--> [Database Warning] {error}")


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
        )
        user_id = UUID(payload.get("sub", ""))
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="กรุณาเข้าสู่ระบบใหม่")

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, email, full_name, role, is_active, created_at,
                       updated_at, last_sign_in_at
                FROM users
                WHERE id = %s
                """,
                (user_id,),
            )
            user = cursor.fetchone()

    if not user:
        raise HTTPException(status_code=401, detail="ไม่พบบัญชีผู้ใช้")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="บัญชีนี้ถูกปิดใช้งาน")
    return user


def get_current_admin(current_user=Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="เฉพาะผู้ดูแลระบบเท่านั้น")
    return current_user


@app.post("/api/auth/signup")
async def signup():
    raise HTTPException(
        status_code=403,
        detail="การเพิ่มผู้ใช้ทำได้จากหน้า Admin เท่านั้น",
    )


@app.post("/api/auth/login")
async def login(credentials: UserAuth):
    email = credentials.email.strip().lower()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, email, password_hash, full_name, role, is_active,
                       created_at, updated_at, last_sign_in_at
                FROM users
                WHERE LOWER(email) = %s
                """,
                (email,),
            )
            user = cursor.fetchone()

            if not user or not verify_password(credentials.password, user["password_hash"]):
                raise HTTPException(status_code=401, detail="อีเมลหรือรหัสผ่านไม่ถูกต้อง")
            if not user["is_active"]:
                raise HTTPException(status_code=403, detail="บัญชีนี้ถูกปิดใช้งาน")

            cursor.execute(
                "UPDATE users SET last_sign_in_at = NOW() WHERE id = %s",
                (user["id"],),
            )
        connection.commit()

    user["last_sign_in_at"] = datetime.datetime.now(datetime.timezone.utc)
    return {
        "status": "success",
        "access_token": create_access_token(user),
        "user": serialize_user(user),
    }


@app.get("/api/auth/me")
async def get_me(current_user=Depends(get_current_user)):
    return {"status": "success", "user": serialize_user(current_user)}


@app.get("/api/projects")
async def list_projects(
    q: str = "",
    limit: int = 10,
    current_user=Depends(get_current_user),
):
    search = q.strip()
    safe_limit = max(1, min(limit, 30))
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            if search:
                pattern = f"%{search}%"
                cursor.execute(
                    """
                    SELECT id, project_name, address, contact_name, phone,
                           email, line_id, last_used_at
                    FROM projects
                    WHERE project_name ILIKE %s OR address ILIKE %s
                    ORDER BY
                        CASE WHEN project_name ILIKE %s THEN 0 ELSE 1 END,
                        last_used_at DESC
                    LIMIT %s
                    """,
                    (pattern, pattern, f"{search}%", safe_limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, project_name, address, contact_name, phone,
                           email, line_id, last_used_at
                    FROM projects
                    ORDER BY last_used_at DESC
                    LIMIT %s
                    """,
                    (safe_limit,),
                )
            projects = cursor.fetchall()
    return {
        "status": "success",
        "projects": [serialize_project(project) for project in projects],
    }


async def process_ocr_scan(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    content_type = (file.content_type or "").lower()
    filename = (file.filename or "").lower()
    is_allowed_extension = filename.endswith((".jpg", ".jpeg", ".png"))

    if content_type not in ALLOWED_IMAGE_TYPES and not is_allowed_extension:
        raise HTTPException(
            status_code=400,
            detail="รองรับเฉพาะไฟล์ jpg, jpeg, png เท่านั้น",
        )

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="ไม่พบไฟล์รูปภาพ")
    if len(image_bytes) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="ไฟล์รูปภาพต้องไม่เกิน 8MB")

    try:
        result = scan_document_image(
            image_bytes,
            filename=file.filename or "document.jpg",
            content_type=content_type,
        )
        print(f"--> [OCR] Scanned by {current_user['email']}: {file.filename}")
        return {
            "success": True,
            "status": "success",
            "message": "AI Fill Success",
            "data": result["fields"],
            "ocr_text": result["ocr_text"],
        }
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except Exception as error:
        print(f"--> [OCR] Unexpected error: {error}")
        raise HTTPException(status_code=500, detail="Cannot detect information")


@app.post("/api/ocr/scan", response_model=ScanDocumentResponse)
async def scan_document_with_ocr_space(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    return await process_ocr_scan(file, current_user)


@app.post("/api/scan-document", response_model=ScanDocumentResponse)
async def scan_document_legacy_alias(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    return await process_ocr_scan(file, current_user)


def remember_project(info: dict):
    project_name = str(info.get("projectName", "") or "").strip()
    if not project_name:
        return None

    project_key = " ".join(project_name.lower().split())
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO projects (
                    id, project_key, project_name, address, contact_name,
                    phone, email, line_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (project_key) DO UPDATE SET
                    project_name = EXCLUDED.project_name,
                    address = EXCLUDED.address,
                    contact_name = EXCLUDED.contact_name,
                    phone = EXCLUDED.phone,
                    email = EXCLUDED.email,
                    line_id = EXCLUDED.line_id,
                    updated_at = NOW(),
                    last_used_at = NOW()
                RETURNING id, project_name, address, contact_name, phone,
                          email, line_id, last_used_at
                """,
                (
                    uuid4(),
                    project_key,
                    project_name,
                    str(info.get("address", "") or "").strip(),
                    str(info.get("contactName", "") or "").strip(),
                    str(info.get("phone", "") or "").strip(),
                    str(info.get("email", "") or "").strip(),
                    str(info.get("lineId", "") or "").strip(),
                ),
            )
            project = cursor.fetchone()
        connection.commit()
    return serialize_project(project)


@app.get("/api/admin/users")
async def admin_list_users(current_admin=Depends(get_current_admin)):
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, email, full_name, role, is_active, created_at,
                       updated_at, last_sign_in_at
                FROM users
                ORDER BY created_at DESC
                """
            )
            users = cursor.fetchall()
    return {"status": "success", "users": [serialize_user(user) for user in users]}


@app.post("/api/admin/users")
async def admin_create_user(
    payload: AdminCreateUser,
    current_admin=Depends(get_current_admin),
):
    email = payload.email.strip().lower()
    role = "admin" if payload.role == "admin" else "user"
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO users
                        (id, email, password_hash, full_name, role, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, email, full_name, role, is_active, created_at,
                              updated_at, last_sign_in_at
                    """,
                    (
                        uuid4(),
                        email,
                        hash_password(payload.password),
                        payload.full_name.strip(),
                        role,
                        payload.is_active,
                    ),
                )
                user = cursor.fetchone()
            connection.commit()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="อีเมลนี้มีผู้ใช้งานแล้ว")

    return {
        "status": "success",
        "message": "เพิ่มผู้ใช้เรียบร้อยแล้ว",
        "user": serialize_user(user),
    }


@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(
    user_id: UUID,
    payload: AdminUpdateUser,
    current_admin=Depends(get_current_admin),
):
    updates = []
    values = []

    if payload.email is not None:
        updates.append("email = %s")
        values.append(payload.email.strip().lower())
    if payload.password:
        updates.append("password_hash = %s")
        values.append(hash_password(payload.password))
    if payload.full_name is not None:
        updates.append("full_name = %s")
        values.append(payload.full_name.strip())
    if payload.role is not None:
        next_role = "admin" if payload.role == "admin" else "user"
        if current_admin["id"] == user_id and next_role != "admin":
            raise HTTPException(status_code=400, detail="ไม่สามารถลดสิทธิ์บัญชีที่กำลังใช้งาน")
        updates.append("role = %s")
        values.append(next_role)
    if payload.is_active is not None:
        if current_admin["id"] == user_id and not payload.is_active:
            raise HTTPException(status_code=400, detail="ไม่สามารถปิดบัญชีที่กำลังใช้งาน")
        updates.append("is_active = %s")
        values.append(payload.is_active)

    if not updates:
        raise HTTPException(status_code=400, detail="ไม่มีข้อมูลที่ต้องแก้ไข")

    updates.append("updated_at = NOW()")
    values.append(user_id)

    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE users
                    SET {", ".join(updates)}
                    WHERE id = %s
                    RETURNING id, email, full_name, role, is_active, created_at,
                              updated_at, last_sign_in_at
                    """,
                    values,
                )
                user = cursor.fetchone()
            connection.commit()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="อีเมลนี้มีผู้ใช้งานแล้ว")

    if not user:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")
    return {
        "status": "success",
        "message": "บันทึกข้อมูลผู้ใช้เรียบร้อยแล้ว",
        "user": serialize_user(user),
    }


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(
    user_id: UUID,
    current_admin=Depends(get_current_admin),
):
    if current_admin["id"] == user_id:
        raise HTTPException(status_code=400, detail="ไม่สามารถลบบัญชีที่กำลังใช้งาน")

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE id = %s RETURNING id", (user_id,))
            deleted = cursor.fetchone()
        connection.commit()

    if not deleted:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")
    return {"status": "success", "message": "ลบผู้ใช้เรียบร้อยแล้ว"}


# ─── 1. PDF Generation (Playwright) ──────────────────────────────────────────


async def generate_pdf(html_content: str, filename: str) -> str:
    reports_dir = tempfile.gettempdir()
    pdf_path = os.path.join(reports_dir, filename)

    async with async_playwright() as p:
        browser = None
        try:
            browser = await asyncio.wait_for(p.chromium.launch(headless=True), timeout=30)
            page = await browser.new_page()

            try:
                await page.set_content(html_content, wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                print(f"--> [Warning] set_content timeout/error: {e}")

            try:
                await asyncio.wait_for(page.evaluate("document.fonts.ready"), timeout=10)
            except Exception:
                print("--> [Warning] Fonts did not load in time, proceeding anyway.")

            await asyncio.wait_for(
                page.pdf(path=pdf_path, format="A4", print_background=True),
                timeout=45,
            )
        finally:
            if browser:
                await browser.close()

    print(f"--> [PDF Generated] {pdf_path}")
    return pdf_path

# ─── 2. Email Service ────────────────────────────────────────────────────────

def send_email_with_pdf(data: dict, pdf_path: str):
    info = data.get("generalInfo", {})
    project = info.get("projectName", "—")

    # ── รับ recipients array จาก frontend ──────────────────────────
    recipients: list = data.get("recipients", [])
    if not recipients:
        fallback = info.get("email", "").strip()
        recipients = [fallback] if fallback else []

    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")

    if not recipients or not smtp_user or not smtp_pass:
        msg = f"Skipped sending (recipients={recipients}, missing SMTP config in .env)"
        print(f"--> [EMAIL] {msg}")
        return False, msg

    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 465))

    # ── อ่าน PDF ครั้งเดียว ใช้แนบทุก email ──────────────────────────
    pdf_data = None
    pdf_filename = None
    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_data = f.read()
        pdf_filename = os.path.basename(pdf_path)

    results = []

    try:
        # เปิด connection ครั้งเดียว ส่งทุกคน
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as server:
            server.login(smtp_user, smtp_pass)

            for recipient in recipients:
                try:
                    msg = EmailMessage()
                    msg["Subject"] = f"เอกสารรายงานการปฏิบัติงานเบื้องต้น — โครงการ {project}"
                    msg["From"] = smtp_user
                    msg["To"] = recipient

                    # --- เนื้อหาอีเมล (Plain Text สำรอง) ---
                    plain_text = f"""เรียนคุณ {info.get('contactName', '—')}

ขอนำส่งรายงานการปฎิบัติงานเบื้องต้นสำหรับโครงการ {project}
คุณสามารถดาวน์โหลดหรือดูรายละเอียดได้ในไฟล์ PDF ที่แนบมานี้ครับ

ขอแสดงความนับถือ,
ระบบ Service Report อัตโนมัติ

Best Regards,
Sontaya Compeetong (Boy)
Project Manager
M: +66 99245 4363
E: testtrueservice@gmail.com

Test True Company Limited (Head Office)
64/1 Moo 2, Lam Toi Ting Subdistrict, Nong Chok, Bangkok 10530
Tax Registration Number 0105566123472
W) http://www.testtrue.co.th, Line) @testtrue, FB) testtruepage
"""
                    msg.set_content(plain_text)

                    # --- เนื้อหาอีเมล (HTML เพื่อความสวยงาม) ---
                    html_content = f"""
                    <html>
                    <body style="font-family: 'Tahoma', sans-serif; line-height: 1.4; color: #222;">
                        <p>เรียนคุณ <strong>{info.get("contactName", "—")}</strong></p>
                        <p>ขอนำส่งรายงานการปฎิบัติงานเบื้องต้นสำหรับโครงการ <strong>{project}</strong></p>
                        <p>คุณสามารถดาวน์โหลดหรือดูรายละเอียดได้ในไฟล์ PDF ที่แนบมานี้ครับ</p>
                        <br>
                        <p>ขอแสดงความนับถือ,<br>ระบบ Service Report อัตโนมัติ</p>
                        
                        <div style="margin-top: 30px;">
                            <img src="https://ci3.googleusercontent.com/mail-sig/AIorK4yqmyG9vpOQHy0KZmVVm1u-m644TcEW6wuCV63aHRt8j9WROJ4sBp5ilLwmWuPDo6RsmSAGpXFAlAfw" width="144" height="144" style="display: block; margin-bottom: 5px;">
                            <p style="margin: 0; color: #2222cc; font-size: 16px;"><strong>Best Regards,</strong></p>
                            <p style="margin: 5px 0 0 0; font-size: 13px; color: #1766d4;"><strong>Sontaya Compeetong (Boy)</strong></p>
                            <p style="margin: 0; font-size: 13px; color: #1766d4;"><strong>Project Manager</strong></p>
                            <p style="margin: 5px 0 0 0; font-size: 13px; color: #1766d4;"><strong>M: +66 99245 4363</strong></p>
                            <p style="margin: 0; font-size: 13px; color: #1766d4;"><strong>E: <a href="mailto:testtrueservice@gmail.com" style="color: #1155cc; text-decoration: underline;">testtrueservice@gmail.com</a></strong></p>
                        </div>

                        <div style="text-align: center; margin-top: 30px;">
                            <img src="https://ci3.googleusercontent.com/mail-sig/AIorK4zbK_3aNMtcpYsMb8x2-ewNtJ5HzMvVS9lUsUOcsPs4s3d0b9X2QrZ2JkPUt3VlRmIymItVhZogT_ou" width="200" height="112" style="display: inline-block;">
                            <p style="margin: 10px 0 0 0; font-size: 13px; color: #0b5394;"><strong>Test True Company Limited (Head Office)</strong></p>
                            <p style="margin: 0; font-size: 13px; color: #929292;">64/1 Moo 2, Lam Toi Ting Subdistrict,</p>
                            <p style="margin: 0; font-size: 13px; color: #929292;">Nong Chok, Bangkok 10530</p>
                            <p style="margin: 0; font-size: 13px; color: #929292;">Tax Registration Number 0105566123472</p>
                            <p style="margin: 5px 0 0 0; font-size: 13px; color: #7f7f7f;">
                                W) <a href="https://www.testtrue.co.th/" style="color: #1155cc; text-decoration: none;">http://www.testtrue.co.th</a>, 
                                Line) <a href="https://lin.ee/9Qqs1nJ" style="color: #1155cc; text-decoration: none;">@testtrue</a>, 
                                FB) <a href="https://web.facebook.com/Testtrue" style="color: #1155cc; text-decoration: none;">testtruepage</a>
                            </p>
                        </div>
                        
                        <div style="margin-top: 40px; font-size: 10px;">
                            <table border="0" cellpadding="4" cellspacing="0" style="border-radius:5px;border:1px solid #cfcfcf;background-color:#ffffff;border-collapse:separate">
                              <tbody>
                                <tr>
                                  <td>
                                    <img alt="" width="40" height="40" src="https://firebasestorage.googleapis.com/v0/b/gmailtrack-main.appspot.com/o/neverDelete%2Fgmtgifv1.gif?alt=media&token=90c5a820-9b1f-4822-8e91-8580475b4dda">
                                  </td>
                                  <td>
                                    <span style="color:#a4a4a4;font-family:Arial;font-size:10px;">sender notified by</span><br>
                                    <a href="https://www.mailtrack.email/" style="color:#767676;font-weight:bold;text-decoration:none;font-family:Arial;font-size:10px;">Mail Track for Gmail</a>
                                  </td>
                                </tr>
                              </tbody>
                            </table>
                        </div>
                    </body>
                    </html>
                    """
                    msg.add_alternative(html_content, subtype='html')

                    if pdf_data:
                        msg.add_attachment(
                            pdf_data,
                            maintype="application",
                            subtype="pdf",
                            filename=pdf_filename,
                        )

                    server.send_message(msg)
                    print(f"--> [EMAIL] Sent successfully to {recipient}")
                    results.append({"to": recipient, "ok": True})

                except Exception as e:
                    print(f"--> [EMAIL] Failed to send to {recipient}: {e}")
                    results.append({"to": recipient, "ok": False, "error": str(e)})

    except Exception as e:
        err_msg = str(e)
        print(f"--> [EMAIL] SMTP connection error: {err_msg}")
        return False, f"SMTP connection error: {err_msg}"

    failed = [r for r in results if not r["ok"]]
    if not failed:
        return True, f"Email sent successfully to {len(results)} recipient(s)"
    elif len(failed) < len(results):
        return True, f"Partial success: {len(results) - len(failed)}/{len(results)} sent. Failed: {[r['to'] for r in failed]}"
    else:
        return False, f"All emails failed: {[r['error'] for r in failed]}"

# ─── 3. Google Sheet & Calendar ──────────────────────────────────────────────

def send_email_with_pdf(data: dict, pdf_path: str):
    info = data.get("generalInfo", {})
    project = info.get("projectName", "-")
    contact_name = info.get("contactName", "-")
    recipients = [
        str(recipient).strip()
        for recipient in data.get("recipients", [])
        if str(recipient).strip()
    ]
    if not recipients:
        fallback = str(info.get("email", "")).strip()
        recipients = [fallback] if fallback else []

    api_key = os.getenv("BREVO_API_KEY", "").strip()
    sender_email = os.getenv("BREVO_SENDER_EMAIL", "").strip()
    sender_name = os.getenv("BREVO_SENDER_NAME", "Service Report").strip()

    if not recipients or not api_key or not sender_email:
        message = (
            "Skipped sending "
            f"(recipients={recipients}, missing BREVO_API_KEY or BREVO_SENDER_EMAIL)"
        )
        print(f"--> [EMAIL] {message}")
        return False, message

    attachment = None
    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as pdf_file:
            attachment = {
                "content": base64.b64encode(pdf_file.read()).decode("ascii"),
                "name": os.path.basename(pdf_path),
            }

    subject = f"เอกสารรายงานการปฏิบัติงานเบื้องต้น - โครงการ {project}"
    text_content = f"""เรียนคุณ {contact_name}

ขอนำส่งรายงานการปฏิบัติงานเบื้องต้นสำหรับโครงการ {project}
รายละเอียดอยู่ในไฟล์ PDF ที่แนบมากับอีเมลนี้

ขอแสดงความนับถือ
ระบบ Service Report
"""
    html_content = f"""
    <html>
      <body style="font-family:Tahoma,Arial,sans-serif;line-height:1.6;color:#1f2937">
        <p>เรียนคุณ <strong>{contact_name}</strong></p>
        <p>
          ขอนำส่งรายงานการปฏิบัติงานเบื้องต้นสำหรับโครงการ
          <strong>{project}</strong>
        </p>
        <p>รายละเอียดอยู่ในไฟล์ PDF ที่แนบมากับอีเมลนี้</p>
        <br>
        <p>ขอแสดงความนับถือ<br>ระบบ Service Report</p>
      </body>
    </html>
    """

    results = []
    for recipient in recipients:
        payload = {
            "sender": {"name": sender_name, "email": sender_email},
            "to": [{"email": recipient}],
            "subject": subject,
            "textContent": text_content,
            "htmlContent": html_content,
        }
        if attachment:
            payload["attachment"] = [attachment]

        brevo_request = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "api-key": api_key,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(brevo_request, timeout=30) as response:
                response.read()
            print(f"--> [EMAIL] Brevo sent successfully to {recipient}")
            results.append({"to": recipient, "ok": True})
        except urllib.error.HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            error_message = f"Brevo API error {error.code}: {error_body}"
            print(f"--> [EMAIL] Failed to send to {recipient}: {error_message}")
            results.append({"to": recipient, "ok": False, "error": error_message})
        except Exception as error:
            error_message = f"Brevo connection error: {error}"
            print(f"--> [EMAIL] Failed to send to {recipient}: {error_message}")
            results.append({"to": recipient, "ok": False, "error": error_message})

    failed = [result for result in results if not result["ok"]]
    if not failed:
        return True, f"Email sent successfully to {len(results)} recipient(s)"
    if len(failed) < len(results):
        sent_count = len(results) - len(failed)
        failed_recipients = [result["to"] for result in failed]
        return (
            True,
            f"Partial success: {sent_count}/{len(results)} sent. "
            f"Failed: {failed_recipients}",
        )
    return False, f"All emails failed: {[result['error'] for result in failed]}"


def save_to_google_sheet(data: dict) -> (bool, str):
    webhook_url = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
    if not webhook_url:
        msg = "GOOGLE_SHEET_WEBHOOK_URL is missing in .env. Skipping Google Sheet save."
        print(f"--> [SHEET] {msg}")
        return False, msg
        
    info = data.get("generalInfo", {})
    
    report_datetime = info.get("reportDate", "")
    date_part = "-"
    time_part = "-"
    if "T" in report_datetime:
        parts = report_datetime.split("T")
        date_part = parts[0]
        # Get only HH:mm from time part
        time_part = parts[1][:5]
    elif report_datetime:
        date_part = report_datetime
    
    overall_status = "-"
    if "overallStatus" in data and isinstance(data["overallStatus"], dict):
        overall_status = data["overallStatus"].get("status", "-")
    elif "overallStatus" in data:
        overall_status = str(data["overallStatus"])
        
    current_year = datetime.datetime.now().year
    next_year = current_year + 1
    project_name = info.get("projectName") or "-"

    try:
        if report_datetime and "T" in report_datetime:
            report_dt = datetime.datetime.fromisoformat(report_datetime.replace("Z", ""))
        else:
            report_dt = datetime.datetime.now()
            
        next_due_dt = report_dt + datetime.timedelta(days=365)
        next_year_alert = next_due_dt.strftime('%d/%m/%Y')
        next_due_date = next_due_dt.strftime('%Y-%m-%d')
    except:
        next_year_alert = f"{datetime.datetime.now().strftime('%d/%m')}/{next_year}"
        next_due_date = f"{next_year}-{datetime.datetime.now().strftime('%m-%d')}"

    job_type = data.get("inspectionType") if data.get("inspectionType") else data.get("jobType", "-")

    # ── ใช้ email แรกใน recipients สำหรับบันทึกลง Sheet ───────────────
    recipients = data.get("recipients", [])
    sheet_email = ",\n".join(recipients) if recipients else info.get("email", "-")
    
    payload = {
        "date": date_part,
        "time": time_part,
        "projectName": project_name,
        "address": info.get("address") or "-",
        "contactName": info.get("contactName") or "-",
        "phone": info.get("phone") or "-",
        "email": sheet_email,
        "lineId": info.get("lineId") or "-",
        "jobType": job_type,
        "operatedBy": info.get("operatedBy") or "-",
        "overallStatus": overall_status,
        "nextYearAlert": next_year_alert,
        "nextDueDate": next_due_date
    }
    
    try:
        req = urllib.request.Request(webhook_url, method="POST")
        req.add_header('Content-Type', 'application/json')
        jsondata = json.dumps(payload).encode('utf-8')
        
        with urllib.request.urlopen(req, data=jsondata, timeout=10) as response:
            res_body = response.read().decode('utf-8')
            res_json = json.loads(res_body)
            if res_json.get("status") == "success":
                print("--> [SHEET] Successfully saved data to Google Sheets.")
                return True, "Success"
            else:
                msg = f"Apps Script error: {res_json.get('message', 'Unknown error')}"
                print(f"--> [SHEET] {msg}")
                return False, msg
    except Exception as e:
        err_msg = str(e)
        print(f"--> [SHEET] Failed to send: {err_msg}")
        return False, f"Network/DNS error: {err_msg}"

# ─── Endpoint ────────────────────────────────────────────────────────────────

# สังเกตตรงนี้: เพิ่ม current_user = Depends(get_current_user) เข้าไปเพื่อล็อคการใช้งาน
@app.post("/api/submit-report")
async def submit_report(request: Request, current_user = Depends(get_current_user)):
    try:
        data = await request.json()
        form_type = data.get("formType", "Form")
        html_content = data.get("htmlContent")
        
        # print ว่าใครเป็นคนเรียกใช้งาน (ดึงอีเมลคนที่ล็อกอินมาแสดง)
        print(f"\n=== Report Received: {form_type} ===")
        print(f"--> Request by Auth User: {current_user['email']}")
        
        recipients = data.get("recipients", [])
        print(f"--> Recipients: {recipients}")
        
        # ── Setup File Name ──
        info = data.get("generalInfo", {})
        remembered_project = None
        try:
            remembered_project = remember_project(info)
            if remembered_project:
                print(f"--> [PROJECT] Remembered: {remembered_project['project_name']}")
        except Exception as project_error:
            print(f"--> [PROJECT] Could not remember project: {project_error}")
        report_datetime = info.get("reportDate", "")
        date_part = report_datetime.split("T")[0] if "T" in report_datetime else (report_datetime or datetime.datetime.now().strftime('%Y-%m-%d'))
        
        job_type = data.get("inspectionType") if data.get("inspectionType") else data.get("jobType", form_type)
        project_name = info.get("projectName", "Untitled")

        safe_job_type = str(job_type).replace("/", "-").replace("\\", "-").strip()
        safe_project_name = str(project_name).replace("/", "-").replace("\\", "-").strip()
        safe_date = str(date_part).replace("/", "-")
        
        pdf_path = None
        if html_content:
            filename = f"Report_{safe_job_type}_{safe_project_name}_{safe_date}.pdf".replace(" ", "_")
            print(f"--> Generating PDF via Playwright as: {filename}")
            pdf_path = await generate_pdf(html_content, filename)
            
        print(f"--> Sending Email to {len(recipients)} recipient(s)...")
        email_ok, email_msg = send_email_with_pdf(data, pdf_path)
        
        # 🧹 ลบไฟล์ PDF ทิ้งหลังส่งเสร็จ
        if pdf_path and os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
                print("--> [CLEANUP] Deleted temporary PDF file.")
            except Exception as e:
                print(f"--> [Warning] Could not delete temp PDF: {e}")
        
        print("--> Saving to Google Sheets...")
        sheet_ok, sheet_msg = save_to_google_sheet(data)
        
        errors = []
        if not email_ok:
            errors.append(email_msg)
        if not sheet_ok:
            errors.append(sheet_msg)
            
        msg = "Report processed."
        if errors:
            msg += " Some tasks failed (Email/Sheet)."
            
        return {
            "status": "success",
            "message": msg,
            "pdf_saved": os.path.basename(pdf_path) if pdf_path else None,
            "details": {
                "email": {"ok": email_ok, "msg": email_msg},
                "sheet": {"ok": sheet_ok, "msg": sheet_msg},
                "project": {
                    "ok": remembered_project is not None,
                    "data": remembered_project,
                },
            },
            "errors": errors,
        }
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
