# Source adapter provenance

Copied from the local scheduler on 2026-09-09. adapter.py retains only read-only Oracle and recognition functions; imports relocated to this package. No runtime dependency on the sibling project, its ledger, or local delivery.

Local deltas: adapter identity requires an integer nonnegative Revision and a string StatusCode; source_checks uses zip(strict=True) after its existing length check. Imports were sorted by Ruff. Source hashes below identify the originals before these integration edits.

```json
{
  "scheduler/vendor/po_vendor/__init__.py": "0c4d03a0c3dcc4570e8a6e5ca0420ff0d6abef84a6afb7c14b55d5215a1d7a14",
  "scheduler/vendor/po_vendor/extraction.py": "1e391403ab982f329893fe30d530af93994ac5f5825ce3509e0e42c4886ac6a3",
  "scheduler/vendor/po_vendor/source_checks.py": "2788825ceacafd6fba5841268c0b010a788d0862831a43e0af6dc74bf4a4c137",
  "scheduler/vendor/po_vendor/events.py": "8df8151eb37e934c05448466d9f6914dd060b2e559e7ee321e13de6e4dea1e71",
  "scheduler/vendor/po_vendor/store.py": "807abaa55e9b97338dc7014f42c42619e0a893827983e2813d9f8a60ba4f8699",
  "scheduler/src/po_scheduler/integrations.py": "c0792cdefd04824af3241695c3f68e656c7afd839c04f690115f009ff598fc95"
}
```

Scan iteration: JSON Schema validator is imported before a paid model request; jsonschema is an explicit backend dependency.
