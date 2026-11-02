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
