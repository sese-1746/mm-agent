import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import xlrd
import json
from pathlib import Path

# 配置中文支持
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

INPUT_FILE = "data/att1.xls"
OUTPUT_PNG = "figures/satisfaction_line.png"
MANIFEST_FILE = "manifest.json"

# 定义列映射：使用 schema 提供的 canonical name
CANONICAL_COLS = [
    "store_id",
    "satisfaction_overall_m01", "satisfaction_overall_m02", "satisfaction_overall_m03",
    "satisfaction_overall_m04", "satisfaction_overall_m05", "satisfaction_overall_m06",
    "satisfaction_overall_m07", "satisfaction_overall_m08", "satisfaction_overall_m09",
    "satisfaction_overall_m10", "satisfaction_overall_m11", "satisfaction_overall_m12"
]

try:
    # 检查输入文件是否存在
    if not Path(INPUT_FILE).exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_FILE}")
    
    # 打开 Excel (xlrd)
    wb = xlrd.open_workbook(INPUT_FILE)
    sh = wb.sheet_by_index(0)
    
    # 检查表头行
    header_row_idx = 1  # 任务规格指定 header_row: 1
    nrows, ncols = sh.nrows, sh.ncols
    
    # 打印调试信息以便定位问题
    print(f"Sheet rows: {nrows}, cols: {ncols}")
    print(f"Row 0: {sh.row(0)}")
    print(f"Row 1 (Header): {sh.row(header_row_idx)}")
    
    # 验证表头列名是否匹配 schema
    header_values = [sh.cell_value(header_row_idx, i) for i in range(ncols)]
    expected_headers = CANONICAL_COLS
    
    if len(header_values) != len(expected_headers):
        raise ValueError(f"Header mismatch. Expected {len(expected_headers)} columns, got {len(header_values)}. Headers: {header_values}")
    
    for i, (exp, act) in enumerate(zip(expected_headers, header_values)):
        if exp != act:
            raise ValueError(f"Column {i} mismatch. Expected '{exp}', found '{act}'")
    
    # 提取数据
    data = []
    for r_idx in range(header_row_idx + 1, nrows):
        row_vals = [sh.cell_value(r_idx, c) for c in range(ncols)]
        store_id = row_vals[0]
        scores = row_vals[1:]
        # 过滤掉非数值行（如空行或备注）
        if len(scores) == ncols - 1 and all(isinstance(s, (int, float)) for s in scores):
            data.append([store_id] + list(scores))
    
    if not data:
        raise ValueError("No valid data rows found.")
    
    # 准备绘图数据
    stores = [row[0] for row in data]
    months = CANONICAL_COLS[1:]
    
    # 创建图形
    fig, ax = plt.subplots(figsize=(12, 8))
    colors = plt.cm.tab10.colors
    
    for i, store in enumerate(stores):
        y_vals = [row[i+1] for row in data]
        ax.plot(months, y_vals, label=store, color=colors[i % len(colors)], linewidth=1.5)
    
    ax.set_title("各门店月度客户满意度综合得分趋势", fontsize=16)
    ax.set_xlabel("月份")
    ax.set_ylabel("满意度得分")
    ax.legend(loc='best', bbox_to_anchor=(1.05, 1), ncol=1)
    ax.grid(True, linestyle='--', alpha=0.7)
    
    # 确保输出目录存在
    Path(OUTPUT_PNG).parent.mkdir(parents=True, exist_ok=True)
    
    # 保存图片
    fig.savefig(OUTPUT_PNG, dpi=300, bbox_inches='tight')
    plt.close(fig)
    
    # 写入 manifest.json
    manifest = {"files": [OUTPUT_PNG]}
    with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    
    print(f"Successfully generated: {OUTPUT_PNG}")
    print(f"Manifest written to: {MANIFEST_FILE}")

except Exception as e:
    # 打印异常信息以便调试，然后让程序自然退出（非 0 退出码）
    print(f"Error: {e}")