# OCR.Space Auto Fill

ฟีเจอร์นี้รับไฟล์รูปภาพ `jpg`, `jpeg`, `png` จากหน้าบ้าน แล้วทำงานตามลำดับ:

1. ผู้ใช้ถ่ายรูปหรืออัปโหลดรูป
2. Backend ส่งไฟล์ไป OCR.Space API
3. OCR.Space ส่งข้อความ OCR กลับมา
4. Backend วิเคราะห์ข้อความด้วย regex/rule-based parser โดยไม่ใช้ AI และไม่ใช้ mock data
5. Frontend นำ JSON ที่ได้ไป Auto Fill Form

## Environment Variables

ต้องใส่ค่าใน `.env` ของ backend:

```env
OCR_SPACE_API_KEY=your_ocr_space_api_key
```

## API

```http
POST /api/ocr/scan
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

field:

```txt
file=<jpg|jpeg|png>
```

## Response

```json
{
  "success": true,
  "data": {
    "project_name": "",
    "address": "",
    "contact": "",
    "mobile": "",
    "email": "",
    "line": ""
  }
}
```

Frontend จะ map เป็น:

- `project_name` → Project Name
- `address` → Address
- `contact` → Contact Name
- `mobile` → Mobile
- `email` → Email
- `line` → ID Line

ถ้าระบบอ่านข้อมูลไม่ได้ จะตอบ error:

```text
Cannot detect information
```
