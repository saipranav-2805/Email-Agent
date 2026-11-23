import io
import logging
import re
from typing import Any, Dict, List, Optional
import openpyxl
import pandas as pd
import pdfplumber
from pydantic import BaseModel, Field
import pypdf

logger = logging.getLogger(__name__)


class AttachmentScanResult(BaseModel):
    filename: str
    matched: bool = False
    matched_by: Optional[str] = None  # reg_no, college_email, personal_email, full_name
    matched_value: Optional[str] = None
    sheet_or_page: Optional[str] = None
    column_headers: List[str] = Field(default_factory=list)
    matched_row_dict: Optional[Dict[str, Any]] = None
    total_rows_or_pages: int = 0
    error: Optional[str] = None


def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, float) and pd.isna(val):
        return ""
    return str(val).strip()


def _is_exact_match(target: str, text: str, is_reg_no: bool = False) -> bool:
    """Performs exact case-insensitive matching with boundary protection for register numbers."""
    if not target or not text:
        return False

    norm_target = target.strip().lower()
    norm_text = text.strip().lower()

    if is_reg_no:
        # Regex boundary check so 21BCE1001 doesn't falsely match 21BCE10010
        pattern = rf"(?<![a-z0-9]){re.escape(norm_target)}(?![a-z0-9])"
        return bool(re.search(pattern, norm_text))
    else:
        # For email and full name, case-insensitive substring/token match
        return norm_target in norm_text


def _check_identifiers(
    cell_or_line: str,
    reg_no: str,
    college_email: str,
    personal_email: str,
    full_name: str,
) -> Optional[tuple[str, str]]:
    """Checks cell/line for candidate identifiers. Returns (matched_by, matched_value) or None."""
    if reg_no and _is_exact_match(reg_no, cell_or_line, is_reg_no=True):
        return ("reg_no", reg_no)
    if college_email and _is_exact_match(college_email, cell_or_line, is_reg_no=False):
        return ("college_email", college_email)
    if personal_email and _is_exact_match(personal_email, cell_or_line, is_reg_no=False):
        return ("personal_email", personal_email)
    if full_name and len(full_name.strip()) >= 3 and _is_exact_match(full_name, cell_or_line, is_reg_no=False):
        return ("full_name", full_name)
    return None


def scan_excel(
    file_bytes: bytes,
    filename: str,
    reg_no: str,
    college_email: str,
    personal_email: str,
    full_name: str,
) -> AttachmentScanResult:
    """Scans all sheets of an Excel file (.xlsx, .xls) for candidate identifiers."""
    total_rows = 0
    try:
        excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
        sheet_names = excel_file.sheet_names

        for sheet in sheet_names:
            df = pd.read_excel(excel_file, sheet_name=sheet)
            total_rows += len(df)
            headers = [str(col).strip() for col in df.columns]

            # 1. Check column headers in case identifiers are in header rows
            for header in headers:
                hit = _check_identifiers(header, reg_no, college_email, personal_email, full_name)
                if hit:
                    return AttachmentScanResult(
                        filename=filename,
                        matched=True,
                        matched_by=hit[0],
                        matched_value=hit[1],
                        sheet_or_page=sheet,
                        column_headers=headers,
                        matched_row_dict={"matched_header": header},
                        total_rows_or_pages=total_rows,
                    )

            # 2. Iterate each row
            for idx, row in df.iterrows():
                row_dict = {}
                row_matched = False
                hit_info = None

                for col, val in row.items():
                    val_str = _clean_str(val)
                    row_dict[str(col).strip()] = val_str

                    if not row_matched:
                        hit = _check_identifiers(val_str, reg_no, college_email, personal_email, full_name)
                        if hit:
                            row_matched = True
                            hit_info = hit

                if row_matched and hit_info:
                    return AttachmentScanResult(
                        filename=filename,
                        matched=True,
                        matched_by=hit_info[0],
                        matched_value=hit_info[1],
                        sheet_or_page=sheet,
                        column_headers=headers,
                        matched_row_dict=row_dict,
                        total_rows_or_pages=total_rows,
                    )

        # Scanned all sheets without match
        return AttachmentScanResult(
            filename=filename,
            matched=False,
            sheet_or_page="All Sheets",
            column_headers=[],
            total_rows_or_pages=total_rows,
        )

    except Exception as e:
        logger.error(f"Error parsing Excel attachment {filename}: {e}")
        # Try openpyxl direct fallback if pandas parse failed
        try:
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
            for sheetname in wb.sheetnames:
                ws = wb[sheetname]
                headers = []
                for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
                    total_rows += 1
                    row_vals = [_clean_str(v) for v in row]
                    if row_idx == 0:
                        headers = row_vals
                    for val_str in row_vals:
                        hit = _check_identifiers(val_str, reg_no, college_email, personal_email, full_name)
                        if hit:
                            row_dict = {
                                headers[i] if i < len(headers) else f"col_{i}": row_vals[i]
                                for i in range(len(row_vals))
                            }
                            return AttachmentScanResult(
                                filename=filename,
                                matched=True,
                                matched_by=hit[0],
                                matched_value=hit[1],
                                sheet_or_page=sheetname,
                                column_headers=headers,
                                matched_row_dict=row_dict,
                                total_rows_or_pages=total_rows,
                            )
        except Exception as inner_e:
            logger.error(f"openpyxl fallback also failed for {filename}: {inner_e}")

        return AttachmentScanResult(
            filename=filename,
            matched=False,
            error=str(e),
            total_rows_or_pages=total_rows,
        )


