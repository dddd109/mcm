"""Uniform material-coordinate radial shrinkage, Appendix 4 properties.

Reuses problem2.solve_coupled_step and problem1's tridiagonal FVM. The fixed
xi grid is mapped to the new physical radius only through metric weights;
old T/C stay on the same material cells, with no regridding or extra advection.
python problem4.py                    # full requested calculation/validation
python problem4.py --single           # default only
python problem4.py --report-only      # rebuild deliverables from saved runs
node export_result4.mjs               # fill the official template, no pictures
"""
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import argparse
import json
import sys
import warnings
import problem3 as p3
import numpy as np

p2=p3.p2
p1=p3.p1
ROOT=p1.ROOT
OUT=ROOT/'outputs/problem4'
ATTACHMENT2=ROOT/'A题/A题/附件/附件2.xlsx'


@dataclass
class RadiusData:
    times: np.ndarray
    radii_m: np.ndarray

    def __call__(self,t):
        t=np.asarray(t,dtype=np.float64)
        if not np.isfinite(t).all() or np.any(t<self.times[0]) or np.any(t>self.times[-1]):
            raise ValueError(f'半径数据不足：只覆盖{self.times[0]:g}–{self.times[-1]:g} s，禁止外推。')
        return np.interp(t,self.times,self.radii_m)


_radius_data=None


def load_attachment2(path=ATTACHMENT2):
    global _radius_data
    w=p1.openpyxl.load_workbook(path,read_only=True,data_only=True)
    try:
        rows=list(w.active.values)
    finally:
        w.close()
    if tuple(rows[0][:2])!=('时间','半径'):
        raise ValueError('Expected attachment 2 headers: 时间, 半径.')
    values=[]
    for index,row in enumerate(rows[1:],2):
        if all(x is None for x in row):
            continue
        if len(row)<2 or any(not isinstance(x,(int,float)) for x in row[:2]):
            raise ValueError(f'Invalid radius input at row {index}.')
        values.append(row[:2])
    data=np.array(values,dtype=np.float64)
    if data.ndim!=2 or data.shape[0]<2 or not np.isfinite(data).all():
        raise ValueError('Invalid radius data.')
    if np.any(np.diff(data[:,0])<=0) or np.any(data[:,1]<=0) or data[0,0]!=0:
        raise ValueError('Require positive radii and strictly increasing time starting at 0.')
    _radius_data=RadiusData(data[:,0],data[:,1]/100.)
    if not np.isclose(_radius_data.radii_m[0],.02,rtol=0,atol=1e-6):
        warnings.warn('附件2初始半径与0.02 m不接近，继续使用附件原值。',RuntimeWarning)
    return _radius_data


def radius_at_time(t,data=None):
    source=data if data is not None else _radius_data
    if source is None:
        raise RuntimeError('Call load_attachment2 first.')
    return source(t)


def rho_q4(C):
    return 760.0+90.0*p2._positive_C(C)


def cp_q4(C):
    C=p2._positive_C(C)
    return 1850.0+2150.0*C/(C+1.0)


def k_q4(C):
    C=p2._positive_C(C)
    return .12+.20*C/(C+1.0)


def D_q4(C,T):
    C=p2._positive_C(C)
    T_K=np.asarray(T,dtype=np.float64)+273.15
    if not np.isfinite(T_K).all() or np.any(T_K<=0):
        raise ValueError('Invalid Kelvin temperature.')
    D=4.2e-4*np.exp(-.30/C)*np.exp(-3850./T_K)
    if not np.isfinite(D).all() or np.any(D<=0):
        raise FloatingPointError('Invalid diffusivity. No clipping applied.')
    return D


MATERIAL_Q4=(rho_q4,cp_q4,k_q4,D_q4)


