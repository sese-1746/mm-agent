import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import xlrd
import json
from pathlib import Path

# 配置中文支持
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

INPUT_FILE = "data/att1.xls"
OUTPUT_PNG = "figures/satisfaction_line.png"
MANIFEST_FILE = "manifest.json"

# 确保输出目录存在
Path("figures").mkdir(parents=True, exist_ok=True)

def main():
    # 使用 xlrd 打开 .xls 文件
    try:
        wb = xlrd.open_workbook(INPUT_FILE)
    except Exception as e:
        print(f"Error opening workbook: {e}")
        raise

    sh = wb.sheet_by_index(0)
    
    # 检查表头行 (header_row=1)
    header_row_idx = 1
    headers = [sh.cell_value(row, col) for col in range(sh.ncols)]
    print(f"Sheet rows: {sh.nrows}, cols: {sh.ncols}")
    print(f"Header row ({header_row_idx}): {headers}")
    
    # 检查第 0 行内容 (通常是大标题)
    if sh.nrows > 0:
        print(f"Row 0: {[sh.cell_value(0, c) for c in range(sh.ncols)]}")
    
    # 构建列名映射
    expected_cols = [
        "store_id", "satisfaction_overall_m01", "satisfaction_overall_m02",
        "satisfaction_overall_m03", "satisfaction_overall_m04", "satisfaction_overall_m05",
        "satisfaction_overall_m06", "satisfaction_overall_m07", "satisfaction_overall_m08",
        "satisfaction_overall_m09", "satisfaction_overall_m10", "satisfaction_overall_m11",
        "satisfaction_overall_m12"
    ]
    
    col_map = {}
    for i, raw_header in enumerate(headers):
        # 尝试匹配：先找完全匹配的，再找包含关系的（处理 '1.0' vs '1月' 等情况）
        found = False
        for expected in expected_cols:
            if raw_header == expected or (expected.startswith('satisfaction') and raw_header.isdigit()):
                # 如果是数字字符串，映射到对应的满意度列
                if expected.startswith('satisfaction'):
                    col_map[expected] = i
                else:
                    continue # store_id 通常不是纯数字
            elif raw_header == '月份门店':
                col_map['store_id'] = i
        
        # 如果还没找到，且当前列名是数字（如 '1.0'），尝试映射到对应的月份列
        if not found and raw_header.replace('.', '').replace(' ', '').isdigit():
            month_num = int(raw_header.replace('.', ''))
            expected_col_name = f"satisfaction_overall_m{month_num:02d}"
            if expected_col_name in expected_cols:
                col_map[expected_col_name] = i
        
        # 如果当前列名是 '月份门店'，映射 store_id
        if raw_header == '月份门店':
            col_map['store_id'] = i
    
    # 验证是否找到所有必要列
    missing_cols = [c for c in expected_cols if c not in col_map]
    if missing_cols:
        print(f"Missing columns: {missing_cols}")
        raise ValueError(f"Could not map columns. Missing: {missing_cols}")
    
    # 读取数据 (从第 2 行开始，即索引 1)
    data_rows = []
    for r in range(header_row_idx, sh.nrows):
        row_values = [sh.cell_value(r, c) for c in range(sh.ncols)]
        store_id = row_values[0]
        # 提取满意度数据
        sat_data = {}
        for col_name, idx in col_map.items():
            if col_name == 'store_id':
                continue
            val = row_values[idx]
            try:
                sat_data[col_name] = float(val)
            except (ValueError, TypeError):
                sat_data[col_name] = None
        data_rows.append({"store_id": store_id, **sat_data})
    
    # 转换为 DataFrame
    df = pd.DataFrame(data_rows)
    
    # 绘图
    plt.figure(figsize=(12, 8))
    colors = plt.cm.viridis(range(len(df)))
    
    for idx, store in enumerate(df['store_id'].unique()):
        mask = df['store_id'] == store
        sat_series = df.loc[mask, 'satisfaction_overall_m01':'satisfaction_overall_m12']
        # 过滤掉 None 值
        valid_idx = sat_series[sat_series.notna()].index
        if len(valid_idx) > 0:
            plt.plot(valid_idx, sat_series[valid_idx], label=store, color=colors[idx])
    
    plt.title("各门店月度客户满意度综合得分")
    plt.xlabel("月份")
    plt.ylabel("满意度得分")
    plt.legend(loc='best')
    plt.grid(True, linestyle='--', alpha=0.7)
    
    # 保存图片
    plt.savefig(OUTPUT_PNG, dpi=300)
    plt.close()
    print(f"Figure saved to {OUTPUT_PNG}")
    
    # 写入 manifest.json
    manifest = {"files": [OUTPUT_PNG]}
    with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    print(f"Manifest saved to {MANIFEST_FILE}")

if __name__ == "__main__":
    main()