def scan_csv(
    file_bytes: bytes,
    filename: str,
    reg_no: str,
    college_email: str,
    personal_email: str,
    full_name: str,
) -> AttachmentScanResult:
    """Scans CSV file for candidate identifiers."""
    encodings = ["utf-8", "latin-1", "cp1252", "iso-8859-1"]
    df = None
    for enc in encodings:
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc)
            break
        except Exception:
            continue

    if df is None:
        return AttachmentScanResult(
            filename=filename,
            matched=False,
            error="Could not decode CSV file with standard encodings.",
        )

    headers = [str(col).strip() for col in df.columns]
    total_rows = len(df)

    # Check headers
    for header in headers:
        hit = _check_identifiers(header, reg_no, college_email, personal_email, full_name)
        if hit:
            return AttachmentScanResult(
                filename=filename,
                matched=True,
                matched_by=hit[0],
                matched_value=hit[1],
                sheet_or_page="CSV",
                column_headers=headers,
                matched_row_dict={"matched_header": header},
                total_rows_or_pages=total_rows,
            )

    # Check rows
    for idx, row in df.iterrows():
        row_dict = {}
        row_matched = False
        hit_info = None

        for col, val in row.items():
            val_str = _clean_str(val)
            row_dict[str(col).strip()] = val_str

            if not row_matched:
                hit = _check_identifiers(val_str, reg_no, college_email, personal_email, full_name)
                if hit:
                    row_matched = True
                    hit_info = hit

        if row_matched and hit_info:
            return AttachmentScanResult(
                filename=filename,
                matched=True,
                matched_by=hit_info[0],
                matched_value=hit_info[1],
                sheet_or_page="CSV",
                column_headers=headers,
                matched_row_dict=row_dict,
                total_rows_or_pages=total_rows,
            )

    return AttachmentScanResult(
        filename=filename,
        matched=False,
        sheet_or_page="CSV",
        column_headers=headers,
        total_rows_or_pages=total_rows,
    )


