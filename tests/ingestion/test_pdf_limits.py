"""Exercise genuine multi-page PDF parsing and bounded source downloads."""

from unittest.mock import patch

import httpx
import pytest

from ingestion import supplied_source as source


def text_pdf(page_count, page_stream=None):
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               ('<< /Type /Pages /Count %d /Kids [%s] >>' % (page_count, ' '.join(f'{4 + i * 2} 0 R' for i in range(page_count)))).encode(),
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for _ in range(page_count):
        stream = page_stream or b'BT /F1 10 Tf 20 100 Td (Nexperia PBSS5350T) Tj ET'
        objects.append(('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>' % (len(objects) + 2)).encode())
        objects.append(b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream')
    data = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = len(data)
    data.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    data.extend(''.join(f'{offset:010d} 00000 n \n' for offset in offsets[1:]).encode())
    data.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode())
    return bytes(data)


def test_long_manufacturer_document_preserves_every_page():
    pages = source.parse_pdf(text_pdf(39))
    assert len(pages) == 39
    assert all('Nexperia PBSS5350T' in page for page in pages)


def test_page_limit_rejects_extra_page_instead_of_truncating():
    with pytest.raises(source.EnrichmentError, match='page/text budget'):
        source.parse_pdf(text_pdf(source.MAX_PAGES + 1))


def test_long_datasheet_with_ordering_addendum_keeps_all_pages():
    pages = source.parse_pdf(text_pdf(68))
    assert len(pages) == 68
    assert 'Nexperia PBSS5350T' in pages[-1]


def test_text_budget_remains_independent_of_page_count():
    with patch.object(source, 'MAX_TEXT_CHARS', 10):
        with pytest.raises(source.EnrichmentError, match='page/text budget'):
            source.parse_pdf(text_pdf(1))


async def test_manufacturer_request_identifies_application_without_browser_impersonation():
    def respond(request):
        assert request.headers['user-agent'].startswith('PartsBin/')
        assert request.headers['accept'] == 'application/pdf'
        return httpx.Response(200, content=b'%PDF-fixture')
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with patch.object(source, 'public_destination'), patch.object(source, 'parse_pdf_bounded', return_value=(('page',), (), False)):
            result = await source.retrieve_pdf('https://assets.nexperia.com/documents/data-sheet/PBSS5350T.pdf', client)
    assert result.pages == ('page',)


async def test_streaming_download_cannot_bypass_byte_limit_with_small_declared_length():
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'%PDF'
            yield b'-too-large'
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={'content-length': '0'}, stream=Chunks()))) as client:
        with patch.object(source, 'public_destination'), patch.object(source, 'MAX_BYTES', 4), patch.object(source, 'parse_pdf_bounded') as parse:
            with pytest.raises(source.EnrichmentError, match='download budget'):
                await source.retrieve_pdf('https://www.ti.com/lit/ds/symlink/ne555.pdf', client)
            parse.assert_not_called()


def test_excerpt_selection_keeps_summary_when_ordering_codes_dominate():
    summary = 'Precision timer family description. ' * 60
    ordering = 'NE555P Package ordering information. ' * 500
    document = source.Document('https://www.ti.com/lit/ds/symlink/ne555.pdf', 'hash', 'now', (summary, ordering))
    excerpts, omitted = source.select_excerpts(document, 'NE555P')
    assert omitted
    assert excerpts[0] == {'page': 1, 'text': summary.encode('utf-8')[:4000].decode('utf-8', errors='ignore')}
    assert any(item['page'] == 2 and 'NE555P' in item['text'] for item in excerpts)
    assert sum(len(item['text'].encode('utf-8')) for item in excerpts) <= source.MAX_EXCERPT_BYTES


