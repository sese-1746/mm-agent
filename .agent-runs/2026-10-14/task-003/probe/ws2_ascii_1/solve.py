import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import xlrd
import pandas as pd
from pathlib import Path
import json

# 配置中文字体支持
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
    
    # 检查表头行 (header_row=1)，第 0 行通常是合并大标题
    header_row_idx = 1
    nrows = sh.nrows
    ncols = sh.ncols
    
    # 打印调试信息以便定位问题
    print(f"Sheet rows: {nrows}, cols: {ncols}")
    print(f"Row 0 (merged title?): {sh.row(0)}")
    print(f"Row 1 (headers): {[sh.cell_value(1, i) for i in range(ncols)]}")
    
    # 构建列名映射
    headers = [sh.cell_value(header_row_idx, i).strip() if sh.cell_type(header_row_idx, i) != xlrd.XL_CELL_EMPTY else f"col_{i}" for i in range(ncols)]
    
    # 提取数据：从第 2 行开始 (data_start_row=2)
    data_rows = []
    for r in range(header_row_idx + 1, nrows):
        row_data = [sh.cell_value(r, c) for c in range(ncols)]
        data_rows.append(row_data)
    
    if not data_rows:
        raise ValueError("No data rows found after header.")
    
    # 转换为 DataFrame
    df = pd.DataFrame(data_rows, columns=headers)
    
    # 清理列名：去除可能的空格或特殊字符，确保与 schema 对应
    # Schema 提示：原始表头可能是 '1.0' 等数值，需按 col 定位。这里我们直接使用读取到的 headers。
    # 如果 headers[0] 不是 '月份门店'，则尝试调整
    if df.columns[0].strip() != '月份门店':
        raise ValueError(f"Expected first column header '月份门店', got '{df.columns[0]}'")
    
    # 提取 x (store_id) 和 y (各月满意度)
    store_ids = df.iloc[:, 0]
    
    # 准备绘图数据：将各月列提取出来
    months = ['1 月', '2 月', '3 月', '4 月', '5 月', '6 月', '7 月', '8 月', '9 月', '10 月', '11 月', '12 月']
    y_cols = [f'satisfaction_overall_m{i:02d}' for i in range(1, 13)]
    
    # 检查列是否存在，如果 headers 中是数字字符串如 '1.0'，则需映射
    available_months = [str(h).strip() for h in df.columns[1:] if str(h).strip().replace('.', '').isdigit()]
    
    # 重新构建 y 数据列表，根据实际读取的列名
    actual_y_cols = []
    for m in months:
        col_name = f"{m}"
        if col_name in df.columns:
            actual_y_cols.append(df[col_name])
        else:
            # 尝试匹配类似 '1.0' 的列
            found = False
            for c in df.columns[1:]:
                if str(c).strip() == m or (str(c).replace('.', '').isdigit() and int(str(c).replace('.', '')) == int(m.replace('月', ''))):
                    actual_y_cols.append(df[c])
                    found = True
                    break
            if not found:
                raise ValueError(f"Month column '{m}' not found in data.")
    
    # 绘图
    plt.figure(figsize=(12, 8))
    colors = plt.cm.viridis(np.linspace(0, 1, len(actual_y_cols)))
    
    for i, (month, y_data) in enumerate(zip(months, actual_y_cols)):
        plt.plot(store_ids, y_data, label=month, color=colors[i], linewidth=1.5)
    
    plt.title('各门店月度客户满意度综合得分趋势', fontsize=14)
    plt.xlabel('月份门店', fontsize=12)
    plt.ylabel('满意度得分', fontsize=12)
    plt.legend(loc='upper right', bbox_to_anchor=(1.3, 1))
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    
    # 保存图片
    plt.savefig(OUTPUT_PNG, dpi=300, bbox_inches='tight')
    plt.close()
    
    print(f"Figure saved to {OUTPUT_PNG}")
    
except Exception as e:
    # 打印异常信息以便调试，然后让程序正常退出（非 0 退出码由 Python 默认处理）
    print(f"Error: {e}")
    raise

# 生成 manifest.json
manifest = {"files": [OUTPUT_PNG]}
with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, ensure_ascii=False)

print(f"Manifest saved to {MANIFEST_FILE}")