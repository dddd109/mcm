"""Continue the exact problem-2 model to its first discrete drying event.

python problem3.py             # default and requested 3 sensitivity runs
python problem3.py --single    # only N=100, dt=1
node export_result3.mjs        # official result3.xlsx, no pictures
"""
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse
import json
from pathlib import Path
import sys
import warnings
import problem2 as p2
import numpy as np

p1 = p2.p1
ROOT = p1.ROOT
OUT = ROOT / 'outputs/problem3'


def ambient_problem3(t, ambient):
    """Use measured interpolation through 14400 inclusive; then (50,.05)."""
    if t <= 14400.0:
        return p1.ambient_conditions(t, ambient)
    return 50.0, 0.05


def drying_criterion(C, C_surface, grid, threshold=0.15):
    """Scan reconstructed center, every cell center, and reconstructed surface."""
    center = float((9*C[0]-C[1])/8)
    values = np.r_[center, C, C_surface]
    if not np.isfinite(values).all():
        raise FloatingPointError('Nonfinite field in drying criterion.')
    raw_index = int(np.argmax(values))
    maximum = float(values[raw_index])
    if raw_index == 0:
        index, radius = -1, 0.0
    elif raw_index == grid.N + 1:
        index, radius = grid.N, grid.R
    else:
        index = raw_index - 1
        radius = grid.r[index]
    return maximum < threshold, maximum, index, float(radius)