class MaterialGrid:
    """Fixed xi topology with new-time physical metrics for the shared core.

    V=R_new² Vxi, faces/dr=xi_faces/dxi, boundary=R_new*h_eff.
    Thus the shared FVM is exactly the user-specified xi discrete equations.
    No old-radius volume multiplies the RHS, no conservative d(R²C)/dt term.
    """
    def __init__(self,N=100,R=.02):
        self.xi=p1.Grid(N,1.)
        self.N=N
        self.set_radius(R)

    def set_radius(self,R):
        if not np.isfinite(R) or R<=0:
            raise ValueError('R must be finite and positive.')
        self.R=float(R)
        self.dr=self.R*self.xi.dr
        self.faces=self.R*self.xi.faces
        self.r=self.R*self.xi.r
        self.V=self.R**2*self.xi.V


def solve_coupled_shrinking_step(T_old,C_old,dt,grid,R_new,T_inf,C_inf,time_new=None):
    grid.set_radius(R_new)
    return p2.solve_coupled_step(T_old,C_old,dt,grid,T_inf,C_inf,
                                 time_new=time_new,material=MATERIAL_Q4)


def reconstruct_q4(T_last,C_last,T_inf,C_inf,grid):
    return p2.reconstruct_surface(T_last,C_last,T_inf,C_inf,grid.dr,material=MATERIAL_Q4)


def sample_physical_radius(field,r_target,R,xi_grid,surface_value):
    """Return NaN only for requested positions outside [0,R]. No extrapolation."""
    r_target=np.asarray(r_target,dtype=np.float64)
    if not np.isfinite(r_target).all() or np.any(r_target<0):
        raise ValueError('Invalid requested radial positions.')
    values=np.full(r_target.shape,np.nan,dtype=np.float64)
    inside=r_target<=R
    # Divide only valid positions; no outside values ever reach the sampler.
    values[inside]=p1.sample_at_positions(field,r_target[inside]/R,xi_grid,surface_value)
    return values


