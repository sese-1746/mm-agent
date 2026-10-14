import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import xlrd
import json
from pathlib import Path

# 配置中文显示支持 (防止乱码)
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

INPUT_FILE = "data/附件 1 各门店客户满意度综合得分-A题.xls"
OUTPUT_PNG = "figures/satisfaction_line.png"
MANIFEST_FILE = "manifest.json"

# 定义列映射：原始表头 -> 内部变量名
# 根据 data_schema，第 0 行是大标题，第 1 行是真实列名
# columns[].col 对应 xlrd 的列索引 (A=0, B=1...)
# columns[].name 是我们要用的逻辑列名
COL_MAP = {
    "月份门店": "store_id",
    "1.0": "satisfaction_overall_m01",
    "2.0": "satisfaction_overall_m02",
    "3.0": "satisfaction_overall_m03",
    "4.0": "satisfaction_overall_m04",
    "5.0": "satisfaction_overall_m05",
    "6.0": "satisfaction_overall_m06",
    "7.0": "satisfaction_overall_m07",
    "8.0": "satisfaction_overall_m08",
    "9.0": "satisfaction_overall_m09",
    "10.0": "satisfaction_overall_m10",
    "11.0": "satisfaction_overall_m11",
    "12.0": "satisfaction_overall_m12"
}

try:
    # 检查输入文件是否存在
    if not Path(INPUT_FILE).exists():
        raise FileNotFoundError(f"输入文件不存在：{INPUT_FILE}")
    
    # 打开 Excel (xlrd)
    wb = xlrd.open_workbook(INPUT_FILE)
    sh = wb.sheet_by_index(0)  # 使用第一个 sheet
    
    # 打印调试信息以验证表结构 (符合任务要求：先打印 nrows/ncols 及第 0,1 行内容)
    print(f"Sheet 行数：{sh.nrows}, 列数：{sh.ncols}")
    print(f"第 0 行内容：{[cell_value for cell_value in sh.row(0)]}")
    print(f"第 1 行内容：{[cell_value for cell_value in sh.row(1)]}")
    
    # 验证表头是否匹配
    header_row_idx = 1  # 任务规格指定 header_row=1
    actual_headers = [sh.cell_value(rowx=header_row_idx, colx=i) for i in range(sh.ncols)]
    expected_headers = list(COL_MAP.keys())
    
    if actual_headers != expected_headers:
        raise ValueError(f"表头不匹配！\n期望：{expected_headers}\n实际：{actual_headers}")
    
    # 提取数据
    store_ids = []
    satisfaction_data = {}
    
    for col_idx, (raw_name, canonical_name) in COL_MAP.items():
        if raw_name == "月份门店":
            continue
        satisfaction_data[canonical_name] = []
    
    # 遍历数据行 (从第 2 行开始，即 data_start_row=2)
    for row_idx in range(2, sh.nrows):
        store_id = sh.cell_value(rowx=row_idx, colx=0)  # A 列
        store_ids.append(store_id)
        
        for col_idx, (raw_name, canonical_name) in COL_MAP.items():
            if raw_name == "月份门店":
                continue
            val = sh.cell_value(rowx=row_idx, colx=col_idx)  # xlrd cell_value 直接返回数值/字符串
            satisfaction_data[canonical_name].append(val)
    
    # 绘图
    plt.figure(figsize=(12, 8))
    colors = plt.cm.viridis(range(len(satisfaction_data)))
    
    for idx, (col_name, values) in enumerate(satisfaction_data.items()):
        plt.plot(store_ids, values, label=col_name.replace('satisfaction_overall_m', ''), color=colors[idx], linewidth=1.5)
    
    plt.title("各门店月度客户满意度综合得分趋势", fontsize=14)
    plt.xlabel("月份门店", fontsize=12)
    plt.ylabel("满意度得分", fontsize=12)
    plt.legend(loc='best', fontsize=10)
    plt.grid(True, linestyle='--', alpha=0.6)
    
    # 确保输出目录存在
    Path(OUTPUT_PNG).parent.mkdir(parents=True, exist_ok=True)
    
    # 保存图片
    plt.savefig(OUTPUT_PNG, dpi=300, bbox_inches='tight')
    plt.close()
    
    # 生成 manifest.json
    manifest = {"files": [OUTPUT_PNG]}
    with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    
except Exception as e:
    # 禁止吞异常，直接抛出以非 0 退出码结束
    raise