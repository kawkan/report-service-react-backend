# วิธี Backup Database

ระบบนี้ใช้ PostgreSQL เก็บข้อมูลผู้ใช้และข้อมูลโครงการ ดังนั้นถ้าใช้ Render PG Free ควร backup เป็นระยะ โดยเฉพาะก่อนครบ 30 วันของฐานข้อมูลฟรี

## Backup ฐานข้อมูล local ปัจจุบัน

เปิด PowerShell ที่โฟลเดอร์ backend แล้วรัน:

```powershell
.\scripts\backup_database.ps1
```

ไฟล์ backup จะถูกเก็บในโฟลเดอร์ `backups/` เป็นไฟล์ `.sql`

## Backup ฐานข้อมูล Render

นำ External Database URL จากหน้า Render Postgres มาใส่แบบนี้:

```powershell
.\scripts\backup_database.ps1 -DatabaseUrl "postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require"
```

## Restore กลับเข้า database

แนะนำให้ restore เข้า database เปล่าหรือ database ใหม่:

```powershell
.\scripts\restore_database.ps1 -BackupFile ".\backups\service_report-YYYYMMDD-HHMMSS.sql"
```

หรือ restore เข้า Render:

```powershell
.\scripts\restore_database.ps1 -BackupFile ".\backups\service_report-YYYYMMDD-HHMMSS.sql" -DatabaseUrl "postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require"
```

## ตารางที่แนะนำ

- Backup ทุกวันศุกร์
- Backup เพิ่มอีกรอบช่วงวันที่ 25-27 หลังสร้าง Render PG Free
- อย่ารอถึงวันสุดท้าย เพราะถ้าลืม ข้อมูล user/project อาจหายได้

โฟลเดอร์ `backups/` ถูก ignore จาก Git แล้ว เพื่อไม่ให้ข้อมูลจริงหลุดขึ้น GitHub
