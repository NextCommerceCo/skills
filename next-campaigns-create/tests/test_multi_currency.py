"""Unit tests for multi-currency campaigns in scripts/campaign_admin.py (no network).

A campaign can be offered in currencies beside its own. These tests hold the
engine to the Campaigns API's rules for them: a package or shipping method create
fills every additional currency by forex from the campaign-currency price, a
`prices[]` PATCH upserts only the currencies it names, adding a currency to a
campaign forex-fills it everywhere and removing one deletes its prices, and the
Cart API leaves out of a currency anything with no price in it (and answers a
currency the campaign does not offer in the campaign currency). The fixed prices
come from a --prices-file; everything here is synthetic.
"""
from __future__ import annotations

import json
import tempfile
import unittest
import urllib.parse
from decimal import Decimal
from pathlib import Path
from unittest import mock

import test_campaign_admin as base

ca = base.ca
ns = base.ns

CURRENCIES = ("USD", "EUR", "GBP", "JPY")
FOREX = {"EUR": "0.90", "GBP": "0.80", "JPY": "150"}


def mc_discovery(store=CURRENCIES, group=None, is_default=True):
    d = base.load_fixture("discovery.json")
    d["store"]["available_currencies"] = list(store)
    d["gateway_groups"][0]["currencies"] = list(store if group is None else group)
    d["gateway_groups"][0]["is_default"] = is_default
    return d


def prices_doc(post=True, **over):
    """A synthetic prices file: the Photo Bracelet hero (variants 23 to 26), the
    3D Photo Crystal Small (variant 11) as a post-purchase package priced apart
    from its variant (`post=False` drops that override, for a plan without it),
    and the store's standard shipping method."""
    doc = {"source": "synthetic test prices", "currencies": {
        "USD": {"variants": {"23": 40, "24": 40, "25": 40, "26": 40, "11": 35},
                "shipping": {"standard": "6.95"}},
        "EUR": {"variants": {"23": "44.00", "24": "44.00", "25": "44.00", "26": "44.00", "11": "39.00"},
                "packages": {"post-purchase-11": "38.00"}, "shipping": {"standard": "5.00"}},
        "GBP": {"variants": {"23": 36, "24": 36, "25": 36, "26": 36, "11": 30},
                "packages": {"post-purchase-11": 29}, "shipping": {"standard": 0}},
        "JPY": {"variants": {"23": 7000, "24": 7000, "25": 7000, "26": 7000, "11": 6000},
                "packages": {"post-purchase-11": 5800}, "shipping": {"standard": 900}},
    }}
    doc["currencies"].update(over)
    if not post:
        for entry in doc["currencies"].values():
            entry.pop("packages", None)
    return doc


class _Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write_prices(self, doc=None, name="prices.json"):
        path = self.root / name
        path.write_text(json.dumps(prices_doc() if doc is None else doc))
        return str(path)

    def recommend(self, disc=None, **kw):
        args = dict(ctc="high", anchor_price="40.00", exit="0", currency="USD",
                    additional_currencies="EUR,GBP,JPY", post_purchase=["11:35.00"], shipping=["standard:6.95"])
        args.update(kw)
        if "prices_file" not in kw:
            args["prices_file"] = self.write_prices(prices_doc(post=bool(args.get("post_purchase"))),
                                                    name="default-prices.json")
        return ca.recommend(disc or mc_discovery(), ns(**args))

    def refused(self, *needles, disc=None, **kw):
        with self.assertRaises(ca.CampaignAdminError) as cm:
            self.recommend(disc, **kw)
        for n in needles:
            self.assertIn(n, str(cm.exception))
        return str(cm.exception)


# --------------------------------------------------------------------------- #
# the fake store, currency-aware
# --------------------------------------------------------------------------- #

def _rate_price(state, price, code):
    return base._forex_price(price, code, state)


class MCTransport(base.DynamicTransport):
    """The fake Admin API with the Campaigns API's currency rules on top: creates
    forex-fill the campaign's additional currencies, a campaign PATCH that changes
    `additional_currencies` fills or deletes those prices on every package and
    shipping method, and a `prices[]` PATCH refuses a currency the campaign does not
    offer."""

    def __init__(self, disc, state, **kw):
        state.setdefault("forex", dict(FOREX))
        super().__init__(disc, state, **kw)

    def _offered(self, cid):
        c = self.state["campaigns"].get(cid) or {}
        return [c.get("currency")] + list(c.get("additional_currencies") or [])

    def _fill(self, cid, kind, item, codes):
        usd = next((r for r in item.get("prices") or [] if r.get("currency") == "USD"), None)
        for code in codes:
            if usd is None or any(r.get("currency") == code for r in item["prices"]):
                continue
            row = {"currency": code, "price": _rate_price(self.state, usd["price"], code)}
            if kind == "packages":
                row["price_recurring"] = None
            item["prices"].append(row)

    def _handle(self, method, path, base_path, parts, body):
        cid = int(parts[3]) if len(parts) >= 4 and parts[2] == "campaigns" and parts[3].isdigit() else None
        old = list((self.state["campaigns"].get(cid) or {}).get("additional_currencies") or []) if cid else []
        if (cid and method == "PATCH" and len(parts) == 6 and parts[4] in ("packages", "shipping-methods")
                and isinstance(body, dict) and body.get("prices")):
            bad = [r.get("currency") for r in body["prices"] if r.get("currency") not in self._offered(cid)]
            if bad:
                return 400, {}, json.dumps({"prices": [f"Currency '{bad[0]}' is not available for this campaign."]})
        st, headers, text = super()._handle(method, path, base_path, parts, body)
        if cid is None or st not in (200, 201):
            return st, headers, text
        if method == "POST" and len(parts) == 5 and parts[4] in ("packages", "shipping-methods"):
            resp = json.loads(text)
            item_id = (resp[0] if isinstance(resp, list) else resp)["id"]
            item = self.state[parts[4]][cid][item_id]
            self._fill(cid, parts[4], item, self._offered(cid)[1:])
            return st, headers, json.dumps([item] if isinstance(resp, list) else item)
        if method == "PATCH" and len(parts) == 4 and "additional_currencies" in (body or {}):
            new = list(body.get("additional_currencies") or [])
            for kind in ("packages", "shipping-methods"):
                for item in self.state[kind].get(cid, {}).values():
                    item["prices"] = [r for r in item.get("prices") or []
                                      if r.get("currency") == "USD" or r.get("currency") in new]
                    self._fill(cid, kind, item, [c for c in new if c not in old])
        return st, headers, text


class MCInterrupting(base.InterruptingTransport, MCTransport):
    pass