def scan_pdf(
    file_bytes: bytes,
    filename: str,
    reg_no: str,
    college_email: str,
    personal_email: str,
    full_name: str,
) -> AttachmentScanResult:
    """Dual-parser for PDF: pdfplumber primary, pypdf fallback."""
    total_pages = 0
    # 1. Try pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            total_pages = len(pdf.pages)
            for page_num, page in enumerate(pdf.pages, start=1):
                # Check tables first
                tables = page.extract_tables() or []
                for table in tables:
                    if not table:
                        continue
                    headers = [_clean_str(c) for c in table[0]]
                    for row_idx, row in enumerate(table):
                        row_vals = [_clean_str(c) for c in row]
                        for c_idx, cell_val in enumerate(row_vals):
                            hit = _check_identifiers(cell_val, reg_no, college_email, personal_email, full_name)
                            if hit:
                                row_dict = {
                                    headers[i] if i < len(headers) else f"col_{i}": row_vals[i]
                                    for i in range(len(row_vals))
                                }
                                return AttachmentScanResult(
                                    filename=filename,
                                    matched=True,
                                    matched_by=hit[0],
                                    matched_value=hit[1],
                                    sheet_or_page=f"Page {page_num}",
                                    column_headers=headers,
                                    matched_row_dict=row_dict,
                                    total_rows_or_pages=total_pages,
                                )

                # Check raw page text
                page_text = page.extract_text() or ""
                lines = page_text.splitlines()
                for line in lines:
                    hit = _check_identifiers(line, reg_no, college_email, personal_email, full_name)
                    if hit:
                        return AttachmentScanResult(
                            filename=filename,
                            matched=True,
                            matched_by=hit[0],
                            matched_value=hit[1],
                            sheet_or_page=f"Page {page_num}",
                            column_headers=[],
                            matched_row_dict={"extracted_line": line.strip()},
                            total_rows_or_pages=total_pages,
                        )

            # Scanned all pages with pdfplumber without match
            return AttachmentScanResult(
                filename=filename,
                matched=False,
                sheet_or_page="All Pages",
                total_rows_or_pages=total_pages,
            )

    except Exception as e:
        logger.warning(f"pdfplumber failed on {filename}: {e}. Falling back to pypdf...")

    # 2. Fallback to pypdf
    try:
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        total_pages = len(reader.pages)
        for page_num, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            lines = text.splitlines()
            for line in lines:
                hit = _check_identifiers(line, reg_no, college_email, personal_email, full_name)
                if hit:
                    return AttachmentScanResult(
                        filename=filename,
                        matched=True,
                        matched_by=hit[0],
                        matched_value=hit[1],
                        sheet_or_page=f"Page {page_num} (pypdf fallback)",
                        column_headers=[],
                        matched_row_dict={"extracted_line": line.strip()},
                        total_rows_or_pages=total_pages,
                    )

        return AttachmentScanResult(
            filename=filename,
            matched=False,
            sheet_or_page="All Pages (pypdf fallback)",
            total_rows_or_pages=total_pages,
        )
    except Exception as fallback_e:
        logger.error(f"Both pdfplumber and pypdf failed for {filename}: {fallback_e}")
        return AttachmentScanResult(
            filename=filename,
            matched=False,
            error=f"PDF parsing error: {fallback_e}",
            total_rows_or_pages=total_pages,
        )


def scan_attachment(
    filename: str,
    file_bytes: bytes,
    profile: Dict[str, Any],
) -> AttachmentScanResult:
    """Dispatches attachment bytes to the appropriate deterministic parser."""
    fn_lower = filename.lower()
    reg_no = profile.get("reg_no", "")
    college_email = profile.get("college_email", "")
    personal_email = profile.get("personal_email", "")
    full_name = profile.get("name", "")

    if fn_lower.endswith((".xlsx", ".xls")):
        return scan_excel(file_bytes, filename, reg_no, college_email, personal_email, full_name)
    elif fn_lower.endswith(".csv"):
        return scan_csv(file_bytes, filename, reg_no, college_email, personal_email, full_name)
    elif fn_lower.endswith(".pdf"):
        return scan_pdf(file_bytes, filename, reg_no, college_email, personal_email, full_name)
    else:
        return AttachmentScanResult(
            filename=filename,
            matched=False,
            error=f"Unsupported attachment extension: {filename}",
        )
