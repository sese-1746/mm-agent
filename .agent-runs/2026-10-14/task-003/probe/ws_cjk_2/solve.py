import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import xlrd
import json
from pathlib import Path

# 配置中文支持，防止乱码
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# 输入文件路径（逐字符匹配任务规格）
input_file = "data/附件 1 各门店客户满意度综合得分 -A 题.xls"
output_fig = "figures/satisfaction_line.png"
manifest_path = "manifest.json"
header_row = 1

# 确保输出目录存在
Path("figures").mkdir(parents=True, exist_ok=True)

try:
    # 使用 xlrd 读取 .xls 文件
    wb = xlrd.open_workbook(input_file)
    sh = wb.sheet_by_index(0)
    
    # 检查表头行是否存在
    if header_row >= sh.nrows:
        raise ValueError(f"Header row index {header_row} is out of range (total rows: {sh.nrows})")
    
    # 获取列名（直接使用 cell_value 返回值，不再取 .value）
    headers = [cell_value for cell_value in sh.row(header_row)]
    
    # 打印调试信息：行数、列数、第 0 行和第 1 行内容
    print(f"Total rows: {sh.nrows}, Total cols: {sh.ncols}")
    print(f"Row 0: {[cell_value for cell_value in sh.row(0)]}")
    print(f"Row 1 (Headers): {headers}")
    
    # 寻找包含 '满意度' 或 '得分' 的列作为 Y 轴，以及 '月份' 或 '时间' 作为 X 轴
    y_col_idx = -1
    x_col_idx = -1
    store_col_idx = -1
    
    for i, h in enumerate(headers):
        h_str = str(h).strip()
        if '月份' in h_str or '时间' in h_str or '月' in h_str:
            x_col_idx = i
        elif '满意度' in h_str or '得分' in h_str or '综合' in h_str:
            y_col_idx = i
        # 假设第一列是门店名称（如果不是月份或分数）
        if store_col_idx == -1 and i == 0 and not ('月份' in str(h) or '满意度' in str(h)):
             store_col_idx = i
    
    # 如果找不到明确的 X/Y 列，尝试默认策略：第 1 列为 X（月份），第 2 列为 Y（得分）
    if x_col_idx == -1 and y_col_idx == -1:
        if sh.ncols >= 3:
            x_col_idx = 1
            y_col_idx = 2
            print("Warning: No explicit headers found. Assuming col 1 is X (Month) and col 2 is Y (Score).")
    
    if x_col_idx == -1 or y_col_idx == -1:
        raise ValueError(f"Could not identify X (month) or Y (score) columns in headers: {headers}")
    
    # 读取数据
    nrows = sh.nrows
    data_x = []
    data_y = []
    stores = []
    
    for r in range(header_row + 1, nrows):
        val_x = sh.cell_value(r, x_col_idx)
        val_y = sh.cell_value(r, y_col_idx)
        # 跳过空值
        if val_x is None or val_y is None:
            continue
        data_x.append(val_x)
        data_y.append(val_y)
    
    if not data_x:
        raise ValueError("No valid data rows found after header.")
    
    # 绘图
    plt.figure(figsize=(10, 6))
    plt.plot(data_x, data_y, marker='o', linestyle='-', color='b', label='Customer Satisfaction Score')
    plt.title('各门店月度客户满意度综合得分趋势图')
    plt.xlabel('月份')
    plt.ylabel('满意度得分')
    plt.grid(True, linestyle='--', alpha=0.7)
    
    # 保存图片
    plt.savefig(output_fig, dpi=300)
    plt.close()
    
    print(f"Figure saved to {output_fig}")
    
except Exception as e:
    # 打印异常信息以便调试，然后让程序正常退出（非 0 退出码由 Python 默认处理）
    print(f"Error: {e}")
    raise

# 写入 manifest.json
manifest = {
    "files": [output_fig]
}
with open(manifest_path, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, ensure_ascii=False)