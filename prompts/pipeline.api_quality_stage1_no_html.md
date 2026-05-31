Audit the following parsed Revit API record for data quality.

No raw HTML is available. Be conservative: without HTML you cannot know if missing fields are parser failures or absent source documentation. Only deduct for clearly detectable issues.

## Score Criteria
- -0.5 name/full_id indicates a constructor (`#ctor` or `Constructor`) -> noise.
- -0.3 summary or info is clearly generic boilerplate.
- -0.25 parameters field is empty but syntax shows the method takes arguments with actual parameter names.
- -0.1 any field contains raw HTML tags, garbled text, or excessive whitespace.

Do not deduct for empty summary/info, missing syntax, or missing parameters when source absence is plausible.

Parsed record:
  name        : {name}
  full_id     : {full_id}
  syntax      : {syntax}
  summary     : {summary}
  info        : {info}
  parameters  : {parameters}

Return ONLY a JSON object:
{{
  "quality_score": 0.8,
  "issues": [],
  "needs_rewrite": false
}}

Use threshold {threshold}. JSON only. No markdown. No explanation.
