The following Revit API record has quality issues. Use the raw HTML as ground truth to produce corrected fields.

Identified issues:
{issues}

Original parsed record:
  name        : {name}
  full_id     : {full_id}
  syntax      : {syntax}
  summary     : {summary}
  info        : {info}
  parameters  : {parameters}
  remark      : {remark}

Raw HTML excerpt:
{html_excerpt}

## Rules
- Only include fields where you can make a genuine improvement based on HTML evidence.
- If the original field already matches the HTML, do not include it.
- If a field is empty in both the record and the HTML, omit it.
- Do not fabricate content.
- Keep output concise.

Return ONLY a JSON object with improved fields:
{{
  "summary": "clear one-sentence English description",
  "parameters": "one parameter per line, or empty string if none",
  "syntax": "correct C# public signature from HTML",
  "info": "concise description if summary is missing"
}}

Omit unchanged fields. JSON only. No markdown. No explanation.