@pytest.mark.parametrize('identity,body,supported_context', [
    ('ABC-123', 'Order code\tPackage\tPins\nABC-123\tQFN\t24\nABC-123X\tSOIC\t8\n', 'ABC-123\tQFN\t24'),
    ('MOD-24', 'Module\tDimensions\nMOD-24\t12 mm x\n\t8 mm\nNEXT-25\t20 mm\n', 'MOD-24\t12 mm x\n\t8 mm'),
    ('SW-7', 'Contact rating\nAt ambient 25 °C\nSW-7\t30 V\t2 A\n', 'At ambient 25 °C\nSW-7\t30 V\t2 A'),
    ('XΩ-42', 'Ordering code\tPackage\nXΩ-42\tµModule\t16\n', 'XΩ-42\tµModule\t16'),
    ('LONG-9', 'x' * 1800 + ' LONG-9 Package BGA 64 ' + 'y' * 1800, 'LONG-9 Package BGA 64'),
])
def test_excerpt_selection_preserves_formatting_classes(identity, body, supported_context):
    document = source.Document('https://www.ti.com/test.pdf', 'hash', 'now', (
        'Family description\n' + 'Background\n' * 2000,
        'Unrelated notes\n' * 100 + body + 'More notes\n' * 100,
    ))
    selected, omitted = source.select_excerpts(document, identity)
    assert omitted
    assert any(item['page'] == 2 and supported_context in item['text'] for item in selected)
    assert sum(len(item['text'].encode()) for item in selected) <= source.MAX_EXCERPT_BYTES
    assert all(item['text'] in document.pages[item['page'] - 1] for item in selected)
    passages, _ = source.electrical_passages(selected)
    assert any(item['page'] == 2 and supported_context in item['text'] for item in passages)


@pytest.mark.parametrize('cells,expected', [
    ([(20, 160, 'Code'), (20, 140, 'TYPE-A'), (20, 120, 'TYPE-B'),
      (100, 160, 'Package'), (100, 140, 'QFN'), (100, 120, 'SOIC')],
     'Code\tPackage\nTYPE-A\tQFN\nTYPE-B\tSOIC\n'),
    ([(20, 160, 'Module'), (20, 140, 'MOD-24'), (100, 160, 'Size'),
      (100, 140, '12 mm x'), (100, 128, '8 mm')],
     'Module\tSize\nMOD-24\t12 mm x\n8 mm\n'),
    ([(20, 160, 'Ordering code'), (100, 160, 'Package'), (150, 160, 'Number'),
      (150, 148, 'of pins'), (20, 130, 'ABC-123'), (100, 130, 'BGA'), (150, 130, '64')],
     'Ordering code\tPackage Number\nof pins\nABC-123\tBGA\t64\n'),
    ([(20, 160, 'Features'), (20, 140, 'Fast switching'), (20, 128, 'Low power'),
      (100, 160, 'Description'), (100, 140, 'Precision timing'), (100, 128, 'circuits')],
     'Features\tDescription\nFast switching\tPrecision timing\nLow power\tcircuits\n'),
])
def test_pdf_layout_classes_preserve_spatial_rows_without_reconstructing(cells, expected):
    # Genuine PDF operators let PDFMiner choose its own column containers.
    # Close headings can become one text line; preserve that text, not invented cells.
    stream = '\n'.join(f'BT /F1 10 Tf {x} {y} Td ({text}) Tj ET' for x, y, text in cells).encode()
    assert source.parse_pdf(text_pdf(1, stream)) == (expected,)


def test_detected_merged_cells_keep_bounds_without_filling_other_rows():
    from ingestion.pdf_tables import relevant_tables
    # Two columns, two data rows; left data cell spans both rows.
    stream = b'\n'.join([
        b'10 100 m 190 100 l S', b'100 130 m 190 130 l S',
        b'10 160 m 190 160 l S', b'10 180 m 190 180 l S',
        b'10 100 m 10 180 l S', b'100 100 m 100 180 l S', b'190 100 m 190 180 l S',
        b'BT /F1 10 Tf 20 165 Td (Voltage) Tj ET', b'BT /F1 10 Tf 110 165 Td (Code) Tj ET',
        b'BT /F1 10 Tf 20 135 Td (500 V) Tj ET', b'BT /F1 10 Tf 110 145 Td (PART-A) Tj ET',
        b'BT /F1 10 Tf 110 115 Td (PART-B) Tj ET',
    ])
    pages, tables, omitted = source.parse_pdf(text_pdf(1, stream), with_tables=True)
    selected, omitted = relevant_tables(tables, 'PART-B')
    assert not omitted and 'PART-B' in pages[0]
    cells = selected[0]['cells']
    voltage = next(cell for cell in cells if cell['text'] == '500 V')
    identity = next(cell for cell in cells if cell['text'] == 'PART-B')
    assert voltage['box'][1] < identity['box'][1] and voltage['box'][3] == identity['box'][3]
    assert sum(cell['text'] == '500 V' for cell in cells) == 1


