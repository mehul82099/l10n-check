#!/usr/bin/env python3
"""l10n_check - lint JSON locale (translation) files.

Compares JSON locale files against a base locale and reports the drift that
breaks apps at runtime: missing keys, keys that were renamed or left behind,
translations whose placeholders no longer match the source, values that
changed type, duplicate object names, and empty or untranslated strings.

Python 3.9+, standard library only. No network.

Usage:
    python l10n_check.py locales/                 # all *.json in a directory
    python l10n_check.py en.json fr.json de.json  # explicit files
    python l10n_check.py locales/ --base en       # pick the base locale
    python l10n_check.py locales/ --json          # machine-readable output
    python l10n_check.py locales/ --strict        # warnings also give exit 1

Exit codes: 0 = no findings, 1 = errors (or warnings with --strict),
2 = unreadable input (missing path, not UTF-8, no .json files, bad --base).
"""

import argparse
import json
import os
import re
import sys
from collections import Counter

# Placeholder syntaxes recognised in message strings.
# ICU MessageFormat / Python str.format style: {name}, {0}. A brace that is
# part of a Handlebars {{...}} marker is excluded by the look-arounds.
BRACE_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*|\d+)\}(?!\})")
# Handlebars / Mustache: {{name}}, {{{name}}} (unescaped form), {{ obj.prop }}.
HANDLEBARS_RE = re.compile(r"\{\{\{?\s*([A-Za-z0-9_.\-]+)\s*\}?\}\}")
# printf style: %s, %d, %(name)s. %% is a literal percent and never matches.
PRINTF_NAMED_RE = re.compile(r"%\(([^)]+)\)[#0\- +]*\d*(?:\.\d+)?([diouxXeEfFgGcrs])")
PRINTF_RE = re.compile(r"(?<!%)%(?!%|\()[#0\- +]*\d*(?:\.\d+)?[diouxXeEfFgGcrs]")

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


class _Pairs(list):
    """Marker for the raw member list json gives us via object_pairs_hook."""


def extract_placeholders(text):
    """Return a Counter of placeholder tokens in a message string.

    Tokens keep their syntax: '{name}', '{{name}}', '%(name)s', '%s'.
    Counts matter, so '%%s %%s' vs '%%s' is a mismatch.
    """
    found = Counter()
    for m in HANDLEBARS_RE.finditer(text):
        found["{{%s}}" % m.group(1)] += 1
    for m in BRACE_RE.finditer(text):
        found["{%s}" % m.group(1)] += 1
    for m in PRINTF_NAMED_RE.finditer(text):
        found["%%(%s)%s" % (m.group(1), m.group(2))] += 1
    for m in PRINTF_RE.finditer(text):
        found["%" + m.group(0)[-1]] += 1
    return found


def _category(value):
    if isinstance(value, dict):
        return "object"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return "unknown"


def _finding(file, locale, severity, rule, key, message):
    return {
        "file": file,
        "locale": locale,
        "severity": severity,
        "rule": rule,
        "key": key,
        "message": message,
    }


def _to_plain(node, path, file, locale, findings):
    """Convert the _Pairs tree from json into plain dicts, recording duplicate
    object names (RFC 8259 section 4: names SHOULD be unique)."""
    if isinstance(node, _Pairs):
        out = {}
        for name, value in node:
            child_path = name if not path else path + "." + name
            if name in out:
                findings.append(_finding(
                    file, locale, SEVERITY_ERROR, "duplicate-key", child_path,
                    "name %r occurs more than once in the same object" % name))
            out[name] = _to_plain(value, child_path, file, locale, findings)
        return out
    if isinstance(node, list):
        return [_to_plain(v, "%s[%d]" % (path, i), file, locale, findings)
                for i, v in enumerate(node)]
    return node


