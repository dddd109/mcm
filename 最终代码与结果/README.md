# 最终代码与结果

本目录对应《四个问题模型建立与求解_最终版.md》中的最终方法。

## 方法

- 问题一：单元中心型径向有限体积法、一阶后向差分、三对角线性方程组直接求解，含水率采用 Picard 迭代。
- 问题二：每个时间步内采用温度—含水率耦合 Picard 迭代。
- 问题三：终止判据为重建圆心、全部控制体中心和真实表面含水率的最大值严格小于 0.15。
- 问题四：采用材料坐标 xi=r/R(t)，每个时间层内固定当前半径及几何系数，终止判据沿用问题三。

## 默认结果

| 问题 | 默认设置 | 结果 |
|---|---|---|
| 问题一 | N=100，dt=1 s | 计算至 1800 s |
| 问题二 | N=100，dt=1 s | 计算至 10800 s，全部时间步 Picard 收敛 |
| 问题三 | N=100，dt=1 s | t_end=207360 s=57.60000000 h |
| 问题四 | N=100，dt=1 s | t_end=184012 s=51.11444444 h |

问题四比问题三缩短 23348 s，即 6.48555556 h，相对变化为 -11.259645%。该差值同时包含半径收缩和附录 4 物性变化的影响。

## 运行

安装 Python 依赖：

```powershell
python -m pip install -r requirements.txt
```

依次运行：

```powershell
python problem1.py --export-xlsx
python problem2.py
python problem3.py
python problem4.py
```

运行测试：

```powershell
python -m unittest test_problem1.py test_problem3.py test_problem4.py
```

四个 Excel 结果位于 `outputs/problem1/result1.xlsx` 至 `outputs/problem4/result4.xlsx`。输入附件保留在程序所需的 `A题/A题/附件` 相对路径下。

Excel 导出脚本依赖 Codex 工作区提供的 `@oai/artifact-tool`；目录内已经包含当前计算生成的最终 Excel 文件，普通复核只需运行 Python 程序和测试。