def mc_engine(state, cid, honour_currency=True):
    """carts/calculate priced from what the fake store holds, in the cart's
    currency: the store's live_engine rules, with prices read in `currency` and the
    campaign currency used for a code the campaign does not offer, as the Cart API
    does. `honour_currency=False` is a store that ignores the field."""
    def scope(o):
        return {p["id"] for p in o["condition"]["packages"]}

    def calc(path, body):
        camp = state["campaigns"][cid]
        offered = [camp["currency"]] + list(camp.get("additional_currencies") or [])
        cur = (body.get("currency") or camp["currency"]).upper() if honour_currency else camp["currency"]
        if cur not in offered:
            cur = camp["currency"]
        upsell = path.endswith("?upsell=true")
        packages = state["packages"][cid]
        live = [o for o in state["offers"].get(cid, {}).values() if o.get("available", True)]
        lines = [(l["package_id"], l["quantity"]) for l in body["lines"]]

        def met(o):
            need = o["condition"].get("value") if o["condition"]["type"] == "count" else 1
            return sum(q for i, q in lines if i in scope(o)) >= Decimal(str(need or 1))

        def pct(o, kind):
            return o.get("offer_type", "offer") == kind and o["benefit"]["type"] == "package_percentage"

        total = Decimal(0)
        for i, q in lines:
            row = next((r for r in packages[i]["prices"] if r["currency"] == cur), None)
            if row is None:
                return 500, {"detail": "server error"}
            unit = Decimal(row["price"])
            autos = [] if upsell else [o for o in live if pct(o, "offer") and i in scope(o) and met(o)]
            if autos:
                best = max(autos, key=lambda o: Decimal(o["benefit"]["value"]))
                unit = ca.landed_unit(unit, Decimal(best["benefit"]["value"]), best["benefit"].get("price_rounding"))
            for code in body.get("vouchers", []):
                v = next((o for o in live if pct(o, "voucher") and o.get("code") == code), None)
                if v and i in scope(v) and met(v):
                    unit = ca.landed_unit(unit, Decimal(v["benefit"]["value"]), v["benefit"].get("price_rounding"))
            total += unit * q
        if "shipping_method" in body:
            free = any(o.get("offer_type", "offer") == "offer" and o["benefit"]["type"] == "shipping_percentage"
                       and Decimal(o["benefit"]["value"]) == 100 and met(o) for o in live)
            if not free:
                method = state["shipping-methods"][cid][body["shipping_method"]]
                total += Decimal(next(r["price"] for r in method["prices"] if r["currency"] == cur))
        return 200, {"total": str(total), "currency": cur, "lines": []}
    return calc


def mc_listing(state, cid):
    """GET /api/v1/campaigns/?currency=: only what has a price in that currency."""
    def get(path, body):
        camp = state["campaigns"][cid]
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        cur = (q.get("currency") or [""])[0].upper() or camp["currency"]
        if cur not in [camp["currency"]] + list(camp.get("additional_currencies") or []):
            cur = camp["currency"]

        def listed(kind):
            out = []
            for i, x in state[kind].get(cid, {}).items():
                row = next((r for r in x.get("prices") or [] if r.get("currency") == cur), None)
                if row is not None:
                    out.append({"ref_id": i, "price": row["price"]})
            return out
        return 200, {"currency": cur, "packages": listed("packages"), "shipping_methods": listed("shipping-methods")}
    return get


def cart_client(state, cid, **kw):
    tt = base.FakeTransport({("POST", "/api/v1/carts/calculate/"): mc_engine(state, cid, **kw),
                             ("GET", "/api/v1/campaigns/"): mc_listing(state, cid)})
    return ca.Client(ca.CART_API_ORIGIN, "k", auth_scheme="raw", send_version_header=False,
                     transport=tt, clock=base.FakeClock(), sleep=lambda s: None), tt


# --------------------------------------------------------------------------- #
# recommend
# --------------------------------------------------------------------------- #

class AdditionalCurrencies(_Files):
    def test_fixed_prices_on_every_object(self):
        plan = self.recommend()
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertEqual(plan["campaign"]["additional_currencies"], ["EUR", "GBP", "JPY"])
        hero = [p for p in plan["packages"] if p["role"] == "hero"]
        self.assertEqual(len(hero), 4)
        for p in hero:
            self.assertEqual(p["price"], "40.00")
            self.assertEqual(p["prices"], {"EUR": "44.00", "GBP": "36.00", "JPY": "7000.00"})
        post = next(p for p in plan["packages"] if p["role"] == "post-purchase")
        # packages.<key> beats the variant's own price
        self.assertEqual(post["prices"], {"EUR": "38.00", "GBP": "29.00", "JPY": "5800.00"})
        self.assertEqual(plan["shipping_methods"][0]["prices"], {"EUR": "5.00", "GBP": "0.00", "JPY": "900.00"})
        self.assertEqual(ca.currency_coverage(plan), {"EUR": "fixed", "GBP": "fixed", "JPY": "fixed"})

    def test_only_the_listed_currencies_are_used_and_the_rest_are_named(self):
        plan = self.recommend(additional_currencies="EUR")
        self.assertEqual(plan["campaign"]["additional_currencies"], ["EUR"])
        self.assertTrue(all(set(p["prices"]) == {"EUR"} for p in plan["packages"]))
        self.assertTrue(any("also prices GBP, JPY" in r for r in plan["rationale"]), plan["rationale"])

    def test_without_a_prices_file_the_currencies_are_forex(self):
        plan = self.recommend(prices_file=None)
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertFalse(any("prices" in p for p in plan["packages"]))
        self.assertEqual(ca.currency_coverage(plan), {"EUR": "forex", "GBP": "forex", "JPY": "forex"})
        self.assertEqual(ca.currency_landed(plan), {})
        self.assertNotIn("PATCH", [m for m, _p, _b in ca.render_requests(plan)])
        self.assertTrue(any("forex" in r for r in plan["rationale"]))

    def test_a_campaign_without_additional_currencies_is_unchanged(self):
        plan = self.recommend(additional_currencies=None, prices_file=None)
        self.assertEqual(plan["campaign"]["additional_currencies"], [])
        self.assertFalse(any("prices" in p for p in plan["packages"] + plan["shipping_methods"]))

    def test_refusals(self):
        self.refused("is the campaign currency", additional_currencies="EUR,USD")
        self.refused("names GBP twice", additional_currencies="GBP,EUR,gbp")
        self.refused("ISO 4217", additional_currencies="EUR,EURO")
        self.refused("not enabled on the store", "Settings > Markets", additional_currencies="EUR,CHF")
        self.refused("names no currency", additional_currencies=" ")
        self.refused("pass --additional-currencies", additional_currencies=None)

    def test_the_gateway_group_has_to_list_every_currency(self):
        disc = mc_discovery(group=("USD", "EUR"))
        msg = self.refused("no gateway group on the store lists USD, EUR, GBP, JPY", disc=disc)
        self.assertNotIn("lists no currencies", msg)
        msg = self.refused("does not list GBP, JPY", "Accepted Currencies", disc=disc, gateway_group=1)
        self.assertIn("drop them from --additional-currencies", msg)
        plan = self.recommend(disc, additional_currencies="EUR")
        self.assertEqual(plan["campaign"]["payment_gateway_group_id"], 1)
        self.assertTrue(any("(the store default)" in r for r in plan["rationale"]), plan["rationale"])

    def test_a_group_that_lists_no_currency_has_no_active_gateway(self):
        disc = mc_discovery(group=())
        msg = self.refused("lists no currencies", "no active gateway", "listed for every group",
                           disc=disc, gateway_group=1)
        self.assertIn("Settings > Payments", msg)
        msg = self.refused("no gateway group on the store lists USD", disc=disc)
        self.assertIn("[1] Default list no currencies", msg)
        # The campaign currency alone was refused the same way before 1.3.0.
        self.refused("lists no currencies", disc=disc, gateway_group=1, additional_currencies=None,
                     prices_file=None)


