# 0016: Reserve governing specification context before repeated ordering rows

Status: Accepted

## Context

Long family datasheets repeat exact ordering codes across packaging addenda.
Ranking those repetitions first can exclude operating limits, stress ratings,
frequency response and governing table conditions. Flattened text can place a
merged temperature cell between rows without showing which rows it governs.

## Decision

Within the existing source budget, reserve verbatim windows for stress,
recommended operating and electrical sections before filling the remaining space
with ordering context. Prefer electrical section headings containing the longest
device designation that prefixes the supplied code. This is a retrieval heuristic;
it does not establish exact identity or authorize using another grade's ratings.
Retain the existing reservation for distant measurement methods.

Supply detected merged cells visible in selected passages with their original
bounds, neighboring rows and headers. Prioritize small governing tables within
the geometry budget. Do not fill blank cells, reconstruct values or persist
geometry as factual evidence. Facts continue to cite server-owned passages under
[0010](0010-supplied-electrical-passages.md).

Each proposed fact must repeat its applicable global conditions, with explicit
row or merged-cell conditions overriding defaults. Retain characterization and
other footnote restrictions. Missing context leaves affected facts unsupported.

## Consequences

Less opening and repeated ordering text fits in the same budget. Heading and
geometry detection remain best effort and can omit context on other layouts.
Independent source review and bounded live evaluation remain necessary: supplying
the right context does not ensure the model retains every qualifier.
