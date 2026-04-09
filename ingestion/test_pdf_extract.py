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


def test_extract_pdf_candidates_evidence_included():
    pdf_bytes = b"Manufacturer: Vishay\nPackage: 0402\n"
    candidates = extract_pdf_candidates(pdf_bytes)
    assert "evidence" in candidates["manufacturer"]
    assert "Manufacturer" in candidates["manufacturer"]["evidence"]


def test_extract_pdf_candidates_no_match_returns_empty():
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<</Type /Catalog>>\nendobj\n"
    candidates = extract_pdf_candidates(pdf_bytes)
    assert candidates == {}


def test_extract_pdf_candidates_partial_match():
    # Only manufacturer present, no part number, package, or description.
    pdf_bytes = b"Manufacturer: Microchip Technology\nendstream\n"
    candidates = extract_pdf_candidates(pdf_bytes)
    assert "manufacturer" in candidates
    assert candidates["manufacturer"]["value"] == "Microchip Technology"
    assert "part_number" not in candidates
    assert "package" not in candidates


def test_extract_pdf_candidates_page_ref_single_page():
    # No form-feed → single page → page_ref should be None.
    pdf_bytes = b"Manufacturer: NXP\nMPN: LPC1768FBD100\nendstream\n"
    candidates = extract_pdf_candidates(pdf_bytes)
    assert candidates["manufacturer"]["page_ref"] is None
    assert candidates["part_number"]["page_ref"] is None


def test_extract_pdf_candidates_page_ref_multipage():
    # Form-feed separates pages; first field on page 1, second on page 2.
    page1 = b"Manufacturer: STMicroelectronics\n"
    page2 = b"Package / Case: LQFP-64\n"
    pdf_bytes = page1 + b"\x0c" + page2
    candidates = extract_pdf_candidates(pdf_bytes)
    assert candidates["manufacturer"]["page_ref"] == 1
    assert candidates["package"]["page_ref"] == 2


def test_extract_pdf_candidates_mpn_label_variant():
    pdf_bytes = b"MPN: ATmega328P-AU\nManufacturer: Microchip\n"
    candidates = extract_pdf_candidates(pdf_bytes)
    assert candidates["part_number"]["value"] == "ATmega328P-AU"


def test_extract_pdf_candidates_supplier_device_package():
    pdf_bytes = b"Supplier Device Package: SOT-23\n"
    candidates = extract_pdf_candidates(pdf_bytes)
    assert candidates["package"]["value"] == "SOT-23"