class PricesFile(_Files):
    def test_a_field_the_engine_does_not_read_is_refused(self):
        doc = prices_doc()
        doc["currencies"]["EUR"]["upsell_price"] = 38
        self.refused("currencies.EUR has field(s) ['upsell_price']", "packages.<package key>",
                     prices_file=self.write_prices(doc))
        doc = prices_doc()
        doc["rates"] = {}
        self.refused("unsupported top-level field(s) ['rates']", prices_file=self.write_prices(doc))

    def test_gaps_and_strays_are_refused(self):
        doc = prices_doc()
        del doc["currencies"]["EUR"]["variants"]["24"]
        self.refused("EUR has no price for package hero-24 (variant 24)", prices_file=self.write_prices(doc))
        doc = prices_doc()
        del doc["currencies"]["GBP"]["shipping"]["standard"]
        self.refused("GBP has no price for shipping method standard", prices_file=self.write_prices(doc))
        doc = prices_doc()
        doc["currencies"]["EUR"]["packages"]["post-purchase-12"] = 1
        self.refused("names ['post-purchase-12'], which this plan does not have", prices_file=self.write_prices(doc))
        doc = prices_doc()
        del doc["currencies"]["JPY"]
        self.refused("names JPY, which the prices file does not price", prices_file=self.write_prices(doc))

    def test_the_campaign_currency_row_has_to_agree_with_the_command_line(self):
        doc = prices_doc()
        doc["currencies"]["USD"]["variants"]["23"] = 41
        self.refused("package hero-23 is 41.00 in USD", "make them agree", prices_file=self.write_prices(doc))
        doc = prices_doc()
        doc["currencies"]["USD"]["shipping"]["standard"] = 7
        self.refused("shipping standard is 7.00 in USD", prices_file=self.write_prices(doc))

    def test_values(self):
        for bad, needle in ((True, "must be a number"), ("abc", "number >= 0"), (-1, "number >= 0"),
                            (123456789, "more than 8 digits")):
            doc = prices_doc()
            doc["currencies"]["EUR"]["variants"]["23"] = bad
            self.refused(needle, prices_file=self.write_prices(doc))
        doc = prices_doc()
        doc["currencies"]["JPY"]["variants"]["25"] = "7000.50"
        self.refused("JPY has no minor unit", prices_file=self.write_prices(doc))
        doc = prices_doc()
        doc["currencies"]["EUR"]["variants"]["26"] = 0
        self.refused("is 0.00 in EUR and 40.00 in USD", "free in every currency or in none",
                     prices_file=self.write_prices(doc))
        doc = prices_doc()
        doc["currencies"]["USD"]["variants"]["11"] = 0
        doc["currencies"]["USD"]["packages"] = {"post-purchase-11": 0}
        self.refused("is 38.00 in EUR and 0.00 in USD", prices_file=self.write_prices(doc),
                     post_purchase=["11:0.00"])

    def test_a_partly_priced_currency_fails_validation(self):
        plan = self.recommend()
        del plan["packages"][0]["prices"]["EUR"]
        errs = ca.validate_plan(plan)
        self.assertTrue(any("additional currency EUR has a fixed price on some" in e for e in errs), errs)
        plan = self.recommend()
        plan["packages"][0]["prices"]["CHF"] = "1.00"
        self.assertTrue(any("prices.CHF is not one of campaign.additional_currencies" in e
                            for e in ca.validate_plan(plan)))
        plan = self.recommend()
        plan["campaign"]["additional_currencies"] = ["EUR", "USD"]
        self.assertTrue(any("includes the campaign currency USD" in e for e in ca.validate_plan(plan)))


class LandedPerCurrency(_Files):
    def test_offers_price_each_currency_by_the_same_rule(self):
        plan = self.recommend(ctc="low", exit=None, exit_code="BRACELET10", post_purchase=None)
        landed = ca.currency_landed(plan)
        eur = {l["tier"]: l for l in landed["EUR"]}
        self.assertEqual(eur["Buy 2"]["unit_after"], ca.money(ca.landed_unit(Decimal("44.00"), Decimal(55), None)))
        self.assertEqual(eur["Buy 3"]["order_total"], "52.80")
        jpy = {l["tier"]: l for l in landed["JPY"]}
        self.assertEqual(jpy["Buy 1"]["unit_after"], "3500.00")
        self.assertTrue(any("JPY has no minor unit" in r for r in plan["rationale"]), plan["rationale"])

    def test_rounding_is_checked_in_every_currency(self):
        # 40.00 at 1% is 39.60 and .95 keeps it below 40; 20.50 in EUR at 1% rounds back up to 20.95.
        doc = prices_doc(post=False)
        for v in ("23", "24", "25", "26"):
            doc["currencies"]["EUR"]["variants"][v] = "20.50"
        self.refused("landed prices in EUR", "20.95", ctc="low", tiers="1,2,3", rounding="0.95",
                     prices_file=self.write_prices(doc), post_purchase=None)

    def test_variants_of_one_row_priced_apart_are_refused(self):
        doc = prices_doc(post=False)
        doc["currencies"]["GBP"]["variants"]["24"] = 37
        self.refused("landed prices in GBP", "would no longer share one price", ctc="low",
                     prices_file=self.write_prices(doc), post_purchase=None)

    def test_the_plan_prints_requests_and_landed_prices_per_currency_and_hashes_them(self):
        plan = self.recommend()
        path = self.root / "campaign-plan.json"
        ca.atomic_write_json(path, plan)
        lines = []
        with mock.patch.object(ca, "print", lambda *a, **k: lines.append(" ".join(map(str, a)))):
            ca.print_plan(plan, path)
        out = "\n".join(lines)
        self.assertIn("additional currencies: EUR, GBP, JPY", out)
        self.assertIn("Prices in EUR (fixed): hero-23 44.00", out)
        self.assertIn("Landed prices in JPY (confirm these):", out)
        self.assertIn("Post-purchase 3D Photo Crystal", out)
        self.assertIn('PATCH /api/admin/campaigns/{campaign_id}/packages/<post-purchase-11>/  {"prices": '
                      '[{"currency": "EUR", "price": "38.00"}, {"currency": "GBP", "price": "29.00"}, '
                      '{"currency": "JPY", "price": "5800.00"}], "recalculate_prices": false}', out)
        self.assertIn('PATCH /api/admin/campaigns/{campaign_id}/shipping-methods/<standard>/  {"prices": '
                      '[{"currency": "EUR", "price": "5.00"}, {"currency": "GBP", "price": "0.00"}, '
                      '{"currency": "JPY", "price": "900.00"}], "recalculate_prices": false}', out)
        sha = ca.sha256_file(path)
        plan["packages"][0]["prices"]["EUR"] = "44.50"
        ca.atomic_write_json(path, plan)
        self.assertNotEqual(ca.sha256_file(path), sha, "a per-currency price is part of the approved hash")

    def test_request_order_puts_each_prices_patch_right_after_its_create(self):
        plan = self.recommend()
        reqs = [(m, p) for m, p, _b in ca.render_requests(plan)]
        self.assertEqual(reqs[:3], [("POST", "/api/admin/campaigns/"),
                                    ("POST", "/api/admin/campaigns/{campaign_id}/packages/"),
                                    ("PATCH", "/api/admin/campaigns/{campaign_id}/packages/<hero-23>/")])
        self.assertEqual(reqs[-2:], [("POST", "/api/admin/campaigns/{campaign_id}/shipping-methods/"),
                                     ("PATCH", "/api/admin/campaigns/{campaign_id}/shipping-methods/<standard>/")])
        body = ca.render_requests(plan)[0][2]
        self.assertEqual(body["additional_currencies"], ["EUR", "GBP", "JPY"])


