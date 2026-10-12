from evaluation.source_selection import assess_selection
from ingestion.supplied_source import Document


def test_missing_anchor_and_geometry_are_separate_from_source_drift():
    case = {'id': 'synthetic', 'source': {'sha256': 'reviewed'},
            'selection_context': {'passages': [{'page': 2, 'text': '1 kHz at room temperature'}],
                'merged_cells': [{'page': 2, 'cell': {'text': '500', 'box': [0, 0, 10, 50]}}]}}
    document = Document('https://www.vishay.com/test.pdf', 'changed', 'offline', ('PART-1',))
    result = assess_selection(case, document, 'PART-1')
    assert not result['source_hash_matches_review']
    assert result['missing_passages'] == case['selection_context']['passages']
    assert result['missing_merged_cells'] == case['selection_context']['merged_cells']
    assert result['status'] == 'needs_review'


def test_anchor_must_be_on_reviewed_page_and_wholly_in_one_passage():
    case = {'id': 'synthetic', 'source': {'sha256': 'reviewed'},
            'selection_context': {'passages': [{'page': 2, 'text': '1 kHz at room temperature'}]}}
    wrong_page = Document('https://www.vishay.com/test.pdf', 'reviewed', 'offline',
                          ('1 kHz at room temperature', 'PART-1'))
    assert assess_selection(case, wrong_page, 'PART-1')['missing_passages']
    correct = Document(wrong_page.url, 'reviewed', 'offline', ('PART-1', '1 kHz\tat room temperature'))
    assert assess_selection(case, correct, 'PART-1')['missing_passages'] == []
