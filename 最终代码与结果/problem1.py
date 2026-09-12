"""2026 A, problem 1: specified cell-centered radial FVM / backward Euler.

Run: python problem1.py --export-xlsx
All radial coordinates are metres internally. Temperature is in deg C (only
temperature differences occur); C is dry-basis kg/kg. No fitted parameters.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, asdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import warnings

ROOT = Path(__file__).resolve().parent
# Optional project-local dependencies; never write to the bundled runtime.
if (ROOT / '.runtime_python').is_dir():
    sys.path.insert(0, str(ROOT / '.runtime_python'))
import numpy as np
from scipy.linalg import solve_banded
import openpyxl  # read-only attachment input and output verification

ATTACHMENT = ROOT / 'A题/A题/附件/附件1.xlsx'
TEMPLATE = ROOT / 'A题/A题/附件/附件3/result1.xlsx'
OUTPUT = ROOT / 'outputs/problem1'


@dataclass(frozen=True)
class Parameters:
    R: float = 0.02
    rho: float = 820.0
    cp: float = 2600.0
    k: float = 0.36
    hT: float = 25.0
    hm: float = 8e-7
    T0: float = 28.0
    C0: float = 2.55


@dataclass
class Grid:
    N: int = 100
    R: float = 0.02

    def __post_init__(self):
        if not isinstance(self.N, (int, np.integer)) or self.N < 2 or self.R <= 0:
            raise ValueError('Require integer N >= 2 and R > 0.')
        self.dr = self.R / self.N
        self.faces = np.arange(self.N + 1) * self.dr
        self.r = (np.arange(self.N) + 0.5) * self.dr
        self.V = np.diff(self.faces ** 2) / 2


@dataclass
class Ambient:
    time: np.ndarray
    temperature: np.ndarray
    moisture: np.ndarray

    def __call__(self, t):
        t = np.asarray(t, dtype=float)
        if not np.all(np.isfinite(t)) or np.any(t < self.time[0]) or np.any(t > self.time[-1]):
            raise ValueError('Ambient query outside attachment range; extrapolation is disabled.')
        return (np.interp(t, self.time, self.temperature),
                np.interp(t, self.time, self.moisture))


_ambient = None


def load_attachment1(path=ATTACHMENT):
    """Read three columns (s, deg C, kg/kg), validate, and initialize ambient."""
    global _ambient
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(workbook.active.values)
    finally:
        workbook.close()
    if len(rows) < 3 or tuple(rows[0][:3]) != ('时间', '温度', '水分浓度'):
        raise ValueError('Expected attachment headers 时间, 温度, 水分浓度.')
    data = []
    for rowno, row in enumerate(rows[1:], 2):
        if all(x is None for x in row):
            continue
        if len(row) < 3 or any(not isinstance(x, (int, float)) for x in row[:3]):
            raise ValueError(f'Invalid numeric input in row {rowno}.')
        data.append(row[:3])
    a = np.asarray(data, dtype=float)
    if not np.all(np.isfinite(a)) or np.any(np.diff(a[:, 0]) <= 0) or np.any(a[:, 2] < 0):
        raise ValueError('Nonfinite data, unordered/duplicate times, or negative moisture.')
    _ambient = Ambient(a[:, 0], a[:, 1], a[:, 2])
    return _ambient


def ambient_conditions(t, ambient=None):
    """Linear interpolation at integer or fractional seconds."""
    source = ambient if ambient is not None else _ambient
    if source is None:
        raise RuntimeError('Call load_attachment1() first or supply an Ambient instance.')
    return source(t)


def D_of_C(C):
    """Specified diffusivity; invalid C is an error, never clipped or fitted."""
    C = np.asarray(C, dtype=float)
    if not np.all(np.isfinite(C)) or np.any(C <= 0):
        raise ValueError('D(C) requires finite, strictly positive C.')
    D = 7e-9 * np.exp(-0.89 / C)
    if np.any(D <= 0):
        raise FloatingPointError('Diffusivity underflow; no coefficient floor applied.')
    return D


def _linear_step(old, storage, conductance, boundary, external):
    """Conservative tridiagonal system; one shared conductance per inner face."""
    n = len(old)
    west = np.r_[0.0, conductance]
    east = np.r_[conductance, 0.0]
    ab = np.zeros((3, n))
    ab[0, 1:] = -east[:-1]
    ab[1] = storage + west + east
    ab[1, -1] += boundary
    ab[2, :-1] = -west[1:]
    rhs = storage * old
    rhs[-1] += boundary * external
    return solve_banded((1, 1), ab, rhs, check_finite=True)


def solve_temperature_step(T_old, dt, grid, T_inf, params=Parameters()):
    h_eff = 1.0 / (1.0 / params.hT + grid.dr / (2 * params.k))
    return _linear_step(T_old, params.rho * params.cp * grid.V / dt,
                        grid.faces[1:-1] * params.k / grid.dr,
                        grid.R * h_eff, T_inf)


def solve_moisture_step(C_old, dt, grid, C_inf, params=Parameters(),
                        tol=1e-9, max_iter=50):
    """Return (field, iteration count, converged, last infinity-norm update)."""
    if tol <= 0 or max_iter < 1:
        raise ValueError('Require tol > 0 and max_iter >= 1.')
    C_iter = np.asarray(C_old, dtype=float).copy()
    for iteration in range(1, max_iter + 1):
        D = D_of_C(C_iter)
        D_face = 2 * D[:-1] * D[1:] / (D[:-1] + D[1:])
        h_eff = 1.0 / (1.0 / params.hm + grid.dr / (2 * D[-1]))
        C_new = _linear_step(C_old, grid.V / dt,
                            grid.faces[1:-1] * D_face / grid.dr,
                            grid.R * h_eff, C_inf)
        error = float(np.max(np.abs(C_new - C_iter)))
        if error < tol:
            return C_new, iteration, True, error
        C_iter = C_new
    warnings.warn(f'Picard failed after {max_iter} iterations: update={error:.6e}, '
                  f'dt={dt:g} s; returned field is unconverged.', RuntimeWarning, stacklevel=2)
    return C_new, max_iter, False, error


def surface_temperature(T_last, T_inf, dr, params=Parameters()):
    a = 2 * params.k / dr
    return (a * T_last + params.hT * T_inf) / (a + params.hT)


def surface_moisture(C_last, C_inf, dr, params=Parameters()):
    a = 2 * D_of_C(C_last) / dr
    return (a * C_last + params.hm * C_inf) / (a + params.hm)


def sample_at_positions(field, positions, grid, surface_value):
    """Interpolate in metres, with symmetric center and reconstructed surface.

    Between the first two centers use their line. In the tiny interval between
    r=0 and r_0 use the symmetric quadratic through the first two centers; in
    (r_last,R) use the last-center-to-true-surface line. No center is called a
    surface. The exact requested center value is (9*f[0]-f[1])/8.
    """
    positions = np.asarray(positions, dtype=float)
    if np.any(~np.isfinite(positions)) or np.any(positions < 0) or np.any(positions > grid.R):
        raise ValueError('Sampling coordinates must be finite and in [0,R] metres.')
    center = (9 * field[0] - field[1]) / 8
    sampled = np.interp(positions, np.r_[0, grid.r, grid.R],
                        np.r_[center, field, surface_value])
    near = positions < grid.r[0]
    quadratic = center + (field[1] - field[0]) * positions ** 2 / (2 * grid.dr ** 2)
    return np.where(near, quadratic, sampled)


@dataclass
class Simulation:
    grid: Grid
    params: Parameters
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
    updates: np.ndarray
    mean_C_steps: np.ndarray
    heat_balance: np.ndarray
    moisture_balance: np.ndarray
    all_steps_finite: bool


def run_simulation(N=100, dt=1.0, t_end=1800, ambient=None, params=Parameters()):
    """Save every integer second, splitting steps at output times if necessary.

    dt is the maximum step; for default 1 and comparison 0.5 it is exact.
    Diagnostics cover every integration step, including fractional times.
    """
    if not np.isfinite(dt) or dt <= 0 or not np.isfinite(t_end) or t_end < 1 or int(t_end) != t_end:
        raise ValueError('Require dt > 0 and positive integer t_end.')
    if ambient is None:
        ambient = _ambient if _ambient is not None else load_attachment1()
    grid = Grid(N, params.R)
    times = np.arange(int(t_end) + 1, dtype=float)
    at, ac = ambient_conditions(times, ambient)
    T = np.empty((len(times), N)); C = np.empty_like(T)
    T[0] = params.T0; C[0] = params.C0
    current_T = T[0].copy(); current_C = C[0].copy()
    step_times, sizes, counts, flags, updates = [], [], [], [], []
    means = [params.C0]; heat_balance, moisture_balance = [], []
    finite = True
    t = 0.0
    for second in range(1, int(t_end) + 1):
        while t < second - 1e-12:
            next_t = min(t + dt, float(second))
            step = next_t - t
            T_inf, C_inf = ambient_conditions(next_t, ambient)
            new_T = solve_temperature_step(current_T, step, grid, T_inf, params)
            new_C, count, ok, update = solve_moisture_step(current_C, step, grid, C_inf, params)
            if not ok:
                warnings.warn(f'Unconverged moisture step at t={next_t:g} s.', RuntimeWarning)
            finite = finite and bool(np.isfinite(new_T).all() and np.isfinite(new_C).all())
            Ts = surface_temperature(new_T[-1], T_inf, grid.dr, params)
            Cs = surface_moisture(new_C[-1], C_inf, grid.dr, params)
            heat_balance.append(float(params.rho * params.cp * np.dot(grid.V, new_T-current_T)
                                      + step * grid.R * params.hT * (Ts-T_inf)))
            moisture_balance.append(float(np.dot(grid.V, new_C-current_C)
                                          + step * grid.R * params.hm * (Cs-C_inf)))
            means.append(float(np.dot(grid.V, new_C) / grid.V.sum()))
            step_times.append(next_t); sizes.append(step); counts.append(count)
            flags.append(ok); updates.append(update)
            current_T, current_C, t = new_T, new_C, next_t
        T[second] = current_T; C[second] = current_C
    return Simulation(grid, params, dt, times, T, C, at, ac,
                      np.array(step_times), np.array(sizes), np.array(counts),
                      np.array(flags), np.array(updates), np.array(means),
                      np.array(heat_balance), np.array(moisture_balance), finite)


def sampled_fields(sim, positions):
    sampled_T, sampled_C = [], []
    for i, t in enumerate(sim.times):
        if t == 0:
            # Initial condition is uniform. Robin reconstruction applies for t>0;
            # C0 and ambient C(0) are incompatible at the initial surface corner.
            sampled_T.append(np.full(len(positions), sim.params.T0))
            sampled_C.append(np.full(len(positions), sim.params.C0))
            continue
        Ts = surface_temperature(sim.T[i, -1], sim.ambient_T[i], sim.grid.dr, sim.params)
        Cs = surface_moisture(sim.C[i, -1], sim.ambient_C[i], sim.grid.dr, sim.params)
        sampled_T.append(sample_at_positions(sim.T[i], positions, sim.grid, Ts))
        sampled_C.append(sample_at_positions(sim.C[i], positions, sim.grid, Cs))
    return np.asarray(sampled_T), np.asarray(sampled_C)


def validation_checks(sim):
    edges_T, edges_C = sampled_fields(sim, np.array([0.0, sim.grid.R]))
    early = (sim.times >= 1) & (sim.times <= min(300, sim.times[-1]))
    mean_changes = np.diff(sim.mean_C_steps)
    return {
        'N': sim.grid.N, 'dt_s': sim.dt,
        'all_steps_and_samples_finite': bool(sim.all_steps_finite and np.isfinite(sim.T).all()
            and np.isfinite(sim.C).all() and np.isfinite(edges_T).all() and np.isfinite(edges_C).all()),
        'T_min_C': float(min(sim.T.min(), edges_T.min())),
        'T_max_C': float(max(sim.T.max(), edges_T.max())),
        'C_min_kg_kg': float(min(sim.C.min(), edges_C.min())),
        'C_max_kg_kg': float(max(sim.C.max(), edges_C.max())),
        'mean_C_initial': float(sim.mean_C_steps[0]),
        'mean_C_final': float(sim.mean_C_steps[-1]),
        'mean_C_overall_decreases': bool(sim.mean_C_steps[-1] < sim.mean_C_steps[0]),
        'mean_C_nondecreasing_step_count_tol_1e_12': int(np.count_nonzero(mean_changes > 1e-12)),
        'max_mean_C_step_change': float(mean_changes.max()),
        'early_T_surface_above_center_fraction': float(np.mean(edges_T[early, 1] > edges_T[early, 0])),
        'early_C_surface_below_center_fraction': float(np.mean(edges_C[early, 1] < edges_C[early, 0])),
        'picard_min': int(sim.iterations.min()), 'picard_max': int(sim.iterations.max()),
        'picard_mean': float(sim.iterations.mean()),
        'picard_histogram': {str(int(k)): int(v) for k, v in zip(*np.unique(sim.iterations, return_counts=True))},
        'unconverged_steps': int(np.count_nonzero(~sim.converged)),
        'max_final_picard_update': float(sim.updates.max()),
        'max_abs_heat_balance_J_per_m_per_2pi': float(np.max(np.abs(sim.heat_balance))),
        'max_abs_moisture_balance_m2_per_step': float(np.max(np.abs(sim.moisture_balance))),
    }


def _difference(a, b):
    d = np.abs(a-b)
    i, j = np.unravel_index(np.argmax(d), d.shape)
    return {'max_all': float(d.max()), 'rms_all': float(np.sqrt(np.mean(d**2))),
            'max_at_1800s': float(d[-1].max()), 'max_time_s': int(i+1),
            'max_radius_cm': float(j*0.1)}


def convergence_test(ambient=None, base=None):
    """Compare common 1 s times and 0.1 cm positions, without rounding."""
    runs = {}
    for n, dt in [(50, 1.0), (100, 1.0), (200, 1.0), (100, 0.5)]:
        print(f'Convergence run N={n}, dt={dt}', flush=True)
        runs[(n, dt)] = (base if base is not None and base.grid.N == n and base.dt == dt
                         and base.times[-1] == 1800 else run_simulation(n, dt, ambient=ambient))
    positions = np.linspace(0, 0.02, 21)
    fields = {key: sampled_fields(sim, positions) for key, sim in runs.items()}
    pairs = [('space_50_100', (50, 1.0), (100, 1.0)),
             ('space_100_200', (100, 1.0), (200, 1.0)),
             ('space_50_200', (50, 1.0), (200, 1.0)),
             ('time_1_0.5', (100, 1.0), (100, 0.5))]
    metrics = {}
    for label, a, b in pairs:
        metrics[label] = {name: _difference(fields[a][j][1:], fields[b][j][1:])
                          for j, name in enumerate(['T', 'C'])}
    metrics['space_difference_ratio'] = {
        name: metrics['space_50_100'][name]['max_all'] / metrics['space_100_200'][name]['max_all']
        for name in ['T', 'C']}
    return runs, metrics


def _csv(path, headers, rows):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f); writer.writerow(headers); writer.writerows(rows)


def plot_results(sim, out=OUTPUT, metrics=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': ['Microsoft YaHei', 'DejaVu Sans'],
                         'axes.unicode_minus': False, 'font.size': 10, 'savefig.dpi': 180})
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    positions = np.linspace(0, sim.grid.R, 201)
    T, C = sampled_fields(sim, positions)
    typical = [t for t in [100, 300, 600, 900, 1200, 1500, 1800] if t <= sim.times[-1]]
    def save(fig, name):
        fig.savefig(out / (name+'.png'), bbox_inches='tight')
        fig.savefig(out / (name+'.svg'), bbox_inches='tight')
        plt.close(fig)
    for values, name, label, cmap in [(T, 'temperature', '温度 / °C', 'inferno'),
                                       (C, 'moisture', '干基含水率 / (kg/kg)', 'viridis')]:
        fig, ax = plt.subplots(figsize=(8, 4.8))
        fig.subplots_adjust(left=.13, right=.88, bottom=.15, top=.9)
        mesh = ax.pcolormesh(positions*100, sim.times/60, values, shading='auto', cmap=cmap,
                             rasterized=True)
        fig.colorbar(mesh, ax=ax, label=label)
        ax.set(xlabel='到圆心的距离 / cm', ylabel='时间 / min', title=f'问题 1：{label.split(" /")[0]}的时空变化')
        ax.set_ylim(0, sim.times[-1]/60)
        ax.set_yticks(np.arange(0, sim.times[-1]/60+1, 5))
        ax.tick_params(axis='y', labelleft=True)
        save(fig, name+'_space_time')
        fig, ax = plt.subplots(figsize=(8, 4.8), layout='constrained')
        for t in typical:
            ax.plot(positions*100, values[t], label=f'{t} s', linewidth=1.8)
        ax.set(xlabel='到圆心的距离 / cm', ylabel=label, xlim=(0, 2), title='典型时刻的径向分布')
        ax.grid(alpha=.2); ax.legend(ncol=2)
        save(fig, name+'_radial_profiles')
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), layout='constrained')
    axes[0].step(sim.step_times, sim.iterations, where='post')
    axes[0].set(xlabel='时间 / s', ylabel='Picard 迭代次数', title='每一步的迭代次数')
    axes[0].set_yticks(np.arange(sim.iterations.min(), sim.iterations.max()+1))
    axes[0].set_ylim(sim.iterations.min()-.5, sim.iterations.max()+.5)
    k, v = np.unique(sim.iterations, return_counts=True)
    axes[1].bar(k, v, width=.6)
    axes[1].set(xlabel='Picard 迭代次数', ylabel='时间步数', title='迭代次数分布', xticks=k)
    for x, y in zip(k, v): axes[1].annotate(str(y), (x, y), ha='center', va='bottom')
    axes[1].set_ylim(0, v.max()*1.15)
    save(fig, 'picard_iterations')
    if metrics:
        fig, axes = plt.subplots(1, 2, figsize=(10, 4), layout='constrained')
        labels = ['N50–N100', 'N100–N200', 'dt1–dt0.5']
        keys = ['space_50_100', 'space_100_200', 'time_1_0.5']
        for ax, field, unit in zip(axes, ['T', 'C'], ['温度差 / °C', '含水率差 / (kg/kg)']):
            vals = [metrics[x][field]['max_all'] for x in keys]
            ax.bar(labels, vals, color=['#526c9a', '#7399b5', '#cc9657'])
            ax.set(ylabel=unit, title='公共输出点的最大绝对差', yscale='log')
        save(fig, 'convergence_comparison')


def write_report(sim, metrics, checks, out, attachment):
    positions = np.arange(5)*0.005
    T, C = sampled_fields(sim, positions)
    selected = [100, 300, 600, 900, 1200, 1500, 1800]
    selected = [t for t in selected if t <= sim.times[-1]]
    def table(values):
        text = '| 时间 / s | 0 cm | 0.5 cm | 1 cm | 1.5 cm | 2 cm |\n|---:|---:|---:|---:|---:|---:|\n'
        return text + '\n'.join('| '+str(t)+' | '+' | '.join(f'{v:.4f}' for v in values[t])+' |' for t in selected)
    q = checks[0]
    report = [
        '# 问题 1 数值结果与验证报告',
        f'默认结果：N={sim.grid.N}，dt={sim.dt:g} s，时间 0–{sim.times[-1]:g} s。计算和验证均使用双精度未舍入数据，题目表格及 result1.xlsx 保留四位小数。',
        '## 模型和数值实现',
        '仅使用用户指定的一维圆柱径向温度扩散与非线性水分扩散模型，无蒸发潜热、轴向传输或收缩项。温度与水分独立推进；D 仅依赖 C。',
        '参数：R=0.02 m，rho=820 kg/m³，cp=2600 J/(kg·K)，k=0.36 W/(m·K)，hT=25 W/(m²·K)，hm=8e-7 m/s，T0=28 °C，C0=2.55 kg/kg；D(C)=7e-9 exp(-0.89/C) m²/s。没有参数拟合、裁剪或手工校正。',
        '单元中心 r_i=(i+0.5)dr，V_i=(r_e²-r_w²)/2。内部面采用共享通量，水分面扩散系数为调和平均；圆心面的半径为零。后向 Euler 在新时刻取线性插值环境值；三对角系统由 scipy.linalg.solve_banded 求解。',
        '温度表面等效系数为 1/(1/hT+dr/(2k))；水分表面等效系数为 1/(1/hm+dr/(2D_last))。Picard 从上一步 C 开始，每次重算 D、调和平均和表面等效系数，更新无穷范数严格小于 1e-9 才收敛，最多 50 次；失败会发出 RuntimeWarning 并记录失败时刻。',
        '内部输出采用相邻单元中心线性插值；圆心采用 (9f0-f1)/8；表面由半单元扩散与对流串联公式重建，D_last 使用最终 C_last 计算。对于 (0,r_0) 的额外绘图位置，用对称二次函数；对于 (r_last,R)，连接末单元中心与真实表面值。',
        '初始状态：t=0 保存全域 T0、C0。初始 C0 与环境 C_inf(0) 不同，初值与 Robin 边界在初始表面处不相容；从第一步起应用边界和表面重建，不改初值。Excel 模板要求从 t=1 开始，故填入 1–1800 s；NPZ 和 CSV 保留 t=0。',
        '内部长度统一用 m；图表输出转为 cm。温度使用 °C，因为本问只涉及温差和梯度，与使用 K 完全等价。',
        '## 附件读取',
        f'数据来源：`{Path(attachment).relative_to(ROOT) if Path(attachment).is_relative_to(ROOT) else attachment}`；SHA256：`{hashlib.sha256(Path(attachment).read_bytes()).hexdigest()}`。',
        f'共 {len(_ambient.time)} 个有效时刻，范围 {_ambient.time[0]:g}–{_ambient.time[-1]:g} s。逐列检查数值有限、时间严格递增、水分非负；区间内线性插值，区间外报错。',
        '## 表 1：药材温度 / °C', table(T),
        '## 表 2：药材干基含水率 / (kg/kg)', table(C),
        '## 数值收敛比较',
        '在相同的 t=1,…,1800 s、r=0,0.1,…,2 cm 上比较，各网格均按相同规则重建中心和表面。以下差值来自未舍入结果，细网格/小时间步只是数值参照，不是解析真解。',
        '| 比较 | 温度最大差 / °C | 温度 RMS / °C | 1800 s 温度最大差 | 含水率最大差 | 含水率 RMS | 1800 s 含水率最大差 |',
        '|---|---:|---:|---:|---:|---:|---:|',
    ]
    for name, m in metrics.items():
        if name == 'space_difference_ratio': continue
        report.append('| '+name+' | '+' | '.join(f'{m[field][stat]:.8g}' for field in ['T', 'C']
                         for stat in ['max_all', 'rms_all', 'max_at_1800s'])+' |')
    if metrics:
        ratio = metrics['space_difference_ratio']
        largest = metrics['space_100_200']['C']
        report.append(f'N=100 与 200 的含水率最大差 {largest["max_all"]:.8g} kg/kg 出现在 t={largest["max_time_s"]} s、r={largest["max_radius_cm"]:g} cm；1800 s 最大差降至 {largest["max_at_1800s"]:.8g} kg/kg。初始附近表面结果对空间分辨率更敏感。')
        report.append(f'相邻网格最大差之比（50–100 除以 100–200）：温度 {ratio["T"]:.4f}，含水率 {ratio["C"]:.4f}。比值大于 1 表示本次细化的最大差减小；初始边界不相容和表面输出重建可能影响全时域最大差的观测阶，不能只据三组网格断言严格二阶。')
        report.append('四位小数是提交格式，不代表已经证明四位小数的离散精度。dt=1 与 0.5 s 的比较只能量化时间步敏感性，未用它宣称时间收敛阶。')
    report.extend(['## 有限性、物理趋势及 Picard 检查',
                   '早期定义为 1–300 s。平均含水率为 sum(V_i C_i)/sum(V_i)，不是单元值算术平均。检查覆盖每个积分步，dt=0.5 s 的半秒状态也检查。',
                   '| N | dt / s | 有限性 | 平均 C 初值 → 末值 | 上升步数 (>1e-12) | 早期 Ts>Tc 比例 | 早期 Cs<Cc 比例 | Picard 最小/平均/最大 | 未收敛步数 |',
                   '|---:|---:|---|---|---:|---:|---:|---|---:|'])
    for check in checks:
        report.append(f'| {check["N"]} | {check["dt_s"]} | {check["all_steps_and_samples_finite"]} | '
            f'{check["mean_C_initial"]:.8f} → {check["mean_C_final"]:.8f} | '
            f'{check["mean_C_nondecreasing_step_count_tol_1e_12"]} | '
            f'{check["early_T_surface_above_center_fraction"]:.2%} | {check["early_C_surface_below_center_fraction"]:.2%} | '
            f'{check["picard_min"]}/{check["picard_mean"]:.4f}/{check["picard_max"]} | {check["unconverged_steps"]} |')
    report.extend([
        f'默认网格温度范围：[{q["T_min_C"]:.8f}, {q["T_max_C"]:.8f}] °C；含水率范围：[{q["C_min_kg_kg"]:.8f}, {q["C_max_kg_kg"]:.8f}] kg/kg。',
        f'默认 Picard 次数分布：{q["picard_histogram"]}；最大最终更新量 {q["max_final_picard_update"]:.6e}。逐步明细见 `picard_iterations.csv`，其他验证运行也分别保存逐步统计。',
        '## 离散守恒核对',
        '逐步计算 rho cp sum[V_i(T_new-T_old)]+dt R hT(T_s-T_inf) 与 sum[V_i(C_new-C_old)]+dt R hm(C_s-C_inf)。内部面通量应相消。这是离散代数守恒核对，不是完整物理能量或总水质量模型。',
        f'默认运行每步最大绝对热平衡残差：{q["max_abs_heat_balance_J_per_m_per_2pi"]:.6e} J/m（省略公共因子 2π）；最大含水率方程平衡残差：{q["max_abs_moisture_balance_m2_per_step"]:.6e} m²（C 视为质量比）。',
        '水分平衡使用最终 C 重算 D_last，因此也检验 Picard 收敛后边界通量的一致性。',
        '## 图形',
    ])
    for name, title in [('temperature_space_time', '温度时空分布'), ('moisture_space_time', '含水率时空分布'),
                        ('temperature_radial_profiles', '径向温度曲线'), ('moisture_radial_profiles', '径向含水率曲线'),
                        ('picard_iterations', 'Picard 统计'), ('convergence_comparison', '收敛比较')]:
        if (out / (name+'.png')).exists(): report.append(f'![{title}]({name}.png)')
    report.extend(['## 文件与复现',
        '`result1.xlsx`：按原模板两个工作表输出，1–1800 s、0–2 cm（步长 0.1 cm）。`table1_temperature.csv`、`table2_moisture.csv` 为题目两张表。',
        '`fields_N100_dt1.npz`（默认命名）保存 times_s、r_centers_m、T_C、C_kg_kg、环境值、积分步、Picard 次数及结果采样场；保留双精度。`temperature_full.csv`、`moisture_full.csv` 保存 t=0–1800 s 的 21 个指定物理位置。',
        '`convergence_metrics.json` 保存所有比较指标，`validation_checks.json` 保存各运行检查。图片同时提供 PNG 与 SVG。',
        '运行方式见项目 `README_problem1.md`。程序不会覆盖附件或原始模板。',
    ])
    # Keep contiguous Markdown table rows together; paragraphs need blank lines.
    joined = ''
    for item in report:
        separator = '\n' if joined.rstrip().endswith('|') and item.startswith('|') else '\n\n'
        joined += (separator if joined else '') + item
    (out / '数值验证报告.md').write_text(joined, encoding='utf-8')
    for values, name in [(T, 'table1_temperature.csv'), (C, 'table2_moisture.csv')]:
        _csv(out/name, ['时间/s', '0 cm', '0.5 cm', '1 cm', '1.5 cm', '2 cm'],
             [[t]+[f'{x:.4f}' for x in values[t]] for t in selected])


def save_outputs(sim, runs, metrics, out, attachment):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    positions = np.linspace(0, sim.grid.R, 21)
    T, C = sampled_fields(sim, positions)
    if not np.isfinite(T).all() or not np.isfinite(C).all():
        raise FloatingPointError('Nonfinite output samples.')
    np.savez_compressed(out/f'fields_N{sim.grid.N}_dt{sim.dt:g}.npz',
                        times_s=sim.times, r_centers_m=sim.grid.r, T_C=sim.T, C_kg_kg=sim.C,
                        positions_m=positions, sampled_T_C=T, sampled_C_kg_kg=C,
                        ambient_T_C=sim.ambient_T, ambient_C_kg_kg=sim.ambient_C,
                        step_times_s=sim.step_times, step_sizes_s=sim.step_sizes,
                        picard_iterations=sim.iterations, picard_converged=sim.converged,
                        picard_update=sim.updates, mean_C_steps=sim.mean_C_steps)
    for field, name in [(T, 'temperature_full.csv'), (C, 'moisture_full.csv')]:
        _csv(out/name, ['time_s']+[f'r_{p*100:g}_cm' for p in positions],
             [[int(t)]+list(row) for t, row in zip(sim.times, field)])
    all_runs = [sim]+[s for s in runs.values() if s is not sim]
    checks = [validation_checks(s) for s in all_runs]
    for s in all_runs:
        name = 'picard_iterations.csv' if s is sim else f'picard_N{s.grid.N}_dt{s.dt:g}.csv'
        _csv(out/name, ['time_s', 'dt_s', 'iterations', 'converged', 'max_update',
                       'volume_mean_C', 'heat_balance', 'moisture_balance'],
             zip(s.step_times, s.step_sizes, s.iterations, s.converged, s.updates,
                 s.mean_C_steps[1:], s.heat_balance, s.moisture_balance))
    for name, obj in [('convergence_metrics', metrics), ('validation_checks', checks), ('parameters', asdict(sim.params))]:
        (out/(name+'.json')).write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    # Typed numeric values rounded only at delivery, as required by the problem.
    payload = {'template': str(TEMPLATE), 'times': sim.times[1:].astype(int).tolist(),
               'positions_cm': np.round(positions*100, 10).tolist(),
               '温度': np.round(T[1:], 4).tolist(), '水分浓度': np.round(C[1:], 4).tolist()}
    (out/'xlsx_data.json').write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    plot_results(sim, out, metrics)
    write_report(sim, metrics, checks, out, attachment)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attachment', type=Path, default=ATTACHMENT)
    parser.add_argument('--N', type=int, default=100)
    parser.add_argument('--dt', type=float, default=1)
    parser.add_argument('--out', type=Path, default=OUTPUT)
    parser.add_argument('--skip-convergence', action='store_true')
    parser.add_argument('--export-xlsx', action='store_true')
    parser.add_argument('--node', default=None, help='Optional absolute Node executable')
    args = parser.parse_args()
    ambient = load_attachment1(args.attachment)
    print(f'Attachment: {len(ambient.time)} rows, {ambient.time[0]}..{ambient.time[-1]} s', flush=True)
    sim = run_simulation(args.N, args.dt, ambient=ambient)
    runs, metrics = ({}, {}) if args.skip_convergence else convergence_test(ambient, sim)
    checks = save_outputs(sim, runs, metrics, args.out, args.attachment)
    if args.export_xlsx:
        bundled = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
        node = args.node or (str(bundled) if bundled.exists() else shutil.which('node'))
        if not node:
            raise RuntimeError('Node.js is required for XLSX export; CSV/NPZ already saved.')
        subprocess.run([node, str(ROOT/'export_result1.mjs'), str(args.out.resolve())], check=True)
    print(json.dumps({'output': str(args.out.resolve()), 'checks': checks, 'convergence': metrics},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    main()
