"""Validator and scaffolder tests for the live-site design handoff format.

The two committed fixture packages were built from real capture-script runs
against the synthetic pages in tests/fixtures/live-site/*/source. Each failing
case below copies a fixture, breaks one rule, and checks the validator names
it. CI needs no browser: it validates the committed packages.
"""

import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
ROOT = SKILL.parent
SCRIPT = SKILL / "scripts" / "design-handoff.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "live-site"
READY = FIXTURES / "northlight" / "package"
BLOCKED = FIXTURES / "tidewell" / "package"
FIGMA_VALIDATOR = ROOT / "next-theme-figma" / "scripts" / "theme-figma.js"


def png(width, height):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\x00" * (width * 3) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def run(*args, script=SCRIPT):
    return subprocess.run([sys.executable, str(script), *map(str, args)], text=True, capture_output=True)


class PackageCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def copy(self, source=READY):
        target = self.root / source.parent.name
        shutil.copytree(source, target)
        return target

    def load(self, package, name):
        return json.loads((package / name).read_text(encoding="utf-8"))

    def save(self, package, name, body):
        (package / name).write_text(json.dumps(body, indent=2), encoding="utf-8")

    def edit(self, package, name, change):
        body = self.load(package, name)
        change(body)
        self.save(package, name, body)

    def validate(self, package, *args):
        return run("validate", package, *args)

    def assert_invalid(self, package, *fragments):
        result = self.validate(package)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("STRUCTURE: INVALID", result.stdout)
        for fragment in fragments:
            self.assertIn(fragment, result.stdout)
        return result


