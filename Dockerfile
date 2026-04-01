# เลือก Base Image ของ Python ชนิดขนาดเล็ก
FROM python:3.11-slim

# กำหนดโฟลเดอร์ทำงานใน Server
WORKDIR /app

# ติดตั้ง Font ภาษาไทย และโปรแกรมพื้นฐานสำหรับเปิดเว็บ
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    fonts-thai-tlwg \
    && rm -rf /var/lib/apt/lists/*

# ก๊อปปี้ไฟล์รายชื่อไลบรารี่และสั่งติดตั้ง
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# สั่งให้ Playwright โหลดโปรแกรม Chromium ลงใน Server
RUN playwright install chromium
RUN playwright install-deps chromium

# ก๊อปไฟล์โค้ดทั้งหมด (main.py ฯลฯ) ลงไปใน Server
COPY . .

# เปิดพอร์ตใช้งาน
EXPOSE 8000

# คำสั่งรัน Server เมื่อ Deploy เสร็จสิ้น
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
