"""Regression tests for the drying endpoint criterion."""
import unittest

import numpy as np

import problem3 as q


class DryingCriterionTests(unittest.TestCase):
    def test_reconstructed_center_is_included(self):
        grid = q.p1.Grid(100)
        C = np.full(grid.N, 0.10)
        C[0] = 0.16
        attained, maximum, index, radius = q.drying_criterion(C, 0.14, grid)
        self.assertFalse(attained)
        self.assertAlmostEqual(maximum, (9 * C[0] - C[1]) / 8)
        self.assertEqual(index, -1)
        self.assertEqual(radius, 0.0)

    def test_surface_location_mapping_is_preserved(self):
        grid = q.p1.Grid(100)
        C = np.full(grid.N, 0.10)
        attained, maximum, index, radius = q.drying_criterion(C, 0.20, grid)
        self.assertFalse(attained)
        self.assertEqual(maximum, 0.20)
        self.assertEqual(index, grid.N)
        self.assertEqual(radius, grid.R)


if __name__ == '__main__':
    unittest.main(verbosity=2)
