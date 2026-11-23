import io
import os
import pytest
import pytest_asyncio
import pandas as pd
from pypdf import PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from sqlalchemy import text
from app.config import CandidateProfile
from app.db.models import Base
from app.db.session import engine, get_db_session, init_db


@pytest_asyncio.fixture(autouse=True)
async def clean_database():
    """Wipes test database tables before each test to guarantee complete test isolation."""
    await init_db()
    async with get_db_session() as session:
        try:
            await session.execute(text("DELETE FROM emails"))
            await session.execute(text("DELETE FROM companies"))
            await session.execute(text("DELETE FROM user_preferences"))
            await session.execute(text("DELETE FROM scheduled_reminders"))
        except Exception:
            pass


@pytest.fixture
def sample_profile() -> CandidateProfile:
    return CandidateProfile(
        reg_no="21BCE1001",
        name="Pranav Sharma",
        college_email="pranav.sharma2021@vitstudent.ac.in",
        personal_email="pranav.dev.chief@gmail.com",
        branch="Computer Science and Engineering",
        degree="B.Tech",
        cgpa=8.85,
        tenth_pct=94.2,
        twelfth_pct=93.6,
        active_backlogs=0,
        career_interests=["Software Development", "Backend Engineering"],
        red_flags=["Vellore campus", "offline interview at Vellore", "service agreement"],
        min_ctc_lpa=6.5,
    )


@pytest.fixture
def make_excel_multi_sheet():
    """Generates a binary in-memory multi-sheet Excel file."""
    def _generator(include_candidate: bool = True, sheet_target: str = "Shortlist_Round_1"):
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            # Sheet 1: Other candidates
            df1 = pd.DataFrame([
                {"RegNo": "21BCE9998", "Name": "Alice Smith", "CGPA": 9.1, "Status": "Shortlisted"},
                {"RegNo": "21BCE9999", "Name": "Bob Jones", "CGPA": 8.5, "Status": "Shortlisted"},
            ])
            df1.to_excel(writer, sheet_name="General_Info", index=False)

            # Sheet 2: Target sheet
            if include_candidate:
                df2 = pd.DataFrame([
                    {"RegNo": "21BCE1002", "Name": "Charlie Brown", "Email": "charlie@college.edu", "Interview_Slot": "10:00 AM"},
                    {"RegNo": "21BCE1001", "Name": "Pranav Sharma", "Email": "pranav.sharma2021@vitstudent.ac.in", "Interview_Slot": "11:30 AM"},
                ])
            else:
                df2 = pd.DataFrame([
                    # Notice 21BCE10010 looks similar to 21BCE1001 to test boundary protection!
                    {"RegNo": "21BCE10010", "Name": "Different Student", "Email": "diff@college.edu", "Interview_Slot": "12:00 PM"},
                    {"RegNo": "21BCE2005", "Name": "David Lee", "Email": "david@college.edu", "Interview_Slot": "02:00 PM"},
                ])
            df2.to_excel(writer, sheet_name=sheet_target, index=False)

        buffer.seek(0)
        return buffer.getvalue()
    return _generator


@pytest.fixture
def make_csv():
    """Generates a binary in-memory CSV file."""
    def _generator(include_candidate: bool = True):
        buffer = io.BytesIO()
        if include_candidate:
            df = pd.DataFrame([
                {"Candidate_ID": "21BCE1001", "Candidate_Name": "Pranav Sharma", "Test_Score": 95},
                {"Candidate_ID": "21BCE1050", "Candidate_Name": "Other Candidate", "Test_Score": 88},
            ])
        else:
            df = pd.DataFrame([
                {"Candidate_ID": "21BCE10010", "Candidate_Name": "Fake Match", "Test_Score": 70},
                {"Candidate_ID": "21BCE5555", "Candidate_Name": "Another Person", "Test_Score": 80},
            ])
        df.to_csv(buffer, index=False)
        return buffer.getvalue()
    return _generator


@pytest.fixture
def make_pdf():
    """Generates a valid binary PDF with text on pages using reportlab."""
    def _generator(include_candidate: bool = True):
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)

        # Page 1: Announcement info
        c.drawString(100, 750, "Campus Placement Shortlist Announcement")
        c.drawString(100, 720, "Company: Microsoft India R&D")
        c.showPage()

        # Page 2: Shortlisted table
        c.drawString(100, 750, "Selected Students for Final Technical Interview:")
        if include_candidate:
            c.drawString(100, 720, "1. RegNo: 21BCE1001 | Name: Pranav Sharma | Time: 2:00 PM")
            c.drawString(100, 700, "2. RegNo: 21BCE3045 | Name: Jane Doe | Time: 2:45 PM")
        else:
            c.drawString(100, 720, "1. RegNo: 21BCE10010 | Name: Not Pranav | Time: 2:00 PM")
            c.drawString(100, 700, "2. RegNo: 21BCE3045 | Name: Jane Doe | Time: 2:45 PM")

        c.showPage()
        c.save()
        buffer.seek(0)
        return buffer.getvalue()
    return _generator
