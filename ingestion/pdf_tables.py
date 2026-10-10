"""Detected table geometry is context, never a source of inferred cell values."""

import json
import re

MAX_TABLE_CONTEXT_BYTES = 3000


def extract_tables(page) -> list[dict]:
    tables = []
    for table in page.find_tables():
        values = table.extract()
        cells = []
        for row, texts in zip(table.rows, values):
            for box, text in zip(row.cells, texts):
                if box is not None and text:
                    cells.append({'box': [round(value, 2) for value in box], 'text': text})
        tables.append({'page': page.page_number, 'cells': cells})
    return tables


def relevant_tables(tables: tuple[dict, ...], part_number: str) -> tuple[list[dict], bool]:
    """Keep headers and cells intersecting the exact row, including tall merged cells.

    Coordinates use PDF points, with y measured from the page top. A tall cell
    stays one cell; blank cells are never filled or assigned another row's value.
    """
    exact = re.compile(r'(?<![\w,./-])' + re.escape(part_number) + r'(?![\w,./-])', re.I)
    selected, omitted = [], False
    for table in tables:
        cells = table['cells']
        identities = [cell for cell in cells if exact.search(cell['text'])]
        if not identities:
            continue
        top = min(cell['box'][1] for cell in cells)
        relevant = [cell for cell in cells if cell['box'][1] < top + 90 or any(
            cell['box'][1] < identity['box'][3] and cell['box'][3] > identity['box'][1]
            for identity in identities)]
        # Keep row cells before headers if the budget cannot hold the whole context.
        relevant.sort(key=lambda cell: (not any(
            cell['box'][1] < identity['box'][3] and cell['box'][3] > identity['box'][1]
            for identity in identities), cell['box'][1], cell['box'][0]))
        chosen = []
        for cell in relevant:
            trial = selected + [{'page': table['page'], 'cells': chosen + [cell]}]
            if len(json.dumps(trial, ensure_ascii=False).encode()) <= MAX_TABLE_CONTEXT_BYTES:
                chosen.append(cell)
            else:
                omitted = True
        if chosen:
            selected.append({'page': table['page'], 'cells': chosen})
    return selected, omitted
