"""Bounded regression and mapping tests; no new physical model or figures."""
import unittest
import numpy as np
import problem4 as q


class Problem4Tests(unittest.TestCase):
    def test_problem2_regression(self):
        old=np.load(q.ROOT/'outputs/problem2/fields_N100_dt1.npz')
        sim=q.p2.run_problem2(t_end=1800)
        np.testing.assert_array_equal(sim.T,old['T_C'][:1801])
        np.testing.assert_array_equal(sim.C,old['C_kg_kg'][:1801])

    def test_material_metrics(self):
        g=q.MaterialGrid(50,.012)
        np.testing.assert_allclose(g.faces[1:-1]/g.dr,g.xi.faces[1:-1]/g.xi.dr,rtol=1e-15)
        np.testing.assert_allclose(g.V,g.R**2*np.diff(g.xi.faces**2)/2,rtol=1e-15)
        self.assertEqual(g.dr/2,g.R*g.xi.dr/2)

    def test_shrinkage_without_gradient_has_no_extra_source(self):
        g=q.MaterialGrid(10,.02)
        T=np.full(10,28.);C=np.full(10,2.55)
        newT,newC,_,ok,_,_=q.solve_coupled_shrinking_step(T,C,1.,g,.013,28.,2.55)
        self.assertTrue(ok)
        np.testing.assert_allclose(newT,T,rtol=0,atol=1e-12)
        np.testing.assert_allclose(newC,C,rtol=0,atol=1e-12)

    def test_radius_units_interpolation_and_bounds(self):
        data=q.load_attachment2()
        self.assertEqual(q.radius_at_time(0,data),.02)
        self.assertAlmostEqual(q.radius_at_time(900,data),(.02+.01873)/2)
        with self.assertRaises(ValueError):
            q.radius_at_time(data.times[-1]+.5,data)
        with self.assertRaises(ValueError):
            q.radius_at_time(-1,data)

    def test_inside_surface_outside_sampling(self):
        xi=q.p1.Grid(20,1.)
        field=2.-xi.r**2
        values=q.sample_physical_radius(field,np.array([0,.006,.012,.013]),.012,xi,1.)
        self.assertAlmostEqual(values[0],2.)
        self.assertEqual(values[2],1.)
        self.assertTrue(np.isnan(values[3]))
        self.assertTrue(np.isfinite(values[:3]).all())

    def test_material_formulas_and_kelvin(self):
        C=2.55;T=28.
        self.assertEqual(float(q.rho_q4(C)),760+90*C)
        self.assertAlmostEqual(float(q.cp_q4(C)),1850+2150*C/(C+1))
        self.assertAlmostEqual(float(q.k_q4(C)),.12+.20*C/(C+1))
        self.assertEqual(float(q.D_q4(C,T)),float(4.2e-4*np.exp(-.30/C)*np.exp(-3850/(T+273.15))))


if __name__=='__main__':
    unittest.main(verbosity=2)
