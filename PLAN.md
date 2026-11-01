# Current work

Complete the typed manifest pipeline rather than converting normalization alone.

1. Use validated records in manifest merging, with named schema-selection and
   provenance-merge operations. Test conflicts, inferred/published precedence,
   missing schemas and preservation of original producer data.
2. Carry records through capture/import, command orchestration and generation.
   Keep dictionary conversion at JSON input/output and standard format adapters.
3. Audit remaining manifest field lookups, retain version 1 compatibility and run
   focused tests, the full suite, package builds and installed Docker scenarios.

Complete when the quoted dictionary condition and equivalent internal manifest
handling are replaced, both client formats still work, and PR 7 contains the fix.
