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

# 确保输出目录存在
Path("figures").mkdir(parents=True, exist_ok=True)

try:
    # 打开 Excel 文件 (xlrd 只读)
    wb = xlrd.open_workbook(INPUT_FILE)
    sh = wb.sheet_by_index(0)
    
    # 检查表头行 (header_row=1)
    header = [sh.cell_value(1, i) for i in range(sh.ncols)]
    print(f"Detected header row: {header}")
    print(f"Total rows: {sh.nrows}, Total cols: {sh.ncols}")
    
    # 验证关键列是否存在 (根据 schema 中的 raw name)
    required_cols = ["月份门店", "1.0", "2.0", "3.0", "4.0", "5.0", "6.0",
                     "7.0", "8.0", "9.0", "10.0", "11.0", "12.0"]
    missing = [c for c in required_cols if c not in header]
    if missing:
        raise ValueError(f"Missing columns in header: {missing}")
    
    # 获取列索引
    col_idx = {name: i for i, name in enumerate(header)}
    store_col = col_idx["月份门店"]
    month_cols = [col_idx[f"{m}.0"] for m in range(1, 13)]
    
    # 提取数据
    stores = []
    data = {}
    for row_idx in range(2, sh.nrows):  # data_start_row=2
        store_name = sh.cell_value(row_idx, store_col)
        if not store_name:
            continue
        stores.append(store_name)
        
        record = {}
        for m_idx, month_col in enumerate(month_cols):
            val = sh.cell_value(row_idx, month_col)
            # xlrd cell_value 直接返回数值或字符串，无需再取 .value
            if isinstance(val, float):
                record[f"satisfaction_overall_m{m_idx+1:02d}"] = val
            else:
                record[f"satisfaction_overall_m{m_idx+1:02d}"] = float(val) if val else 0.0
        data[store_name] = record
    
    # 绘图
    fig, ax = plt.subplots(figsize=(12, 6))
    colors = plt.cm.tab10.colors
    
    for i, store in enumerate(stores):
        if store not in data:
            continue
        values = [data[store][f"satisfaction_overall_m{m:02d}"] for m in range(1, 13)]
        ax.plot(range(1, 13), values, label=store, color=colors[i % len(colors)], linewidth=1.5)
    
    ax.set_title("各门店月度客户满意度综合得分趋势", fontsize=14)
    ax.set_xlabel("月份")
    ax.set_ylabel("满意度得分")
    ax.legend(loc='best', bbox_to_anchor=(1.05, 1), ncol=1)
    ax.grid(True, linestyle='--', alpha=0.6)
    
    # 保存图片
    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=300)
    plt.close(fig)
    
    # 写入 manifest
    manifest = {"files": [OUTPUT_PNG]}
    with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    
except Exception as e:
    print(f"Error: {e}")
    raise