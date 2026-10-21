# l10n-check

A small linter for JSON locale (translation) files. It compares each locale against a base
locale and reports the drift that breaks apps at runtime: keys that are missing or left
behind after renames, translations whose placeholders no longer match the source, values
that changed type, object names that appear twice, and empty or untranslated strings.

Python 3.9+, standard library only. No network. MIT license.

```
python l10n_check.py locales/                 # all *.json in a directory
python l10n_check.py en.json fr.json de.json  # explicit files
python l10n_check.py locales/ --base en       # pick the base locale
python l10n_check.py locales/ --json          # machine-readable output
python l10n_check.py locales/ --strict        # warnings also give exit code 1
```

The base locale defaults to the file named `en.json`, otherwise the first file
alphabetically. Nested objects are compared key by key; the key in the output is the
dot path (`menu.file.open`).

Exit codes: `0` no findings, `1` errors found (or warnings with `--strict`),
`2` unreadable input (missing path, not UTF-8, no `.json` files, unknown `--base`).
Output is `file: severity [rule] key: message`.

## What it checks

| Rule id | Severity | Basis |
|---|---|---|
| `bad-json` | error | the file does not parse as JSON (RFC 8259) |
| `not-object` | error | locale files map keys to values, so the top level must be a JSON object |
| `duplicate-key` | error | RFC 8259 section 4: "the names within an object SHOULD be unique"; most parsers silently keep one value, which one depends on the parser |
| `missing-key` | error | the key exists in the base locale but not here; lookups fall back or crash depending on the framework |
| `container-mismatch` | error | a key is an object in one locale and a plain value in the other, so the key structure itself disagrees |
| `placeholder-mismatch` | error | the translation's placeholders differ from the source: `{name}` (ICU MessageFormat / `str.format`), `{{name}}` (Mustache/Handlebars), `%s` / `%d` / `%(name)s` (printf). Missing or extra placeholders throw or corrupt output at runtime |
| `extra-key` | warning | the key is not in the base locale: usually a leftover from a renamed or deleted string |
| `type-mismatch` | warning | the value is a string in the base but a number/boolean/array/null here |
| `empty-value` | warning | the translation is an empty string while the source is not |
| `untranslated` | warning | the string is identical to the base locale. Heuristic: brand names and "OK" look the same in many languages, so this is a warning, and `--strict` users should expect to curate it |

Placeholder comparison is order-free but count-aware: `%s %s` against `%s` is a mismatch.

## What it does not do

This is not a translation management system. It only reads JSON files (no `.po`,
`.xliff`, `.yaml`, or `.strings`), it does not know ICU plural/select blocks
(`{count, plural, ...}`) beyond seeing their braces as placeholders, and it does not
judge translation quality. A file that passes can still read wrong.

## Tests

```
python -m unittest -v
```

69 tests cover each placeholder syntax (including `%%` escapes and `{{...}}` not being
double-counted as `{...}`), duplicate-name detection inside nested objects and arrays,
every comparison rule in both directions, exit codes, `--base` selection, and the JSON
report shape. CI runs on Ubuntu and Windows with Python 3.9 and 3.13 (see
`.github/workflows/test.yml`); I have not run it on other versions.

## Sources

- RFC 8259, The JavaScript Object Notation (JSON) Data Interchange Format, section 4: https://www.rfc-editor.org/rfc/rfc8259
- ICU MessageFormat (`{name}` placeholders): https://unicode-org.github.io/icu/userguide/format_parse/messages/
- Python printf-style string formatting (`%s`, `%(name)s`): https://docs.python.org/3/library/stdtypes.html#printf-style-string-formatting
- Mustache specification (`{{name}}`): https://mustache.github.io/mustache.5.html

## License

MIT, see `LICENSE`.
