import io
import shutil
import pytest
from reportlab.pdfgen import canvas
from openpyxl import Workbook
from pypdf import PdfReader, PdfWriter
from goldilocks.ingestion import parse_statement, table_records

HEADERS = ["Date", "Narration", "Debit", "Credit", "Balance"]
ROWS = [
    ["2026-01-01", "SALARY CEDAR", None, 1000, 1500],
    ["2026-01-02", "SWIGGY FOOD", 100, None, 1400],
]


def pdf_bytes(pages=1):
    out = io.BytesIO()
    c = canvas.Canvas(out)
    for p in range(pages):
        c.setFont("Helvetica", 11)
        c.drawString(50, 780, "HDFC Bank - Fictional test document")
        c.drawString(50, 750, "Opening balance: 500.00")
        c.drawString(
            50, 720, f"2026-01-0{p + 1} SALARY CEDAR 1000.00 CR {1500 + p * 1000}.00"
        )
        c.showPage()
    c.save()
    return out.getvalue()


def workbook(bank="HDFC"):
    out = io.BytesIO()
    w = Workbook()
    s = w.active
    s.append([bank + " Bank - Fictional fixture"])
    s.append(HEADERS)
    for r in ROWS:
        s.append(r)
    w.save(out)
    return out.getvalue()


@pytest.mark.parametrize(
    "bank",
    [
        "HDFC",
        "ICICI",
        "State Bank of India",
        "Axis",
        "Punjab National",
        "Union Bank",
        "AU Small Finance",
        "IDFC First",
    ],
)
def test_bank_named_tabular_workbooks(bank):
    p = parse_statement(workbook(bank), bank + ".xlsx")
    assert (
        len(p["records"]) == 2
        and p["opening_balance"] == 500
        and p["closing_balance"] == 1400
    )
    assert p["bank"] != "Other bank"


def test_legacy_xls():
    import xlwt

    w = xlwt.Workbook()
    s = w.add_sheet("Fictional bank")
    for i, row in enumerate([HEADERS] + ROWS):
        for j, value in enumerate(row):
            if value is not None:
                s.write(i, j, value)
    out = io.BytesIO()
    w.save(out)
    assert len(parse_statement(out.getvalue(), "fictional.xls")["records"]) == 2


def test_multipage_pdf_preserves_page_and_explicit_summary():
    r = parse_statement(pdf_bytes(2), "fictional.pdf")
    assert len(r["records"]) == 2 and [x["source_page"] for x in r["records"]] == [1, 2]
    assert (
        r["opening_balance"] == 500
        and r["opening_balance_source"] == "statement summary"
    )


def test_encrypted_pdf_requires_correct_password():
    w = PdfWriter()
    w.append(PdfReader(io.BytesIO(pdf_bytes())))
    w.encrypt("fixture-password")
    out = io.BytesIO()
    w.write(out)
    with pytest.raises(ValueError, match="password"):
        parse_statement(out.getvalue(), "protected.pdf")
    with pytest.raises(ValueError, match="password"):
        parse_statement(out.getvalue(), "protected.pdf", "wrong")
    assert (
        len(
            parse_statement(out.getvalue(), "protected.pdf", "fixture-password")[
                "records"
            ]
        )
        == 1
    )


def test_encrypted_workbook_requires_correct_password():
    from msoffcrypto.format.ooxml import OOXMLFile

    office = OOXMLFile(io.BytesIO(workbook()))
    out = io.BytesIO()
    office.encrypt("fixture-password", out)
    with pytest.raises(ValueError):
        parse_statement(out.getvalue(), "protected.xlsx")
    with pytest.raises(ValueError):
        parse_statement(out.getvalue(), "protected.xlsx", "wrong")
    assert (
        len(
            parse_statement(out.getvalue(), "protected.xlsx", "fixture-password")[
                "records"
            ]
        )
        == 2
    )


def test_scanned_pdf_uses_real_ocr():
    if not shutil.which("tesseract"):
        pytest.skip("Tesseract is not installed in this environment")
    from PIL import Image, ImageDraw, ImageFont
    import fitz

    image = Image.new("RGB", (1800, 600), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
    draw.text((40, 50), "HDFC Bank - Fictional scan", font=font, fill="black")
    draw.text(
        (40, 160), "01/01/2026 SALARY CEDAR 1000.00 CR 1500.00", font=font, fill="black"
    )
    b = io.BytesIO()
    image.save(b, format="PNG")
    doc = fitz.open()
    page = doc.new_page(width=900, height=300)
    page.insert_image(page.rect, stream=b.getvalue())
    r = parse_statement(doc.tobytes(), "scanned.pdf")
    doc.close()
    assert r["ocr"] and len(r["records"]) == 1 and r["records"][0]["amount"] == 1000


def test_ambiguous_directions_are_reported_not_invented():
    rows = [HEADERS, ["2026-01-01", "AMBIGUOUS", 100, 100, 500], *ROWS]
    valid, warnings = table_records(rows)
    assert len(valid) == 2 and "Ambiguous" in warnings[0]
    with pytest.raises(ValueError):
        parse_statement(b"Date,Narration,Amount\n2026-01-01,Unknown,100\n", "bad.csv")


@pytest.mark.parametrize(
    "filename,data",
    [
        ("empty.csv", b""),
        ("bad.txt", b"not supported"),
        ("huge.csv", b"0" * 15_000_001),
    ],
)
def test_bounded_upload_formats(filename, data):
    with pytest.raises(ValueError):
        parse_statement(data, filename)
