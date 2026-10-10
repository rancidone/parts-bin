"""Exercise genuine multi-page PDF parsing and bounded source downloads."""

from unittest.mock import patch

import httpx
import pytest

from ingestion import supplied_source as source


def text_pdf(page_count):
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               ('<< /Type /Pages /Count %d /Kids [%s] >>' % (page_count, ' '.join(f'{4 + i * 2} 0 R' for i in range(page_count)))).encode(),
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for _ in range(page_count):
        stream = b'BT /F1 10 Tf 20 100 Td (Nexperia PBSS5350T) Tj ET'
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
        with patch.object(source, 'public_destination'), patch.object(source, 'parse_pdf_bounded', return_value=('page',)):
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
