"""Explain best-effort extraction coverage without claiming calibrated accuracy."""

import re

from domain.specifications import contract, missing_qualifiers


def assessment(candidate, document, part_number, category, text_omitted):
    expected = list(contract(category)['fields']) if category else ['package', 'description']
    incomplete = {fact['name']: gaps for fact in candidate.get('facts', [])
                  if (gaps := missing_qualifiers(category, fact))} if category else {}
    found = {fact['name'] for fact in candidate.get('facts', []) if fact['name'] not in incomplete} if category else set(candidate['fields'])
    missing = [name for name in expected if name not in found]
    score = round((len(expected) - len(missing)) / len(expected), 3) if expected else 0.0
    reasons = []
    if missing:
        reasons.append('Some supported fields could not be extracted with evidence.')
    if incomplete:
        reasons.append('Some proposed fields lack the minimum conditions required to confirm a match.')
    if text_omitted:
        reasons.append('Source context was omitted to stay within the extraction budget.')
    if not any(page.strip() for page in document.pages):
        reasons.append('No extractable text; visual inspection or OCR is needed.')
    if candidate['outcome'] != 'proposal':
        score = 0.0
        reasons.append('The source did not establish a proposal for the exact inventory identity.')
    # Use full-document retrieval signals for manual navigation, including pages
    # that did not fit the model context. Do not use these signals as facts.
    exact = re.compile(r'(?<![\w,./-])' + re.escape(part_number) + r'(?![\w,./-])', re.I)
    signals = []
    for index, text in enumerate(document.pages, 1):
        labels = []
        if exact.search(text):
            labels.append('Exact ordering code')
        if re.search(r'inspection requirements|measurement|routine test|test conditions', text, re.I):
            labels.append('Measurement or test context')
        if re.search(r'electrical|ratings|characteristics', text, re.I):
            labels.append('Ratings or electrical context')
        if labels:
            signals.append((100 if exact.search(text) else 10 if len(labels) > 1 else 1,
                            index, ', '.join(labels)))
    relevant = [{'page': index, 'reason': reason, 'url': f'{document.url}#page={index}'}
                for _, index, reason in sorted(signals, key=lambda item: (-item[0], item[1]))[:6]]
    if not relevant:
        relevant = [{'page': 1, 'reason': 'Start visual inspection', 'url': f'{document.url}#page=1'}]
    return {'confidence_score': score, 'score_basis': 'qualified_field_coverage',
            'score_explanation': 'Fraction of supported fields with validated citations and minimum qualifiers; '
                                 'not a probability of correctness or a qualifier-completeness check.',
            'missing_fields': missing, 'incomplete_fields': incomplete, 'reasons': reasons, 'review_required': True,
            'relevant_pages': relevant}
