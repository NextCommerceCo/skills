"""Validator contract for the Figma variables manifest."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIGMA = ROOT / "next-theme-figma"
FIXTURE = FIGMA / "tests" / "fixtures" / "complete-package.json"
VALIDATOR = FIGMA / "scripts" / "theme-figma.js"


class TokensManifestTest(unittest.TestCase):
    def load_fixture(self):
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def materialize(self, root, fixture):
        package = Path(root) / "handoff"
        package.mkdir(parents=True)
        files = {
            "figma-handoff.json": fixture["handoff"],
            "routes.json": fixture["routes"],
            "sections.json": fixture["sections"],
            "assets.json": fixture["assets"],
            "platform-divergence-ledger.json": fixture["divergence"],
            "viewport-coverage.json": fixture["coverage"],
        }
        for optional in ("geometry", "copy", "tokens"):
            if optional in fixture:
                files[f"{optional}.json"] = fixture[optional]
        for filename, body in files.items():
            (package / filename).write_text(json.dumps(body), encoding="utf-8")
        (package / "validation-checklist.md").write_text(
            "# Validation checklist\n", encoding="utf-8"
        )
        references = []
        for route in fixture["routes"]["routes"]:
            references.extend(route.get("reference_screenshots", {}).values())
        for entry in fixture["coverage"]["coverage"]:
            for name in ("desktop", "tablet", "mobile"):
                viewport = entry.get(name)
                if isinstance(viewport, dict):
                    references.extend(
                        viewport.get(field) for field in ("figma_ref", "preview_ref")
                    )
        for reference in filter(None, references):
            target = package / reference
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"reference")
        return package

    def validate(self, package, *args):
        return subprocess.run(
            ["node", str(VALIDATOR), "validate-package", str(package), *args],
            text=True,
            capture_output=True,
        )

    def run_case(self, mutate, *args):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            mutate(fixture)
            package = self.materialize(temp, fixture)
            return self.validate(package, *args)

    def test_complete(self):
        result = self.run_case(lambda fixture: None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "tokens: 7 total, 5 theme-setting, 1 css-custom-property, "
            "0 one-off, 1 unmapped; names: 6 canonical, 0 alias, 1 unknown",
            result.stdout,
        )

    def test_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            package = self.materialize(temp, fixture)
            (package / "tokens.json").unlink()
            result = self.validate(package)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing tokens.json: implementation-handoff", result.stdout)
        self.assertIn("manifests.tokens names a missing tokens.json", result.stdout)

    def test_declared_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            fixture["handoff"]["mode"] = "handoff-prep"
            package = self.materialize(temp, fixture)
            (package / "tokens.json").unlink()
            result = self.validate(package)
        self.assertEqual(result.returncode, 1)
        self.assertIn("manifests.tokens names a missing tokens.json", result.stdout)
        self.assertNotIn("implementation-handoff packages", result.stdout)

    def test_prep_warning(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            fixture["handoff"]["mode"] = "handoff-prep"
            del fixture["handoff"]["manifests"]["tokens"]
            del fixture["tokens"]
            package = self.materialize(temp, fixture)
            result = self.validate(package)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("tokens.json not present", result.stdout)

    def test_pointer(self):
        def mutate(fixture):
            fixture["handoff"]["manifests"]["tokens"] = "other.json"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('manifests.tokens must be "tokens.json"', result.stdout)

    def test_source(self):
        def mutate(fixture):
            fixture["tokens"]["source"] = "operator-transcribed"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("source must be one of figma-variables", result.stdout)

    def test_top_shapes(self):
        cases = (
            ("manifest-null", lambda fixture: fixture.update({"tokens": None}),
             "manifest must be an object"),
            ("manifest-false", lambda fixture: fixture.update({"tokens": False}),
             "manifest must be an object"),
            ("manifest-zero", lambda fixture: fixture.update({"tokens": 0}),
             "manifest must be an object"),
            ("manifest-string", lambda fixture: fixture.update({"tokens": ""}),
             "manifest must be an object"),
            ("manifest-list", lambda fixture: fixture.update({"tokens": []}),
             "manifest must be an object"),
            ("schema", lambda fixture: fixture["tokens"].update(
                {"schema_version": "next-theme-figma/tokens/v0"}
            ), 'schema_version must be "next-theme-figma/tokens/v1"'),
            ("sources-null", lambda fixture: fixture["tokens"].update(
                {"sources": None}
            ), "sources must be an object"),
            ("sources-list", lambda fixture: fixture["tokens"].update(
                {"sources": []}
            ), "sources must be an object"),
            ("sources-number", lambda fixture: fixture["tokens"].update(
                {"sources": 7}
            ), "sources must be an object"),
            ("tokens-object", lambda fixture: fixture["tokens"].update(
                {"tokens": {}}
            ), "tokens must be an array"),
            ("tokens-missing", lambda fixture: fixture["tokens"].pop("tokens"),
             "tokens must be an array"),
        )
        for name, mutate, diagnostic in cases:
            with self.subTest(name=name):
                result = self.run_case(mutate)
                self.assertEqual(result.returncode, 1)
                self.assertIn(diagnostic, result.stdout)

    def test_empty(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"] = []

        strict = self.run_case(mutate)
        self.assertEqual(strict.returncode, 1)
        self.assertIn("tokens.json: no tokens recorded", strict.stdout)

        loose = self.run_case(mutate, "--non-strict")
        self.assertEqual(loose.returncode, 0, loose.stdout + loose.stderr)
        self.assertIn("Warning: tokens.json: no tokens recorded", loose.stdout)
        self.assertIn("tokens: 0 total", loose.stdout)

    def test_bad_json(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            package = self.materialize(temp, fixture)
            (package / "tokens.json").write_text("{", encoding="utf-8")
            result = self.validate(package)
        self.assertEqual(result.returncode, 1)
        self.assertIn("tokens.json: invalid JSON", result.stdout)

    def test_empty_object(self):
        result = self.run_case(lambda fixture: fixture.update({"tokens": {}}))
        self.assertEqual(result.returncode, 1)
        self.assertIn('schema_version must be "next-theme-figma/tokens/v1"', result.stdout)
        self.assertIn("tokens must be an array", result.stdout)

    def test_entry_object(self):
        for value in (None, 7, []):
            with self.subTest(value=value):
                def mutate(fixture):
                    fixture["tokens"]["tokens"][0] = value

                result = self.run_case(mutate)
                self.assertEqual(result.returncode, 1)
                self.assertIn("tokens.json: token entry must be an object", result.stdout)
                self.assertNotIn("TypeError", result.stderr)

    def test_id_and_fields(self):
        def field(name, value):
            def mutate(fixture):
                fixture["tokens"]["tokens"][0][name] = value
            return mutate

        cases = (
            ("id-missing", lambda fixture: fixture["tokens"]["tokens"][0].pop("token_id"),
             "token missing token_id"),
            ("id-number", field("token_id", 7), "token missing token_id"),
            ("id-blank", field("token_id", "   "), "token missing token_id"),
            ("name-number", field("figma_name", 7),
             "figma_name must be a non-empty string"),
            ("name-blank", field("figma_name", "   "),
             "figma_name must be a non-empty string"),
            ("collection", field("collection", 7), "collection must be a string"),
            ("nodes-object", field("figma_node_ids", {}),
             "figma_node_ids must be an array of strings"),
            ("nodes-number", field("figma_node_ids", [7]),
             "figma_node_ids must be an array of strings"),
            ("notes", field("notes", 7), "notes must be a string"),
        )
        for name, mutate, diagnostic in cases:
            with self.subTest(name=name):
                result = self.run_case(mutate)
                self.assertEqual(result.returncode, 1)
                self.assertIn(diagnostic, result.stdout)

    def test_duplicate_id(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][1]["token_id"] = fixture["tokens"]["tokens"][0]["token_id"]

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate token_id", result.stdout)

    def test_bad_color(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][0]["value"] = "navy"
            fixture["tokens"]["tokens"][0].pop("observed")

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('value "navy" does not parse as color', result.stdout)

    def test_color_grammar(self):
        invalid = (
            "#GGG", "#12345", "rgb(bogus)", "rgb()", "rgb(1,2)",
            "rgb(a,b,c)", "rgba()", "rgba(1 2)", "hsl(red 50% 50%)",
            "rgb(calc(1) 2 3)", "rgb(1 2 3 / 4 / 5)",
        )
        for value in invalid:
            with self.subTest(value=value):
                def mutate(fixture):
                    token = fixture["tokens"]["tokens"][0]
                    token["value"] = value
                    token.pop("observed")

                result = self.run_case(mutate)
                self.assertEqual(result.returncode, 1)
                self.assertIn(f'value "{value}" does not parse as color', result.stdout)

    def test_color_forms(self):
        valid = (
            "rgb(1, 2, 3)", "rgb(255,255,255,0.5)",
            "rgba(1 2 3 / 50%)", "rgb(-1 -2 -3 / -0.5)",
            "hsl(120deg 50% 25%)", "hsl(120,50,50)",
            "hsla(120,50%,25%,.5)",
        )
        for value in valid:
            with self.subTest(value=value):
                def mutate(fixture):
                    token = fixture["tokens"]["tokens"][0]
                    token["value"] = value
                    token["modes"]["default"] = value
                    token["observed"] = [
                        {"source": "variable_defs", "value": value}
                    ]

                result = self.run_case(mutate)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_value_types(self):
        cases = (
            ("dimension-number", 4, 12, "dimension"),
            ("dimension-null", 4, None, "dimension"),
            ("dimension-bare", 4, "1.5", "dimension"),
            ("radius", 5, "round", "radius"),
            ("font-size", 5, "large", "font-size"),
            ("font-family", 5, "   ", "font-family"),
        )
        for name, index, value, token_type in cases:
            with self.subTest(name=name):
                def mutate(fixture):
                    token = fixture["tokens"]["tokens"][index]
                    token["type"] = token_type
                    token["value"] = value

                result = self.run_case(mutate)
                self.assertEqual(result.returncode, 1)
                shown = "null" if value is None else value
                self.assertIn(
                    f'value "{shown}" does not parse as {token_type}', result.stdout
                )

    def test_bad_dimension(self):
        invalid = ("80", "px", f"{'9' * 400}px")
        for value in invalid:
            with self.subTest(value=value):
                def mutate(fixture):
                    fixture["tokens"]["tokens"][4]["value"] = value

                result = self.run_case(mutate)
                self.assertEqual(result.returncode, 1)
                self.assertIn(
                    f'value "{value}" does not parse as dimension', result.stdout
                )

    def test_dimension_forms(self):
        for value in ("-1px", "+1.5rem", "0"):
            with self.subTest(value=value):
                def mutate(fixture):
                    token = fixture["tokens"]["tokens"][4]
                    token["value"] = value
                    token["modes"] = {"default": value}

                result = self.run_case(mutate)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bad_mode(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][4]["modes"] = {"mobile": "wide"}

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('modes.mobile "wide" does not parse as dimension', result.stdout)

    def test_mode_shape(self):
        for value in (None, 7, []):
            with self.subTest(value=value):
                def mutate(fixture):
                    fixture["tokens"]["tokens"][0]["modes"] = value

                result = self.run_case(mutate)
                self.assertEqual(result.returncode, 1)
                self.assertIn("modes must be an object", result.stdout)

    def test_mode_diff(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][0]["modes"]["dark"] = "#FFFFFF"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bad_observed(self):
        def mutate(fixture):
            token = fixture["tokens"]["tokens"][0]
            token["observed"] = [{"source": "variable_defs", "value": "navy"}]

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('observed variable_defs value "navy" does not parse as color', result.stdout)

    def test_observed_shapes(self):
        def observed(value):
            def mutate(fixture):
                fixture["tokens"]["tokens"][0]["observed"] = value
            return mutate

        cases = (
            ("container", observed({}), "observed must be an array"),
            ("member", observed([7]), "observed entry must be an object"),
            ("null-member", observed([None]), "observed entry must be an object"),
            ("array-member", observed([[]]), "observed entry must be an object"),
            ("source", observed([{"source": "other", "value": "#0F172A"}]),
             'invalid observed source "other"'),
            ("node", observed([{"source": "variable_defs", "value": "#0F172A",
                                "node_id": 7}]), "observed node_id must be a string"),
        )
        for name, mutate, diagnostic in cases:
            with self.subTest(name=name):
                result = self.run_case(mutate)
                self.assertEqual(result.returncode, 1)
                self.assertIn(diagnostic, result.stdout)
                self.assertNotIn("undefined from undefined", result.stdout)

    def test_setting_id(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][0]["target"].pop("setting_id")

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("theme-setting target requires setting_id", result.stdout)

    def test_target_shapes(self):
        def target(value):
            def mutate(fixture):
                fixture["tokens"]["tokens"][0]["target"] = value
            return mutate

        def target_field(name, value):
            def mutate(fixture):
                fixture["tokens"]["tokens"][0]["target"][name] = value
            return mutate

        cases = (
            ("missing", lambda fixture: fixture["tokens"]["tokens"][0].pop("target"),
             "target must be an object"),
            ("null", target(None), "target must be an object"),
            ("number", target(7), "target must be an object"),
            ("array", target([]), "target must be an object"),
            ("setting-syntax", target_field("setting_id", "Body Color"),
             "theme-setting target requires setting_id"),
            ("theme-css", target_field("css_var", "body-color"),
             "theme-setting target css_var is invalid"),
        )
        for name, mutate, diagnostic in cases:
            with self.subTest(name=name):
                result = self.run_case(mutate)
                self.assertEqual(result.returncode, 1)
                self.assertIn(diagnostic, result.stdout)

    def test_css_var(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][3]["target"].pop("css_var")

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("css-custom-property target requires css_var", result.stdout)

    def test_css_var_syntax(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][3]["target"]["css_var"] = "primary_color"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("css-custom-property target requires css_var", result.stdout)

    def test_setting_value(self):
        def invalid(fixture):
            fixture["tokens"]["tokens"][6]["target"]["setting_value"] = 7

        failed = self.run_case(invalid)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("setting_value must be a string", failed.stdout)

        def valid(fixture):
            fixture["tokens"]["tokens"][0]["target"]["setting_value"] = "kept"

        passed = self.run_case(valid)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)

        def unmapped(fixture):
            fixture["tokens"]["tokens"][6]["target"]["setting_id"] = "kept"

        passed = self.run_case(unmapped)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)

        for kind in ("unmapped", "one-off", "css-custom-property"):
            with self.subTest(kind=kind):
                def misrouted(fixture, kind=kind):
                    target = {"kind": kind, "setting_value": "roomy"}
                    if kind == "css-custom-property":
                        target["css_var"] = "--ribbon"
                    fixture["tokens"]["tokens"][6]["target"] = target

                failed = self.run_case(misrouted)
                self.assertEqual(failed.returncode, 1)
                self.assertIn(
                    "setting_value is only meaningful for theme-setting targets",
                    failed.stdout,
                )

    def test_functional_equivalent(self):
        same = (
            ("rgb(1, 2, 3)", "rgb(1,2,3)"),
            ("rgba(1 2 3 / 50%)", "rgba(1,2,3,0.5)"),
            ("RGB(1, 2, 3)", "rgb(1.0,2,3)"),
            ("hsl(120deg 50% 25%)", "hsl(120,50%,25%)"),
        )
        for first, second in same:
            with self.subTest(pair=(first, second)):
                def mutate(fixture, first=first, second=second):
                    token = fixture["tokens"]["tokens"][0]
                    token["value"] = first
                    token["modes"]["default"] = first
                    token["observed"] = [
                        {"source": "variable_defs", "value": first},
                        {"source": "design_context", "value": second},
                    ]

                result = self.run_case(mutate)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("designer-input-needed", result.stdout)

        def differ(fixture):
            token = fixture["tokens"]["tokens"][0]
            token["value"] = "rgb(1,2,3)"
            token["modes"]["default"] = "rgb(1,2,3)"
            token["observed"] = [
                {"source": "variable_defs", "value": "rgb(1, 2, 3)"},
                {"source": "design_context", "value": "rgb(1, 2, 4)"},
            ]

        result = self.run_case(differ)
        self.assertEqual(result.returncode, 1)
        self.assertIn("designer-input-needed", result.stdout)

    def test_bad_type(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][0]["type"] = "number"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('invalid type "number"', result.stdout)

    def test_bad_kind(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][0]["target"]["kind"] = "setting"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('invalid target.kind "setting"', result.stdout)

    def test_spark_option(self):
        def invalid(fixture):
            fixture["handoff"]["target"].update({
                "theme_family": "spark",
                "runtime_contract": "web-components",
            })
            fixture["tokens"]["tokens"][5]["value"] = "6px"

        failed = self.run_case(invalid)
        self.assertEqual(failed.returncode, 1)
        self.assertIn('"6px" is not an option of Spark setting radius_control', failed.stdout)

        def valid(fixture):
            invalid(fixture)
            fixture["tokens"]["tokens"][5]["target"]["setting_value"] = "8px"

        passed = self.run_case(valid)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)

    def test_custom_option(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][5]["value"] = "6px"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_token_conflict(self):
        def mutate(fixture):
            token = copy.deepcopy(fixture["tokens"]["tokens"][0])
            token.update({"token_id": "color.text.alt", "value": "#FFFFFF"})
            token.pop("observed")
            fixture["tokens"]["tokens"].append(token)

        for args in ((), ("--non-strict",)):
            with self.subTest(args=args):
                result = self.run_case(mutate, *args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("designer-input-needed", result.stdout)
                self.assertIn("#0F172A", result.stdout)
                self.assertIn("#FFFFFF", result.stdout)
                self.assertIn("from color.text.primary", result.stdout)
                self.assertIn("from color.text.alt", result.stdout)
                self.assertIn("the manifest never averages or drops a value", result.stdout)

    def test_observed_pair(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][0]["observed"][1]["value"] = "#FFFFFF"

        for args in ((), ("--non-strict",)):
            with self.subTest(args=args):
                result = self.run_case(mutate, *args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("designer-input-needed", result.stdout)
                self.assertIn("#0F172A", result.stdout)
                self.assertIn("#FFFFFF", result.stdout)
                self.assertIn("from variable_defs", result.stdout)
                self.assertIn("from design_context", result.stdout)

    def test_observed_value(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][0]["observed"] = [
                {"source": "design_context", "value": "#FFFFFF"}
            ]

        expected = (
            "Error: tokens.json: designer-input-needed: color/text/primary carries "
            "two values (#0F172A from value, #FFFFFF from design_context); the "
            "designer must pick one, the manifest never averages or drops a value"
        )
        for args in ((), ("--non-strict",)):
            with self.subTest(args=args):
                result = self.run_case(mutate, *args)
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stdout)

    def test_observed_three(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][0]["observed"] = [
                {"source": "variable_defs", "value": "#0F172A"},
                {"source": "design_context", "value": "#0f172a"},
                {"source": "design_context", "value": "#ABCDEF"},
            ]

        for args in ((), ("--non-strict",)):
            with self.subTest(args=args):
                result = self.run_case(mutate, *args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("#ABCDEF from design_context", result.stdout)
                self.assertIn("#0F172A from variable_defs", result.stdout)

    def test_same_name(self):
        def mutate(fixture):
            token = copy.deepcopy(fixture["tokens"]["tokens"][0])
            token["token_id"] = "color.text.alt"
            fixture["tokens"]["tokens"].append(token)

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('duplicate figma_name "color/text/primary"', result.stdout)
        self.assertNotIn("designer-input-needed", result.stdout)

    def test_hex_equivalent(self):
        def mutate(fixture):
            first = fixture["tokens"]["tokens"][0]
            first["value"] = "#0f0"
            first.pop("observed")
            token = copy.deepcopy(first)
            token.update({"token_id": "color.text.alt", "value": "#00FF00"})
            fixture["tokens"]["tokens"].append(token)

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('duplicate figma_name "color/text/primary"', result.stdout)
        self.assertNotIn("designer-input-needed", result.stdout)

    def test_trimmed_value(self):
        def mutate(fixture):
            first = fixture["tokens"]["tokens"][0]
            first.pop("observed")
            token = copy.deepcopy(first)
            token.update({"token_id": "color.text.alt", "value": " #0F172A "})
            fixture["tokens"]["tokens"].append(token)

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('duplicate figma_name "color/text/primary"', result.stdout)
        self.assertNotIn("designer-input-needed", result.stdout)

    def test_trimmed_name(self):
        def mutate(fixture):
            token = copy.deepcopy(fixture["tokens"]["tokens"][0])
            token["token_id"] = "color.text.alt"
            token["figma_name"] = " color/text/primary "
            fixture["tokens"]["tokens"].append(token)

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('duplicate figma_name "color/text/primary"', result.stdout)

    def test_type_duplicate(self):
        def mutate(fixture):
            token = copy.deepcopy(fixture["tokens"]["tokens"][0])
            token["token_id"] = "color.text.alt"
            token["type"] = "font-family"
            fixture["tokens"]["tokens"].append(token)

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('duplicate figma_name "color/text/primary"', result.stdout)

    def test_name_classes(self):
        def mutate(fixture):
            token = fixture["tokens"]["tokens"][6]
            token["figma_name"] = "text/primary"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("names: 6 canonical, 1 alias, 0 unknown", result.stdout)

    def test_new_package(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "package"
            result = subprocess.run(
                [
                    "node", str(VALIDATOR), "new-package",
                    "--out", str(out),
                    "--project", "example-store",
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            tokens = json.loads((out / "tokens.json").read_text(encoding="utf-8"))
            handoff = json.loads((out / "figma-handoff.json").read_text(encoding="utf-8"))
        self.assertEqual(tokens["source"], "figma-variables")
        self.assertEqual(tokens["tokens"], [])
        self.assertEqual(handoff["manifests"]["tokens"], "tokens.json")

    def test_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "package"
            out.mkdir()
            (out / "tokens.json").write_text("sentinel", encoding="utf-8")
            result = subprocess.run(
                [
                    "node", str(VALIDATOR), "new-package",
                    "--out", str(out),
                    "--project", "example-store",
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(
                (out / "tokens.json").read_text(encoding="utf-8"), "sentinel"
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("tokens.json", result.stderr)
        self.assertIn("refusing to overwrite", result.stderr)

    def test_fixture_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            fixture["tokens"]["tokens"][0]["notes"] = "sentinel"
            source = Path(temp) / "fixture.json"
            source.write_text(json.dumps(fixture), encoding="utf-8")
            out = Path(temp) / "package"
            result = subprocess.run(
                [
                    "node", str(VALIDATOR), "new-package",
                    "--out", str(out),
                    "--project", "example-store",
                    "--fixture", str(source),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            tokens = json.loads((out / "tokens.json").read_text(encoding="utf-8"))
        self.assertEqual(tokens, fixture["tokens"])

    def test_spark_selects(self):
        cases = (
            ("radius_control", "6px", "8px"),
            ("radius_card", "6px", "4px"),
            ("section_padding", "48px", "roomy"),
            ("content_gap", "24px", "loose"),
            ("container_max_width", "1200px", "1280px"),
            ("heading_scale", "1.2", "large"),
            ("body_size", "19px", "18px"),
        )
        for setting_id, bad, good in cases:
            with self.subTest(setting_id=setting_id):
                def invalid(fixture, setting_id=setting_id, bad=bad):
                    fixture["handoff"]["target"].update({
                        "theme_family": "spark",
                        "runtime_contract": "web-components",
                    })
                    token = fixture["tokens"]["tokens"][4]
                    token["target"] = {"kind": "theme-setting", "setting_id": setting_id}
                    token["target"]["setting_value"] = bad

                failed = self.run_case(invalid)
                self.assertEqual(failed.returncode, 1)
                self.assertIn(
                    '"{}" is not an option of Spark setting {}'.format(bad, setting_id),
                    failed.stdout,
                )

                def valid(fixture, good=good):
                    invalid(fixture)
                    fixture["tokens"]["tokens"][4]["target"]["setting_value"] = good

                passed = self.run_case(valid)
                self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)

    def test_legacy_missing(self):
        legacy = json.loads(
            (FIGMA / "tests" / "fixtures" / "legacy-v0-package.json").read_text(encoding="utf-8")
        )
        legacy["handoff"]["target"]["runtime_contract"] = "Web-Components"
        with tempfile.TemporaryDirectory() as temp:
            package = self.materialize(temp, legacy)
            (package / "platform-divergence-ledger.json").rename(
                package / "spark-divergence-ledger.json"
            )
            result = self.validate(package)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Warning: tokens.json not present", result.stdout)
        self.assertNotIn("missing tokens.json", result.stdout)

    def test_case_names(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][0]["figma_name"] = "Color/Text/Primary"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("names: 5 canonical, 0 alias, 2 unknown", result.stdout)

    def test_optional(self):
        def mutate(fixture):
            fixture["tokens"].pop("sources")
            token = fixture["tokens"]["tokens"][1]
            token.pop("collection")
            token["target"] = {"kind": "theme-setting", "setting_id": "accent_border_color"}

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("5 theme-setting", result.stdout)

    def test_one_off(self):
        def mutate(fixture):
            fixture["tokens"]["tokens"][6]["target"] = {"kind": "one-off"}

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "5 theme-setting, 1 css-custom-property, 1 one-off, 0 unmapped",
            result.stdout,
        )

    def test_more_forms(self):
        cases = (
            (6, "color", "#ABC"),
            (6, "color", "#AABBCCDD"),
            (6, "font-family", "Inter, sans-serif"),
            (6, "font-size", "1.25rem"),
            (4, "dimension", "2em"),
            (4, "dimension", "50%"),
            (4, "dimension", "10vw"),
            (4, "dimension", "3vh"),
        )
        for index, kind, value in cases:
            with self.subTest(value=value):
                def mutate(fixture, index=index, kind=kind, value=value):
                    token = fixture["tokens"]["tokens"][index]
                    token["type"] = kind
                    token["value"] = value
                    token.pop("modes", None)
                    token.pop("observed", None)

                result = self.run_case(mutate)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