def run_until_dry(N=100, dt=1.0, t_max=259200.0, out=OUT):
    """Advance from t=0; save states every 60 s and at the first dry step.

    Failed Picard steps are recorded and abort the run instead of being used
    to certify a drying event. dt=.5 checks events at half-seconds, no temporal
    interpolation. No changes to the coupled solver or any physical formula.
    """
    if dt not in (1.0, 0.5) or t_max <= 0 or not float(t_max/dt).is_integer():
        raise ValueError('Use dt=1 or .5 s and a positive integral number of steps.')
    out = Path(out)
    label = f'N{N}_dt{dt:g}'
    folder = out / label
    folder.mkdir(parents=True, exist_ok=True)
    grid = p1.Grid(N)
    params = p1.Parameters()
    ambient = p1.load_attachment1()
    # Evaluate the requested piecewise linear interpolation once at every
    # integration time in the measured interval (mathematically identical).
    env_times = np.arange(int(14400/dt)+1, dtype=float)*dt
    env_T, env_C = p1.ambient_conditions(env_times, ambient)
    T = np.full(N, params.T0, dtype=np.float64)
    C = np.full(N, params.C0, dtype=np.float64)
    weights = grid.V / grid.V.sum()
    previous_max = params.C0
    previous_mean = params.C0
    mean_up_count = 0
    finite = positive = True
    T_min = T_max = params.T0
    C_min = C_max = params.C0
    saved_t, saved_T, saved_C = [0.0], [T.copy()], [C.copy()]
    max_steps = int(t_max/dt)
    # time, iterations, err_T, err_C, max_C, mean_C (all unrounded).
    diagnostics = np.empty((max_steps, 6), dtype=np.float64)
    attained = False
    for step in range(1, max_steps+1):
        t = step*dt
        T_inf, C_inf = ((env_T[step], env_C[step]) if step < len(env_times) else (50.0, .05))
        T_prev, C_prev = T, C
        T, C, count, converged, errT, errC = p2.solve_coupled_step(
            T_prev, C_prev, dt, grid, T_inf, C_inf, params, time_new=t)
        if not converged:
            (folder/'failure.json').write_text(json.dumps(
                {'time_s':t,'N':N,'dt':dt,'err_T':errT,'err_C':errC}), encoding='utf-8')
            raise RuntimeError(f'{label}: un-converged step at t={t}; drying time not certified.')
        Ts, Cs = p2.reconstruct_surface(T[-1], C[-1], T_inf, C_inf, grid.dr, params)
        attained, maximum, max_index, max_radius = drying_criterion(C, Cs, grid)
        finite = finite and bool(np.isfinite(T).all() and np.isfinite(Ts))
        positive = positive and bool(np.all(C > 0) and Cs > 0)
        if not finite or not positive:
            raise FloatingPointError(f'{label}: invalid state at t={t}.')
        center_T = float((9*T[0]-T[1])/8)
        center_C = float((9*C[0]-C[1])/8)
        T_min = min(T_min, float(T.min()), float(Ts), center_T)
        T_max = max(T_max, float(T.max()), float(Ts), center_T)
        C_min = min(C_min, float(C.min()), float(Cs), center_C)
        C_max = max(C_max, float(C.max()), float(Cs), center_C)
        mean = float(np.dot(weights, C))
        mean_up_count += int(mean > previous_mean + 1e-12)
        diagnostics[step-1] = [t, count, errT, errC, maximum, mean]
        if t % 60 == 0 or attained or step == max_steps:
            saved_t.append(t); saved_T.append(T.copy()); saved_C.append(C.copy())
        if t % 21600 == 0 or attained:
            print(f'{label}: t={t/3600:.4f} h, max C={maximum:.10f}', flush=True)
        if attained or step == max_steps:
            break
        previous_max, previous_mean = maximum, mean
    if not attained:
        warnings.warn(f'{label}: {t_max/3600:g} h内未达到烘干条件。', RuntimeWarning)
    summary = {
        'N':N,'dt_s':dt,'attained':bool(attained),'t_dry_s':float(t) if attained else None,
        't_dry_h':float(t/3600) if attained else None,'last_time_s':float(t),
        'previous_time_s':float(t-dt),'previous_max_C':float(previous_max),
        'domain_max_C':maximum,'max_index':max_index,'max_radius_m':max_radius,
        'max_radius_cm':max_radius*100,'max_at_surface':bool(max_index==N),
        'center_C_extrapolated':center_C,'surface_C':float(Cs),
        'center_T_C':center_T,'surface_T_C':float(Ts),
        'first_crossing_bracket':bool(attained and previous_max>=.15 and maximum<.15),
        'cells_and_surface_below_threshold':bool(attained and np.all(C<.15) and Cs<.15),
        'extrapolated_center_below_threshold':bool(center_C<.15),
        'finite_all_steps':bool(finite),'positive_C_all_steps':bool(positive and C_min>0),
        'mean_C_initial':params.C0,'mean_C_final':mean,'mean_C_increase_steps_tol_1e_12':mean_up_count,
        'mean_C_overall_decreases':bool(mean<params.C0),'all_picard_converged':True,
        'T_range_C':[T_min,T_max],'C_range':[C_min,C_max],
        'picard_min':int(diagnostics[:step,1].min()),'picard_max':int(diagnostics[:step,1].max()),
        'picard_histogram':{str(int(k)):int(v) for k,v in zip(*np.unique(diagnostics[:step,1],return_counts=True))},
        'environment_extension':'t<=14400 s: attachment1 linear interpolation; t>14400 s: T_inf=50 degC, C_inf=0.05',
    }
    (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    saved_t=np.array(saved_t); saved_T=np.array(saved_T); saved_C=np.array(saved_C)
    np.savez_compressed(folder/'states.npz',times_s=saved_t,r_centers_m=grid.r,
        T_C=saved_T,C_kg_kg=saved_C,previous_T_C=T_prev,previous_C_kg_kg=C_prev,
        final_T_C=T,final_C_kg_kg=C,diagnostics=diagnostics[:step])
    # A human-readable per-step record, including maximum C at every tested time.
    p1._csv(folder/'picard_and_drying_history.csv',
        ['time_s','picard_iterations','err_T','err_C','domain_max_C','volume_mean_C'],diagnostics[:step])
    return summary


def _worker(setting):
    n,dt,out=setting
    return run_until_dry(n,dt,out=out)


def validate_drying_time(out=OUT, workers=4):
    """The four requested runs; the N100/dt1 reference is computed only once."""
    settings=[(100,1.,out),(50,1.,out),(200,1.,out),(100,.5,out)]
    results=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(_worker,s) for s in settings]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results,key=lambda s:(s['N'], -s['dt_s']))