def test_measurement_context_survives_repeated_ordering_rows_and_generic_notes():
    document = source.Document('https://www.vishay.com/test.pdf', 'hash', 'now', (
        'Family notes\n' + 'Background\n' * 2000,
        'Ordering\n' + 'PART-A\t1 uF\n' * 1000,
        'Notes\n' + 'Unrelated mechanical notes\n' * 100,
        'INSPECTION REQUIREMENTS\nROUTINE TEST\nCapacitance\t1 kHz at room temperature\n' + 'Other\n' * 200,
    ))
    excerpts, omitted = source.select_excerpts(document, 'PART-A')
    assert omitted
    assert any(item['page'] == 4 and '1 kHz at room temperature' in item['text'] for item in excerpts)


def test_table_budget_omits_geometry_without_discarding_text():
    from unittest.mock import patch
    with patch.object(source, 'extract_tables', return_value=[{'page': 1, 'cells': [{'text': 'too large'}]}]), \
            patch.object(source, 'MAX_TABLE_BYTES', 1):
        pages, tables, omitted = source.parse_pdf(text_pdf(1), with_tables=True)
    assert 'PBSS5350T' in pages[0] and tables == () and omitted


def test_specification_sections_survive_ordering_repetitions_without_neighboring_grade():
    document = source.Document('https://www.ti.com/test.pdf', 'hash', 'now', (
        'Precision amplifier family\n' + 'Background\n' * 2000,
        '1.1 Absolute Maximum Ratings\nSupply stress\t40 V\n' + 'Stress notes\n' * 30,
        '1.2 Recommended Operating Conditions\nSupply\t3 V\t36 V\nAmbient\t-40 to 85 C\n',
        '1.3 Electrical Characteristics: AMP12B and AMP12BA\nGlobal conditions: 25 C, load 10 kohm\n'
        'Input bias current\t-35 nA\n' + 'Other parameters\n' * 100 +
        'FREQUENCY RESPONSE\nGain bandwidth product\t1.2 MHz\nSlew rate\t0.5 V/us\n',
        '1.3 Electrical Characteristics: AMP12B and AMP12BA (continued)\n'
        'POWER SUPPLY\nQuiescent current per amplifier\t300 uA\nMerged temperature: -40 to 85 C\n',
        '1.4 Electrical Characteristics: AMP12\nLegacy ratings\nFREQUENCY RESPONSE\n0.7 MHz\n',
        'Ordering\n' + 'AMP12BIDR\tSOIC\n' * 1000,
    ))
    excerpts, omitted = source.select_excerpts(document, 'AMP12BIDR')
    passages, _ = source.electrical_passages(excerpts, budget=9000)
    assert omitted
    for required in ('40 V', '3 V\t36 V', '25 C, load 10 kohm', '-35 nA',
                     '1.2 MHz', '0.5 V/us', 'Merged temperature: -40 to 85 C', 'AMP12BIDR'):
        assert any(required in item['text'] for item in passages)
    windows = source.specification_windows(document, 'AMP12BIDR')
    assert not any(i == 5 for i, _, _ in windows)
    assert sum(len(item['text'].encode()) for item in passages) <= 9000
    assert all(item['text'] in document.pages[item['page'] - 1] for item in passages)


def test_contact_form_survives_ordering_repetitions_and_is_citable():
    document = source.Document('https://omronfs.omron.com/test.pdf', 'hash', 'now', (
        'Switch family\n' + 'Background\n' * 2000,
        'List of Models\n' + 'SWITCH-01F\tLow force\n' * 1000 +
        'Contact Form\tContact Specifications\nSPDT\tGold alloy\n'
        'Ratings\n30 VDC\t0.1 A\n',
        'Dimensions\n' + 'SWITCH-01F\t10 mm\n' * 1000,
    ))
    excerpts, omitted = source.select_excerpts(document, 'SWITCH-01F')
    passages, _ = source.electrical_passages(excerpts)
    assert omitted
    assert any(item['page'] == 2 and 'Contact Form' in item['text'] and
               'SPDT' in item['text'] for item in passages)
    assert any('SWITCH-01F' in item['text'] for item in passages)
    assert sum(len(item['text'].encode()) for item in passages) <= source.MAX_EXCERPT_BYTES
    assert all(item['text'] in document.pages[item['page'] - 1] for item in passages)


def test_numeric_rating_row_cannot_end_a_specification_section():
    page = ('Absolute Maximum Ratings\nCurrent\t50 A\n'
            '1.1\tN m\nNotes\nPulse limited by junction temperature\n'
            'Specifications (TJ = 25 C)\nOn resistance\t0.028 ohm\n'
            '1.2 Thermal Information\nUnrelated thermal data\n')
    document = source.Document('https://www.vishay.com/test.pdf', 'hash', 'now', (page,))
    windows = source.specification_windows(document, 'MOS-1')
    assert 'Pulse limited by junction temperature' in windows[0][2]
    assert 'On resistance' not in windows[0][2]
    assert 'Specifications (TJ = 25 C)' in windows[1][2]
    assert 'Unrelated thermal data' not in windows[1][2]


