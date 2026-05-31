Audit the following parsed Revit API record by comparing it against the raw HTML source.

## Principle
Measure parse quality, not documentation completeness. Only deduct points when the HTML source contains information that the parsed record is missing or captured incorrectly. If the HTML lacks a field, an empty parsed field is correct.

## Score Criteria
Cumulative deductions from 1.0, only when HTML evidence exists:
- -0.5 name/full_id indicates a constructor (`#ctor` or `Constructor`) -> noise record.
- -0.4 summary and info are both empty but HTML contains a real description.
- -0.3 summary/info is generic boilerplate while HTML has a real description.
- -0.25 parameters field is empty but HTML shows method parameters with descriptions.
- -0.2 syntax/C# signature is missing but HTML contains a public C# signature.
- -0.2 important HTML content such as description or return value is absent from parsed fields.
- -0.15 all parameter types are `Unknown Type` but HTML has actual type information.
- -0.1 any field contains raw HTML tags, garbled unicode, or excessive whitespace.

Parsed record:
  name        : {name}
  full_id     : {full_id}
  syntax      : {syntax}
  summary     : {summary}
  info        : {info}
  parameters  : {parameters}

Raw HTML excerpt:
{html_excerpt}

Return ONLY a JSON object:
{{
  "quality_score": 0.0,
  "issues": ["short English issue strings"],
  "needs_rewrite": true
}}

Use threshold {threshold}. JSON only. No markdown. No explanation.
