import sys
from pathlib import Path
import unittest
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import refresh_dashboard as refresh


class RefreshRules(unittest.TestCase):
    def test_coupon_code_exclusion_and_current_membership(self):
        class API:
            def events(self, metric, start, end):
                self_metric = metric
                assert self_metric == "WvaquY"
                for name, codes in (("US123456", ["CRTRA10"]),
                                    ("US123457", ["Trade Discount"]),
                                    ("US123458", ["SBB2B10"])):
                    yield {"attributes": {"event_properties": {"$value": 100,
                            "$extra": {"name": name}, "Discount Codes": codes}},
                           "relationships": {"profile": {"data": {"id": "profile-1"}}}}

        flags, codes, eligible = refresh.order_flags(API(), None, None, {}, {}, {}, {},
                                                       {"flow": {}, "form": {}})
        self.assertEqual(eligible, 3)
        self.assertEqual({x["order_name"] for x in flags}, {"US123457", "US123458"})
        self.assertEqual(len(codes), 3)

    def test_large_order_is_inside_core_and_big_only(self):
        dates = [date(2026, 10, 31)]
        rows = [{"d": dates[0], "tier": "core", "orders": 2,
                 "net_sales": 7000, "est_cost": 2000,
                 "n_list": 1, "n_seg": 0, "n_flow": 0, "n_form": 0, "n_coupon": 0,
                 "s_list": 3500, "s_seg": 0, "s_flow": 0, "s_form": 0, "s_coupon": 0,
                 "n_big": 2, "s_big": 7000, "n_bigonly": 1, "s_bigonly": 3500}]
        core = refresh.build_cohort(rows, dates)[0]["core"]
        self.assertEqual((core["o"], core["nb"], core["nbo"]), (2, 2, 1))
        self.assertEqual(core["net"], 7000)


if __name__ == "__main__":
    unittest.main()
