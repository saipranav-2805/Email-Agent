import pytest
from app.config import CandidateProfile
from app.services.parser import scan_attachment, scan_csv, scan_excel, scan_pdf


def test_excel_multi_sheet_positive_match(make_excel_multi_sheet, sample_profile):
    """Verifies that an Excel file with candidate on Sheet 2 correctly identifies candidate."""
    excel_bytes = make_excel_multi_sheet(include_candidate=True, sheet_target="Shortlist_Round_1")

    res = scan_attachment(
        filename="Shortlisted_Candidates.xlsx",
        file_bytes=excel_bytes,
        profile=sample_profile.model_dump(),
    )

    assert res.matched is True
    assert res.matched_by in ["reg_no", "college_email", "full_name"]
    assert res.sheet_or_page == "Shortlist_Round_1"
    assert res.matched_row_dict is not None
    assert "21BCE1001" in str(res.matched_row_dict.values())
    assert "Pranav Sharma" in str(res.matched_row_dict.values())


def test_excel_reg_no_boundary_protection(make_excel_multi_sheet, sample_profile):
    """Critical safety check: 21BCE10010 must NEVER match 21BCE1001."""
    excel_bytes = make_excel_multi_sheet(include_candidate=False, sheet_target="Shortlist_Round_1")

    res = scan_attachment(
        filename="Shortlisted_Candidates.xlsx",
        file_bytes=excel_bytes,
        profile=sample_profile.model_dump(),
    )

    assert res.matched is False
    assert res.matched_by is None
    assert res.matched_row_dict is None


def test_excel_negative_case(make_excel_multi_sheet, sample_profile):
    """Candidate not present in any sheet returns matched=False."""
    excel_bytes = make_excel_multi_sheet(include_candidate=False)

    res = scan_attachment(
        filename="Other_Students_Only.xlsx",
        file_bytes=excel_bytes,
        profile=sample_profile.model_dump(),
    )

    assert res.matched is False
    assert res.total_rows_or_pages > 0


def test_csv_positive_and_negative(make_csv, sample_profile):
    """Tests CSV scanning for both hit and miss."""
    # Positive
    csv_bytes_pos = make_csv(include_candidate=True)
    res_pos = scan_attachment("results.csv", csv_bytes_pos, sample_profile.model_dump())
    assert res_pos.matched is True
    assert res_pos.matched_by == "reg_no"
    assert res_pos.matched_row_dict["Candidate_ID"] == "21BCE1001"

    # Negative (Contains 21BCE10010 to verify boundary protection in CSV)
    csv_bytes_neg = make_csv(include_candidate=False)
    res_neg = scan_attachment("results.csv", csv_bytes_neg, sample_profile.model_dump())
    assert res_neg.matched is False


def test_pdf_parsing_positive_and_negative(make_pdf, sample_profile):
    """Tests PDF parsing across multiple pages for presence of candidate."""
    # Positive
    pdf_bytes_pos = make_pdf(include_candidate=True)
    res_pos = scan_attachment("Shortlist_Round2.pdf", pdf_bytes_pos, sample_profile.model_dump())
    assert res_pos.matched is True
    assert "Page 2" in (res_pos.sheet_or_page or "")
    assert res_pos.matched_by == "reg_no"

    # Negative (Contains 21BCE10010, should not match 21BCE1001)
    pdf_bytes_neg = make_pdf(include_candidate=False)
    res_neg = scan_attachment("Shortlist_Round2.pdf", pdf_bytes_neg, sample_profile.model_dump())
    assert res_neg.matched is False
