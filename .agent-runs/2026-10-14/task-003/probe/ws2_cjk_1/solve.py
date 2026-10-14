import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import xlrd
import json
from pathlib import Path

# 配置中文支持
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

INPUT_FILE = "data/附件 1 各门店客户满意度综合得分-A题.xls"
OUTPUT_PNG = "figures/satisfaction_line.png"
MANIFEST_FILE = "manifest.json"

# 确保输出目录存在
Path("figures").mkdir(parents=True, exist_ok=True)

try:
    # 打开 Excel 文件 (xlrd 必须用 open_workbook)
    wb = xlrd.open_workbook(INPUT_FILE)
    sh = wb.sheet_by_index(0)
    
    # 检查表头行是否存在
    header_row_idx = 1
    if sh.nrows == 0:
        raise ValueError("表格为空")
    
    # 获取第 0 行和第 1 行内容用于调试/报错信息
    row0_content = [sh.cell_value(r, c) for c in range(sh.ncols)]
    row1_content = [sh.cell_value(r, c) for c in range(sh.ncols)]
    
    # 根据 schema 构建列名映射
    # schema 说明：第 0 行是大标题，第 1 行是真实列名
    # columns 定义中 'col' 字段对应 Excel 列字母 (A, B...)
    # 我们需要从 row1_content 中读取实际值作为 x/y 数据源
    
    # 构建列索引映射：Excel 列字母 -> 内部变量名
    col_map = {}
    expected_cols = [
        {"name": "store_id", "raw": "月份门店", "col_idx": 0},
        {"name": "satisfaction_overall_m01", "raw": "1.0", "col_idx": 1},
        {"name": "satisfaction_overall_m02", "raw": "2.0", "col_idx": 2},
        {"name": "satisfaction_overall_m03", "raw": "3.0", "col_idx": 3},
        {"name": "satisfaction_overall_m04", "raw": "4.0", "col_idx": 4},
        {"name": "satisfaction_overall_m05", "raw": "5.0", "col_idx": 5},
        {"name": "satisfaction_overall_m06", "raw": "6.0", "col_idx": 6},
        {"name": "satisfaction_overall_m07", "raw": "7.0", "col_idx": 7},
        {"name": "satisfaction_overall_m08", "raw": "8.0", "col_idx": 8},
        {"name": "satisfaction_overall_m09", "raw": "9.0", "col_idx": 9},
        {"name": "satisfaction_overall_m10", "raw": "10.0", "col_idx": 10},
        {"name": "satisfaction_overall_m11", "raw": "11.0", "col_idx": 11},
        {"name": "satisfaction_overall_m12", "raw": "12.0", "col_idx": 12}
    ]
    
    # 验证第 1 行数据是否与 schema 预期一致（可选，但为了健壮性可以打印）
    print(f"表头行 (row {header_row_idx}): {row1_content[:5]}...")
    
    # 提取列名列表用于绘图
    x_label = "store_id"
    y_labels = [f"satisfaction_overall_m{i:02d}" for i in range(1, 13)]
    
    # 读取数据
    store_ids = []
    data_dict = {y: [] for y in y_labels}
    
    start_row = 2  # data_start_row
    for r_idx in range(start_row, sh.nrows):
        # 读取门店 ID (第 0 列)
        store_id_val = sh.cell_value(r_idx, 0)
        if store_id_val is None:
            continue
        store_ids.append(store_id_val)
        
        for c_idx, y_name in enumerate(y_labels):
            val = sh.cell_value(r_idx, c_idx + 1)  # 月份列从第 1 列开始
            if val is not None:
                try:
                    data_dict[y_name].append(float(val))
                except ValueError:
                    pass
    
    if not store_ids:
        raise ValueError("未读取到任何数据行")
    
    # 绘图
    fig, ax = plt.subplots(figsize=(12, 8))
    colors = plt.cm.tab10.colors
    
    for i, y_name in enumerate(y_labels):
        if y_name not in data_dict or len(data_dict[y_name]) == 0:
            continue
        ax.plot(store_ids, data_dict[y_name], label=y_name, color=colors[i % len(colors)], linewidth=1.5)
    
    ax.set_title("各门店月度客户满意度综合得分趋势", fontsize=16)
    ax.set_xlabel("月份门店", fontsize=12)
    ax.set_ylabel("满意度得分", fontsize=12)
    ax.legend(loc='best', bbox_to_anchor=(1.05, 1), ncol=1)
    ax.grid(True, linestyle='--', alpha=0.7)
    
    # 保存图片
    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=300)
    plt.close(fig)
    
    # 写入 manifest.json
    manifest = {"files": [OUTPUT_PNG]}
    with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    
except Exception as e:
    print(f"Error: {e}")
    raise