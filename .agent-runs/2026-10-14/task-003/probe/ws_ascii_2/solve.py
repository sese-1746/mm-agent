import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import xlrd
from pathlib import Path
import json

# 配置中文支持，防止乱码
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

INPUT_FILE = "data/att1.xls"
OUTPUT_PNG = "figures/satisfaction_line.png"
MANIFEST_FILE = "manifest.json"
HEADER_ROW = 1

# 确保输出目录存在
Path("figures").mkdir(parents=True, exist_ok=True)

try:
    # 使用 xlrd 打开 .xls 文件
    wb = xlrd.open_workbook(INPUT_FILE)
    sh = wb.sheet_by_index(0)
    
    # 检查表头行是否存在
    if HEADER_ROW >= sh.nrows:
        raise ValueError(f"Header row index {HEADER_ROW} is out of range (total rows: {sh.nrows})")
    
    # 获取第 1 行的列名
    headers = [cell_value for cell_value in sh.row(HEADER_ROW)]
    print(f"Detected headers from row {HEADER_ROW}: {headers}")
    
    if len(headers) < 2:
        raise ValueError("Not enough columns detected.")
    
    # 尝试识别门店列和得分列
    # 假设：第 0 列是月份，最后一列或倒数第二列是满意度得分
    # 根据常见附件结构调整：通常第一列是时间/月份，后续有具体指标
    month_col_name = headers[0]
    score_col_name = headers[-1]  # 假设最后一列是综合得分
    
    if not sh.cell_value(HEADER_ROW, 0).decode() if isinstance(sh.cell_value(HEADER_ROW, 0), bytes) else str(sh.cell_value(HEADER_ROW, 0)) == month_col_name:
        pass # 名称匹配逻辑简化，直接取第一列作为 X
    
    # 读取数据
    data = []
    for i in range(HEADER_ROW + 1, sh.nrows):
        row_vals = [sh.cell_value(i, j) for j in range(sh.ncols)]
        x_val = row_vals[0]
        y_val = row_vals[-1] if len(row_vals) > 1 else None
        # 过滤非数值 Y
        if y_val is not None and isinstance(y_val, (int, float)):
            data.append([x_val, y_val])
    
    if not data:
        raise ValueError("No valid data points found.")
    
    x_vals = [d[0] for d in data]
    y_vals = [d[1] for d in data]
    
    # 绘图
    plt.figure(figsize=(10, 6))
    plt.plot(x_vals, y_vals, marker='o', linestyle='-', color='b', linewidth=2)
    plt.title(f"各门店月度满意度趋势图")
    plt.xlabel("月份")
    plt.ylabel("综合得分")
    plt.grid(True, linestyle='--', alpha=0.7)
    
    # 保存图片
    plt.savefig(OUTPUT_PNG, dpi=300)
    plt.close()
    
    # 写入 manifest.json
    manifest = {"files": [OUTPUT_PNG]}
    with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    
except Exception as e:
    print(f"Error: {e}")
    raise