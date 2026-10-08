---
name: findings-writeup
description: Standard template for writing up an answer to a business question -- question, SQL used, result, chart, caveats, interpretation. Use after querying data to answer a specific question, before reporting the answer back.
---

# Findings write-up

Every answered business question gets written up in this shape. Consistency
matters here beyond tidiness: this template is the direct precursor to the
`Finding` record in Phase 4's persistent findings store, so keeping the
fields stable now means that store is just "give this shape a schema and a
table," not a redesign.

## Template

```markdown
### Question
<the business question, as asked>

### SQL
```sql
<the exact query used to produce the result>
```

### Result
<the number/table, summarized -- not a raw dump>

### Chart
<reference/path to the chart, if one was produced; omit section if not applicable>

### Caveats & confidence
<anything that limits how much weight this answer should carry: filtered-out
order statuses, a time window that excludes recent incomplete data, a known
data-quality issue in a table this touched, small sample size, etc. State a
confidence level, not just a list of caveats.>

### Interpretation
<one or two sentences of business meaning -- so what. Not a restatement of
the number.>
```

## Rules

- The SQL block must be the *exact* query that produced the result shown --
  if you cleaned it up for readability afterward, re-run the cleaned version
  and confirm the result still matches before writing it down.
- Every number in "Interpretation" must trace back to "Result." If it
  doesn't, it doesn't belong here -- this is the same bar Phase 6's
  faithfulness eval holds agent-generated answers to, so holding
  human-written ones to it too keeps the bar consistent.
- If the honest caveat is "I'm not confident in this," say that explicitly
  rather than omitting the caveats section.
