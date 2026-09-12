"""Problem 2: coupled variable-property FVM, reusing problem1's core.

No plotting or mesh/time-step validation is run. All states use float64.
Run: python problem2.py
Then: node export_result2.mjs
"""
from dataclasses import dataclass
import argparse
import json
import sys
import warnings

import problem1 as p1
import numpy as np

ROOT = p1.ROOT
OUTPUT = ROOT / 'outputs/problem2'


def _positive_C(C):
    C = np.asarray(C, dtype=np.float64)
    if not np.isfinite(C).all() or np.any(C <= 0):
        raise ValueError('C must be finite and positive; no clipping is applied.')
    return C


def rho_of_C(C):
    return 650.0 + 128.0 * _positive_C(C)


def cp_of_C(C):
    C = _positive_C(C)
    return 1450.0 + 2736.0 * C / (C + 1.0)


def k_of_C(C):
    C = _positive_C(C)
    return 0.21 + 0.38 * C / (C + 1.0)


def D_of_CT(C, T_C):
    C = _positive_C(C)
    T_K = np.asarray(T_C, dtype=np.float64) + 273.15
    if not np.isfinite(T_K).all() or np.any(T_K <= 0):
        raise ValueError('Absolute temperature must be finite and positive.')
    D = 2.4e-3 * np.exp(-0.45 / C) * np.exp(-3850.0 / T_K)
    if not np.isfinite(D).all() or np.any(D <= 0):
        raise FloatingPointError('Invalid/underflowed diffusivity; no floor is applied.')
    return D


def _harmonic_faces(values):
    return 2 * values[:-1] * values[1:] / (values[:-1] + values[1:])


def solve_coupled_step(T_old, C_old, dt, grid, T_inf, C_inf,
                       params=p1.Parameters(), tol_T=1e-8, tol_C=1e-9,
                       max_iter=50, time_new=None, material=None):
    """Gauss-Seidel Picard: thermal solve then D(C_iter,T_new) moisture solve.

    Old-time RHS states stay fixed throughout all coupled iterations.
    Returns T, C, count, converged, err_T, err_C. Failed steps warn explicitly.
    """
    if dt <= 0 or tol_T <= 0 or tol_C <= 0 or max_iter < 1:
        raise ValueError('Invalid time step or Picard control.')
    rho_fn, cp_fn, k_fn, D_fn = (material if material is not None else
                               (rho_of_C, cp_of_C, k_of_C, D_of_CT))
    T_iter = np.asarray(T_old, dtype=np.float64).copy()
    C_iter = np.asarray(C_old, dtype=np.float64).copy()
    for iteration in range(1, max_iter + 1):
        rho = rho_fn(C_iter)
        cp = cp_fn(C_iter)
        k = k_fn(C_iter)
        hT_eff = 1.0 / (1.0 / params.hT + grid.dr / (2 * k[-1]))
        T_new = p1._linear_step(
            T_old, rho * cp * grid.V / dt,
            grid.faces[1:-1] * _harmonic_faces(k) / grid.dr,
            grid.R * hT_eff, T_inf)
        D = D_fn(C_iter, T_new)
        hm_eff = 1.0 / (1.0 / params.hm + grid.dr / (2 * D[-1]))
        C_new = p1._linear_step(
            C_old, grid.V / dt,
            grid.faces[1:-1] * _harmonic_faces(D) / grid.dr,
            grid.R * hm_eff, C_inf)
        err_T = float(np.max(np.abs(T_new - T_iter)))
        err_C = float(np.max(np.abs(C_new - C_iter)))
        if err_T < tol_T and err_C < tol_C:
            return T_new, C_new, iteration, True, err_T, err_C
        T_iter, C_iter = T_new, C_new
    warnings.warn(f'Problem 2 Picard did not converge at t={time_new} s '
                  f'after {max_iter} iterations: err_T={err_T:.6e}, err_C={err_C:.6e}.',
                  RuntimeWarning, stacklevel=2)
    return T_new, C_new, max_iter, False, err_T, err_C


@dataclass
class Problem2Result:
    grid: p1.Grid
    params: p1.Parameters
    dt: float
    times: np.ndarray
    T: np.ndarray
    C: np.ndarray
    ambient_T: np.ndarray
    ambient_C: np.ndarray
    step_times: np.ndarray
    step_sizes: np.ndarray
    iterations: np.ndarray
    converged: np.ndarray
    err_T: np.ndarray
    err_C: np.ndarray


def run_problem2(N=100, dt=1.0, t_end=10800, ambient=None, params=p1.Parameters()):
    """Integrate with exact integer-second storage; dt is maximum step size."""
    if not np.isfinite(dt) or dt <= 0 or t_end < 1 or int(t_end) != t_end:
        raise ValueError('Require dt>0 and positive integer t_end.')
    if ambient is None:
        ambient = p1.load_attachment1()
    grid = p1.Grid(N, params.R)
    times = np.arange(int(t_end) + 1, dtype=np.float64)
    at, ac = p1.ambient_conditions(times, ambient)
    T = np.empty((len(times), N), dtype=np.float64)
    C = np.empty_like(T)
    T[0], C[0] = params.T0, params.C0
    current_T, current_C = T[0].copy(), C[0].copy()
    step_times, step_sizes, iterations, converged, errT, errC = [], [], [], [], [], []
    t = 0.0
    for second in range(1, int(t_end) + 1):
        while t < second - 1e-12:
            next_t = min(t + dt, float(second))
            step = next_t - t
            T_inf, C_inf = p1.ambient_conditions(next_t, ambient)
            current_T, current_C, count, ok, eT, eC = solve_coupled_step(
                current_T, current_C, step, grid, T_inf, C_inf, params,
                time_new=next_t)
            step_times.append(next_t); step_sizes.append(step)
            iterations.append(count); converged.append(ok); errT.append(eT); errC.append(eC)
            t = next_t
        T[second], C[second] = current_T, current_C
        if second % 1800 == 0:
            print(f'Problem 2: {second}/10800 s', flush=True)
    return Problem2Result(grid, params, dt, times, T, C, at, ac,
                          np.array(step_times), np.array(step_sizes), np.array(iterations),
                          np.array(converged), np.array(errT), np.array(errC))