class CommittedFixturesTest(PackageCase):
    def test_ready_fixture_is_valid_and_ready(self):
        result = self.validate(READY, "--require-ready")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("STRUCTURE: VALID", result.stdout)
        self.assertIn("READINESS: READY", result.stdout)

    def test_blocked_fixture_is_valid_not_ready(self):
        result = self.validate(BLOCKED)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("STRUCTURE: VALID", result.stdout)
        self.assertIn("READINESS: NOT READY (3 of 6 intended section(s) blocked)", result.stdout)
        self.assertIn("BLOCKED home/strip\n    blocker: copy strip_text is unresolved", result.stdout)
        self.assertIn("asset intro-wave has no stated treatment", result.stdout)
        self.assertIn("divergence cold-claim is unresolved", result.stdout)
        self.assertIn("BLOCKED home/faq\n    blocker: gap faq-answers-hidden", result.stdout)
        self.assertIn("READY   home/trio", result.stdout)
        self.assertIn("surface to operator: copy buy_price: omit", result.stdout)
        self.assertNotIn("surface to operator: copy strip_text: unresolved", result.stdout)
        required = self.validate(BLOCKED, "--require-ready")
        self.assertEqual(required.returncode, 3)

    def test_report_separates_structure_from_readiness(self):
        report_path = self.root / "report.json"
        self.validate(BLOCKED, "--report", report_path)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["schema_version"], "next-theme-dev/design-handoff-report/v1")
        self.assertTrue(report["structure"]["valid"])
        self.assertFalse(report["readiness"]["ready"])
        blocked = {entry["section_id"] for entry in report["readiness"]["sections"] if not entry["ready"]}
        self.assertEqual(blocked, {"strip", "intro", "faq"})

    def test_fixtures_use_different_section_names_and_markup(self):
        ready = {entry["section_id"] for entry in self.load(READY, "sections.json")["sections"]}
        blocked = {entry["section_id"] for entry in self.load(BLOCKED, "sections.json")["sections"]}
        self.assertFalse(ready & blocked)
        northlight = (READY.parent / "source" / "index.html").read_text(encoding="utf-8")
        tidewell = (BLOCKED.parent / "source" / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="nl-hero"', northlight)
        self.assertIn('data-block="intro"', tidewell)

    def test_fixture_carries_a_below_the_fold_section_normalized_to_its_origin(self):
        geometry = self.load(READY, "geometry.json")
        frame = geometry["routes"][0]["viewports"]["desktop"]
        specs = next(entry for entry in frame["sections"] if entry["section_id"] == "specs")
        self.assertGreater(specs["box"]["y"], 900)
        captures = {entry["capture_id"]: entry for entry in self.load(READY, "captures.json")["captures"]}
        raw = captures[specs["capture_id"]]["boxes"]
        title = next(entry for entry in specs["elements"] if entry["element_id"] == "title")
        self.assertAlmostEqual(title["box"]["y"], raw["specs::title"]["y"] - raw["specs"]["y"], places=1)
        self.assertLess(title["box"]["y"], 200)

    def test_fixture_carries_a_reference_only_asset_without_a_local_file(self):
        assets = {entry["asset_id"]: entry for entry in self.load(READY, "assets.json")["assets"]}
        video = assets["demo-video"]
        self.assertEqual(video["state"], "reference-only")
        self.assertNotIn("local_path", video)

    def test_fixtures_carry_motion_and_media(self):
        behaviors = {entry["behavior_id"]: entry for entry in self.load(BLOCKED, "behaviors.json")["behaviors"]}
        spin = behaviors["badge-spin"]
        self.assertEqual((spin["kind"], spin["evidence"]), ("motion", "extracted"))
        raw = json.loads((BLOCKED / "captures" / "raw" / "home-desktop-motion.json").read_text(encoding="utf-8"))
        animation = next(entry for entry in raw["animations"] if entry["name"] == "badge-spin")
        self.assertEqual(animation["duration_ms"], spin["details"]["duration_ms"])
        media = {entry["behavior_id"]: entry for entry in self.load(READY, "behaviors.json")["behaviors"]}
        self.assertEqual(media["demo-video-claim"]["claims"][0]["time_s"], 2.5)

    def test_figma_validator_does_not_accept_a_live_site_package(self):
        result = subprocess.run(["node", str(FIGMA_VALIDATOR), "validate-package", str(READY)],
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing figma-handoff.json", result.stdout + result.stderr)

    def test_live_site_validator_does_not_accept_a_figma_package(self):
        package = self.root / "figma"
        package.mkdir()
        (package / "figma-handoff.json").write_text("{}", encoding="utf-8")
        result = self.validate(package)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing design-handoff.json", result.stdout)


class ValidatorRuleTest(PackageCase):
    """One failing fixture per rule in the plan's acceptance list."""

    def test_required_width_with_neither_screenshot_nor_gap(self):
        package = self.copy()
        self.edit(package, "captures.json", lambda body: body.__setitem__("captures", [
            entry for entry in body["captures"] if entry["viewport"] != "tablet"]))
        self.assert_invalid(package, "route home: no tablet (768px) capture with a screenshot and no missing-width gap")

    def test_missing_width_gap_blocks(self):
        package = self.copy()
        self.edit(package, "captures.json", lambda body: body.__setitem__("captures", [
            entry for entry in body["captures"] if entry["viewport"] != "tablet"]))
        self.edit(package, "sections.json", lambda body: [
            entry["capture_refs"].pop("tablet") for entry in body["sections"]])
        self.edit(package, "geometry.json", lambda body: body["routes"][0]["viewports"].pop("tablet"))
        self.edit(package, "behaviors.json", lambda body: [
            entry["capture_ids"].remove("home-tablet-static")
            for entry in body["behaviors"] if "home-tablet-static" in entry["capture_ids"]])
        self.edit(package, "design-handoff.json", lambda body: body["gaps"].append({
            "gap_id": "tablet-missing", "kind": "missing-width", "route_id": "home", "section_id": None,
            "viewport": "tablet", "reason": "Tablet capture failed.", "next_action": "Capture at 768px.",
            "blocks_build": True}))
        self.edit(package, "viewport-coverage.json", lambda body: body["coverage"][0].__setitem__(
            "tablet", {"capture_id": "", "source_ref": "", "preview_ref": "", "status": "gap",
                       "gap_id": "tablet-missing"}))
        result = self.validate(package)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("READINESS: NOT READY (7 of 7", result.stdout)
        self.assertIn("gap tablet-missing (missing tablet width)", result.stdout)

    def test_missing_width_gap_cannot_silently_stop_blocking(self):
        package = self.copy()
        self.edit(package, "design-handoff.json", lambda body: body["gaps"].append({
            "gap_id": "tablet-missing", "kind": "missing-width", "route_id": "home", "section_id": None,
            "viewport": "tablet", "reason": "Not needed.", "next_action": "None.", "blocks_build": False}))
        self.assert_invalid(package, "record excluded_by to exclude it")

    def test_screenshot_width_must_match_viewport_times_dpr(self):
        package = self.copy()
        (package / "captures" / "home-mobile-static.png").write_bytes(png(390, 100))
        self.assert_invalid(package, "is 390px wide; viewport_width 390 x device_pixel_ratio 2 requires 780px")

    def test_named_screenshot_must_exist(self):
        package = self.copy()
        (package / "captures" / "home-desktop-scrolled.png").unlink()
        self.assert_invalid(package, "screenshot: file not found: captures/home-desktop-scrolled.png")

    def test_downloaded_asset_path_must_resolve(self):
        package = self.copy()
        self.edit(package, "assets.json", lambda body: body["assets"][0].__setitem__("local_path", "media/missing.png"))
        self.assert_invalid(package, "hero-lamp: local_path: file not found: media/missing.png")

    def test_downloaded_asset_must_pass_media_checks(self):
        package = self.copy()
        (package / "media" / "lamp.png").write_bytes(b"not an image")
        self.assert_invalid(package, "fails the media checks: content does not match its png extension")

    def test_downloaded_asset_needs_a_reuse_statement(self):
        package = self.copy()
        self.edit(package, "design-handoff.json", lambda body: body["source"]["rights"].update(
            {"reuse": "not-stated", "statement": "", "stated_by": ""}))
        self.assert_invalid(package, "without a reuse statement the run is reference-only")

    def test_reference_only_asset_must_not_invent_a_local_path(self):
        package = self.copy()
        self.edit(package, "assets.json", lambda body: body["assets"][1].__setitem__("local_path", "media/demo.webm"))
        self.assert_invalid(package, "demo-video: a reference-only asset has no local file")

    def test_geometry_entry_without_capture_or_inferred_basis(self):
        package = self.copy()
        self.edit(package, "geometry.json", lambda body: body["routes"][0]["viewports"]["desktop"]["sections"][0]
                  .pop("capture_id"))
        self.assert_invalid(package, 'geometry.json: home.desktop.bar: needs a capture_id, or basis "inferred"')

    def test_geometry_entry_citing_unknown_capture(self):
        package = self.copy()
        self.edit(package, "geometry.json", lambda body: body["routes"][0]["viewports"]["desktop"]["sections"][0]
                  .__setitem__("capture_id", "home-desktop-nope"))
        self.assert_invalid(package, "capture_id 'home-desktop-nope' is not in captures.json")

    def test_inferred_geometry_needs_reason_and_notes_listing(self):
        package = self.copy()

        def infer(body):
            element = body["routes"][0]["viewports"]["desktop"]["sections"][2]["elements"][0]
            element.pop("capture_id")
            element["basis"] = "inferred"
        self.edit(package, "geometry.json", infer)
        result = self.assert_invalid(package, "an inferred entry records its reason")
        self.assertIn("inferred entry geometry:home/desktop/hero::title is not listed in notes.md", result.stdout)
        self.edit(package, "geometry.json", lambda body: body["routes"][0]["viewports"]["desktop"]["sections"][2]
                  ["elements"][0].__setitem__("reason", "Measured by eye."))
        with (package / "notes.md").open("a", encoding="utf-8") as notes:
            notes.write("\n- `geometry:home/desktop/hero::title`: measured by eye.\n")
        self.assertEqual(self.validate(package).returncode, 0)

    def test_style_entry_without_capture_or_inferred_basis(self):
        package = self.copy()
        self.edit(package, "observed-styles.json", lambda body: body["styles"][0].pop("capture_id"))
        self.assert_invalid(package, 'observed-styles.json: hero-title-desktop-font-family: needs a capture_id')

    def test_style_entry_must_match_the_computed_value(self):
        package = self.copy()
        self.edit(package, "observed-styles.json", lambda body: body["styles"][1].__setitem__("value", "99px"))
        self.assert_invalid(package, "differs from the computed font-size")

    def test_observed_styles_are_not_variables(self):
        package = self.copy()
        self.edit(package, "observed-styles.json", lambda body: body["styles"][0].__setitem__("variable", "--brand"))
        self.assert_invalid(package, "observed values are not variables; remove variable")

    def test_captured_copy_entry_without_valid_capture_id(self):
        package = self.copy()
        self.edit(package, "copy.json", lambda body: body["strings"][0].pop("capture_id"))
        self.assert_invalid(package, "copy.json: bar_text: missing capture_id")

    def test_copy_source_text_must_be_what_the_capture_recorded(self):
        package = self.copy()
        self.edit(package, "copy.json", lambda body: body["strings"][2].__setitem__("source_text", "Invented heading"))
        self.assert_invalid(package, "source_text is not in the text capture")

    def test_copy_cannot_be_inferred(self):
        package = self.copy()
        self.edit(package, "copy.json", lambda body: body["strings"][2].__setitem__("basis", "inferred"))
        self.assert_invalid(package, "source copy cannot be inferred")

    def test_copy_decisions_control_the_permitted_text(self):
        cases = (
            (lambda entry: entry.update(text="Something else"), "a reuse decision permits exactly the source_text"),
            (lambda entry: entry.pop("reuse_basis"), "a reuse decision records its reuse_basis"),
            (lambda entry: entry.update(decision="omit"), 'an omit decision permits no build text; text must be ""'),
            (lambda entry: entry.update(decision="replace", text="New words"), "a replacement records approved_by"),
        )
        for change, message in cases:
            with self.subTest(message=message):
                package = self.copy()
                self.edit(package, "copy.json", lambda body: change(
                    next(entry for entry in body["strings"] if entry["copy_id"] == "hero_title")))
                self.assert_invalid(package, message)
                shutil.rmtree(package)

    def test_capture_reference_for_the_wrong_viewport(self):
        package = self.copy()
        self.edit(package, "geometry.json", lambda body: body["routes"][0]["viewports"]["desktop"]["sections"][0]
                  .__setitem__("capture_id", "home-mobile-static"))
        self.assert_invalid(package, "capture home-mobile-static is a mobile capture, not desktop")

    def test_capture_reference_for_the_wrong_route(self):
        package = self.copy()

        def add_route(body):
            body["routes"].append({"route_id": "about", "source_url": "https://northlight.example.test/about/",
                                   "storefront_path": "/pages/about", "theme_template": "templates/page.html",
                                   "section_order": []})
        self.edit(package, "routes.json", add_route)
        self.edit(package, "captures.json", lambda body: body["captures"][5].__setitem__("route_id", "about"))
        result = self.assert_invalid(package, "capture home-desktop-video-frame is for route 'about', not 'home'")
        self.assertIn("route about: no tablet (768px) capture", result.stdout)

    def test_geometry_capture_must_be_static(self):
        package = self.copy()
        self.edit(package, "geometry.json", lambda body: body["routes"][0]["viewports"]["desktop"]["sections"][0]
                  .__setitem__("capture_id", "home-desktop-motion"))
        self.assert_invalid(package, "capture home-desktop-motion is in state 'motion'; expected static or interaction")

    def test_element_boxes_must_be_section_relative(self):
        package = self.copy()

        def page_relative(body):
            section = next(entry for entry in body["routes"][0]["viewports"]["desktop"]["sections"]
                           if entry["section_id"] == "specs")
            section["elements"][0]["box"]["y"] += section["box"]["y"]
        self.edit(package, "geometry.json", page_relative)
        self.assert_invalid(package, "specs::title: box must be relative to its section's top-left corner")

    def test_figma_only_fields_in_a_live_site_package(self):
        package = self.copy()
        self.edit(package, "sections.json", lambda body: body["sections"][0].__setitem__(
            "figma_names", {"desktop": "hero1-desktop"}))
        self.edit(package, "geometry.json", lambda body: body["routes"][0]["viewports"]["desktop"]
                  .__setitem__("frame_node_id", "10:1"))
        self.assert_invalid(package, "sections.json: Figma-only field sections[0].figma_names",
                            "geometry.json: Figma-only field routes[0].viewports.desktop.frame_node_id")

    def test_figma_package_files_in_a_live_site_package(self):
        package = self.copy()
        (package / "tokens.json").write_text("{}", encoding="utf-8")
        self.assert_invalid(package, "tokens.json found: a live-site package must not carry Figma package files")

    def test_figma_ledger_decision_in_a_live_site_package(self):
        package = self.copy()
        self.edit(package, "platform-divergence-ledger.json", lambda body: body["entries"][0].__setitem__(
            "decision", "figma-wins-with-guardrails"))
        self.assert_invalid(package, "use source-wins-with-guardrails")

    def test_spark_section_without_roster_status(self):
        package = self.copy()
        self.edit(package, "sections.json", lambda body: body["sections"][2].__setitem__("roster_status", ""))
        self.assert_invalid(package, "sections.json: hero: missing roster_status")

    def test_roster_status_must_agree_with_the_roster(self):
        package = self.copy()
        self.edit(package, "sections.json", lambda body: body["sections"][2].__setitem__("roster_status", "unshipped"))
        self.assert_invalid(package, 'roster_status "unshipped" contradicts the roster (section_hero is shipped)')

    def test_static_capture_taken_while_motion_ran(self):
        package = self.copy(BLOCKED)
        self.edit(package, "captures.json", lambda body: body["captures"][0].__setitem__("motion", "running"))
        self.assert_invalid(package, "a static capture must be taken with motion paused")

    def test_extracted_motion_needs_a_motion_capture(self):
        package = self.copy(BLOCKED)
        self.edit(package, "behaviors.json", lambda body: body["behaviors"][0].__setitem__(
            "capture_ids", ["home-desktop-static"]))
        self.assert_invalid(package, "extracted motion cites a capture taken while motion ran")

    def test_empty_behavior_captures_give_one_error(self):
        package = self.copy(BLOCKED)
        self.edit(package, "behaviors.json", lambda body: body["behaviors"][0].__setitem__("capture_ids", []))
        result = self.assert_invalid(package, "capture_ids must cite at least one capture")
        self.assertNotIn("extracted motion cites a capture taken while motion ran", result.stdout)

    def test_source_url_needs_a_scheme_and_host(self):
        for url in ("https://", "http:// ", "ftp://example.test/", "example.test/page"):
            with self.subTest(url=url):
                package = self.copy()
                self.edit(package, "design-handoff.json", lambda body: body["source"].__setitem__("urls", [url]))
                self.assert_invalid(package, "must be an http:// or https:// URL with a host")
                shutil.rmtree(package)

    def test_video_claim_needs_a_media_frame_capture_at_its_time(self):
        package = self.copy()
        self.edit(package, "behaviors.json", lambda body: body["behaviors"][3]["claims"][0].__setitem__("time_s", 1.0))
        self.assert_invalid(package, "is at 2.5s, not the claim time 1.0s")

    def test_divergence_needs_source_evidence(self):
        package = self.copy()
        self.edit(package, "platform-divergence-ledger.json", lambda body: body["entries"][0].__setitem__(
            "capture_ids", []))
        self.assert_invalid(package, "capture_ids must cite the captures that show the source side")


class ReadinessTest(PackageCase):
    def test_documented_gap_stays_visible_and_blocks_its_section(self):
        package = self.copy()
        self.edit(package, "design-handoff.json", lambda body: body["gaps"].append({
            "gap_id": "price-source", "kind": "commerce", "route_id": "home", "section_id": "offer",
            "viewport": None, "reason": "Store prices not yet confirmed.", "next_action": "Read the catalog.",
            "blocks_build": True}))
        result = self.validate(package)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("NOT READY (1 of 7", result.stdout)
        self.assertIn("BLOCKED home/offer\n    blocker: gap price-source (commerce)", result.stdout)

    def test_unresolved_divergence_blocks_the_sections_it_names(self):
        package = self.copy()
        self.edit(package, "platform-divergence-ledger.json", lambda body: body["entries"][0].update(
            {"decision": "needs-approval", "status": "open"}))
        result = self.validate(package)
        self.assertIn("blocker: divergence delivery-promise is unresolved (needs-approval/open)", result.stdout)

    def test_excluded_section_leaves_readiness(self):
        package = self.copy(BLOCKED)
        self.edit(package, "sections.json", lambda body: [entry.update(
            {"in_scope": False, "exclusion_reason": "Not in this build."})
            for entry in body["sections"] if entry["section_id"] in ("strip", "intro", "faq")])
        result = self.validate(package, "--require-ready")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("EXCLUDED home/strip: Not in this build.", result.stdout)

    def test_each_blocker_is_listed_once(self):
        report_path = self.root / "report.json"
        result = self.validate(BLOCKED, "--report", report_path)
        self.assertEqual(result.stdout.count("strip_text"), 1, result.stdout)
        self.assertEqual(result.stdout.count("intro-wave"), 1, result.stdout)
        sections = {entry["section_id"]: entry for entry in
                    json.loads(report_path.read_text(encoding="utf-8"))["readiness"]["sections"]}
        for entry in sections.values():
            for item in entry["surface"]:
                self.assertFalse(any(item.split(":")[0] in blocker for blocker in entry["blockers"]), item)

    def test_every_section_excluded_reports_no_in_scope_sections(self):
        package = self.copy(BLOCKED)
        self.edit(package, "sections.json", lambda body: [entry.update(
            {"in_scope": False, "exclusion_reason": "Not in this build."}) for entry in body["sections"]])
        report_path = self.root / "report.json"
        result = self.validate(package, "--report", report_path)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("READINESS: NOT READY (no in-scope sections)", result.stdout)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["readiness"]["reason"], "no in-scope sections")

    def test_intake_only_package_is_never_ready(self):
        package = self.root / "intake"
        created = run("new", "--out", package, "--project", "intake", "--source-url", "https://example.test/",
                      "--mode", "intake-only", "--owner", "not stated")
        self.assertEqual(created.returncode, 0, created.stderr)
        result = self.validate(package)
        self.assertEqual(result.returncode, 1)
        self.assertIn("an intake-only package must name the gaps that stop capture", result.stdout)
        self.edit(package, "design-handoff.json", lambda body: body["gaps"].append({
            "gap_id": "not-rendered", "kind": "input", "route_id": "home", "section_id": None, "viewport": None,
            "reason": "The supplied HTML references assets that were not supplied.",
            "next_action": "Supply the assets or a live URL.", "blocks_build": True}))
        result = self.validate(package)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("NOT READY (intake-only package", result.stdout)


class ScaffolderTest(PackageCase):
    def test_new_writes_every_file_with_its_schema(self):
        package = self.root / "blank"
        result = run("new", "--out", package, "--project", "blank", "--source-url", "https://example.test/",
                     "--routes", "/,/pages/about", "--theme-family", "spark")
        self.assertEqual(result.returncode, 0, result.stderr)
        handoff = self.load(package, "design-handoff.json")
        self.assertEqual(handoff["schema_version"], "next-theme-dev/design-handoff/v1")
        self.assertEqual(handoff["source"]["kind"], "live-site")
        self.assertEqual(handoff["source"]["rights"]["reuse"], "not-stated")
        self.assertEqual(handoff["target"]["runtime_contract"], "web-components")
        self.assertEqual([route["route_id"] for route in self.load(package, "routes.json")["routes"]],
                         ["home", "pages-about"])
        for name, schema in (
            ("captures.json", "next-theme-dev/handoff-captures/v1"),
            ("geometry.json", "next-theme-dev/handoff-geometry/v1"),
            ("copy.json", "next-theme-dev/handoff-copy/v1"),
            ("observed-styles.json", "next-theme-dev/handoff-observed-styles/v1"),
            ("behaviors.json", "next-theme-dev/handoff-behaviors/v1"),
        ):
            self.assertEqual(self.load(package, name)["schema_version"], schema)
        self.assertTrue((package / "captures" / "raw").is_dir())
        self.assertIn("## Inferred entries", (package / "notes.md").read_text(encoding="utf-8"))
        blank = self.validate(package)
        self.assertEqual(blank.returncode, 1)
        self.assertIn("no desktop (1440px) capture", blank.stdout)

    def test_new_refuses_to_overwrite_without_force(self):
        package = self.root / "blank"
        self.assertEqual(run("new", "--out", package, "--project", "blank").returncode, 0)
        again = run("new", "--out", package, "--project", "blank")
        self.assertEqual(again.returncode, 2)
        self.assertIn("pass --force", again.stderr)
        self.assertEqual(run("new", "--out", package, "--project", "blank", "--force").returncode, 0)

    def test_new_refuses_an_out_path_that_is_not_a_directory(self):
        blocker = self.root / "a-file"
        blocker.write_text("not a directory", encoding="utf-8")
        for out in (blocker, blocker / "package"):
            with self.subTest(out=out):
                result = run("new", "--out", out, "--project", "p")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("design-handoff:", result.stderr)

    def test_stated_reuse_needs_the_statement(self):
        result = run("new", "--out", self.root / "p", "--project", "p", "--reuse", "reuse-allowed")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--reuse-statement is required", result.stderr)


class MissingSiblingTest(PackageCase):
    def test_missing_figma_sibling_stops(self):
        skills = self.root / "skills"
        shutil.copytree(SKILL, skills / "next-theme-dev", ignore=shutil.ignore_patterns("fixtures", "__pycache__"))
        result = run("validate", READY, script=skills / "next-theme-dev" / "scripts" / "design-handoff.py")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("the sibling skill next-theme-figma is not installed next to next-theme-dev", result.stderr)
        self.assertIn("./skills.sh install <target> next-theme-figma", result.stderr)
        self.assertIn("npx skills add NextCommerceCo/skills -g --skill next-theme-figma", result.stderr)
        self.assertNotIn("STRUCTURE", result.stdout)


if __name__ == "__main__":
    unittest.main()
