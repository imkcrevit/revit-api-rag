Analyze these C# files from the Revit SDK project "{project_name}".

Project context: {project_summary}

{file_sections}

Return a JSON array with one object per file in the same order:
[
  {{
    "filename": "exact filename",
    "file_purpose": "1-sentence English description of what this file does",
    "key_classes": ["ClassName1", "ClassName2"],
    "key_methods": ["MethodName1 - brief description", "MethodName2 - brief description"]
  }}
]

JSON array only. No markdown. No explanation.