def run_problem4(N=100,dt=1.,out=OUT):
    if dt not in (1.,.5):
        raise ValueError('Use dt=1 or .5 s.')
    radius=load_attachment2()
    ambient=p1.load_attachment1()
    grid=MaterialGrid(N,float(radius_at_time(0,radius)))
    folder=Path(out)/f'N{N}_dt{dt:g}'
    folder.mkdir(parents=True,exist_ok=True)
    # Run only while radius data exist; never fabricate a radius beyond its end.
    last_available=float(radius.times[-1])
    max_steps=int(np.floor(last_available/dt))
    times=np.arange(max_steps+1,dtype=np.float64)*dt
    radii=radius_at_time(times,radius)
    env_T=np.full(len(times),50.);env_C=np.full(len(times),.05)
    measured=times<=ambient.time[-1]
    env_T[measured],env_C[measured]=p1.ambient_conditions(times[measured],ambient)
    T=np.full(N,28.,dtype=np.float64);C=np.full(N,2.55,dtype=np.float64)
    previous_max=2.55;previous_mean=2.55
    means_up=0;surface_below_count=0
    Tmin=Tmax=28.;Cmin=Cmax=2.55
    sample_t=[0.];sample_R=[grid.R];sample_T=[T.copy()];sample_C=[C.copy()]
    history=np.empty((max_steps,8),dtype=np.float64)
    attained=False
    for step in range(1,max_steps+1):
        t=times[step]
        T_prev,C_prev=T,C
        T,C,count,ok,eT,eC=solve_coupled_shrinking_step(
            T_prev,C_prev,dt,grid,radii[step],env_T[step],env_C[step],time_new=t)
        if not ok:
            (folder/'failure.json').write_text(json.dumps({'time_s':t,'err_T':eT,'err_C':eC}),encoding='utf-8')
            raise RuntimeError(f'Q4 N={N} dt={dt}: Picard failed at {t} s; no drying event accepted.')
        Ts,Cs=reconstruct_q4(T[-1],C[-1],env_T[step],env_C[step],grid)
        attained,maximum,max_index,max_radius=p3.drying_criterion(C,Cs,grid)
        center_T=float((9*T[0]-T[1])/8)
        center_C=float((9*C[0]-C[1])/8)
        if not np.isfinite(T).all() or not np.isfinite(Ts) or not np.isfinite(center_T) or not np.isfinite(center_C):
            raise FloatingPointError(f'Nonfinite Q4 state at t={t}.')
        if np.any(C<=0) or Cs<=0 or center_C<=0:
            raise FloatingPointError(f'Nonpositive Q4 moisture at t={t}.')
        mean=float(np.dot(grid.xi.V,C)/grid.xi.V.sum())
        means_up+=int(mean>previous_mean+1e-12)
        surface_below_count+=int(Cs<center_C)
        Tmin=min(Tmin,float(T.min()),float(Ts),center_T)
        Tmax=max(Tmax,float(T.max()),float(Ts),center_T)
        Cmin=min(Cmin,float(C.min()),float(Cs),center_C)
        Cmax=max(Cmax,float(C.max()),float(Cs),center_C)
        history[step-1]=[t,grid.R,count,eT,eC,maximum,mean,float(Cs)]
        if t%60==0 or attained or step==max_steps:
            sample_t.append(t);sample_R.append(grid.R);sample_T.append(T.copy());sample_C.append(C.copy())
        if t%21600==0 or attained:
            print(f'Q4 N{N} dt{dt:g}: {t/3600:.5f} h, R={grid.R*100:.6f} cm, max C={maximum:.10f}',flush=True)
        if attained or step==max_steps:
            break
        previous_max,previous_mean=maximum,mean
    summary={'N':N,'dt_s':dt,'attained':bool(attained),'last_time_s':float(t),
        't_dry_s':float(t) if attained else None,'t_dry_h':float(t/3600) if attained else None,
        'previous_time_s':float(t-dt),'previous_max_C':previous_max,'domain_max_C':maximum,
        'max_index':max_index,'max_radius_m':max_radius,'max_radius_cm':max_radius*100,
        'final_R_m':grid.R,'final_R_cm':grid.R*100,
        'center_C_extrapolated':center_C,'surface_C':float(Cs),'center_T_C':center_T,'surface_T_C':float(Ts),
        'first_crossing_bracket':bool(attained and previous_max>=.15 and maximum<.15),
        'extrapolated_center_below_threshold':bool(center_C<.15),
        'radius_positive_finite':bool(np.isfinite(radii[:step+1]).all() and np.all(radii[:step+1]>0)),
        'radius_initial_m':float(radii[0]),'radius_min_m':float(radii[:step+1].min()),'radius_max_m':float(radii[:step+1].max()),
        'finite_all_steps':True,'positive_C_all_steps':True,'all_picard_converged':True,
        'mean_C_initial':2.55,'mean_C_final':mean,'mean_C_increase_steps_tol_1e_12':means_up,
        'mean_C_overall_decreases':bool(mean<2.55),'surface_C_below_center_fraction':surface_below_count/step,
        'T_range_C':[Tmin,Tmax],'C_range':[Cmin,Cmax],
        'picard_histogram':{str(int(k)):int(v) for k,v in zip(*np.unique(history[:step,2],return_counts=True))}}
    (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    np.savez_compressed(folder/'states.npz',times_s=sample_t,radii_m=sample_R,xi_centers=grid.xi.r,
        T_C=sample_T,C_kg_kg=sample_C,final_T_C=T,final_C_kg_kg=C,previous_T_C=T_prev,previous_C_kg_kg=C_prev,
        history=history[:step])
    p1._csv(folder/'picard_and_drying_history.csv',
        ['time_s','R_m','iterations','err_T','err_C','domain_max_C','volume_mean_C','surface_C'],history[:step])
    if not attained:
        message=f'半径数据不足：截至{last_available:g} s（{last_available/3600:g} h）仍未烘干，禁止外推半径。'
        (folder/'failure.txt').write_text(message,encoding='utf-8')
        raise RuntimeError(message)
    return summary


def fixed_radius_validation(out=OUT,t_end=10800):
    """Compare fixed-xi mapping with directly constructed fixed-r geometry.

    Both use Appendix 4 and the existing coupled solver; independently built
    geometry checks the coordinate metrics, with all steps/centers compared.
    """
    ambient=p1.load_attachment1()
    mapped=MaterialGrid(100,.02)
    direct=p1.Grid(100,.02)
    Tx=np.full(100,28.);Cx=np.full(100,2.55)
    Tr=Tx.copy();Cr=Cx.copy()
    max_T=max_C=0.
    et,ec=p1.ambient_conditions(np.arange(t_end+1),ambient)
    for t in range(1,t_end+1):
        Tx,Cx,_,okx,_,_=solve_coupled_shrinking_step(Tx,Cx,1.,mapped,.02,et[t],ec[t],time_new=t)
        Tr,Cr,_,okr,_,_=p2.solve_coupled_step(Tr,Cr,1.,direct,et[t],ec[t],time_new=t,material=MATERIAL_Q4)
        if not okx or not okr:
            raise RuntimeError('Fixed-radius validation Picard failed.')
        max_T=max(max_T,float(np.max(np.abs(Tx-Tr))))
        max_C=max(max_C,float(np.max(np.abs(Cx-Cr))))
    result={'R_m':.02,'N':100,'dt_s':1,'duration_s':t_end,
            'max_abs_T_difference_C':max_T,'max_abs_C_difference':max_C,
            'pass':bool(max_T<1e-8 and max_C<1e-10)}
    Path(out).mkdir(parents=True,exist_ok=True)
    (Path(out)/'fixed_radius_validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    if not result['pass']:
        raise AssertionError(f'Fixed-radius degeneration check failed: {result}')
    print('Fixed-radius degeneration: '+json.dumps(result),flush=True)
    return result


def _worker(args):
    return run_problem4(*args)


def validate_problem4(out=OUT,workers=4):
    fixed=fixed_radius_validation(out)
    results=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending=[pool.submit(_worker,(n,dt,out)) for n,dt in [(100,1.),(50,1.),(200,1.),(100,.5)]]
        for future in as_completed(pending):
            results.append(future.result())
    return sorted(results,key=lambda r:(r['N'],-r['dt_s'])),fixed


def write_outputs(results,fixed,out=OUT):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    base=next(r for r in results if r['N']==100 and r['dt_s']==1)
    a=np.load(out/'N100_dt1/states.npz')
    xi=p1.Grid(100,1.)
    ambient=p1.load_attachment1()
    radius=load_attachment2()
    # Read the actual template. Its ellipsis expands to fixed 0.1 cm columns;
    # the separate final surface column is preserved, not replaced by r=2 cm.
    template=ROOT/'A题/A题/附件/附件3/result4.xlsx'
    w=p1.openpyxl.load_workbook(template,read_only=True,data_only=True)
    header=list(next(w.active.values));w.close()
    if header[-1]!='药材表面' or header[1:4]!=[0,.1,.2]:
        raise ValueError('Unexpected result4 template radial headings.')
    fixed_cm=np.arange(0,int(np.ceil(radius.radii_m.max()*1000)),dtype=float)/10.
    positions=fixed_cm/100.
    sample_T=[];sample_C=[];surface_T=[];surface_C=[]
    for t,R,T,C in zip(a['times_s'],a['radii_m'],a['T_C'],a['C_kg_kg']):
        if t==0:
            Ts=28.;Cs=2.55
        else:
            envT,envC=p3.ambient_problem3(t,ambient)
            grid=MaterialGrid(100,R)
            Ts,Cs=reconstruct_q4(T[-1],C[-1],envT,envC,grid)
        sample_T.append(sample_physical_radius(T,positions,R,xi,Ts))
        sample_C.append(sample_physical_radius(C,positions,R,xi,Cs))
        surface_T.append(float(Ts));surface_C.append(float(Cs))
    sample_T=np.array(sample_T);sample_C=np.array(sample_C)
    surface_T=np.array(surface_T);surface_C=np.array(surface_C)
    expected_outside=positions[None,:]>a['radii_m'][:,None]
    assert np.array_equal(np.isnan(sample_C),expected_outside)
    assert np.array_equal(np.isnan(sample_T),expected_outside)
    base['no_extrapolated_external_positions']=True
    base['empty_external_output_cells']=int(expected_outside[1:].sum())
    np.savez_compressed(out/'sampled_results.npz',times_s=a['times_s'],radii_m=a['radii_m'],
        fixed_positions_m=positions,T_C=sample_T,C_kg_kg=sample_C,surface_T_C=surface_T,surface_C_kg_kg=surface_C)
    values=np.column_stack([sample_C[1:],surface_C[1:]])
    payload={'template':str(template),'header_label':header[0],'surface_label':header[-1],
        'positions_cm':fixed_cm.tolist(),'times':a['times_s'][1:].tolist(),
        'values':[[None if np.isnan(v) else round(float(v),4) for v in row] for row in values]}
    (out/'xlsx_data.json').write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    # Paper table 6: fixed half-centimetre points strictly inside, plus true surface.
    paper_times=(a['times_s']>0)&((a['times_s']%21600==0)|(a['times_s']==base['t_dry_s']))
    paper_max_R_cm=float(a['radii_m'][paper_times].max()*100)
    table_cm=np.arange(0,int(np.ceil(paper_max_R_cm/.5)),dtype=float)*.5
    rows=[]
    for i,(t,R) in enumerate(zip(a['times_s'],a['radii_m'])):
        if t==0 or (t%21600!=0 and t!=base['t_dry_s']):
            continue
        vals=sample_physical_radius(a['C_kg_kg'][i],table_cm/100,R,xi,surface_C[i])
        row=[t/3600]+[f'{v:.4f}' if rr<R*100 else '' for rr,v in zip(table_cm,vals)]+[f'{surface_C[i]:.4f}']
        rows.append(row)
    headers=['时间/h']+[f'{v:g} cm' for v in table_cm]+['药材表面']
    p1._csv(out/'table6_moisture.csv',headers,rows)
    final_inside=positions<base['final_R_m']
    p1._csv(out/'final_radial_distribution.csv',['r_cm','T_C','C_kg_kg'],zip(
        np.r_[fixed_cm[final_inside],base['final_R_cm']],
        np.r_[sample_T[-1,final_inside],surface_T[-1]],np.r_[sample_C[-1,final_inside],surface_C[-1]]))
    p1._csv(out/'final_cells_and_boundaries.csv',['xi','r_m','T_C','C_kg_kg'],zip(
        np.r_[0,xi.r,1],base['final_R_m']*np.r_[0,xi.r,1],
        np.r_[base['center_T_C'],a['final_T_C'],base['surface_T_C']],
        np.r_[base['center_C_extrapolated'],a['final_C_kg_kg'],base['surface_C']]))
    q3=json.loads((ROOT/'outputs/problem3/N100_dt1/summary.json').read_text(encoding='utf-8'))
    delta=base['t_dry_s']-q3['t_dry_s']
    comparison={'q3_t_dry_s':q3['t_dry_s'],'q4_t_dry_s':base['t_dry_s'],
        'delta_s':delta,'delta_h':delta/3600,'relative_change':delta/q3['t_dry_s']}
    differences={}
    bykey={(r['N'],r['dt_s']):r for r in results}
    for label,keya,keyb in [('N50_N100',(50,1.),(100,1.)),('N100_N200',(100,1.),(200,1.)),('dt1_dt05',(100,1.),(100,.5))]:
        if keya in bykey and keyb in bykey:
            differences[label]=abs(bykey[keya]['t_dry_s']-bykey[keyb]['t_dry_s'])
    report=['# 问题4结果与验证','',
        '固定材料坐标 ξ=r/R(t)，均匀径向整体收缩，不增加R_dot对流项、几何压缩含水率源项、轴向传输、显式蒸发潜热或机械压缩功。','',
        '## 输入与离散','',
        f'附件2有{len(radius.times)}个时刻，覆盖{radius.times[0]:g}–{radius.times[-1]:g} s；初始半径{radius.radii_m[0]*100:g} cm。'
        '半径从cm除以100转为m，直接线性插值，无平滑、拟合或强制单调化，范围外报错。','',
        '环境沿用问题3：至14400 s使用附件1线性插值，之后T_inf=50℃、C_inf=0.05 kg/kg。', '',
        '附录4：ρ=760+90C；cp=1850+2150C/(C+1)；k=0.12+0.20C/(C+1)；'
        'D=4.2e-4 exp(-0.30/C)exp[-3850/(T_C+273.15)]。hT=25，hm=8e-7，初值T=28℃、C=2.55。','',
        '复用问题2耦合Picard和问题1三对角FVM，新增可选物性函数参数，原调用默认仍为附录3。'
        '固定ξ网格的几何映射为V=R_new²Vξ、faces/dr=ξ_face/dξ、表面项R_new*h_eff；'
        '半单元阻力使用R_new*dξ/2。旧场不重插值，所有储存项与旧场右端均乘同一个新时刻几何权重。','',
        '## 默认烘干时间','',
        f'**N=100、dt=1 s：t_dry={base["t_dry_s"]:.0f} s = {base["t_dry_h"]:.8f} h。**', '',
        '| 时刻/s | max(C(0),C_i,C_s)/(kg/kg) |','|---:|---:|',
        f'| {base["previous_time_s"]:.0f} | {base["previous_max_C"]:.12f} |',
        f'| {base["t_dry_s"]:.0f} | {base["domain_max_C"]:.12f} |','',
        f'终点半径为{base["final_R_cm"]:.8f} cm；扫描所得最大值位置索引为{base["max_index"]}，'
        f'实际位置r={base["max_radius_cm"]:.8f} cm；索引−1表示重建圆心，索引N表示真实表面。','',
        '| 终点量 | 值 |','|---|---:|',
        f'| 圆心外推C | {base["center_C_extrapolated"]:.12f} |',
        f'| 真实表面C | {base["surface_C"]:.12f} |',
        f'| 圆心温度/℃ | {base["center_T_C"]:.8f} |',
        f'| 表面温度/℃ | {base["surface_T_C"]:.8f} |','',
        '终止条件严格使用未舍入的重建圆心、全部单元中心与真实表面值；Excel显示0.1500不能代替阈值判断。']
    report+=['','## 表6：药材水分浓度（kg/kg）','',
        '| '+' | '.join(headers)+' |','|'+'---:|'*len(headers)]
    report+=['| '+f'{row[0]:.8f}'+' | '+' | '.join(row[1:])+' |' for row in rows]
    report+=['','固定距离不在当前药材内时留空；末列始终为当前真实表面值，位置随R(t)变化。','',
        '## 固定半径退化验证','']
    if fixed:
        report+=[f'R恒为0.02 m，均使用附录4，N=100、dt=1 s。比较0–{fixed["duration_s"]} s'
            '全部时间步和全部单元；ξ映射网格与独立构造的固定r网格共用同一数值求解内核。'
            f'温度最大差{fixed["max_abs_T_difference_C"]:.6e}℃，含水率最大差'
            f'{fixed["max_abs_C_difference"]:.6e} kg/kg，通过：{fixed["pass"]}。']
    else:
        report+=['本次未执行固定半径退化验证。']
    report+=['','## 网格与时间步敏感性','',
        '| N | dt/s | t_dry/s | t_dry/h | 前后夹逼成立 |','|---:|---:|---:|---:|---|']
    report += [f'| {r["N"]} | {r["dt_s"]} | {r["t_dry_s"]} | {r["t_dry_h"]:.8f} | {r["first_crossing_bracket"]} |' for r in results]
    report+=['']+[f'{label}：时间绝对差{value:g} s（{value/3600:.8f} h）。' for label,value in differences.items()]
    if 'N100_N200' in differences:
        report+=['',f'N100与N200相差默认烘干时间的{differences["N100_N200"]/base["t_dry_s"]:.6%}。'
            '时间步比较均以各自积分步第一次达标时刻为准，无小数秒插值。']
    report+=['','## 物理检查','',
        '| N | dt/s | R正且有限 | T/C有限 | C>0 | 平均C上升步数 | 表面C低于中心比例 | Picard全收敛 |',
        '|---:|---:|---|---|---|---:|---:|---|']
    report += [f'| {r["N"]} | {r["dt_s"]} | {r["radius_positive_finite"]} | {r["finite_all_steps"]} | '
        f'{r["positive_C_all_steps"]} | {r["mean_C_increase_steps_tol_1e_12"]} | '
        f'{r["surface_C_below_center_fraction"]:.4%} | {r["all_picard_converged"]} |' for r in results]
    report+=['',f'默认体积加权平均C从2.55降至{base["mean_C_final"]:.10f}。'
        '权重为Vξ/sum(Vξ)，与当前体积权重R²Vξ归一化相同；单调检查容差1e-12。'
        f'Picard次数分布：{base["picard_histogram"]}。','',
        f'输出空单元格{base["empty_external_output_cells"]}个，均对应r>R(t)；'
        '程序只将r≤R(t)的位置传入插值，逐格核对空值掩码，不对药材外部外推。','',
        '## 与问题3比较','',
        '| 量 | 值 |','|---|---:|',
        f'| 问题3时间/s | {q3["t_dry_s"]:.0f} |',
        f'| 问题4时间/s | {base["t_dry_s"]:.0f} |',
        f'| 差值Q4−Q3/s | {delta:.0f} |',
        f'| 差值/h | {delta/3600:.8f} |',
        f'| 相对变化 | {delta/q3["t_dry_s"]:.6%} |','',
        '收缩缩短传热传质路径，在ξ方程中扩散速度随1/R²增大；附录4同时改变热容、导热系数和扩散系数。'
        '故总烘干时间差由半径收缩与新物性共同决定，不能把差值单独归因于收缩，未为预期快慢调整参数。','',
        '## 文件与复现','',
        '`result4.xlsx`展开模板0.1 cm固定距离列并保留“药材表面”末列，每60 s输出并追加精确终点；'
        '`sampled_results.npz`另保存每行真实半径，NaN只标识材料外部的无效输出位置。'
        '`table6_moisture.csv`为每6 h结果；`final_radial_distribution.csv`为终点有效物理位置温度/C。','',
        '每个N/dt目录保留60秒场、终点与前一步场、每步Picard和最大C记录。'
        '运行：`python problem4.py`；重建报告：`python problem4.py --report-only`；导出Excel：`node export_result4.mjs`。'
        '未生成图片。']
    (out/'问题4结果与验证.md').write_text('\n'.join(report),encoding='utf-8')
    (out/'validation_summary.json').write_text(json.dumps(
        {'runs':results,'fixed_radius':fixed,'time_differences_s':differences,'q3_comparison':comparison},
        ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'default':base,'differences_s':differences,'q3_comparison':comparison},ensure_ascii=False,indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--single',action='store_true')
    parser.add_argument('--report-only',action='store_true')
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    if args.report_only:
        results=[json.loads(p.read_text(encoding='utf-8')) for p in sorted(OUT.glob('N*/summary.json'))]
        fp=OUT/'fixed_radius_validation.json'
        fixed=json.loads(fp.read_text(encoding='utf-8')) if fp.exists() else None
    elif args.single:
        results=[run_problem4()];fixed=None
    else:
        results,fixed=validate_problem4(workers=args.workers)
    write_outputs(results,fixed)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
