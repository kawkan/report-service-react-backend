# Render Deploy Checklist

## 1. Backend Web Service

Deploy this folder as a Render Web Service using Docker.

Required environment variables:

```env
DATABASE_URL=<Render PostgreSQL External/Internal Database URL>
JWT_SECRET=<long random secret>
JWT_EXPIRE_HOURS=0
BREVO_API_KEY=<Brevo API key>
BREVO_SENDER_EMAIL=<verified sender email>
BREVO_SENDER_NAME=Service Report
GOOGLE_SHEET_WEBHOOK_URL=<Google Apps Script Web App URL>
OCR_SPACE_API_KEY=<OCR.Space API key>
ADMIN_EMAIL=<first admin email>
ADMIN_PASSWORD=<first admin password>
ADMIN_NAME=System Administrator
```

Notes:

- `JWT_EXPIRE_HOURS=0` means users stay signed in until they log out, the token is deleted, user is disabled, or `JWT_SECRET` changes.
- Do not paste `.env` into GitHub. Set the values in Render Environment Variables.
- If using Render PostgreSQL Free, back up before the database free period ends.

## 2. Health check

After deploy, open:

```text
https://<backend-service>.onrender.com/docs
```

If Swagger docs opens, backend is running.

## 3. Frontend

Set the frontend variable:

```env
VITE_BACKEND_URL=https://<backend-service>.onrender.com
```

Then deploy the frontend as a Render Static Site.
