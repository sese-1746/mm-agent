import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
from pathlib import Path
import json

# 配置中文支持，防止乱码
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

INPUT_FILE = "data/att1.xls"
OUTPUT_PNG = "figures/satisfaction_line.png"
MANIFEST_FILE = "manifest.json"
HEADER_ROW = 1

# 确保输出目录存在
Path("figures").mkdir(parents=True, exist_ok=True)

try:
    # 使用 xlrd 读取 .xls 文件
    import xlrd
    wb = xlrd.open_workbook(INPUT_FILE)
    sh = wb.sheet_by_index(0)
    
    nrows = sh.nrows
    ncols = sh.ncols
    print(f"Sheet 尺寸：{nrows}行 x {ncols}列")
    
    # 检查第 0 行和第 1 行内容以辅助调试（不吞异常）
    row0_content = [sh.cell_value(0, c) for c in range(ncols)]
    row1_content = [sh.cell_value(HEADER_ROW, c) for c in range(ncols)]
    print(f"第 0 行 (大标题): {row0_content}")
    print(f"第 1 行 (真实表头): {row1_content}")
    
    # 根据 header_row=1 获取列名
    headers = [sh.cell_value(HEADER_ROW, c).strip() if isinstance(sh.cell_value(HEADER_ROW, c), str) else sh.cell_value(HEADER_ROW, c)
               for c in range(ncols)]
    
    # 寻找包含 '满意度' 或 'score' 的列作为 Y 轴，以及 '门店' 或 'store' 作为 X 轴
    y_col_name = None
    x_col_name = None
    
    for i, h in enumerate(headers):
        h_str = str(h).lower()
        if '满意度' in h or 'score' in h_str:
            y_col_name = headers[i]
        elif '门店' in h or 'store' in h_str or 'shop' in h_str:
            x_col_name = headers[i]
    
    if not y_col_name:
        raise ValueError(f"未找到包含‘满意度’或'score'的列。可用列名：{headers}")
    if not x_col_name:
        raise ValueError(f"未找到包含‘门店’、'store'或'shop'的列。可用列名：{headers}")
    
    print(f"检测到 X 轴列：'{x_col_name}', Y 轴列：'{y_col_name}'")
    
    # 使用 pandas 读取数据以便绘图（xlrd 返回的是 xlrd.sheet，需转换或直接用 pandas 读 xls）
    # 注意：pandas.read_excel 默认用 openpyxl 处理 .xlsx，对 .xls 需要 xlrd 引擎
    df = pd.read_excel(INPUT_FILE, header=HEADER_ROW, engine='xlrd')
    
    # 确保列名是字符串（有时 xlrd 读取后 pandas 可能保留为其他类型）
    df.columns = [str(c) for c in df.columns]
    
    # 提取数据
    x_data = df[x_col_name].tolist()
    y_data = df[y_col_name].tolist()
    
    # 绘图
    plt.figure(figsize=(10, 6))
    plt.plot(x_data, y_data, marker='o', linestyle='-', linewidth=2, markersize=4)
    plt.title(f"各门店月度客户满意度综合得分趋势图")
    plt.xlabel(x_col_name)
    plt.ylabel(y_col_name)
    plt.grid(True, axis='y', linestyle='--', alpha=0.6)
    
    # 保存图片
    plt.savefig(OUTPUT_PNG, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"图表已保存至：{OUTPUT_PNG}")
    
except Exception as e:
    # 打印异常信息以便调试，然后让程序正常退出（非 0 退出码由 Python 解释器自动处理）
    print(f"脚本执行失败：{e}")
    raise

# 写入 manifest.json
manifest_data = {"files": [OUTPUT_PNG]}
with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
    json.dump(manifest_data, f, ensure_ascii=False)
print(f"清单已更新：{MANIFEST_FILE}")