class PostPurchase(_Files):
    def test_a_full_price_package_sold_after_the_order(self):
        plan = self.recommend(additional_currencies=None, prices_file=None)
        self.assertEqual(ca.validate_plan(plan), [])
        pkg = next(p for p in plan["packages"] if p["key"] == "post-purchase-11")
        self.assertEqual((pkg["role"], pkg["price"], pkg["product_variant_ids"]), ("post-purchase", "35.00", [11]))
        row = next(l for l in plan["landed_prices"] if l["kind"] == "upsell")
        self.assertEqual((row["tier"], row["offer_key"], row["pct"], row["unit_after"]),
                         ("Post-purchase 3D Photo Crystal", None, 0, "35.00"))
        self.assertFalse(any(o["key"].startswith("upsell-") for o in plan["offers"]))
        self.assertFalse(any("Checkout bump" in r for r in plan["rationale"]))
        self.assertEqual(ca._landed_shipping(plan, row), "no shipping (post-purchase)")
        cases = ca._cart_cases_from_plan(plan, {p["key"]: i for i, p in enumerate(plan["packages"], 1)})
        case = next(c for c in cases if c.name == "Post-purchase 3D Photo Crystal")
        self.assertEqual((case.ship, case.vouchers, case.expected), ("none", [], Decimal("35.00")))

    def test_it_reuses_a_package_at_the_same_price_and_refuses_another(self):
        plan = self.recommend(additional_currencies=None, prices_file=None, post_purchase=["24:40.00"])
        self.assertFalse(any(p["role"] == "post-purchase" for p in plan["packages"]))
        self.assertEqual(next(l for l in plan["landed_prices"] if l["kind"] == "upsell")["package_keys"],
                         ["hero-24"])
        self.refused("is already package hero-24 at 40.00", additional_currencies=None, prices_file=None,
                     post_purchase=["24:45.00"])
        self.refused("given twice", additional_currencies=None, prices_file=None,
                     post_purchase=["11:35.00", "11:35.00"])
        self.refused("--post-purchase variant id must be an integer", additional_currencies=None,
                     prices_file=None, post_purchase=["x:35.00"])

    def test_it_refuses_a_package_an_upsell_voucher_discounts(self):
        self.refused("is package upsell-11, which upsell voucher", "not both", additional_currencies=None,
                     prices_file=None, upsell=["11:35.00:50"], post_purchase=["11:35.00"])

    def test_reusing_a_hero_package_warns_about_the_exit_voucher_only_when_there_is_one(self):
        plan = self.recommend(additional_currencies=None, prices_file=None, post_purchase=["24:40.00"],
                              exit=None, exit_code="BRACELET10")
        self.assertTrue(any(h.startswith("Exit voucher BRACELET10") and "hero-24" in h for h in plan["handoff"]),
                        plan["handoff"])
        plan = self.recommend(additional_currencies=None, prices_file=None, post_purchase=["24:40.00"], exit="0")
        self.assertFalse(any("xit voucher" in h for h in plan["handoff"]), plan["handoff"])
        self.assertTrue(any("hero-24 is also a hero package" in h for h in plan["handoff"]), plan["handoff"])

    def test_a_zero_percent_upsell_points_at_post_purchase(self):
        self.refused("got 0", "--post-purchase <variant_id>:<price>", additional_currencies=None,
                     prices_file=None, post_purchase=None, upsell=["11:35.00:0"])

    def test_reland_reprices_it_with_its_package(self):
        plan = self.recommend(additional_currencies=None, prices_file=None)
        next(p for p in plan["packages"] if p["key"] == "post-purchase-11")["price"] = "36.00"
        row = next(l for l in ca.reland(plan) if l["kind"] == "upsell")
        self.assertEqual((row["anchor"], row["unit_after"], row["order_total"]), ("36.00", "36.00", "36.00"))


class RecommendCommandLine(_Files):
    def test_the_flags_reach_the_plan(self):
        disc = self.root / "discovery.json"
        disc.write_text(json.dumps(mc_discovery()))
        out = self.root / "run"
        lines = []
        with mock.patch.object(ca, "print", lambda *a, **k: lines.append(" ".join(map(str, a)))):
            rc = ca.main(["recommend", "--discovery", str(disc), "--hero", "22", "--ctc", "high",
                          "--anchor-price", "40.00", "--shipping", "standard:6.95", "--exit", "0",
                          "--additional-currencies", "eur,GBP", "--prices-file", self.write_prices(),
                          "--post-purchase", "11:35.00", "--out", str(out)])
        self.assertEqual(rc, 0, lines[-5:])
        plan = json.loads((out / "campaign-plan.json").read_text())
        self.assertEqual(plan["campaign"]["additional_currencies"], ["EUR", "GBP"])
        self.assertEqual(next(p for p in plan["packages"] if p["key"] == "post-purchase-11")["prices"],
                         {"EUR": "38.00", "GBP": "29.00"})
        self.assertTrue(any(l.startswith("Landed prices in GBP") for l in lines))


class DiscoveryRecordsTheDefaultGroup(unittest.TestCase):
    def test_is_default_is_kept_and_printed(self):
        store = {"name": "S", "available_currencies": [{"code": "USD"}], "available_languages": [{"code": "en"}]}
        groups = {"results": [{"id": 1, "name": "Default", "is_default": True, "available_currencies": [],
                               "available_payment_methods": [{"code": "bankcard"}],
                               "available_express_payment_methods": []},
                              {"id": 2, "name": "Campaigns", "is_default": False,
                               "available_currencies": [{"code": "USD"}, {"code": "EUR"}],
                               "available_payment_methods": [{"code": "bankcard"}],
                               "available_express_payment_methods": []}], "next": None}
        empty = {"results": [], "next": None}
        t = base.FakeTransport({("GET", "/api/admin/store/"): (200, store),
                                ("GET", "/api/admin/gateway-groups/"): (200, groups),
                                ("GET", "/api/admin/shipping-methods/"): (200, empty),
                                ("GET", "/api/admin/products/"): (200, empty),
                                ("GET", "/api/admin/campaigns/"): (200, empty),
                                ("GET", "/api/admin/metadata/"): (200, empty)})
        d = ca.discover(base.make_client(t), "teststore")
        self.assertEqual([(g["id"], g["is_default"], g["currencies"]) for g in d["gateway_groups"]],
                         [(1, True, []), (2, False, ["USD", "EUR"])])
        lines = []
        with mock.patch.object(ca, "print", lambda *a, **k: lines.append(" ".join(map(str, a)))):
            ca.print_discovery(d)
        self.assertIn("[1] Default  (store default): currencies=[]", "\n".join(lines))


