from ingestion.pdf_extract import extract_pdf_candidates


def test_extract_pdf_candidates_from_text_like_pdf():
    pdf_bytes = b"""
    %PDF-1.4
    1 0 obj
    << /Type /Catalog >>
    stream
    Manufacturer: Texas Instruments
    Manufacturer Part Number: TLV62565DBVR
    Package / Case: SOT-23-5
    Description: Buck Switching Regulator IC
    endstream
    endobj
    """

    candidates = extract_pdf_candidates(pdf_bytes)

    assert candidates["manufacturer"]["value"] == "Texas Instruments"
    assert candidates["part_number"]["value"] == "TLV62565DBVR"
    assert candidates["package"]["value"] == "SOT-23-5"
    assert candidates["description"]["value"] == "Buck Switching Regulator IC"
    assert candidates["package"]["method"] == "pdf-labeled-text"
