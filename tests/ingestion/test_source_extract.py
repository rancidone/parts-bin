from ingestion.source_extract import classify_content, detect_provider, extract_html_candidates, extract_html_fields


def test_classify_html_from_header():
    assert classify_content("text/html; charset=utf-8", "<html><body>x</body></html>") == "structured_html_product_page"


def test_classify_pdf_from_header():
    assert classify_content("application/pdf", "%PDF-1.7") == "pdf_document"


def test_classify_html_from_body_sniff_doctype():
    assert classify_content(None, "<!doctype html><html><body>x</body></html>") == "structured_html_product_page"


def test_classify_html_from_body_sniff_html_tag():
    assert classify_content(None, "<html><head></head><body></body></html>") == "structured_html_product_page"


def test_classify_pdf_from_body_sniff():
    assert classify_content(None, "%PDF-1.4 some content") == "pdf_document"


def test_classify_unsupported_content():
    assert classify_content("application/json", "{}") == "unsupported_content"


def test_classify_unknown_content_type_unknown_body():
    assert classify_content("text/plain", "just some plain text") == "unsupported_content"


def test_detect_provider():
    assert detect_provider("https://www.digikey.com/en/products/detail/abc") == "digikey"


def test_detect_provider_unknown():
    assert detect_provider("https://mouser.com/ProductDetail/abc") is None


def test_extract_html_fields_from_json_ld_and_table_rows():
    body = """
    <html>
      <head>
        <meta name="description" content="Buck regulator product page">
        <script type="application/ld+json">
          {
            "@type": "Product",
            "mpn": "TLV62565DBVR",
            "manufacturer": {"name": "Texas Instruments"},
            "additionalProperty": [
              {"name": "Package / Case", "value": "SOT-23-5"}
            ]
          }
        </script>
      </head>
      <body>
        <table>
          <tr><th>Manufacturer</th><td>Texas Instruments</td></tr>
          <tr><th>Package / Case</th><td>SOT-23-5</td></tr>
        </table>
      </body>
    </html>
    """

    fields = extract_html_fields("https://www.digikey.com/en/products/detail/example", body)

    assert fields["manufacturer"] == "Texas Instruments"
    assert fields["package"] == "SOT-23-5"
    assert fields["part_number"] == "TLV62565DBVR"
    assert fields["description"] == "Buck regulator product page"


def test_extract_html_candidates_include_method_and_evidence():
    body = """
    <html>
      <body>
        <table>
          <tr><th>Supplier Device Package</th><td>SC-74A, SOT-753</td></tr>
        </table>
      </body>
    </html>
    """

    candidates = extract_html_candidates("https://www.digikey.com/en/products/detail/example", body)

    assert candidates["package"]["value"] == "SC-74A, SOT-753"
    assert candidates["package"]["method"] == "digikey-html-labeled-row"
    assert "Supplier Device Package" in candidates["package"]["evidence"]



def test_extract_html_candidates_no_candidates_empty_page():
    body = "<html><body><p>Nothing useful here</p></body></html>"
    candidates = extract_html_candidates("https://www.digikey.com/en/products/detail/example", body)
    assert candidates == {}


def test_extract_html_candidates_unknown_provider_uses_json_ld():
    body = """
    <html>
      <head>
        <script type="application/ld+json">
          {"@type": "Product", "mpn": "BC547", "manufacturer": {"name": "Fairchild"}}
        </script>
      </head>
    </html>
    """
    # Unknown provider — labeled row extraction is skipped, but JSON-LD still works.
    candidates = extract_html_candidates("https://mouser.com/ProductDetail/BC547", body)
    assert candidates["part_number"]["value"] == "BC547"
    assert candidates["manufacturer"]["value"] == "Fairchild"
    assert "package" not in candidates


def test_extract_html_candidates_meta_description_fallback():
    body = '<html><head><meta name="description" content="100nF 0402 Capacitor"></head></html>'
    candidates = extract_html_candidates("https://www.digikey.com/en/products/detail/example", body)
    assert candidates["description"]["value"] == "100nF 0402 Capacitor"
    assert candidates["description"]["method"] == "html-meta-description"


def test_extract_html_candidates_json_ld_graph():
    body = """
    <html>
      <head>
        <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@graph": [
              {"@type": "BreadcrumbList"},
              {"@type": "Product", "mpn": "LM358", "manufacturer": {"name": "TI"}}
            ]
          }
        </script>
      </head>
    </html>
    """
    candidates = extract_html_candidates("https://www.digikey.com/en/products/detail/lm358", body)
    assert candidates["part_number"]["value"] == "LM358"
    assert candidates["manufacturer"]["value"] == "TI"
