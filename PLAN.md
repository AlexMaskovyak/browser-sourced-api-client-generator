# Expense submission with an attached file

Generate the upload and expense-creation flow from discovered contracts, with no
handwritten service client or edits to generated output.

1. Inspect current upload declarations and existing evidence. Record the upload
   protocol, metadata endpoint, file bytes transfer, attachment field and mutation.
2. Add portable validated workflow contracts, automatic discovery, generated
   runtime/API/CLI support and reproducible packaging. Keep authentication scoped
   to the application; storage receives only the signed upload request.
3. Test discovery through a generated client against a local service, including
   exact file bytes, checksum, attachment association, confirmation, failures,
   ambiguous outcomes and absence of automatic retries.
4. Rebuild the Gusto client using generator commands only and update the PRs.
   Use read-only Gusto evidence. Do not upload a real file or submit an expense
   without a separately reviewed, explicit authorization for that operation.

The browser and captured declarations own endpoint evidence. The versioned
manifest owns the portable workflow. The generator owns every generated file.
An upload can leave an unattached object if creation fails: report its stage and
uncertainty without retrying. A successful expense creation is not payment.
Completion requires a generated command and Python API that upload a file and
associate it with the created record in the end-to-end test, plus reproducible
Gusto output. Separate tested local behavior from live service verification.

## Completed verification

- Source declarations are normalized into versioned upload contracts; a matching
  mutation gets generated CLI flags, Python helpers, documentation and tests.
  `--require-upload` refuses incomplete builds. The initial protocol is Rails
  Active Storage; no application-specific URLs or operation names are in the
  generator's upload implementation.
- All 247 tests pass locally and in Docker. All 29 browser scenarios pass. The
  upload scenario uses the ordinary discovery/build commands and an independently
  installed client. It verifies one login, one metadata POST, one exact-byte PUT,
  one mutation and the stored receipt association. Storage carries no application
  cookies or credentials. Failure tests verify no automatic retries.
- The generated application client was rebuilt solely through generator commands
  from saved discovery and portable inputs. All 18 generated tests pass; source
  and wheel builds succeed; all 35 project files reproduce byte for byte.
- Preview is local and makes no requests. Real files were not uploaded and real
  expenses were not created or changed. Live service write compatibility remains
  unverified and requires a separately reviewed real operation.

Limits: source-field association is inferred from an unambiguous declaring module,
not a complete server schema; custom input fields remain JSON. Other upload
protocols and schema-backed composition require additional implementations.
