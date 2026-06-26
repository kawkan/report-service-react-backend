import json
import mimetypes
import os
import re
import uuid
import urllib.error
import urllib.request


OCR_SPACE_ENDPOINT = "https://api.ocr.space/parse/image"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png"}


def _clean_value(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" :-–—\t\r\n")


def _build_multipart_form(
    fields: dict[str, str],
    file_field: str,
    filename: str,
    content_type: str,
    file_bytes: bytes,
) -> tuple[bytes, str]:
    boundary = f"----ServiceReportOCR{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )

    safe_filename = filename or "document.jpg"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{safe_filename}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n".encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(chunks), boundary


def scan_text_with_ocr_space(
    image_bytes: bytes,
    filename: str = "document.jpg",
    content_type: str = "",
) -> str:
    api_key = os.getenv("OCR_SPACE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OCR_SPACE_API_KEY is missing")

    guessed_type = content_type or mimetypes.guess_type(filename)[0] or "image/jpeg"
    body, boundary = _build_multipart_form(
        fields={
            "language": "tha",
            "isOverlayRequired": "false",
            "scale": "true",
            "detectOrientation": "true",
            "OCREngine": "2",
        },
        file_field="file",
        filename=filename,
        content_type=guessed_type,
        file_bytes=image_bytes,
    )

    request = urllib.request.Request(
        OCR_SPACE_ENDPOINT,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "apikey": api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as error:
        body_text = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OCR.Space API error {error.code}: {body_text}") from error

    if result.get("IsErroredOnProcessing"):
        message = result.get("ErrorMessage") or result.get("ErrorDetails") or "OCR.Space processing failed"
        if isinstance(message, list):
            message = "; ".join(str(item) for item in message)
        raise RuntimeError(str(message))

    parsed_results = result.get("ParsedResults") or []
    parsed_text = "\n".join(
        str(item.get("ParsedText", "") or "").strip()
        for item in parsed_results
        if item.get("ParsedText")
    ).strip()

    return parsed_text


def _extract_email(text: str) -> str:
    match = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", text, re.IGNORECASE)
    return match.group(0).strip() if match else ""


def _extract_mobile(text: str) -> str:
    match = re.search(r"(\+66|0)[0-9\- ]{8,12}", text)
    return re.sub(r"[^\d+]", "", match.group(0)) if match else ""


def _extract_line_id(lines: list[str]) -> str:
    for index, line in enumerate(lines):
        if re.search(r"\b(line\s*id|id\s*line|line)\b|ไลน์|ไอดีไลน์", line, re.IGNORECASE):
            value = re.sub(
                r"(?i)\b(line\s*id|id\s*line|line)\b|ไลน์|ไอดีไลน์",
                "",
                line,
            )
            value = _clean_value(value)
            if value and len(value) <= 80:
                return value
            if index + 1 < len(lines):
                return _clean_value(lines[index + 1])
    return ""


def _extract_contact(lines: list[str]) -> str:
    contact_pattern = re.compile(r"(คุณ|นาย|นาง|น\.ส\.|นางสาว|Mr\.?|Ms\.?|Mrs\.?)\s*[\wก-๙ .'-]{2,80}", re.IGNORECASE)
    for line in lines:
        match = contact_pattern.search(line)
        if match:
            return _clean_value(match.group(0))
    return ""


def _extract_project_name(lines: list[str]) -> str:
    label_pattern = re.compile(r"(project\s*name|project|ชื่อโครงการ|โครงการ)\s*[:：\-–—]?\s*(.+)", re.IGNORECASE)
    for line in lines:
        match = label_pattern.search(line)
        if match and _clean_value(match.group(2)):
            return _clean_value(match.group(2))
    return ""


def _extract_address(lines: list[str]) -> str:
    address_keywords = re.compile(
        r"(address|site|location|ที่อยู่|ที่ตั้ง|สถานที่|ถนน|ตำบล|แขวง|อำเภอ|เขต|จังหวัด|กรุงเทพ|หมู่|ซอย)",
        re.IGNORECASE,
    )
    stop_keywords = re.compile(r"(contact|ผู้ติดต่อ|mobile|phone|tel|โทร|email|line|project|ชื่อโครงการ)", re.IGNORECASE)

    for index, line in enumerate(lines):
        if address_keywords.search(line):
            collected = []
            first = re.sub(
                r"(?i)\b(address|site address|site|location)\b|ที่อยู่|ที่ตั้ง|สถานที่",
                "",
                line,
            )
            first = _clean_value(first)
            if first:
                collected.append(first)

            for next_line in lines[index + 1 : index + 4]:
                if stop_keywords.search(next_line):
                    break
                if next_line:
                    collected.append(next_line)

            return _clean_value(" ".join(collected))

    return ""


def parse_ocr_text(ocr_text: str) -> dict:
    lines = [_clean_value(line) for line in ocr_text.splitlines()]
    lines = [line for line in lines if line]

    return {
        "project_name": _extract_project_name(lines),
        "address": _extract_address(lines),
        "contact": _extract_contact(lines),
        "mobile": _extract_mobile(ocr_text),
        "email": _extract_email(ocr_text),
        "line": _extract_line_id(lines),
    }


def scan_document_image(
    image_bytes: bytes,
    filename: str = "document.jpg",
    content_type: str = "",
) -> dict:
    ocr_text = scan_text_with_ocr_space(image_bytes, filename, content_type)
    if not ocr_text:
        raise RuntimeError("Cannot detect information")

    structured = parse_ocr_text(ocr_text)
    if not any(structured.values()):
        raise RuntimeError("Cannot detect information")

    return {
        "fields": structured,
        "ocr_text": ocr_text,
    }