# --------------------------------------------------------------------------- #
# apply and verify against the currency-aware fake store
# --------------------------------------------------------------------------- #

class _AppliedRun(_Files):
    kw = {}

    def setUp(self):
        super().setUp()
        self.disc = mc_discovery()
        self.plan = self.recommend(self.disc, name="Bracelet - multi-currency", **self.kw)
        self.plan_path = self.root / "campaign-plan.json"
        ca.atomic_write_json(self.plan_path, self.plan)
        self.sha = ca.sha256_file(self.plan_path)
        self.manifest = self.root / "run-manifest.json"
        self.state = base.fresh_state()
        self.t = MCTransport(self.disc, self.state)

    def apply(self, transport=None, resume=False):
        return ca.apply(base.make_client(transport or self.t), self.plan, self.sha, self.manifest,
                        self.manifest if resume else None)

    def verify(self, man, **kw):
        cart, tt = cart_client(self.state, man.data["campaign"]["id"], **kw)
        return ca.verify(base.make_client(self.t), cart, man, self.plan, self.sha), tt

    def live(self, kind, key, man):
        e = next(x for x in man.data[kind.replace("-", "_")] if x["key"] == key)
        return self.state[kind][man.data["campaign"]["id"]][e["id"]]

    def failing(self, report):
        return [x for x in report["admin_checks"] + report["calculate_cases"] if x["result"] == "FAIL"]


class ApplySetsFixedPrices(_AppliedRun):
    def test_each_create_is_followed_by_its_prices_patch(self):
        man = self.apply()
        writes = [(c[0], c[1]) for c in self.t.calls if c[0] != "GET"]
        cid = man.data["campaign"]["id"]
        ids = {e["key"]: e["id"] for e in man.data["packages"]}
        sid = man.data["shipping_methods"][0]["id"]
        expected = [("POST", "/api/admin/campaigns/")]
        for key in ("hero-23", "hero-24", "hero-25", "hero-26", "post-purchase-11"):
            expected += [("POST", f"/api/admin/campaigns/{cid}/packages/"),
                         ("PATCH", f"/api/admin/campaigns/{cid}/packages/{ids[key]}/")]
        expected += [("POST", f"/api/admin/campaigns/{cid}/shipping-methods/"),
                     ("PATCH", f"/api/admin/campaigns/{cid}/shipping-methods/{sid}/")]
        self.assertEqual(writes, expected)
        patch = next(c[2] for c in self.t.calls
                     if c[0] == "PATCH" and c[1].endswith(f"/packages/{ids['post-purchase-11']}/"))
        # Currency and price only: a recurring price in that currency is the store's to keep.
        self.assertEqual(patch, {"prices": [{"currency": "EUR", "price": "38.00"},
                                            {"currency": "GBP", "price": "29.00"},
                                            {"currency": "JPY", "price": "5800.00"}],
                                 "recalculate_prices": False})
        first_post = next(c for c in self.t.calls if c[0] == "POST")
        self.assertEqual(first_post[2]["additional_currencies"], ["EUR", "GBP", "JPY"])
        self.assertTrue(all(e.get("prices_status") == "set"
                            for e in man.data["packages"] + man.data["shipping_methods"]))
        rows = {r["currency"]: r["price"] for r in self.live("packages", "hero-23", man)["prices"]}
        self.assertEqual(rows, {"USD": "40.00", "EUR": "44.00", "GBP": "36.00", "JPY": "7000.00"})

    def test_a_failed_prices_patch_stops_the_run_and_resume_sends_it_again(self):
        cid, hero_24 = 101, 103  # the fake store numbers the campaign, then its packages in plan order
        self.t.fail_on[("PATCH", f"/api/admin/campaigns/{cid}/packages/{hero_24}/")] = 500
        with self.assertRaises(ca.CampaignAdminError) as cm:
            self.apply()
        self.assertIn("returned 500", str(cm.exception))
        self.assertIn("apply --resume sends the PATCH again", str(cm.exception))
        man = ca.Manifest.load(self.manifest)
        e = man.entry("packages", "hero-24")
        self.assertEqual((e["status"], e["prices_status"]), ("created", "pending"))
        # The forex price the create filled is what the store holds until then.
        self.assertEqual(next(r["price"] for r in self.live("packages", "hero-24", man)["prices"]
                              if r["currency"] == "EUR"), "36.00")
        t2 = MCTransport(self.disc, self.state)
        man = self.apply(t2, resume=True)
        posts = [c for c in t2.calls if c[0] == "POST" and c[1].endswith("/packages/")]
        self.assertEqual(len(posts), 3, "only the packages never created are posted on resume")
        self.assertEqual(man.entry("packages", "hero-24")["prices_status"], "set")
        self.assertEqual(next(r["price"] for r in self.live("packages", "hero-24", man)["prices"]
                              if r["currency"] == "EUR"), "44.00")

    def test_a_200_that_did_not_set_the_prices_stops_the_run(self):
        cid, hero_23 = 101, 102
        self.t.ignore_patch.add(("PATCH", f"/api/admin/campaigns/{cid}/packages/{hero_23}/"))
        with self.assertRaises(ca.CampaignAdminError) as cm:
            self.apply()
        self.assertIn("answered 200 but holds EUR 36.00 (sent 44.00)", str(cm.exception))

    def test_a_200_without_the_row_says_so(self):
        cid, hero_23 = 101, 102
        path = f"/api/admin/campaigns/{cid}/packages/{hero_23}/"

        def strip():  # the store answers the PATCH with no EUR row at all
            item = self.state["packages"][cid][hero_23]
            item["prices"] = [r for r in item["prices"] if r["currency"] != "EUR"]
        self.t.ignore_patch.add(("PATCH", path))
        self.t.before[("PATCH", path)] = strip
        with self.assertRaises(ca.CampaignAdminError) as cm:
            self.apply()
        self.assertIn("holds no EUR price row (sent 44.00)", str(cm.exception))


