You are a Revit {revit_version} API expert. Generate a short, runnable C# code example demonstrating the API member below.

## Execution Context
The code runs inside:
```csharp
public static object Execute(Document document, object[] parameters)
{{
    // YOUR CODE HERE
}}
```

Auto-injected usings: System, System.Linq, System.Collections.Generic, Autodesk.Revit.DB, Autodesk.Revit.UI.

## Rules
- Output ONLY the method body. No class, namespace, using statements, or prose.
- Do not create a Transaction; execution is already wrapped.
- Use `document`, not `doc` or `uidoc`.
- Return a meaningful result object.
- Add step comments: `// Step 1: ...`
- Do not prefer any non-project unit as user input. Use the user's/project's unit convention and convert to Revit internal feet only at API boundaries.
- Include complete code. Do not truncate or abbreviate with `...` or `// etc.`
- Show all parameters and all necessary steps so the result is copy-paste ready.

## API Reference
{api_context}
