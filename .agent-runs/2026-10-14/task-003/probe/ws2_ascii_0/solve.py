import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import xlrd
import json
from pathlib import Path

# 配置中文字体支持，防止乱码
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
    print(f"Sheet 行数：{nrows}, 列数：{ncols}")
    print(f"第 0 行内容：{[sh.cell_value(0, i) for i in range(ncols)]}")
    print(f"第 1 行 (表头) 内容：{[sh.cell_value(header_row_idx, i) for i in range(ncols)]}")
    
    # 根据实际列数动态构建期望的表头列表
    # 假设第一列是 store_id，后面依次是 1-12 月的满意度
    expected_headers = ["store_id"] + [f"satisfaction_overall_m{i:02d}" for i in range(1, 13)]
    
    actual_headers = [sh.cell_value(header_row_idx, i) for i in range(ncols)]
    
    if len(actual_headers) != len(expected_headers):
        raise ValueError(f"列数不匹配。期望 {len(expected_headers)} 列，实际 {len(actual_headers)} 列")
        
    for i, (exp, act) in enumerate(zip(expected_headers, actual_headers)):
        if exp != act:
            raise ValueError(f"第 {i} 列表头不匹配：期望 '{exp}'，实际 '{act}'")
            
    # 提取数据
    store_ids = []
    satisfaction_data = {}
    
    for row_idx in range(header_row_idx + 1, nrows):
        store_id = sh.cell_value(row_idx, 0)
        store_ids.append(store_id)
        
        row_values = [sh.cell_value(row_idx, i) for i in range(1, ncols)]
        satisfaction_data[store_id] = row_values
        
    # 准备绘图数据：取第一个门店的数据作为示例，或者绘制所有门店的叠加图
    # 这里选择绘制所有门店在同一张图上，x 轴为月份，不同颜色代表不同门店
    months = list(range(1, 13))
    x = months
    
    # 创建图表
    fig, ax = plt.subplots(figsize=(12, 6))
    colors = plt.cm.rainbow(np.linspace(0, 1, len(store_ids)))
    
    for idx, store_id in enumerate(store_ids):
        y_values = satisfaction_data[store_id]
        # 过滤掉 None 或空值
        valid_indices = [i for i, v in enumerate(y_values) if v is not None and v != '']
        valid_y = [y_values[i] for i in valid_indices]
        valid_x = [months[i-1] for i in valid_indices]
        ax.plot(valid_x, valid_y, marker='o', linestyle='-', color=colors[idx], label=store_id)
    
    ax.set_title('各门店月度客户满意度综合得分趋势图')
    ax.set_xlabel('月份')
    ax.set_ylabel('满意度得分')
    ax.legend(loc='best')
    
    # 保存图像
    fig.savefig(OUTPUT_PNG, dpi=300)
    plt.close(fig)
    
    # 生成 manifest.json
    output_list = [OUTPUT_PNG]
    manifest = {"files": output_list}
    with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
        
except Exception as e:
    # 打印异常信息以便调试，然后让程序正常退出（非 0 退出码由 Python 默认处理）
    print(f"发生错误：{e}")
    raise