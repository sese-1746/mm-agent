import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import xlrd
import json
from pathlib import Path

# 配置中文支持，防止乱码
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

INPUT_FILE = "data/att1.xls"
OUTPUT_PNG = "figures/satisfaction_line.png"
MANIFEST_FILE = "manifest.json"

# 定义列映射：使用 schema 提供的 canonical 名称和 col 索引
# 注意：xlrd 的 cell_value(row, col) 中 col 是 0-based 整数索引
# schema 中的 'col' 字段如 'A', 'B' 对应 Excel 列标，需转换为 0-based 索引
COL_MAP = [
    {"name": "store_id", "idx": 0},
    {"name": "satisfaction_overall_m01", "idx": 1},
    {"name": "satisfaction_overall_m02", "idx": 2},
    {"name": "satisfaction_overall_m03", "idx": 3},
    {"name": "satisfaction_overall_m04", "idx": 4},
    {"name": "satisfaction_overall_m05", "idx": 5},
    {"name": "satisfaction_overall_m06", "idx": 6},
    {"name": "satisfaction_overall_m07", "idx": 7},
    {"name": "satisfaction_overall_m08", "idx": 8},
    {"name": "satisfaction_overall_m09", "idx": 9},
    {"name": "satisfaction_overall_m10", "idx": 10},
    {"name": "satisfaction_overall_m11", "idx": 11},
    {"name": "satisfaction_overall_m12", "idx": 12}
]

HEADER_ROW = 1  # 任务规格指定 header_row: 1
DATA_START_ROW = 2

try:
    # 检查输入文件是否存在
    if not Path(INPUT_FILE).exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_FILE}")
    
    # 打开 Excel 文件 (xlrd)
    wb = xlrd.open_workbook(INPUT_FILE)
    sh = wb.sheet_by_index(0)  # 使用第一个 sheet
    
    # 打印调试信息：行数、列数、第 0 行和第 1 行内容
    print(f"Sheet info: rows={sh.nrows}, cols={sh.ncols}")
    print(f"Row 0 (merged title?): {sh.row(0)}")
    print(f"Row 1 (header): {sh.row(HEADER_ROW)}")
    
    # 验证表头是否匹配 schema
    expected_headers = [col["name"] for col in COL_MAP]
    actual_headers = [sh.cell_value(HEADER_ROW, i) for i in range(sh.ncols)]
    
    if actual_headers != expected_headers:
        raise ValueError(f"Header mismatch. Expected: {expected_headers}, Got: {actual_headers}")
    
    # 提取数据
    stores = []
    months_data = {}
    for col_def in COL_MAP:
        key = col_def["name"]
        idx = col_def["idx"]
        if key == "store_id":
            continue
        months_data[key] = []
    
    # 遍历数据行
    for row_idx in range(DATA_START_ROW, sh.nrows):
        store_val = sh.cell_value(row_idx, COL_MAP[0]["idx"])
        stores.append(store_val)
        
        for col_def in COL_MAP:
            if col_def["name"] == "store_id":
                continue
            val = sh.cell_value(row_idx, col_def["idx"])
            # 确保是 float，处理可能的空值
            if val is None or (isinstance(val, float) and pd.isna(val)):
                val = 0.0
            months_data[col_def["name"]].append(float(val))
    
    # 绘图
    fig, ax = plt.subplots(figsize=(12, 6))
    
    x_labels = ["1 月", "2 月", "3 月", "4 月", "5 月", "6 月",
                 "7 月", "8 月", "9 月", "10 月", "11 月", "12 月"]
    
    for col_def in COL_MAP:
        if col_def["name"] == "store_id":
            continue
        ax.plot(stores, months_data[col_def["name"]], label=col_def["name"], linewidth=1.5)
    
    ax.set_xlabel("门店")
    ax.set_ylabel("满意度得分")
    ax.set_title("各门店月度客户满意度综合得分趋势图")
    ax.legend(loc='best')
    ax.grid(True, linestyle='--', alpha=0.7)
    
    # 确保输出目录存在
    Path(OUTPUT_PNG).parent.mkdir(parents=True, exist_ok=True)
    
    # 保存图片
    plt.savefig(OUTPUT_PNG, dpi=300, bbox_inches='tight')
    plt.close(fig)
    
    print(f"Figure saved to: {OUTPUT_PNG}")
    
    # 写入 manifest.json
    manifest = {"files": [OUTPUT_PNG]}
    with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    
    print(f"Manifest saved to: {MANIFEST_FILE}")

except Exception as e:
    # 打印异常信息以便调试，然后让程序非正常退出（抛出异常）
    print(f"Error occurred: {e}")
    raise