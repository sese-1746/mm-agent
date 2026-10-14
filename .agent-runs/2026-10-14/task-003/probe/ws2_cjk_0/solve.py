import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import xlrd
import json
from pathlib import Path

# 配置中文支持，防止乱码
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

INPUT_FILE = "data/附件 1 各门店客户满意度综合得分-A题.xls"
OUTPUT_PNG = "figures/satisfaction_line.png"
MANIFEST_FILE = "manifest.json"

# 确保输出目录存在
Path("figures").mkdir(parents=True, exist_ok=True)

try:
    # 使用 xlrd 打开 .xls 文件
    wb = xlrd.open_workbook(INPUT_FILE)
    sh = wb.sheet_by_index(0)
    
    # 检查表头行 (header_row=1)，打印调试信息以便定位问题
    print(f"Sheet 行数: {sh.nrows}, 列数: {sh.ncols}")
    print(f"第 0 行内容: {sh.row(0)}")
    print(f"第 1 行内容 (表头): {sh.row(1)}")
    
    # 根据 schema 定义列映射
    header_row = 1
    data_start_row = 2
    
    # 构建列名到 xlrd 索引的映射
    # 注意：xlrd 返回的是 Cell 对象，需要 .value 获取内容
    col_map = {}
    for idx in range(sh.ncols):
        cell_val = sh.cell_value(header_row, idx)
        # 处理可能的字符串格式 (如 '1.0')
        raw_name = str(cell_val).strip()
        if raw_name:
            col_map[raw_name] = idx
    
    # 验证必要列是否存在
    required_cols = ['月份门店'] + [f'{m}.0' for m in range(1, 13)]
    missing_cols = [c for c in required_cols if c not in col_map]
    if missing_cols:
        raise ValueError(f"缺失必要的列名: {missing_cols}")
    
    # 提取数据
    store_ids = []
    satisfaction_data = {}
    
    for r_idx in range(data_start_row, sh.nrows):
        cell_store = sh.cell_value(r_idx, col_map['月份门店'])
        store_id = str(cell_store).strip()
        if not store_id:
            continue
            
        store_ids.append(store_id)
        satisfaction_data[store_id] = {}
        
        for m in range(1, 13):
            col_name = f'{m}.0'
            if col_name in col_map:
                val = sh.cell_value(r_idx, col_map[col_name])
                # xlrd cell_value 直接返回数值或字符串，无需再取 .value
                satisfaction_data[store_id][f'satisfaction_overall_m{m:02d}'] = float(val)
    
    if not store_ids:
        raise ValueError("未读取到任何有效数据行")
    
    # 绘图
    plt.figure(figsize=(14, 8))
    colors = plt.cm.tab10.colors
    
    for i, store_id in enumerate(store_ids):
        data = satisfaction_data[store_id]
        months = list(range(1, 13))
        values = [data[f'satisfaction_overall_m{m:02d}'] for m in months]
        plt.plot(months, values, label=store_id, color=colors[i % len(colors)], linewidth=1.5)
    
    plt.title('各门店月度客户满意度综合得分变化趋势', fontsize=14)
    plt.xlabel('月份', fontsize=12)
    plt.ylabel('满意度得分', fontsize=12)
    plt.xticks(range(1, 13))
    plt.legend(loc='best')
    plt.grid(True, linestyle='--', alpha=0.6)
    
    # 保存图片
    plt.savefig(OUTPUT_PNG, dpi=300, bbox_inches='tight')
    plt.close()
    
    # 生成 manifest.json
    output_list = [OUTPUT_PNG]
    with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
        json.dump({"files": output_list}, f, ensure_ascii=False)
        
except Exception as e:
    print(f"脚本执行失败: {e}")
    raise