def load_locale(path, locale):
    """Parse one locale file. Returns (data, findings, fatal).

    fatal is an exit-2 style problem: file unreadable or not UTF-8.
    bad-json / not-object are findings (exit 1) with data None.
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, [], "cannot read %s: %s" % (path, exc)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return None, [], "%s is not valid UTF-8: %s" % (path, exc)
    try:
        parsed = json.loads(text, object_pairs_hook=_Pairs)
    except json.JSONDecodeError as exc:
        return None, [_finding(
            path, locale, SEVERITY_ERROR, "bad-json", None,
            "invalid JSON: %s" % exc)], None
    findings = []
    data = _to_plain(parsed, "", path, locale, findings)
    if not isinstance(data, dict):
        findings.append(_finding(
            path, locale, SEVERITY_ERROR, "not-object", None,
            "top level is a %s, expected a JSON object" % _category(data)))
        data = None
    return data, findings, None


def _check_leaf(file, locale, key, base_value, value, base_name, findings):
    rule_key = key or "(top level)"
    if _category(base_value) != _category(value):
        findings.append(_finding(
            file, locale, SEVERITY_WARNING, "type-mismatch", rule_key,
            "%s in the base locale but %s here"
            % (_category(base_value), _category(value))))
        return
    if not isinstance(value, str):
        return
    if value == "" and base_value != "":
        findings.append(_finding(
            file, locale, SEVERITY_WARNING, "empty-value", rule_key,
            "empty translation string"))
    base_ph = extract_placeholders(base_value)
    ph = extract_placeholders(value)
    if base_ph != ph:
        missing = list((base_ph - ph).elements())
        extra = list((ph - base_ph).elements())
        parts = []
        if missing:
            parts.append("missing " + ", ".join(sorted(missing)))
        if extra:
            parts.append("has extra " + ", ".join(sorted(extra)))
        findings.append(_finding(
            file, locale, SEVERITY_ERROR, "placeholder-mismatch", rule_key,
            "placeholders differ from the base locale: " + "; ".join(parts)))
    if locale != base_name and value == base_value and base_value != "":
        findings.append(_finding(
            file, locale, SEVERITY_WARNING, "untranslated", rule_key,
            "identical to the %r source string" % base_name))


def compare(file, locale, base_node, node, base_name, path, findings):
    """Recursively compare a locale tree against the base locale tree."""
    rule_key = path or "(top level)"
    if isinstance(base_node, dict) and isinstance(node, dict):
        for name in base_node:
            child = name if not path else path + "." + name
            if name not in node:
                findings.append(_finding(
                    file, locale, SEVERITY_ERROR, "missing-key", child,
                    "present in base locale %r but missing here" % base_name))
        for name in node:
            child = name if not path else path + "." + name
            if name not in base_node:
                findings.append(_finding(
                    file, locale, SEVERITY_WARNING, "extra-key", child,
                    "not present in base locale %r" % base_name))
        for name in base_node:
            if name in node:
                compare(file, locale, base_node[name], node[name],
                        base_name, name if not path else path + "." + name,
                        findings)
    elif isinstance(base_node, dict) != isinstance(node, dict):
        findings.append(_finding(
            file, locale, SEVERITY_ERROR, "container-mismatch", rule_key,
            "object in one locale and a %s in the other"
            % _category(node if isinstance(base_node, dict) else base_node)))
    else:
        _check_leaf(file, locale, path, base_node, node, base_name, findings)


def collect_files(paths):
    """Expand arguments into (locale_name, file_path) pairs. Returns (pairs,
    fatal_error)."""
    pairs = []
    for path in paths:
        if not os.path.exists(path):
            return None, "no such file or directory: %s" % path
        if os.path.isdir(path):
            names = sorted(n for n in os.listdir(path)
                           if n.lower().endswith(".json")
                           and os.path.isfile(os.path.join(path, n)))
            if not names:
                return None, "no .json files in directory: %s" % path
            for name in names:
                pairs.append((os.path.splitext(name)[0],
                              os.path.join(path, name)))
        elif os.path.isfile(path):
            pairs.append((os.path.splitext(os.path.basename(path))[0], path))
        else:
            return None, "not a regular file: %s" % path
    return pairs, None


def pick_base(pairs, base_arg):
    """Choose the base locale. Returns (base_pair, fatal_error)."""
    if base_arg:
        for pair in pairs:
            if pair[0] == base_arg or pair[1] == base_arg:
                return pair, None
        return None, ("base locale %r not found among: %s"
                      % (base_arg, ", ".join(sorted(p[0] for p in pairs))))
    for pair in pairs:
        if pair[0] == "en":
            return pair, None
    return pairs[0], None


def run(pairs, base_pair):
    """Lint all locales. Returns (findings, fatal_errors)."""
    findings = []
    fatals = []
    loaded = {}
    for locale, path in pairs:
        data, file_findings, fatal = load_locale(path, locale)
        if fatal:
            fatals.append(fatal)
        else:
            loaded[path] = (locale, data)
            findings.extend(file_findings)
    base_locale, base_path = base_pair
    base_entry = loaded.get(base_path)
    if base_entry is None or base_entry[1] is None:
        return findings, fatals
    base_data = base_entry[1]
    for locale, path in pairs:
        entry = loaded.get(path)
        if entry is None or entry[1] is None:
            continue
        compare(path, locale, base_data, entry[1], base_locale, "", findings)
    return findings, fatals


def format_text(findings):
    lines = []
    for f in sorted(findings, key=lambda x: (x["file"], x["severity"],
                                             x["rule"], x["key"] or "")):
        key = " %s:" % f["key"] if f["key"] else ""
        lines.append("%s: %s [%s]%s %s"
                     % (f["file"], f["severity"], f["rule"], key, f["message"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="l10n_check",
        description="Lint JSON locale files against a base locale.")
    parser.add_argument("paths", nargs="+",
                        help="JSON files and/or directories of *.json locales")
    parser.add_argument("--base", metavar="LOCALE",
                        help="base locale name or path "
                             "(default: the file named en.json, else the first)")
    parser.add_argument("--json", action="store_true",
                        help="emit findings as JSON")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 on warnings too")
    args = parser.parse_args(argv)

    pairs, fatal = collect_files(args.paths)
    if fatal:
        print("l10n_check: %s" % fatal, file=sys.stderr)
        return 2
    base_pair, fatal = pick_base(pairs, args.base)
    if fatal:
        print("l10n_check: %s" % fatal, file=sys.stderr)
        return 2
    findings, fatals = run(pairs, base_pair)
    for msg in fatals:
        print("l10n_check: %s" % msg, file=sys.stderr)
    if fatals:
        return 2

    errors = sum(1 for f in findings if f["severity"] == SEVERITY_ERROR)
    warnings = sum(1 for f in findings if f["severity"] == SEVERITY_WARNING)
    if args.json:
        print(json.dumps({
            "base": base_pair[0],
            "files": [p[1] for p in pairs],
            "errors": errors,
            "warnings": warnings,
            "findings": findings,
        }, indent=2, ensure_ascii=False))
    elif findings:
        print(format_text(findings))
    if errors or (args.strict and warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