def reconstruct_surface(T_last, C_last, T_inf, C_inf, dr, params=p1.Parameters(), material=None):
    """Shared problem-2/3 Robin reconstruction using final center properties."""
    k_fn, D_fn = (material[2:] if material is not None else (k_of_C, D_of_CT))
    k_last = k_fn(C_last)
    D_last = D_fn(C_last, T_last)
    aT = 2 * k_last / dr
    aC = 2 * D_last / dr
    Ts = (aT * T_last + params.hT * T_inf) / (aT + params.hT)
    Cs = (aC * C_last + params.hm * C_inf) / (aC + params.hm)
    return Ts, Cs


def sample_problem2(sim, positions):
    """Recompute boundary coefficients from the final converged center states."""
    Ts, Cs = reconstruct_surface(sim.T[:, -1], sim.C[:, -1], sim.ambient_T,
                                 sim.ambient_C, sim.grid.dr, sim.params)
    T = np.array([p1.sample_at_positions(f, positions, sim.grid, surface)
                  for f, surface in zip(sim.T, Ts)], dtype=np.float64)
    C = np.array([p1.sample_at_positions(f, positions, sim.grid, surface)
                  for f, surface in zip(sim.C, Cs)], dtype=np.float64)
    # As in problem 1: store the prescribed uniform initial condition at t=0.
    T[0], C[0] = sim.params.T0, sim.params.C0
    return T, C


def save_problem2(sim, out=OUTPUT):
    out.mkdir(parents=True, exist_ok=True)
    positions = np.linspace(0, sim.grid.R, 21)
    T, C = sample_problem2(sim, positions)
    np.savez_compressed(out / f'fields_N{sim.grid.N}_dt{sim.dt:g}.npz',
        times_s=sim.times, r_centers_m=sim.grid.r, T_C=sim.T, C_kg_kg=sim.C,
        positions_m=positions, sampled_T_C=T, sampled_C_kg_kg=C,
        ambient_T_C=sim.ambient_T, ambient_C_kg_kg=sim.ambient_C,
        step_times_s=sim.step_times, step_sizes_s=sim.step_sizes,
        picard_iterations=sim.iterations, picard_converged=sim.converged,
        picard_err_T=sim.err_T, picard_err_C=sim.err_C)
    p1._csv(out / 'coupled_picard_iterations.csv',
            ['time_s', 'dt_s', 'iterations', 'converged', 'err_T_C', 'err_C_kg_kg'],
            zip(sim.step_times, sim.step_sizes, sim.iterations, sim.converged, sim.err_T, sim.err_C))
    payload = {'template': str(ROOT / 'A题/A题/附件/附件3/result2.xlsx'),
               'times': sim.times[1:].astype(int).tolist(),
               'positions_cm': np.round(positions * 100, 10).tolist(),
               '温度': np.round(T[1:], 4).tolist(), '水分浓度': np.round(C[1:], 4).tolist()}
    (out / 'xlsx_data.json').write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    lines = ['# 问题2计算结果', '',
             f'N={sim.grid.N}，dt={sim.dt:g} s；固定半径0.02 m，耦合 Picard，'
             '附录3物性，D中温度使用 Kelvin。未进行网格或时间步验证，未生成图片。', '']
    for values, title, filename in [(T, '表3：3小时内药材温度（℃）', 'table3_temperature.csv'),
                                     (C, '表4：3小时内药材水分浓度（kg/kg）', 'table4_moisture.csv')]:
        rows = [[second / 3600] + [f'{v:.4f}' for v in values[second, ::5]]
                for second in range(1800, len(sim.times), 1800)]
        p1._csv(out / filename, ['时间/h', '0 cm', '0.5 cm', '1 cm', '1.5 cm', '2 cm'], rows)
        lines += [f'## {title}', '', '| 时间/h | 0 cm | 0.5 cm | 1 cm | 1.5 cm | 2 cm |',
                  '|---:|---:|---:|---:|---:|---:|']
        lines += ['| ' + ' | '.join(map(str, row)) + ' |' for row in rows]
        lines.append('')
    failed = sim.step_times[~sim.converged].tolist()
    counts = {str(int(k)): int(v) for k, v in zip(*np.unique(sim.iterations, return_counts=True))}
    status = {'N': sim.grid.N, 'dt_s': sim.dt, 'iteration_histogram': counts,
              'unconverged_step_times_s': failed}
    (out / 'run_status.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
    lines += [f'耦合 Picard 迭代次数分布：{counts}。未收敛步数：{len(failed)}。', '',
              '完整每秒结果见 result2.xlsx；原始双精度单元场及输出场见 NPZ；逐步迭代次数见 coupled_picard_iterations.csv。']
    (out / '问题2结果.md').write_text('\n'.join(lines), encoding='utf-8')
    print('\n'.join(lines), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--N', type=int, default=100)
    parser.add_argument('--dt', type=float, default=1.0)
    parser.add_argument('--out', type=p1.Path, default=OUTPUT)
    args = parser.parse_args()
    save_problem2(run_problem2(args.N, args.dt), args.out)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