def test_specification_footnotes_precede_repeated_table_test_headers():
    document = source.Document('https://www.vishay.com/test.pdf', 'hash', 'now', (
        'Power device\nAbsolute Maximum Ratings\nCurrent\t50 A\n1.1\tN m\n'
        'Notes\nPulse limited by junction temperature\n' + 'MOS-1\tOrdering\n' * 1000,
        'Specifications (TJ = 25 C)\nPARAMETER\tTEST CONDITIONS\n'
        'On resistance\tGate voltage 5 V\n' + 'Other parameter\n' * 180 +
        'Notes\nPulse width <= 300 us; duty cycle <= 2 %\n',
    ))
    excerpts, _ = source.select_excerpts(document, 'MOS-1')
    passages, _ = source.electrical_passages(excerpts)
    assert any('Pulse width <= 300 us; duty cycle <= 2 %' in item['text'] for item in passages)
    assert sum(len(item['text'].encode()) for item in passages) <= source.MAX_EXCERPT_BYTES


def test_short_numeric_merged_cell_retains_bounds_without_filling_rows():
    from ingestion.pdf_tables import relevant_tables
    cells = [
        {'box': [0, 0, 30, 10], 'text': 'Voltage'},
        {'box': [30, 0, 100, 10], 'text': 'Ordering code'},
        {'box': [0, 10, 30, 50], 'text': '500'},
        {'box': [30, 10, 100, 20], 'text': 'CAP-1'},
        {'box': [30, 20, 100, 30], 'text': 'CAP-2'},
        {'box': [30, 30, 100, 40], 'text': 'CAP-3'},
        {'box': [30, 40, 100, 50], 'text': 'CAP-4'},
    ]
    selected, _ = relevant_tables(({'page': 3, 'cells': cells},), 'CAP-1',
        excerpts=[{'page': 3, 'text': 'CAP-1\n500\n'}])
    assert next(c for c in selected[0]['cells'] if c['text'] == '500')['box'] == [0, 10, 30, 50]
    assert sum(c['text'] == '500' for c in selected[0]['cells']) == 1
    assert all(c in cells for c in selected[0]['cells'])


def test_selected_specification_rows_retain_governing_merged_temperature_geometry():
    from ingestion.pdf_tables import relevant_tables
    table = {'page': 2, 'cells': [
        {'box': [0, 0, 100, 10], 'text': 'TEST CONDITIONS'},
        {'box': [0, 10, 50, 20], 'text': 'Quiescent current at 5 V'},
        {'box': [0, 20, 50, 30], 'text': 'Quiescent current at 36 V'},
        {'box': [50, 10, 100, 30], 'text': 'Temperature -40 to 85 C'},
        {'box': [100, 10, 150, 20], 'text': '300 uA'},
        {'box': [100, 20, 150, 30], 'text': '800 uA'},
    ]}
    assert relevant_tables((table,), 'AMP12BIDR') == ([], False)
    selected, omitted = relevant_tables((table,), 'AMP12BIDR', excerpts=[
        {'page': 2, 'text': 'Quiescent current at 5 V\nTemperature -40 to 85 C\n300 uA\n800 uA'}])
    assert not omitted
    assert selected[0]['page'] == 2
    assert sorted(selected[0]['cells'], key=lambda cell: cell['text']) == sorted(
        table['cells'], key=lambda cell: cell['text'])
    assert next(cell for cell in selected[0]['cells'] if 'Temperature' in cell['text'])['box'] == [50, 10, 100, 30]


def test_voltage_variant_titles_with_layout_spaces_reserve_correct_section():
    from ingestion.supplied_source import Document, specification_windows
    doc = Document('https://example.com/source.pdf', 'a' * 64, '2026-10-12T00:00:00+00:00', (
        'Electrical Characteristics\nAP2112-1.2  Electrical  Characteristics\nwrong voltage row',
        'Electrical Characteristics (Cont.)\nAP2112-3.3  Electrical  Characteristics\ncorrect voltage row',
    ))
    selected = specification_windows(doc, 'AP2112K-3.3TRG1')
    assert any('correct voltage row' in text for _, _, text in selected)
    assert not any('wrong voltage row' in text for _, _, text in selected)
