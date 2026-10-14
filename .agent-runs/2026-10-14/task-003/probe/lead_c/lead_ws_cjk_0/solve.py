import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import xlrd
import json
from pathlib import Path

# 配置中文字体支持，防止乱码
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
    
    # 根据 schema 选择 sheet，这里假设是第一个 sheet 或指定名称
    # 任务说明中提到 sheet_name: "sheet1"，但 xlrd 通常按索引。若需精确匹配名称需遍历。
    # 鉴于竞赛数据常见结构，先尝试按索引 0，若列名不对再调整。根据 notes 提示第 0 行是大标题，header_row=1
    sh = wb.sheet_by_index(0)
    
    nrows = sh.nrows
    ncols = sh.ncols
    print(f"Sheet 行数: {nrows}, 列数: {ncols}")
    print(f"第 0 行内容: {sh.row_values(0)}")
    print(f"第 1 行内容: {sh.row_values(1)}")
    
    # 验证表头是否符合预期 (header_row=1)
    expected_headers = ["store_id", "satisfaction_overall_m01", "satisfaction_overall_m02",
                        "satisfaction_overall_m03", "satisfaction_overall_m04", "satisfaction_overall_m05",
                        "satisfaction_overall_m06", "satisfaction_overall_m07", "satisfaction_overall_m08",
                        "satisfaction_overall_m09", "satisfaction_overall_m10", "satisfaction_overall_m11",
                        "satisfaction_overall_m12"]
    
    actual_headers = [sh.cell_value(1, i) for i in range(ncols)]
    
    if actual_headers != expected_headers:
        raise ValueError(f"表头不匹配！期望: {expected_headers}, 实际: {actual_headers}")
    
    # 提取数据
    store_ids = [sh.cell_value(i, 0) for i in range(2, nrows)]
    
    # 定义月份列的索引 (从第 1 列开始，即 index 1)
    month_cols = list(range(1, ncols))
    satisfaction_data = []
    
    for row_idx in range(2, nrows):
        row_vals = sh.row_values(row_idx)
        # 构造字典以便后续绘图
        point_dict = {"store": store_ids[row_idx - 2]}
        for col_idx, month_col_name in zip(month_cols, expected_headers[1:]):
            val = row_vals[col_idx]
            # xlrd cell_value 直接返回数值或字符串，无需再取 .value
            if isinstance(val, float):
                point_dict[month_col_name] = val
            else:
                point_dict[month_col_name] = float(val) if val is not None else 0.0
        satisfaction_data.append(point_dict)
    
    # 准备绘图数据：以 store_id 为 x，各月为 y
    # 为了画多条线，我们需要将数据重塑或循环绘制
    # 这里选择：x 轴为月份 (1-12)，y 轴为满意度，不同颜色代表不同门店
    
    months = list(range(1, 13))
    colors = plt.cm.rainbow.colors[:len(store_ids)]
    
    fig, ax = plt.subplots(figsize=(12, 8))
    
    for i, store in enumerate(store_ids):
        y_vals = []
        for m_idx, month_name in zip(month_cols, expected_headers[1:]):
            # 查找该门店在该月的值
            val = satisfaction_data[i].get(month_name)
            if val is not None:
                y_vals.append(val)
            else:
                y_vals.append(0.0) # 处理缺失值，或者根据实际数据调整
        
        ax.plot(months, y_vals, label=str(store), color=colors[i], linewidth=1.5, marker='o', markersize=4)
    
    ax.set_title("各门店月度客户满意度综合得分", fontsize=16)
    ax.set_xlabel("月份", fontsize=12)
    ax.set_ylabel("满意度得分", fontsize=12)
    ax.legend(loc='best', bbox_to_anchor=(1.05, 1), ncol=1)
    ax.grid(True, linestyle='--', alpha=0.7)
    
    # 保存图片
    plt.savefig(OUTPUT_PNG, dpi=300, bbox_inches='tight')
    plt.close(fig)
    
    print(f"图表已保存至: {OUTPUT_PNG}")
    
except Exception as e:
    # 打印错误信息以便调试，不吞掉异常
    print(f"脚本执行失败: {e}")
    raise

# 写入 manifest.json
manifest_content = {"files": [OUTPUT_PNG]}
with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
    json.dump(manifest_content, f, ensure_ascii=False)

print(f"清单已写入: {MANIFEST_FILE}")