import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import xlrd
import json
from pathlib import Path

# 配置中文支持 (防止乱码)
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

INPUT_FILE = "data/att1.xls"
OUTPUT_PNG = "figures/satisfaction_line.png"
MANIFEST_FILE = "manifest.json"
HEADER_ROW = 1

# 确保输出目录存在
Path("figures").mkdir(parents=True, exist_ok=True)

try:
    # 使用 xlrd 读取 .xls
    wb = xlrd.open_workbook(INPUT_FILE)
    sh = wb.sheet_by_index(0)
    
    # 检查表头行是否存在
    if HEADER_ROW >= sh.nrows:
        raise ValueError(f"Header row index {HEADER_ROW} is out of range (total rows: {sh.nrows})")
    
    header_row_values = [cell.value for cell in sh.row(HEADER_ROW)]
    print(f"Detected columns from row {HEADER_ROW}: {header_row_values}")
    
    # 寻找包含 '满意度' 或 'score' 等关键词的列作为 Y 轴，第一列作为 X 轴
    y_col_idx = -1
    x_col_idx = 0  # 默认第一列作为 X (门店/月份)
    
    for i, col_name in enumerate(header_row_values):
        col_str = str(col_name).lower()
        if '满意度' in col_str or 'score' in col_str or 'satisfaction' in col_str:
            y_col_idx = i
            break
    
    if y_col_idx == -1:
        raise ValueError("Could not find a column containing '满意度', 'score', or 'satisfaction'.")
    
    # 读取数据 (跳过前 HEADER_ROW 行)
    x_data = []
    y_data = []
    
    for r in range(HEADER_ROW + 1, sh.nrows):
        cell_x = sh.cell(r, x_col_idx)
        cell_y = sh.cell(r, y_col_idx)
        # xlrd: Cell 对象需要 .value
        val_x = cell_x.value
        val_y = cell_y.value
        
        if val_x is not None and val_y is not None:
            x_data.append(val_x)
            y_data.append(val_y)
    
    if len(x_data) == 0:
        raise ValueError("No data points found after header row.")
    
    # 绘图
    plt.figure(figsize=(10, 6))
    plt.plot(x_data, y_data, marker='o', linestyle='-', color='b', label='满意度得分')
    plt.title('各门店月度客户满意度综合得分趋势')
    plt.xlabel('月份/门店')
    plt.ylabel('满意度得分')
    plt.grid(True, linestyle='--', alpha=0.7)
    
    # 保存图片
    plt.savefig(OUTPUT_PNG, dpi=300)
    plt.close()
    
    print(f"Figure saved to {OUTPUT_PNG}")
    
except Exception as e:
    # 打印错误信息以便调试，然后让程序自然退出 (非 0 退出码)
    print(f"Error: {e}")
    raise

# 写入 manifest.json
manifest = {"files": [OUTPUT_PNG]}
with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, ensure_ascii=False)