import httpx

from ingestion.extraction_quality import assessment
from ingestion.supplied_source import Document, extract


def test_coverage_is_explained_and_navigation_can_find_omitted_context():
    document = Document('https://www.vishay.com/test.pdf', 'a' * 64, 'now', (
        'PART-A Electrical ratings', 'Inspection requirements\nCapacitance 1 kHz at room temperature'))
    result = assessment({'outcome': 'proposal', 'facts': [{'name': 'capacitance', 'conditions': {'measurement_frequency': '1 kHz', 'measurement_temperature': 'room temperature'}}]},
                        document, 'PART-A', 'capacitor', True)
    assert result['confidence_score'] == 0.25
    assert result['missing_fields'] == ['tolerance', 'rated_voltage', 'dielectric']
    assert result['score_basis'] == 'qualified_field_coverage'
    assert result['review_required'] and result['reasons']
    assert result['relevant_pages'][1]['url'].endswith('#page=2')


async def test_image_only_source_has_visual_fallback_without_paid_request():
    def fail(_):
        raise AssertionError('Scanned PDF must not make a text-only paid request')
    document = Document('https://www.vishay.com/test.pdf', 'a' * 64, 'now', ('', ''))
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        result = await extract(document, 'PART-A', None, api_key='fake', model='test',
                               client=client, category='capacitor')
    assert result['outcome'] == 'needs_clarification' and result['facts'] == []
    assert result['extraction_assessment']['confidence_score'] == 0
    assert result['extraction_assessment']['relevant_pages'][0]['url'].endswith('#page=1')
