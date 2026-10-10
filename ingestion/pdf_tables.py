"""Detected table geometry is context, never a source of inferred cell values."""

import json
import re
from statistics import median

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


def relevant_tables(tables: tuple[dict, ...], part_number: str, *,
                    excerpts: list[dict] | None = None) -> tuple[list[dict], bool]:
    """Keep exact rows and governing merged cells visible in selected passages.

    Coordinates use PDF points, with y measured from the page top. A tall cell
    stays one cell; blank cells are never filled or assigned another row's value.
    """
    exact = re.compile(r'(?<![\w,./\u2010-\u2015\u2212-])' + re.escape(part_number) + r'(?![\w,./\u2010-\u2015\u2212-])', re.I)
    selected, omitted = [], False
    # Governing merged cells on selected specification pages precede ordering
    # geometry. Preserve their real bounds; never copy values into blank cells.
    def merged_cells(table):
        cells = table['cells']
        height = median(cell['box'][3] - cell['box'][1] for cell in cells) if cells else 0
        visible = ' '.join(' '.join(item['text'].split()) for item in (excerpts or ()) if item['page'] == table['page'])
        return [cell for cell in cells if cell['box'][3] - cell['box'][1] > height * 1.5
                and len(cell['text']) >= 8 and ' '.join(cell['text'].split()) in visible]

    for table in sorted(tables, key=lambda table: (not bool(merged_cells(table)), len(table['cells']))):
        cells = table['cells']
        merged = merged_cells(table)
        identities = [cell for cell in cells if exact.search(cell['text'])]
        anchors = merged + identities
        if not anchors:
            continue
        top = min(cell['box'][1] for cell in cells)
        relevant = [cell for cell in cells if cell['box'][1] < top + 90 or any(
            cell['box'][1] < identity['box'][3] and cell['box'][3] > identity['box'][1]
            for identity in anchors)]
        # Keep row cells before headers if the budget cannot hold the whole context.
        relevant.sort(key=lambda cell: (cell not in merged, not any(
            cell['box'][1] < identity['box'][3] and cell['box'][3] > identity['box'][1]
            for identity in anchors), cell['box'][1], cell['box'][0]))
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
