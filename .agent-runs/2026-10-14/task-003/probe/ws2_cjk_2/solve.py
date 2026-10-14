import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import xlrd
from pathlib import Path

# 配置中文显示
plt.rcParams['font.sans-serif'] = ['SimHei']  # 用来正常显示中文标签
plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号

def main():
    # 1. 准备路径与参数
    input_file = "data/附件 1 各门店客户满意度综合得分-A题.xls"
    output_fig_path = "figures/satisfaction_line.png"
    manifest_path = "manifest.json"
    
    # 确保输出目录存在 (使用 pathlib, 禁止 os.makedirs)
    Path("figures").mkdir(parents=True, exist_ok=True)
    
    # 2. 读取 Excel (xlrd)
    try:
        wb = xlrd.open_workbook(input_file)
        sh = wb.sheet_by_index(0)  # sheet1
    except Exception as e:
        print(f"Error opening workbook: {e}")
        raise
    
    # 3. 检查表头与数据行 (按任务规格：第 0 行是大标题，第 1 行是真实列名)
    nrows = sh.nrows
    ncols = sh.ncols
    header_row_idx = 1  # data_schema 指定 header_row: 1
    
    if nrows <= header_row_idx:
        print(f"Error: Not enough rows. Total rows={nrows}, Header row index={header_row_idx}")
        raise ValueError("Insufficient rows in Excel file.")
    
    # 获取表头列名 (从第 1 行读取)
    header_names = [sh.cell_value(header_row_idx, i) for i in range(ncols)]
    print(f"Detected header row content: {header_names}")
    print(f"Total rows: {nrows}, Total cols: {ncols}")
    
    # 4. 定义列映射 (基于 data_schema)
    # schema 说明：第 0 行是大标题，第 1 行是列名。数据从第 2 行开始。
    # columns 列表中的 'col' 是 xlrd 的列索引 (A=0, B=1...), 'canonical' 是 Python 变量名
    col_mapping = [
        {"raw": "月份门店", "col_idx": 0, "py_name": "store_id"},
        {"raw": "1.0", "col_idx": 1, "py_name": "satisfaction_overall_m01"},
        {"raw": "2.0", "col_idx": 2, "py_name": "satisfaction_overall_m02"},
        {"raw": "3.0", "col_idx": 3, "py_name": "satisfaction_overall_m03"},
        {"raw": "4.0", "col_idx": 4, "py_name": "satisfaction_overall_m04"},
        {"raw": "5.0", "col_idx": 5, "py_name": "satisfaction_overall_m05"},
        {"raw": "6.0", "col_idx": 6, "py_name": "satisfaction_overall_m06"},
        {"raw": "7.0", "col_idx": 7, "py_name": "satisfaction_overall_m07"},
        {"raw": "8.0", "col_idx": 8, "py_name": "satisfaction_overall_m08"},
        {"raw": "9.0", "col_idx": 9, "py_name": "satisfaction_overall_m09"},
        {"raw": "10.0", "col_idx": 10, "py_name": "satisfaction_overall_m10"},
        {"raw": "11.0", "col_idx": 11, "py_name": "satisfaction_overall_m11"},
        {"raw": "12.0", "col_idx": 12, "py_name": "satisfaction_overall_m12"}
    ]
    
    # 验证列名是否匹配 (可选，但为了健壮性，我们检查第 1 行的值是否与 schema 预期一致)
    # 注意：schema 中的 'raw' 是示例值，实际读取的是 sh.cell_value(1, i)
    # 如果实际表头与 schema 的 raw 不完全一致（例如空格），这里不做严格断言，而是按 col_idx 取
    
    # 5. 提取数据
    data_rows_start = 2  # data_start_row: 2 (0-based index)
    if nrows < data_rows_start:
        print(f"Error: Data starts at row {data_rows_start} but file has only {nrows} rows.")
        raise ValueError("Data rows missing.")
    
    stores = []
    months_data = {}
    
    for r_idx in range(data_rows_start, nrows):
        # 读取门店 ID
        store_val = sh.cell_value(r_idx, 0)
        if not store_val:
            continue
        stores.append(store_val)
        
        row_vals = [sh.cell_value(r_idx, c) for c in range(ncols)]
        
        # 构建该门店的字典
        month_data = {}
        for i, col_info in enumerate(col_mapping):
            val = row_vals[col_info["col_idx"]]
            if i == 0:
                month_data["store_id"] = store_val
            else:
                # 转换为 float，处理可能的空值
                try:
                    month_data[col_info["py_name"]] = float(val) if val is not None else 0.0
                except ValueError:
                    month_data[col_info["py_name"]] = 0.0
        
        # 合并到总列表 (按门店 ID 分组)
        if store_val in months_data:
            # 如果有重复门店，这里可以选择覆盖或报错。通常竞赛数据是唯一的。
            # 为安全起见，如果 ID 重复，我们打印警告并跳过后续行（或者覆盖）
            # 这里选择覆盖，因为可能是更新的数据
            months_data[store_val].update(month_data)
        else:
            months_data[store_val] = month_data
    
    if not stores:
        print("Error: No data rows found.")
        raise ValueError("No data found in the specified range.")
    
    # 6. 绘图
    fig, ax = plt.subplots(figsize=(12, 8))
    
    # 准备绘图数据：X 轴为月份，Y 轴为满意度
    # 为了美观，我们按门店绘制多条线，或者只画一条平均线？
    # 任务要求："画出各门店月度满意度折线图" -> 通常意味着所有门店的线叠在一起，或分色。
    # 考虑到可能有多个门店，如果数量多会乱。这里假设需要展示趋势，将所有门店画在同一图上。
    
    x_labels = ["1 月", "2 月", "3 月", "4 月", "5 月", "6 月", "7 月", "8 月", "9 月", "10 月", "11 月", "12 月"]
    x_positions = list(range(1, 13))
    
    # 提取所有门店的满意度数据
    store_names = sorted(months_data.keys())
    
    for store in store_names:
        data = months_data[store]
        y_vals = []
        for m_idx, month_name in enumerate(x_labels):
            key = f"satisfaction_overall_m{m_idx + 1:02d}"
            if key in data:
                y_vals.append(data[key])
            else:
                y_vals.append(0.0)
        
        ax.plot(x_positions, y_vals, label=store, linestyle='-', marker='o', linewidth=1.5, markersize=4)
    
    ax.set_xlabel("月份")
    ax.set_ylabel("满意度得分")
    ax.set_title("各门店月度客户满意度综合得分趋势图")
    ax.legend(loc='best')
    ax.grid(True, linestyle='--', alpha=0.7)
    
    # 保存图片
    plt.savefig(output_fig_path, dpi=300, bbox_inches='tight')
    plt.close(fig)
    print(f"Figure saved to: {output_fig_path}")
    
    # 7. 写入 manifest.json
    manifest = {
        "files": [
            output_fig_path
        ]
    }
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    print(f"Manifest saved to: {manifest_path}")

if __name__ == "__main__":
    main()