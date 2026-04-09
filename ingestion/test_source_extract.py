from ingestion.source_extract import classify_content, detect_provider, extract_html_candidates, extract_html_fields


def test_classify_html_from_header():
    assert classify_content("text/html; charset=utf-8", "<html><body>x</body></html>") == "structured_html_product_page"


def test_classify_pdf_from_header():
    assert classify_content("application/pdf", "%PDF-1.7") == "pdf_document"


def test_detect_provider():
    assert detect_provider("https://www.digikey.com/en/products/detail/abc") == "digikey"
    assert detect_provider("https://www.lcsc.com/product-detail/abc") == "lcsc"


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
