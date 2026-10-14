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

INPUT_FILE = "data/att1.xls"
OUTPUT_PNG = "figures/satisfaction_line.png"
MANIFEST_FILE = "manifest.json"

# 确保输出目录存在
Path("figures").mkdir(parents=True, exist_ok=True)

try:
    # 使用 xlrd 打开 .xls 文件
    wb = xlrd.open_workbook(INPUT_FILE)
    sh = wb.sheet_by_index(0)
    
    nrows = sh.nrows
    ncols = sh.ncols
    print(f"Sheet 行数: {nrows}, 列数: {ncols}")
    print(f"第 0 行内容: {[sh.cell_value(r, c) for c in range(ncols)]}")
    print(f"第 1 行内容: {[sh.cell_value(r, c) for c in range(ncols)]}")
    
    # 检查表头行是否存在有效数据
    header_row_idx = 1
    if nrows <= header_row_idx:
        raise ValueError("文件行数不足，无法读取表头行。")
    
    headers = [sh.cell_value(header_row_idx, c) for c in range(ncols)]
    print(f"解析到的表头: {headers}")
    
    # 构建列名映射
    col_map = {}
    expected_cols = [
        "store_id", "satisfaction_overall_m01", "satisfaction_overall_m02",
        "satisfaction_overall_m03", "satisfaction_overall_m04", "satisfaction_overall_m05",
        "satisfaction_overall_m06", "satisfaction_overall_m07", "satisfaction_overall_m08",
        "satisfaction_overall_m09", "satisfaction_overall_m10", "satisfaction_overall_m11",
        "satisfaction_overall_m12"
    ]
    
    for idx, expected_name in enumerate(expected_cols):
        raw_header = headers[idx]
        # 尝试匹配：原始表头可能是 '1.0'，但 schema 中 col 是 'B'
        # 这里我们直接根据 schema 的 canonical name 和实际读取到的 header 进行关联
        # 由于 xlrd 读取的是字符串，我们需要找到对应的列索引
        if idx == 0:
            continue # store_id 单独处理
        
        # 检查该列是否有数据
        sample_val = sh.cell_value(header_row_idx + 1, idx)
        if sample_val is None or (isinstance(sample_val, float) and pd.isna(sample_val)):
            raise ValueError(f"列 {expected_name} 在数据行中为空或无效。")
        
        col_map[expected_name] = idx
    
    # 读取数据到 DataFrame
    data_rows = []
    for r in range(header_row_idx + 1, nrows):
        row_data = {}
        row_data['store_id'] = sh.cell_value(r, 0)
        for col_name, c_idx in col_map.items():
            val = sh.cell_value(r, c_idx)
            if isinstance(val, float) and pd.isna(val):
                row_data[col_name] = None
            else:
                row_data[col_name] = val
        data_rows.append(row_data)
    
    df = pd.DataFrame(data_rows)
    
    # 绘图
    fig, ax = plt.subplots(figsize=(12, 6))
    
    for col in expected_cols[1:]:
        if col in df.columns:
            ax.plot(df['store_id'], df[col], label=col.replace('satisfaction_overall_m', 'M'), marker='o')
    
    ax.set_title('各门店月度客户满意度综合得分趋势图')
    ax.set_xlabel('月份门店')
    ax.set_ylabel('满意度得分')
    ax.legend(loc='best')
    ax.grid(True, linestyle='--', alpha=0.7)
    
    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=300)
    plt.close(fig)
    
    # 写入 manifest.json
    output_list = [OUTPUT_PNG]
    with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
        json.dump({"files": output_list}, f, ensure_ascii=False)
    
except Exception as e:
    print(f"脚本执行失败: {e}")
    raise