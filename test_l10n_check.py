import contextlib
import io
import json
import os
import tempfile
import unittest

import l10n_check as lc


def run_cli(argv):
    """Run main() with captured stdout/stderr. Returns (code, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = lc.main(argv)
    return code, out.getvalue(), err.getvalue()


class DirMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def write_json(self, name, obj):
        return self.write(name, json.dumps(obj, ensure_ascii=False))


class PlaceholderTests(unittest.TestCase):
    def test_icu_braces(self):
        ph = lc.extract_placeholders("Hello {name}, you have {count} items")
        self.assertEqual(ph, {"{name}": 1, "{count}": 1})

    def test_numeric_braces(self):
        self.assertEqual(lc.extract_placeholders("{0} beats {1}"),
                         {"{0}": 1, "{1}": 1})

    def test_repeated_placeholder_counts(self):
        self.assertEqual(lc.extract_placeholders("{a} and {a}"),
                         {"{a}": 2})

    def test_handlebars(self):
        self.assertEqual(lc.extract_placeholders("Hi {{user.name}}!"),
                         {"{{user.name}}": 1})

    def test_handlebars_triple(self):
        self.assertEqual(lc.extract_placeholders("{{{html}}}"),
                         {"{{html}}": 1})

    def test_handlebars_inner_brace_not_double_counted(self):
        ph = lc.extract_placeholders("{{name}}")
        self.assertEqual(ph, {"{{name}}": 1})

    def test_printf_bare(self):
        self.assertEqual(lc.extract_placeholders("%s has %d new"),
                         {"%s": 1, "%d": 1})

    def test_printf_named(self):
        self.assertEqual(lc.extract_placeholders("%(user)s owes %(amt)d"),
                         {"%(user)s": 1, "%(amt)d": 1})

    def test_printf_literal_percent_ignored(self):
        self.assertEqual(lc.extract_placeholders("100%% done"), {})

    def test_printf_width_and_precision(self):
        self.assertEqual(lc.extract_placeholders("%05d and %.2f"),
                         {"%d": 1, "%f": 1})

    def test_no_placeholders(self):
        self.assertEqual(lc.extract_placeholders("plain text"), {})

    def test_mixed_syntaxes(self):
        ph = lc.extract_placeholders("{a} {{b}} %(c)d %s")
        self.assertEqual(ph, {"{a}": 1, "{{b}}": 1, "%(c)d": 1, "%s": 1})

    def test_empty_string(self):
        self.assertEqual(lc.extract_placeholders(""), {})


class CategoryTests(unittest.TestCase):
    def test_bool_is_not_number(self):
        self.assertEqual(lc._category(True), "boolean")
        self.assertEqual(lc._category(1), "number")

    def test_categories(self):
        self.assertEqual(lc._category("x"), "string")
        self.assertEqual(lc._category(1.5), "number")
        self.assertEqual(lc._category([]), "array")
        self.assertEqual(lc._category({}), "object")
        self.assertEqual(lc._category(None), "null")


class DuplicateKeyTests(DirMixin, unittest.TestCase):
    def lint_one(self, text):
        path = self.write("en.json", text)
        data, findings, fatal = lc.load_locale(path, "en")
        self.assertIsNone(fatal)
        return data, findings

    def test_duplicate_top_level(self):
        data, findings = self.lint_one('{"a": 1, "a": 2}')
        self.assertEqual([f["rule"] for f in findings], ["duplicate-key"])
        self.assertEqual(findings[0]["severity"], "error")
        self.assertEqual(findings[0]["key"], "a")
        self.assertEqual(data["a"], 2)

    def test_duplicate_nested(self):
        _, findings = self.lint_one('{"a": {"b": 1, "b": 2}}')
        self.assertEqual(findings[0]["key"], "a.b")

    def test_duplicate_inside_array(self):
        _, findings = self.lint_one('{"a": [{"x": 1, "x": 2}]}')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule"], "duplicate-key")

    def test_same_name_in_different_objects_ok(self):
        _, findings = self.lint_one('{"a": {"k": 1}, "b": {"k": 2}}')
        self.assertEqual(findings, [])


class LoadTests(DirMixin, unittest.TestCase):
    def test_bad_json(self):
        path = self.write("en.json", "{not json")
        data, findings, fatal = lc.load_locale(path, "en")
        self.assertIsNone(data)
        self.assertIsNone(fatal)
        self.assertEqual(findings[0]["rule"], "bad-json")
        self.assertEqual(findings[0]["severity"], "error")

    def test_top_level_array(self):
        path = self.write("en.json", "[1, 2]")
        data, findings, fatal = lc.load_locale(path, "en")
        self.assertIsNone(data)
        self.assertEqual(findings[0]["rule"], "not-object")

    def test_top_level_string(self):
        path = self.write("en.json", '"hello"')
        _, findings, _ = lc.load_locale(path, "en")
        self.assertEqual(findings[0]["rule"], "not-object")

    def test_missing_file_is_fatal(self):
        _, _, fatal = lc.load_locale(os.path.join(self.dir, "nope.json"), "en")
        self.assertIsNotNone(fatal)

    def test_not_utf8_is_fatal(self):
        path = os.path.join(self.dir, "en.json")
        with open(path, "wb") as fh:
            fh.write(b'{"a": "\xff\xfe"}')
        _, _, fatal = lc.load_locale(path, "en")
        self.assertIsNotNone(fatal)

    def test_bom_accepted(self):
        path = os.path.join(self.dir, "en.json")
        with open(path, "wb") as fh:
            fh.write(b'\xef\xbb\xbf{"a": 1}')
        data, findings, fatal = lc.load_locale(path, "en")
        self.assertIsNone(fatal)
        self.assertEqual(data, {"a": 1})


class CompareTests(unittest.TestCase):
    def compare(self, base, other):
        findings = []
        lc.compare("fr.json", "fr", base, other, "en", "", findings)
        return findings

    def rules(self, findings):
        return sorted(f["rule"] for f in findings)

    def test_identical_string_is_untranslated(self):
        f = self.compare({"a": "Hello"}, {"a": "Hello"})
        self.assertEqual(self.rules(f), ["untranslated"])

    def test_translated_string_clean(self):
        self.assertEqual(self.compare({"a": "Hello"}, {"a": "Bonjour"}), [])

    def test_missing_key(self):
        f = self.compare({"a": "x", "b": "y"}, {"a": "z"})
        self.assertEqual(self.rules(f), ["missing-key"])
        miss = [x for x in f if x["rule"] == "missing-key"][0]
        self.assertEqual(miss["severity"], "error")
        self.assertEqual(miss["key"], "b")

    def test_extra_key(self):
        f = self.compare({"a": "x"}, {"a": "z", "b": "w"})
        self.assertEqual(self.rules(f), ["extra-key"])
        self.assertEqual(f[0]["severity"], "warning")

    def test_missing_subtree_reported_once(self):
        base = {"menu": {"file": "File", "edit": "Edit", "help": "Help"}}
        f = self.compare(base, {})
        self.assertEqual(self.rules(f), ["missing-key"])
        self.assertEqual(f[0]["key"], "menu")

    def test_nested_missing_key(self):
        base = {"menu": {"file": "File", "edit": "Edit"}}
        other = {"menu": {"file": "Fichier"}}
        f = self.compare(base, other)
        self.assertEqual(self.rules(f), ["missing-key"])
        self.assertEqual(f[0]["key"], "menu.edit")

    def test_container_mismatch_base_object(self):
        f = self.compare({"a": {"b": "x"}}, {"a": "x"})
        self.assertEqual(self.rules(f), ["container-mismatch"])
        self.assertEqual(f[0]["severity"], "error")

    def test_container_mismatch_base_leaf(self):
        f = self.compare({"a": "x"}, {"a": {"b": "x"}})
        self.assertEqual(self.rules(f), ["container-mismatch"])

    def test_type_mismatch_string_vs_number(self):
        f = self.compare({"a": "5"}, {"a": 5})
        self.assertEqual(self.rules(f), ["type-mismatch"])
        self.assertEqual(f[0]["severity"], "warning")

    def test_type_mismatch_bool_vs_number(self):
        f = self.compare({"a": True}, {"a": 1})
        self.assertEqual(self.rules(f), ["type-mismatch"])

    def test_placeholder_missing(self):
        f = self.compare({"a": "Hi {name}"}, {"a": "Salut"})
        self.assertEqual(self.rules(f), ["placeholder-mismatch"])
        self.assertIn("{name}", f[0]["message"])

    def test_placeholder_extra(self):
        f = self.compare({"a": "Hi"}, {"a": "Salut {name}"})
        self.assertEqual(self.rules(f), ["placeholder-mismatch"])
        self.assertIn("extra", f[0]["message"])

    def test_placeholder_count_mismatch(self):
        f = self.compare({"a": "%s and %s"}, {"a": "%s et %s %s"})
        self.assertEqual(self.rules(f), ["placeholder-mismatch"])

    def test_placeholder_syntax_change_flagged(self):
        f = self.compare({"a": "Hi {name}"}, {"a": "Salut {{name}}"})
        self.assertEqual(self.rules(f), ["placeholder-mismatch"])

    def test_placeholder_match_clean(self):
        f = self.compare({"a": "Hi {name}"}, {"a": "Salut {name}"})
        self.assertEqual(f, [])

    def test_placeholder_order_free(self):
        f = self.compare({"a": "{a} then {b}"}, {"a": "{b} puis {a}"})
        self.assertEqual(f, [])

    def test_empty_value(self):
        f = self.compare({"a": "Hello"}, {"a": ""})
        self.assertEqual(self.rules(f), ["empty-value"])
        self.assertEqual(f[0]["severity"], "warning")

    def test_empty_base_and_translation_no_finding(self):
        self.assertEqual(self.compare({"a": ""}, {"a": ""}), [])

    def test_untranslated_skipped_for_non_string(self):
        self.assertEqual(self.compare({"a": 5}, {"a": 5}), [])

    def test_untranslated_skipped_for_base_locale(self):
        findings = []
        lc.compare("en.json", "en", {"a": "Hello"}, {"a": "Hello"},
                   "en", "", findings)
        self.assertEqual(findings, [])


class CollectAndBaseTests(DirMixin, unittest.TestCase):
    def test_collect_directory_sorted(self):
        self.write_json("b.json", {})
        self.write_json("a.json", {})
        self.write("notes.txt", "ignore me")
        pairs, fatal = lc.collect_files([self.dir])
        self.assertIsNone(fatal)
        self.assertEqual([p[0] for p in pairs], ["a", "b"])

    def test_collect_missing_path(self):
        _, fatal = lc.collect_files([os.path.join(self.dir, "nope")])
        self.assertIsNotNone(fatal)

    def test_collect_empty_dir(self):
        _, fatal = lc.collect_files([self.dir])
        self.assertIn("no .json files", fatal)

    def test_collect_explicit_files(self):
        p1 = self.write_json("en.json", {})
        p2 = self.write_json("fr.json", {})
        pairs, fatal = lc.collect_files([p1, p2])
        self.assertIsNone(fatal)
        self.assertEqual([p[0] for p in pairs], ["en", "fr"])

    def test_default_base_prefers_en(self):
        self.write_json("fr.json", {})
        self.write_json("en.json", {})
        pairs, _ = lc.collect_files([self.dir])
        base, fatal = lc.pick_base(pairs, None)
        self.assertEqual(base[0], "en")

    def test_default_base_first_without_en(self):
        self.write_json("de.json", {})
        self.write_json("fr.json", {})
        pairs, _ = lc.collect_files([self.dir])
        base, _ = lc.pick_base(pairs, None)
        self.assertEqual(base[0], "de")

    def test_explicit_base(self):
        self.write_json("en.json", {})
        self.write_json("fr.json", {})
        pairs, _ = lc.collect_files([self.dir])
        base, fatal = lc.pick_base(pairs, "fr")
        self.assertIsNone(fatal)
        self.assertEqual(base[0], "fr")

    def test_unknown_base_is_fatal(self):
        self.write_json("en.json", {})
        pairs, _ = lc.collect_files([self.dir])
        _, fatal = lc.pick_base(pairs, "zz")
        self.assertIsNotNone(fatal)


class CliTests(DirMixin, unittest.TestCase):
    def test_clean_tree_exit_0(self):
        self.write_json("en.json", {"a": "Hi {name}"})
        self.write_json("fr.json", {"a": "Salut {name}"})
        code, out, err = run_cli([self.dir])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_errors_exit_1(self):
        self.write_json("en.json", {"a": "Hi", "b": "Bye"})
        self.write_json("fr.json", {"a": "Salut"})
        code, out, _ = run_cli([self.dir])
        self.assertEqual(code, 1)
        self.assertIn("missing-key", out)

    def test_warnings_only_exit_0_without_strict(self):
        self.write_json("en.json", {"a": "Hi"})
        self.write_json("fr.json", {"a": "Hi"})
        code, out, _ = run_cli([self.dir])
        self.assertEqual(code, 0)
        self.assertIn("untranslated", out)

    def test_strict_warnings_exit_1(self):
        self.write_json("en.json", {"a": "Hi"})
        self.write_json("fr.json", {"a": "Hi"})
        code, _, _ = run_cli([self.dir, "--strict"])
        self.assertEqual(code, 1)

    def test_missing_path_exit_2(self):
        code, _, err = run_cli([os.path.join(self.dir, "nope")])
        self.assertEqual(code, 2)
        self.assertIn("no such file", err)

    def test_empty_dir_exit_2(self):
        code, _, _ = run_cli([self.dir])
        self.assertEqual(code, 2)

    def test_unknown_base_exit_2(self):
        self.write_json("en.json", {"a": "Hi"})
        code, _, err = run_cli([self.dir, "--base", "zz"])
        self.assertEqual(code, 2)
        self.assertIn("zz", err)

    def test_bad_json_exit_1(self):
        self.write("en.json", "{broken")
        self.write_json("fr.json", {"a": 1})
        code, out, _ = run_cli([self.dir])
        self.assertEqual(code, 1)
        self.assertIn("bad-json", out)

    def test_unreadable_still_lints_others(self):
        self.write_json("en.json", {"a": "Hi", "b": "Yo"})
        self.write_json("fr.json", {"a": "Salut"})
        self.write("de.json", "{broken")
        code, out, _ = run_cli([self.dir])
        self.assertEqual(code, 1)
        self.assertIn("bad-json", out)
        self.assertIn("missing-key", out)

    def test_json_output_shape(self):
        self.write_json("en.json", {"a": "Hi", "b": "Yo"})
        self.write_json("fr.json", {"a": "Salut"})
        code, out, _ = run_cli([self.dir, "--json"])
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual(report["base"], "en")
        self.assertEqual(report["errors"], 1)
        self.assertEqual(report["warnings"], 0)
        self.assertEqual(len(report["findings"]), 1)
        f = report["findings"][0]
        self.assertEqual(f["rule"], "missing-key")
        self.assertEqual(f["key"], "b")
        self.assertEqual(f["locale"], "fr")

    def test_json_output_clean(self):
        self.write_json("en.json", {"a": "Hi {n}"})
        self.write_json("fr.json", {"a": "Salut {n}"})
        code, out, _ = run_cli([self.dir, "--json"])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["findings"], [])

    def test_single_file_self_check(self):
        path = self.write("en.json", '{"a": "", "a": 1}')
        code, out, _ = run_cli([path])
        self.assertEqual(code, 1)
        self.assertIn("duplicate-key", out)

    def test_base_empty_value_flagged_via_translation(self):
        self.write_json("en.json", {"a": "Hi"})
        self.write_json("fr.json", {"a": ""})
        code, out, _ = run_cli([self.dir])
        self.assertEqual(code, 0)
        self.assertIn("empty-value", out)
        code, _, _ = run_cli([self.dir, "--strict"])
        self.assertEqual(code, 1)

    def test_explicit_base_flag(self):
        self.write_json("en.json", {"a": "Hi"})
        self.write_json("fr.json", {"a": "Salut", "b": "Yo"})
        code, out, _ = run_cli([self.dir, "--base", "fr"])
        self.assertEqual(code, 1)
        self.assertIn("missing-key", out)
        self.assertIn("'fr'", out)

    def test_multiple_dirs(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        self.write_json("en.json", {"a": "Hi"})
        with open(os.path.join(other.name, "fr.json"), "w") as fh:
            json.dump({"a": "Salut", "b": "extra"}, fh)
        code, out, _ = run_cli([self.dir, other.name])
        self.assertEqual(code, 0)
        self.assertIn("extra-key", out)

    def test_text_output_format(self):
        self.write_json("en.json", {"a": {"b": "Hi"}})
        self.write_json("fr.json", {"a": {}})
        _, out, _ = run_cli([self.dir])
        line = out.strip().splitlines()[0]
        self.assertRegex(
            line,
            r"fr\.json: error \[missing-key\] a\.b: present in base locale 'en'")


if __name__ == "__main__":
    unittest.main()
