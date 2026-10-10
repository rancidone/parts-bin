# 0011: Extract PDFs best effort with explicit coverage and visual review

Status: Accepted

## Context

PDF reading order and cell association are separate problems. Row flattening
loses merged-cell boundaries, and bounded context can exclude distant methods.
Authentic quotations and complete field lists do not prove electrical correctness.
This supersedes the literal-only condition comparison in [0007](0007-reviewed-electrical-facts.md).

## Decision

Use pdfplumber's established table detector over PDFMiner layouts. Supply bounded
cell text and geometry alongside original source passages. Preserve tall cells
once, without filling blank rows. Reserve source context for measurement methods
and inspection notes. Detection remains best effort: an uncertain association
leaves the affected fact unknown, while supported facts may proceed to review.

Expose an explicitly explained confidence score based on supported-field
coverage with minimum qualifiers, missing fields, omitted context and relevant PDF page links. It is not
a calibrated accuracy probability or a semantic qualifier check. Image-only
sources request visual inspection without a text-only paid call. Automatic OCR
is deferred; original PDFs remain transient and manufacturer page links provide
manual inspection. Downloads, parsing and model context remain bounded.

Keep incomplete source facts available for review, while minimum capacitor
measurement and rated-voltage qualifiers gate confirmed matching. These minimum
checks do not prove that all source conditions were captured.

Compare explicitly stated Celsius and humidity intervals and center/tolerance
representations by equal decimal endpoints. Preserve original condition text
and source evidence. Recognize the explicit resistive/resistive-load spelling for load type.
Recognize current_type/voltage_type as explicit AC/DC condition aliases without
merging conflicting duplicate keys. Keep condition names and other values literal; never infer
room temperature, derating, containment or omitted qualifiers.

## Consequences

A small dependency supplies tested table geometry instead of implementing a
bespoke detector. Detection can still fail on borderless tables, diagrams and
scans. Page links depend on the operator's PDF viewer. Coverage can be high while
facts are wrong or incomplete, so approval and independent source review remain
necessary. Additional normalization or OCR requires representative evaluation.

[Table detector documentation](https://github.com/jsvine/pdfplumber#extracting-tables),
[extraction](../../ingestion/supplied_source.py),
[condition comparison](../../domain/specification_conditions.py).
