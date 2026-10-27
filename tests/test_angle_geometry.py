import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
sys.path.insert(0, str(ENGINE))

from config import resolve_config
import stage02_core as stage02
import stage04_core as stage04


class SymmetricCornerGeometryTests(unittest.TestCase):
    def make_case(self, angle):
        config = resolve_config({"case_id": f"angle_{angle}", "corner_angle_deg": angle,
                                "parameters": {"B": 400.0}})
        return config, stage02.load_case(config)

    def test_axis_endpoints_and_reflection_follow_included_angle(self):
        for angle in (60.0, 90.0, 120.0):
            with self.subTest(angle=angle):
                _, case = self.make_case(angle)
                h = math.radians(angle / 2.0)
                D = case["D"]
                end_run = case["e"] + case["c"]
                self.assertAlmostEqual(case["old_start"][0], -D / math.tan(h), places=8)
                self.assertAlmostEqual(case["old_start"][1], -D, places=8)
                self.assertAlmostEqual(case["old_end"][0], end_run / math.tan(h), places=8)
                self.assertAlmostEqual(case["old_end"][1], end_run, places=8)
                self.assertAlmostEqual(case["old_unit"][0], math.cos(h), places=10)
                self.assertAlmostEqual(case["old_unit"][1], math.sin(h), places=10)

                reflected_x = stage02.reflect_xy(case, (100.0, 0.0))
                self.assertAlmostEqual(reflected_x[0], 100.0 * math.cos(math.radians(angle)), places=8)
                self.assertAlmostEqual(reflected_x[1], 100.0 * math.sin(math.radians(angle)), places=8)
                sample = (137.0, -83.0)
                reflected = stage02.reflect_xy(case, sample)
                restored = stage02.reflect_xy(case, reflected)
                self.assertAlmostEqual(restored[0], sample[0], places=8)
                self.assertAlmostEqual(restored[1], sample[1], places=8)
                self.assertAlmostEqual(stage02.reflect_xy(case, case["old_start"])[0],
                                       case["old_start"][0], places=8)
                self.assertAlmostEqual(stage02.reflect_xy(case, case["old_start"])[1],
                                       case["old_start"][1], places=8)

    def test_fan_reference_points_and_material_sizes_remain_millimetres(self):
        for angle in (60.0, 90.0, 120.0):
            with self.subTest(angle=angle):
                _, case = self.make_case(angle)
                stage02.setup_fan_surface(case)
                h = math.radians(angle / 2.0)
                fan = case["fan_surface"]
                self.assertAlmostEqual(fan["A"][0], case["old_start"][0], places=8)
                self.assertAlmostEqual(fan["A"][1], case["e"], places=8)
                self.assertAlmostEqual(fan["O"][0], case["old_start"][0], places=8)
                self.assertAlmostEqual(fan["O"][1], -case["D"] + case["old_width_root"] / (2.0 * math.cos(h)),
                                       places=8)
                self.assertGreater(fan["arc_info"]["arc_length_mm"], 0.0)
                self.assertEqual(case["fan_width"], 70.0)
                self.assertEqual(case["fan_height"], 60.0)
                self.assertEqual(case["p"]["w_fly"], 60.0)
                self.assertEqual(case["board_lower_t"], 20.0)

    def test_seat_search_refines_only_around_near_full_coarse_coverage(self):
        q_coarse=stage04.np.linspace(-136.57804105357536,0.0,31)
        coverage=[0]*len(q_coarse)
        for target in (-72.84,-68.29):
            i=min(range(len(q_coarse)),key=lambda j:abs(float(q_coarse[j])-target))
            coverage[i]=16
        refined=stage04.adaptive_seat_refinement_samples(q_coarse,coverage,17)
        self.assertEqual(refined["scope"],"near_best_coverage")
        self.assertLessEqual(refined["step_mm"],0.05)
        self.assertLessEqual(len(refined["q_values_mm"]),5000)
        self.assertTrue(any(-68.66<=q<=-68.38 for q in refined["q_values_mm"]))

        sparse=stage04.adaptive_seat_refinement_samples(q_coarse,[0]*len(q_coarse),17)
        self.assertEqual(sparse["scope"],"full_q_range")
        self.assertLessEqual(len(sparse["q_values_mm"]),5000)


if __name__ == "__main__":
    unittest.main()
