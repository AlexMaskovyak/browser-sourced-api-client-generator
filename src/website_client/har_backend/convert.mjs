// The external dependency owns all inference. Only its OpenAPI and diagnostics cross back.
import {readFileSync, writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {generateFromHar} = await import(pathToFileURL(process.argv[2]));
const result = generateFromHar(readFileSync(process.argv[3], 'utf8'), {enums: false});
writeFileSync(process.argv[4], JSON.stringify({openapi: JSON.parse(result.files['openapi.json']), warnings: result.warnings, leaks: result.leaks}));