class VerifyEveryCurrency(_AppliedRun):
    def test_pass_reads_back_lists_and_prices_carts_in_every_currency(self):
        man = self.apply()
        report, tt = self.verify(man)
        self.assertEqual(report["result"], "PASS", self.failing(report))
        names = [x["check"] for x in report["admin_checks"]]
        for code in ("EUR", "GBP", "JPY"):
            self.assertIn(f"package hero-23 price {code}", names)
            self.assertIn(f"shipping standard price {code}", names)
            self.assertIn(f"campaign listing in {code}", names)
        self.assertIn("campaign listing in USD", names)
        cases = {x["case"]: x for x in report["calculate_cases"]}
        self.assertEqual(cases["[EUR] Buy 1 single variant"]["expected_total"], "49.00")  # 44.00 + 5.00
        self.assertEqual(cases["[GBP] Buy 1 single variant"]["expected_total"], "36.00")  # free shipping in GBP
        jpy_post = cases["[JPY] Post-purchase 3D Photo Crystal"]
        self.assertEqual((jpy_post["expected_total"], jpy_post["shipping"]), ("5800.00", "none"))
        self.assertTrue(jpy_post["path"].endswith("?upsell=true"))
        self.assertEqual(jpy_post["request"]["currency"], "JPY")
        self.assertEqual(cases["Post-purchase 3D Photo Crystal"]["expected_total"], "35.00")
        self.assertNotIn("currency", cases["Buy 1 single variant"]["request"])
        listings = [c[1] for c in tt.calls if c[0] == "GET"]
        self.assertEqual(listings, [f"/api/v1/campaigns/?currency={c}" for c in ("USD", "EUR", "GBP", "JPY")])

    def test_a_price_changed_in_the_dashboard_fails(self):
        man = self.apply()
        for r in self.live("packages", "hero-23", man)["prices"]:
            if r["currency"] == "GBP":
                r["price"] = "35.00"
        report, _tt = self.verify(man)
        fails = {x.get("check") or x.get("case") for x in self.failing(report)}
        self.assertIn("package hero-23 price GBP", fails)
        self.assertIn("campaign listing in GBP", fails)
        self.assertTrue(any(f.startswith("[GBP] Buy 1") for f in fails), fails)
        self.assertFalse(any(f.startswith("[EUR]") for f in fails), fails)

    def test_a_package_with_no_price_in_a_currency_is_missing_from_it(self):
        man = self.apply()
        item = self.live("packages", "post-purchase-11", man)
        item["prices"] = [r for r in item["prices"] if r["currency"] != "JPY"]
        report, _tt = self.verify(man)
        fails = {x.get("check") or x.get("case"): x for x in self.failing(report)}
        self.assertIn("no JPY price", fails["package post-purchase-11 price JPY"]["detail"])
        self.assertIn("package post-purchase-11", fails["campaign listing in JPY"]["detail"])
        self.assertIn("is not listed", fails["campaign listing in JPY"]["detail"])
        self.assertIn("calculate in JPY", fails)

    def test_a_cart_priced_in_the_campaign_currency_instead_fails(self):
        man = self.apply()
        report, _tt = self.verify(man, honour_currency=False)
        bad = [x for x in report["calculate_cases"] if x["result"] == "FAIL"]
        self.assertTrue(bad)
        self.assertTrue(all(x["case"].startswith("[") for x in bad), [x["case"] for x in bad])
        self.assertIn("priced in USD, not EUR", next(x for x in bad if x["case"].startswith("[EUR]"))["error"])


class VerifyForexCurrencies(_AppliedRun):
    kw = {"prices_file": None, "ctc": "low", "exit": None, "exit_code": "BRACELET10", "post_purchase": None}

    def test_forex_prices_are_read_back_and_carts_priced_from_them(self):
        man = self.apply()
        self.assertFalse(any(c[0] == "PATCH" for c in self.t.calls))
        report, _tt = self.verify(man)
        self.assertEqual(report["result"], "PASS", self.failing(report))
        row = next(x for x in report["admin_checks"] if x["check"] == "package hero-23 price EUR")
        self.assertEqual(row["detail"], "forex 36.00")
        cases = {x["case"]: x for x in report["calculate_cases"]}
        # 40.00 at 0.90 is 36.00; Buy 2 at 55% off is 16.20 a unit, plus 6.26 shipping by forex.
        self.assertEqual(cases["[EUR] Buy 2 single variant"]["expected_total"], "38.66")


class DiffOnACreatedRun(_AppliedRun):
    """The update path on a campaign recommend and apply built, not an adopted one:
    the base plan fixes every currency, and a merged plan must stay fully priced."""

    def diff(self, cand):
        path = self.root / "campaign-plan.next.json"
        path.write_text(json.dumps(cand, indent=2) + "\n")
        lines = []
        with mock.patch.object(ca, "_client_for", lambda slug: base.make_client(self.t)), \
                mock.patch.object(ca, "print", lambda *a, **k: lines.append(" ".join(map(str, a)))):
            rc = ca.main(["diff", "--plan", str(path), "--manifest", str(self.manifest)])
        return rc, "\n".join(lines)

    def test_a_lost_price_is_sent_back_not_refused_as_partial(self):
        man = self.apply()
        item = self.live("packages", "hero-24", man)
        item["prices"] = [r for r in item["prices"] if r["currency"] != "GBP"]
        cand = json.loads(self.plan_path.read_text())
        cand["campaign"]["name"] = "Renamed multi-currency"
        rc, out = self.diff(cand)
        self.assertEqual(rc, 0, out)
        cs = json.loads((self.root / "change-set.json").read_text())
        op = next(o for o in cs["ops"] if o["section"] == "packages")
        self.assertEqual((op["key"], op["body"]), ("hero-24", {"prices": [{"currency": "GBP", "price": "36.00"}],
                                                              "recalculate_prices": False}))
        self.assertTrue(any("hero-24: the store has no GBP price" in w for w in cs["warnings"]), cs["warnings"])
        self.assertEqual(ca.currency_coverage(cs["merged_plan"])["GBP"], "fixed")


class EditKeepsFixedPrices(_AppliedRun):
    """edit changes the campaign currency's prices and the offers in place; each
    fixed currency keeps its prices, and its landed rows follow the offers."""

    kw = {"ctc": "low", "exit": None, "exit_code": "BRACELET10", "post_purchase": None}

    def edit(self, ops):
        spec = self.root / "campaign-edit.json"
        spec.write_text(json.dumps({"operations": ops}))
        lines = []
        argv = ["edit", "--manifest", str(self.manifest), "--plan", str(self.plan_path), "--changes", str(spec)]
        with mock.patch.object(ca, "_client_for", lambda slug: base.make_client(self.t)), \
                mock.patch.object(ca, "print", lambda *a, **k: lines.append(" ".join(map(str, a)))):
            self.assertEqual(ca.main(argv), 2, lines[-3:])
            sha = next(l.split(": ", 1)[1] for l in reversed(lines) if l.startswith("Edit SHA-256: "))
            rc = ca.main(argv + ["--yes", "--edit-sha256", sha, "--live-traffic", "no"])
        self.assertEqual(rc, 0, lines[-5:])
        self.plan, self.sha = ca.load_json_and_hash(self.plan_path)
        return ca.Manifest.load(self.manifest)

    def test_an_offer_and_a_price_edit_then_verify_in_every_currency(self):
        man = self.apply()
        man = self.edit([{"op": "set_offer_benefit", "offer_key": "tier-2", "value": "57"},
                         {"op": "set_package_price", "package_keys": ["hero-23", "hero-24", "hero-25", "hero-26"],
                          "price": "42.00"}])
        self.assertEqual(self.plan["packages"][0]["prices"], {"EUR": "44.00", "GBP": "36.00", "JPY": "7000.00"})
        rows = {r["currency"]: r["price"] for r in self.live("packages", "hero-23", man)["prices"]}
        self.assertEqual(rows, {"USD": "42.00", "EUR": "44.00", "GBP": "36.00", "JPY": "7000.00"})
        eur = {l["tier"]: l for l in ca.currency_landed(self.plan)["EUR"]}
        self.assertEqual(eur["Buy 2"]["unit_after"], ca.money(ca.landed_unit(Decimal("44.00"), Decimal(57), None)))
        report, _tt = self.verify(man)
        self.assertEqual(report["result"], "PASS", self.failing(report))


