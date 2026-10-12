"""Offline context-retention checks against independently reviewed PDF anchors."""

import argparse
import hashlib
import json
from pathlib import Path

from ingestion import supplied_source as source
from ingestion.pdf_tables import MAX_TABLE_CONTEXT_BYTES
from .electrical_completeness import EXPECTATIONS
from .live_electrical import load_cases


def assess_selection(case, document, part_number):
    excerpts, omitted = source.select_excerpts(document, part_number)
    tables, table_omission = source.relevant_tables(document.tables, part_number, excerpts=excerpts)
    budget = source.MAX_EXCERPT_BYTES - (MAX_TABLE_CONTEXT_BYTES if tables else 0)
    passages, passage_omission = source.electrical_passages(excerpts, budget=budget)
    normalize = lambda text: ' '.join(text.split())
    context = case['selection_context']
    missing_text = [anchor for anchor in context['passages'] if not any(
        item['page'] == anchor['page'] and normalize(anchor['text']) in normalize(item['text'])
        for item in passages)]
    missing_cells = [anchor for anchor in context.get('merged_cells', []) if not any(
        table['page'] == anchor['page'] and any(cell == anchor['cell'] for cell in table['cells'])
        for table in tables)]
    return {'case_id': case['id'], 'status': 'needs_review',
            'source_hash_matches_review': document.sha256 == case['source']['sha256'],
            'selected_text_bytes': sum(len(item['text'].encode()) for item in passages),
            'selected_table_context_bytes': len(json.dumps(tables, ensure_ascii=False).encode()),
            'missing_passages': missing_text, 'missing_merged_cells': missing_cells,
            'context_omitted': omitted or table_omission or passage_omission or document.table_context_omitted,
            'passages': passages, 'tables': tables,
            'note': 'Anchor retention does not establish table association or electrical correctness. '
                    'Changed source hashes invalidate the reviewed anchors.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True)
    parser.add_argument('--pdf', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    expectations = {case['id']: case for case in json.loads(EXPECTATIONS.read_text())['cases']}
    if args.case not in expectations or 'selection_context' not in expectations[args.case]:
        parser.error('Case has no independently reviewed selection context')
    case = load_cases([args.case])[0]
    data = args.pdf.read_bytes()
    if len(data) > source.MAX_BYTES:
        parser.error('PDF exceeds the source byte budget')
    pages, tables, omitted = source.parse_pdf_bounded(data)
    document = source.Document(case['url'], hashlib.sha256(data).hexdigest(), 'offline', pages, tables, omitted)
    result = assess_selection(expectations[args.case], document, case['part_number'])
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({key: result[key] for key in ('case_id', 'source_hash_matches_review',
        'missing_passages', 'missing_merged_cells', 'status')}))
    raise SystemExit(2)


if __name__ == '__main__':
    main()
