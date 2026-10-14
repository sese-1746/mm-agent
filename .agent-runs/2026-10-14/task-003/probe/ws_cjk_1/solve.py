import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import xlrd
import json
from pathlib import Path

# 配置中文显示
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# 输入文件路径（逐字符匹配）
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
    
    # 获取列名（注意：row() 返回 Cell 对象列表，需调用 .value）
    headers = [cell.value for cell in sh.row(header_row)]
    print(f"Detected columns: {headers}")
    
    # 打印第0行和第1行内容以便调试
    print(f"Row 0: {[cell.value for cell in sh.row(0)]}")
    print(f"Row 1 (Headers): {headers}")
    
    # 查找关键列：门店名称和满意度得分
    store_col_idx = None
    score_col_idx = None
    
    for i, h in enumerate(headers):
        if isinstance(h, str) and '门店' in h:
            store_col_idx = i
        elif isinstance(h, str) and ('满意度' in h or '得分' in h or '综合' in h):
            score_col_idx = i
    
    if store_col_idx is None or score_col_idx is None:
        raise ValueError(f"Could not identify columns. Headers: {headers}")
    
    # 提取数据（跳过表头行）
    stores = []
    scores = []
    for r in range(header_row + 1, sh.nrows):
        s_name = sh.cell_value(r, store_col_idx)
        s_score = sh.cell_value(r, score_col_idx)
        if s_name and s_score is not None:
            stores.append(s_name)
            scores.append(float(s_score))
    
    if not stores:
        raise ValueError("No data rows found after header.")
    
    # 绘图
    plt.figure(figsize=(10, 6))
    plt.plot(scores, marker='o', linestyle='-', linewidth=2, markersize=6)
    plt.title('各门店月度客户满意度综合得分趋势')
    plt.xlabel('月份/序号')
    plt.ylabel('满意度得分')
    plt.grid(True, linestyle='--', alpha=0.7)
    
    # 保存图片
    plt.savefig(output_fig, dpi=300, bbox_inches='tight')
    plt.close()
    
    print(f"Figure saved to {output_fig}")
    
except Exception as e:
    # 打印错误信息以便调试，然后让程序正常退出（非 0 码）
    print(f"Error: {e}")
    raise

# 写入 manifest.json
manifest = {"files": [output_fig]}
with open(manifest_path, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, ensure_ascii=False)