# --------------------------------------------------------------------------- #
# the update path
# --------------------------------------------------------------------------- #

class _MCAdoptedRun(base._ResumableRun):
    """An adopted campaign that also prices in EUR (forex rows, as a create leaves
    them), on the currency-aware fake store."""

    seed_kw = {"extra_currency": "EUR"}

    def setUp(self):
        self.disc = mc_discovery()
        self.state = base.fresh_state()
        self.state["forex"] = dict(FOREX)
        self.cid = self.seed(self.state)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "run"
        self.lines = []
        self.assertEqual(self._run(["adopt", "--store", "teststore", "--campaign", str(self.cid),
                                    "--out", str(self.dir)]), 0, self.out())
        self.base_path = self.dir / "campaign-plan.json"
        self.manifest_path = self.dir / "run-manifest.json"
        self.base = json.loads(self.base_path.read_text())
        self.offer_keys = [o["key"] for o in self.base["offers"]]

    def _run(self, argv):
        return self._run_with(MCTransport(self.disc, self.state), argv)

    def update(self, *extra, transport=None, plan=None, cs=None, sha=None):
        argv = self.update_argv(*extra, plan=plan, cs=cs, sha=sha)
        return self._run_with(transport or MCTransport(self.disc, self.state), argv)

    def interrupt(self, *extra, stop_after=None, lose=()):
        t = MCInterrupting(self.disc, self.state, stop_after=stop_after, lose_response_on=lose)
        with self.assertRaises(base._Interrupted):
            self.update(*extra, transport=t)
        return t

    def live_prices(self, kind="packages"):
        return {i: {r["currency"]: r["price"] for r in x["prices"]}
                for i, x in self.state[kind][self.cid].items()}


class AdoptRecordsLivePrices(_MCAdoptedRun):
    def test_the_adopted_plan_fixes_what_the_store_holds(self):
        self.assertEqual(self.base["campaign"]["additional_currencies"], ["EUR"])
        self.assertEqual([p["prices"] for p in self.base["packages"]], [{"EUR": "22.46"}, {"EUR": "11.66"}])
        self.assertEqual(self.base["shipping_methods"][0]["prices"], {"EUR": "6.26"})
        self.assertEqual(ca.validate_plan(self.base), [])


class UpdatePerCurrencyPrices(_MCAdoptedRun):
    def test_a_euro_price_change_is_one_prices_row(self):
        cand = self.cand()
        cand["packages"][0]["prices"]["EUR"] = "21.00"
        self.assertEqual(self.diff(cand), 0, self.out())
        op = self.change_set()["ops"][0]
        self.assertEqual((op["method"], op["section"], op["key"]), ("PATCH", "packages", "pkg-801"))
        self.assertEqual(op["body"], {"prices": [{"currency": "EUR", "price": "21.00"}],
                                      "recalculate_prices": False})
        self.assertEqual((op["before"], op["after"]), ({"prices.EUR": "22.46"}, {"prices.EUR": "21.00"}))
        self.assertEqual(self.change_set()["warnings"], [])
        self.assertEqual(self.update(), 0, self.out())
        pid = self.ids("packages")["pkg-801"]
        self.assertEqual(self.live_prices()[pid], {"USD": "24.95", "EUR": "21.00"})
        self.assertEqual(self.plan_now()["packages"][0]["prices"], {"EUR": "21.00"})

    def test_a_dashboard_change_to_another_currency_is_kept(self):
        pid = self.ids("packages")["pkg-802"]
        for r in self.state["packages"][self.cid][pid]["prices"]:
            if r["currency"] == "EUR":
                r["price"] = "12.00"
        cand = self.cand()
        cand["packages"][0]["prices"]["EUR"] = "21.00"
        self.assertEqual(self.diff(cand), 0, self.out())
        cs = self.change_set()
        self.assertEqual([o["key"] for o in cs["ops"]], ["pkg-801"])
        self.assertTrue(any("pkg-802.prices.EUR" in p and "12.00" in p for p in cs["preserved"]), cs["preserved"])
        self.assertEqual(cs["merged_plan"]["packages"][1]["prices"], {"EUR": "12.00"})

    def test_both_sides_changing_one_currency_is_a_conflict(self):
        pid = self.ids("packages")["pkg-801"]
        for r in self.state["packages"][self.cid][pid]["prices"]:
            if r["currency"] == "EUR":
                r["price"] = "23.00"
        cand = self.cand()
        cand["packages"][0]["prices"]["EUR"] = "21.00"
        self.refuse(cand, needle="package pkg-801.prices.EUR: base \"22.46\", candidate \"21.00\", store \"23.00\"")

    def drop_eur(self, key):
        pid = self.ids("packages")[key]
        item = self.state["packages"][self.cid][pid]
        item["prices"] = [r for r in item["prices"] if r["currency"] != "EUR"]

    def test_a_price_the_store_lost_is_sent_back_with_a_warning(self):
        self.drop_eur("pkg-801")
        cand = self.cand()
        cand["campaign"]["name"] = "Renamed"
        self.assertEqual(self.diff(cand), 0, self.out())
        cs = self.change_set()
        op = next(o for o in cs["ops"] if o["section"] == "packages")
        self.assertEqual((op["key"], op["before"], op["after"]), ("pkg-801", {"prices.EUR": None},
                                                                  {"prices.EUR": "22.46"}))
        self.assertTrue(any(w == "package pkg-801: the store has no EUR price, so the Cart API leaves it out of EUR "
                                 "carts and listings; this change set sets prices.EUR to 22.46." for w in cs["warnings"]),
                        cs["warnings"])
        self.assertFalse(any("pkg-801" in p for p in cs["preserved"] + cs["plan_only"]))
        self.assertEqual(cs["merged_plan"]["packages"][0]["prices"], {"EUR": "22.46"})
        self.assertEqual(self.update(), 0, self.out())
        self.assertEqual(self.live_prices()[self.ids("packages")["pkg-801"]]["EUR"], "22.46")

    def test_a_new_price_for_one_the_store_lost_names_the_old_one(self):
        self.drop_eur("pkg-801")
        cand = self.cand()
        cand["packages"][0]["prices"]["EUR"] = "21.00"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertTrue(any("sets prices.EUR to 21.00 (the plan had 22.46)" in w
                            for w in self.change_set()["warnings"]), self.change_set()["warnings"])

    def test_a_currency_the_plan_did_not_fix_is_sent_without_a_merge(self):
        cand = self.cand()
        del cand["packages"][1]["prices"]
        base = json.loads(self.base_path.read_text())
        del base["packages"][1]["prices"]
        self.base_path.write_text(json.dumps(base, indent=2) + "\n")
        man = self.manifest()
        man["plan_sha256"] = ca.sha256_file(self.base_path)
        ca.atomic_write_json(self.manifest_path, man)
        cand["packages"][1]["prices"] = {"EUR": "11.00"}
        self.assertEqual(self.diff(cand), 0, self.out())
        op = self.change_set()["ops"][0]
        self.assertEqual((op["key"], op["before"], op["after"]), ("pkg-802", {"prices.EUR": "11.66"},
                                                                  {"prices.EUR": "11.00"}))

    def test_a_default_price_change_still_warns_about_the_currencies_it_leaves(self):
        cand = self.cand()
        cand["packages"][0]["price"] = "29.95"
        cand["shipping_methods"][0]["price"] = "8.95"
        cand["shipping_methods"][0]["prices"]["EUR"] = "8.00"
        self.assertEqual(self.diff(cand), 0, self.out())
        cs = self.change_set()
        self.assertTrue(any(w.startswith("package pkg-801") and "EUR" in w for w in cs["warnings"]), cs["warnings"])
        self.assertFalse(any(w.startswith("shipping") for w in cs["warnings"]), cs["warnings"])
        ship = next(o for o in cs["ops"] if o["section"] == "shipping_methods")
        self.assertEqual(ship["body"], {"prices": [{"currency": "USD", "price": "8.95"},
                                                   {"currency": "EUR", "price": "8.00"}],
                                        "recalculate_prices": False})


