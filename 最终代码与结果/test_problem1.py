"""Independent numerical checks: equilibria, exact manufactured polynomial,
boundary reconstruction, conservation, interpolation, and failure reporting.
"""
import unittest
import warnings
import problem1 as m
import numpy as np


class NumericalTests(unittest.TestCase):
    def setUp(self):
        self.grid = m.Grid(30)
        self.p = m.Parameters()

    def test_uniform_equilibrium(self):
        T = np.full(30, 28.)
        C = np.full(30, 2.55)
        np.testing.assert_allclose(m.solve_temperature_step(T, 1, self.grid, 28), T, atol=1e-11)
        new, _, ok, _ = m.solve_moisture_step(C, 1, self.grid, 2.55)
        self.assertTrue(ok)
        np.testing.assert_allclose(new, C, atol=1e-12)

    def test_quadratic_center_and_linear_interpolation(self):
        f = 7 + 13*self.grid.r**2
        values = m.sample_at_positions(f, np.array([0., self.grid.r[0]/2, self.grid.R]), self.grid,
                                       7+13*self.grid.R**2)
        np.testing.assert_allclose(values, 7+13*np.array([0., self.grid.r[0]/2, self.grid.R])**2, atol=1e-14)
        x = (self.grid.r[8]+self.grid.r[9])/2
        self.assertAlmostEqual(float(m.sample_at_positions(f, np.array([x]), self.grid, f[-1])[0]),
                               float((f[8]+f[9])/2), places=13)

    def test_manufactured_quadratic_heat_step(self):
        # Exact discrete quadratic: divergence of k grad(a+b*r²) = 4kb.
        # Construct the preceding state and the ambient so this profile is
        # the known solution, independently of tridiagonal coefficient assembly.
        g, p = self.grid, self.p
        dt, b = .7, 2000.
        expected = 29 + b*g.r**2
        old = expected - dt*4*p.k*b/(p.rho*p.cp)
        ts = expected[-1] + b*g.R*g.dr
        external = ts + 2*p.k*b*g.R/p.hT
        actual = m.solve_temperature_step(old, dt, g, external)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-11)

    def test_surface_flux_continuity(self):
        dr, p = self.grid.dr, self.p
        ts = m.surface_temperature(30, 45, dr)
        self.assertAlmostEqual(2*p.k/dr*(ts-30), p.hT*(45-ts), places=9)
        cs = m.surface_moisture(2., .02, dr)
        self.assertAlmostEqual(2*float(m.D_of_C(2.))/dr*(2.-cs), p.hm*(cs-.02), places=15)

    def test_nonconvergence_warns(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            _, n, ok, _ = m.solve_moisture_step(np.full(30,2.55), 1, self.grid, .02, max_iter=1)
        self.assertEqual(n, 1)
        self.assertFalse(ok)
        self.assertTrue(any(issubclass(x.category, RuntimeWarning) for x in caught))

    def test_ambient_interpolation_and_range(self):
        a = m.Ambient(np.array([0.,60.]), np.array([28.,34.]), np.array([.02,.08]))
        t,c = m.ambient_conditions(30.5,a)
        self.assertAlmostEqual(float(t),31.05)
        self.assertAlmostEqual(float(c),.0505)
        with self.assertRaises(ValueError): a(61)
        with self.assertRaises(ValueError): m.D_of_C(np.array([0.]))

    def test_fractional_steps_and_global_balance(self):
        a=m.Ambient(np.array([0.,10.]),np.array([28.,30.]),np.array([.02,.02]))
        s=m.run_simulation(N=30, dt=.5, t_end=10, ambient=a)
        np.testing.assert_array_equal(s.times,np.arange(11))
        self.assertEqual(len(s.step_times),20)
        self.assertTrue(s.converged.all())
        self.assertLess(np.max(np.abs(s.heat_balance)),1e-8)
        self.assertLess(np.max(np.abs(s.moisture_balance)),1e-12)
        self.assertTrue(np.all(np.diff(s.mean_C_steps)<0))


if __name__ == '__main__':
    unittest.main(verbosity=2)
