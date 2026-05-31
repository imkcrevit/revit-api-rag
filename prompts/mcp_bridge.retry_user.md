The following C# code was generated for this Revit task but failed to compile.

## Original User Request
{user_query}

## Broken Code
```csharp
{code}
```

## Compile Error
{error_msg}

## Common Fixes
- Do not add `using` statements; they are auto-injected.
- Do not wrap code in a class or namespace; only write the method body.
- Do not create a Transaction; the plugin already provides one.
- Use `document`, not `doc` or `uidoc`, as the Document variable.
- Fully qualify Structure namespace types, for example `Autodesk.Revit.DB.Structure.StructuralType`.
- Activate `FamilySymbol` before placement.
- Make sure all referenced classes, methods, properties, and enum values exist in Revit 2026 API.
- Preserve user/project units; convert to Revit internal feet only when writing geometry or length values.

Output ONLY the corrected method body. No class, namespace, usings, or explanation.