class UpdateKeepsRecurringPrices(_MCAdoptedRun):
    """A subscription package's recurring price in an additional currency is not
    in the plan, so a prices row must leave it as the store holds it."""

    seed_kw = {"extra_currency": "EUR", "recurring": True}

    def test_a_euro_price_change_keeps_the_euro_recurring_price(self):
        pid = self.ids("packages")["pkg-801"]
        before = next(r for r in self.state["packages"][self.cid][pid]["prices"] if r["currency"] == "EUR")
        self.assertEqual(before["price_recurring"], "22.46")
        cand = self.cand()
        cand["packages"][0]["prices"]["EUR"] = "21.00"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.change_set()["ops"][0]["body"],
                         {"prices": [{"currency": "EUR", "price": "21.00"}], "recalculate_prices": False})
        self.assertEqual(self.update(), 0, self.out())
        after = next(r for r in self.state["packages"][self.cid][pid]["prices"] if r["currency"] == "EUR")
        self.assertEqual((after["price"], after["price_recurring"]), ("21.00", "22.46"))


class UpdateAddsAndRemovesCurrencies(_MCAdoptedRun):
    def test_adding_a_currency_with_fixed_prices(self):
        cand = self.cand()
        cand["campaign"]["additional_currencies"] = ["EUR", "GBP"]
        cand["packages"][0]["prices"]["GBP"] = "20.00"
        cand["packages"][1]["prices"]["GBP"] = "10.00"
        cand["shipping_methods"][0]["prices"]["GBP"] = "5.00"
        self.assertEqual(self.diff(cand), 0, self.out())
        cs = self.change_set()
        self.assertEqual([(o["method"], o["section"]) for o in cs["ops"]],
                         [("PATCH", "campaign"), ("PATCH", "packages"), ("PATCH", "packages"),
                          ("PATCH", "shipping_methods")])
        self.assertEqual(cs["ops"][0]["body"], {"additional_currencies": ["EUR", "GBP"]})
        self.assertEqual(cs["ops"][1]["before"], {"prices.GBP": None})
        self.assertTrue(any("adding GBP" in w and "requests below set" in w for w in cs["warnings"]), cs["warnings"])
        self.assertEqual(self.update(), 0, self.out())
        prices = self.live_prices()
        self.assertEqual(sorted(p["GBP"] for p in prices.values()), ["10.00", "20.00"])
        self.assertEqual(list(self.live_prices("shipping-methods").values())[0]["GBP"], "5.00")

    def test_adding_without_prices_names_the_forex_ones(self):
        cand = self.cand()
        cand["campaign"]["additional_currencies"] = ["EUR", "GBP"]
        self.assertEqual(self.diff(cand), 0, self.out())
        warnings = self.change_set()["warnings"]
        self.assertTrue(any("adding GBP" in w and "pkg-801, pkg-802, standard keep that forex price" in w
                            for w in warnings), warnings)

    def test_removing_a_currency_warns_that_its_prices_are_deleted(self):
        cand = self.cand()
        cand["campaign"]["additional_currencies"] = []
        cand["packages"][0]["price"] = "29.95"  # a price op beside the removal names no removed currency
        self.refuse(cand, needle="prices.EUR is not one of campaign.additional_currencies")
        for obj in cand["packages"] + cand["shipping_methods"]:
            obj.pop("prices", None)
        self.assertEqual(self.diff(cand), 0, self.out())
        cs = self.change_set()
        self.assertEqual(cs["ops"][0]["body"], {"additional_currencies": None})
        self.assertTrue(any("removing EUR deletes every EUR price" in w and "set by hand" in w
                            for w in cs["warnings"]), cs["warnings"])
        self.assertFalse(any("stay as they are" in w for w in cs["warnings"]), cs["warnings"])
        self.assertEqual(self.update(), 0, self.out())
        self.assertTrue(all(set(p) == {"USD"} for p in self.live_prices().values()))

    def test_a_resume_after_the_currency_removal_landed_is_not_drift(self):
        cand = self.cand()
        cand["campaign"]["additional_currencies"] = []
        cand["campaign"]["name"] = "Renamed"
        for obj in cand["packages"] + cand["shipping_methods"]:
            obj.pop("prices", None)
        cand["packages"][0]["price"] = "29.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.interrupt(stop_after=1)
        self.assertEqual(self.journal(), [(1, "done"), (2, "in_flight")])
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertEqual(self.journal(), [])

    def test_a_new_package_gets_its_fixed_prices_after_its_post(self):
        cand = self.cand()
        cand["packages"].append({"key": "upsell-11", "role": "upsell", "name": "3D Photo Crystal",
                                 "product_id": 10, "product_variant_ids": [11], "price": "35.00",
                                 "prices": {"EUR": "33.00"}})
        self.assertEqual(self.diff(cand), 0, self.out())
        ops = self.change_set()["ops"]
        post, patch = ops[-2], ops[-1]
        self.assertEqual((post["method"], patch["method"], patch["depends_on"]), ("POST", "PATCH", post["n"]))
        self.assertEqual(patch["path"], f"/api/admin/campaigns/{self.cid}/packages/<upsell-11>/")
        self.assertNotIn("id", patch)
        self.assertEqual(self.update(), 0, self.out())
        pid = self.ids("packages")["upsell-11"]
        self.assertEqual(self.live_prices()[pid], {"USD": "35.00", "EUR": "33.00"})
        self.assertEqual(self.plan_now()["packages"][-1]["prices"], {"EUR": "33.00"})

    def test_a_settled_update_keeps_forex_prices_on_a_package_its_patch_never_reached(self):
        cand = self.cand()
        cand["packages"].append({"key": "upsell-11", "role": "upsell", "name": "3D Photo Crystal",
                                 "product_id": 10, "product_variant_ids": [11], "price": "35.00",
                                 "prices": {"EUR": "33.00"}})
        self.assertEqual(self.diff(cand), 0, self.out())
        self.interrupt(stop_after=1)
        self.assertEqual(self.update("--settle"), 0, self.out())
        added = next(p for p in self.plan_now()["packages"] if p["key"] == "upsell-11")
        self.assertNotIn("prices", added)


if __name__ == "__main__":
    unittest.main()
