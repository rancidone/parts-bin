# 0017: Check a bounded BOM in one domain operation

**Status:** Accepted

**Context:** Requiring the model to deduplicate rows, choose page sizes and resend
an entire BOM for each result page adds avoidable calls and makes coverage harder
to assess. Availability is deterministic once the input rows are identified.

**Decision:** The typed inventory tool accepts up to 100 parsed rows in one call.
The domain groups identical submitted filters, sums explicit quantities and maps
results back to original row indices. Missing quantities remain unknown, even
when another duplicate has an explicit quantity. The bot may condense the input
but need not do so. Exact ordering variants remain distinct. The server reports
requirements sharing matching stock; checks do not allocate or reserve it.

Results include the most stocked record per requirement and its match count.
Additional variants use the normal search tool. The transport allows a larger,
bounded result for this tool while retaining the shared total input bound;
other tools retain their existing result limit. Oversized results are explicitly
omitted rather than silently truncating BOM rows.

**Consequences:** The model still interprets pasted text and clarifies ambiguous
identities, units or quantities. It no longer manages result pagination. Lists
beyond the input bound require separate submissions; overlap across submissions
cannot establish allocation or total coverage. Pending facts and nominal matches
do not establish electrical suitability. Old pagination arguments are rejected;
there is no compatibility path for retired tool calls.