def write_report(results,out=OUT):
    out=Path(out)
    base=next(r for r in results if r['N']==100 and r['dt_s']==1)
    if not base['attained']:
        (out/'问题3结果与验证.md').write_text('# 问题3\n\n72 h内未达到烘干条件。\n',encoding='utf-8')
        return
    a=np.load(out/'N100_dt1/states.npz')
    grid=p1.Grid(100)
    ambient=p1.load_attachment1()
    positions=np.linspace(0,grid.R,21)
    sampled_T=[];sampled_C=[]
    for t,T,C in zip(a['times_s'],a['T_C'],a['C_kg_kg']):
        if t==0:
            sampled_T.append(np.full(21,28.));sampled_C.append(np.full(21,2.55));continue
        envT,envC=ambient_problem3(t,ambient)
        Ts,Cs=p2.reconstruct_surface(T[-1],C[-1],envT,envC,grid.dr)
        sampled_T.append(p1.sample_at_positions(T,positions,grid,Ts))
        sampled_C.append(p1.sample_at_positions(C,positions,grid,Cs))
    sampled_T=np.array(sampled_T); sampled_C=np.array(sampled_C)
    np.savez_compressed(out/'sampled_results.npz',times_s=a['times_s'],positions_m=positions,
                        T_C=sampled_T,C_kg_kg=sampled_C)
    selected=[i for i,t in enumerate(a['times_s']) if (t>0 and t%21600==0) or t==base['t_dry_s']]
    rows=[[a['times_s'][i]/3600]+[f'{v:.4f}' for v in sampled_C[i,::5]] for i in selected]
    p1._csv(out/'table5_moisture.csv',['时间/h','0 cm','0.5 cm','1 cm','1.5 cm','2 cm'],rows)
    p1._csv(out/'final_radial_distribution.csv',['r_cm','T_C','C_kg_kg'],
            zip(positions*100,sampled_T[-1],sampled_C[-1]))
    # Raw centers plus reconstructed endpoints, preserving full double precision.
    p1._csv(out/'final_cells_and_boundaries.csv',['r_m','T_C','C_kg_kg'],zip(
        np.r_[0,grid.r,grid.R],np.r_[base['center_T_C'],a['final_T_C'],base['surface_T_C']],
        np.r_[base['center_C_extrapolated'],a['final_C_kg_kg'],base['surface_C']]))
    payload={'template':str(ROOT/'A题/A题/附件/附件3/result3.xlsx'),
        'positions_cm':np.round(positions*100,10).tolist(),
        'times':a['times_s'][1:].tolist(),'values':np.round(sampled_C[1:],4).tolist()}
    (out/'xlsx_data.json').write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    lines=['# 问题3结果与验证','',
        '完全复用问题2 solve_coupled_step()、物性函数和问题1的有限体积网格/三对角求解器。'
        '只将问题2已有表面重建公式提取为共享函数，未修改求解方程、参数和迭代过程。', '',
        '**环境延拓假设：**0–14400 s 按附件1线性插值，t>14400 s 固定 T_inf=50℃、C_inf=0.05 kg/kg。'
        '14400 s 本身仍采用附件末行数据，之后切换恒定条件。','',
        f'默认 N=100、dt=1 s 的第一次达标时间为 **{base["t_dry_s"]:.0f} s = {base["t_dry_h"]:.8f} h**。','',
        '## 首次满足终止条件','',
        '| 时刻/s | 圆心、单元与真实表面的最大含水率/(kg/kg) | 条件 |','|---:|---:|---|',
        f'| {base["previous_time_s"]:.0f} | {base["previous_max_C"]:.12f} | ≥0.15 |',
        f'| {base["t_dry_s"]:.0f} | {base["domain_max_C"]:.12f} | <0.15 |','',
        '每个收敛时间步均扫描重建圆心、全部单元中心与真实表面，首次达标立即停止，不插值小数秒。'
        '检查采用未舍入双精度数据，四位小数的0.1500不表示未达标。','',
        f'最大值位置索引为 {base["max_index"]}，r={base["max_radius_cm"]:.8f} cm；'
        '其中索引−1表示由(9C0−C1)/8重建的几何圆心，索引N表示真实表面。'
        '终止判据按 max(C(0),C_i,C_s) 执行。','',
        '| 终点量 | 值 |','|---|---:|',
        f'| 圆心含水率（外推）/kg/kg | {base["center_C_extrapolated"]:.12f} |',
        f'| 表面含水率/kg/kg | {base["surface_C"]:.12f} |',
        f'| 圆心温度/℃ | {base["center_T_C"]:.10f} |',
        f'| 表面温度/℃ | {base["surface_T_C"]:.10f} |','',
        '## 表5：药材烘干过程的水分浓度（kg/kg）','',
        '| 时间/h | 0 cm | 0.5 cm | 1 cm | 1.5 cm | 2 cm |','|---:|---:|---:|---:|---:|---:|']
    lines += ['| '+f'{r[0]:.8f}'+' | '+' | '.join(r[1:])+' |' for r in rows]
    lines += ['', '## 网格与时间步敏感性','',
        '| N | dt/s | 首次达标时间/s | 时间/h | 达标前后夹逼成立 |',
        '|---:|---:|---:|---:|---|']
    for r in results:
        lines.append(f'| {r["N"]} | {r["dt_s"]} | {r["t_dry_s"]} | '
            f'{r["t_dry_h"] if r["attained"] else "72 h内未达标"} | {r["first_crossing_bracket"]} |')
    bykey={(r['N'],r['dt_s']):r for r in results}
    differences={}
    for name,keya,keyb in [('N50_N100',(50,1.),(100,1.)),('N100_N200',(100,1.),(200,1.)),
                          ('dt1_dt05',(100,1.),(100,.5))]:
        if keya in bykey and keyb in bykey and bykey[keya]['attained'] and bykey[keyb]['attained']:
            differences[name]=abs(bykey[keya]['t_dry_s']-bykey[keyb]['t_dry_s'])
            lines += ['',f'{name} 烘干时间绝对差：{differences[name]:g} s（{differences[name]/3600:.8f} h）。']
    if 'N50_N100' in differences and 'N100_N200' in differences:
        lines += ['', '相邻网格的烘干时间差随加密减小，存在收敛趋势。'
                  if differences['N100_N200']<differences['N50_N100'] else
                  '相邻网格的烘干时间差未随加密减小，不能据此判断存在网格收敛趋势。']
    lines += ['', 'dt=0.5 s 的验证在每个半秒积分步检查终止条件，其前一步为 t_dry−0.5 s；'
        '默认dt=1 s的终点仍是第一个达标整数秒。终点时间差同时包含离散误差和事件采样分辨率影响。','',
        '## 基本物理与迭代检查','',
        '| N | dt/s | 全程有限 | 全程C>0 | 平均C增加步数 (>1e-12) | Picard全收敛 | 终点全单元及表面<0.15 | 圆心外推<0.15 |',
        '|---:|---:|---|---|---:|---|---|---|']
    for r in results:
        lines.append(f'| {r["N"]} | {r["dt_s"]} | {r["finite_all_steps"]} | {r["positive_C_all_steps"]} | '
            f'{r["mean_C_increase_steps_tol_1e_12"]} | {r["all_picard_converged"]} | '
            f'{r["cells_and_surface_below_threshold"]} | {r["extrapolated_center_below_threshold"]} |')
    lines += ['',f'默认体积加权平均C从 {base["mean_C_initial"]:.8f} 降至 {base["mean_C_final"]:.10f} kg/kg。'
        f'默认Picard次数分布：{base["picard_histogram"]}。','',
        '## 输出与运行','',
        '`result3.xlsx`沿用题目单表模板，每隔60 s、0.1 cm给出含水率，并追加精确终点行（若非60秒整点）。'
        '`final_radial_distribution.csv`给出终点每0.1 cm的温度与含水率；'
        '`final_cells_and_boundaries.csv`含所有原始单元中心与圆心/表面重建值。','',
        '每组N/dt目录保存summary.json、states.npz（每60秒状态、终点及前一步完整状态、每步诊断）'
        '和picard_and_drying_history.csv（每步迭代次数、更新量、最大C与体积平均C）。所有中间数据不舍入。','',
        '运行：`python problem3.py`；仅默认运行：`python problem3.py --single`；'
        '根据已算数据重建报告：`python problem3.py --report-only`；填表：`node export_result3.mjs`。','',
        '本任务未生成图片；敏感性仅比较烘干时间，没有拟合参数或更改公式。']
    (out/'问题3结果与验证.md').write_text('\n'.join(lines),encoding='utf-8')
    (out/'validation_summary.json').write_text(json.dumps(
        {'runs':results,'drying_time_absolute_differences_s':differences},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'default':base,'differences_s':differences},ensure_ascii=False,indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--single',action='store_true')
    parser.add_argument('--report-only',action='store_true')
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    if args.report_only:
        results=[json.loads(p.read_text(encoding='utf-8')) for p in sorted(OUT.glob('N*/summary.json'))]
    else:
        results=[run_until_dry()] if args.single else validate_drying_time(workers=args.workers)
    write_report(results)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
