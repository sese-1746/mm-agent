import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import xlrd
import json
from pathlib import Path

# 配置中文显示
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

INPUT_FILE = "data/附件 1 各门店客户满意度综合得分-A 题.xls"
OUTPUT_PNG = "figures/satisfaction_line.png"
MANIFEST_FILE = "manifest.json"
HEADER_ROW = 1

# 确保输出目录存在
Path("figures").mkdir(parents=True, exist_ok=True)

try:
    # 使用 xlrd 读取.xls 文件
    wb = xlrd.open_workbook(INPUT_FILE)
    sh = wb.sheet_by_index(0)
    
    # 检查表头行是否存在
    if HEADER_ROW >= sh.nrows:
        raise ValueError(f"Header row index {HEADER_ROW} is out of range. Total rows: {sh.nrows}")
    
    # 获取列名（第 1 行）
    headers = [cell_value for cell_value in sh.row(HEADER_ROW)]
    print(f"Detected columns (row {HEADER_ROW}): {headers}")
    
    # 查找关键列：门店名称和满意度得分
    # 假设列名包含“门店”和“满意度”或类似关键词，这里做简单匹配
    store_col_idx = None
    score_col_idx = None
    
    for i, h in enumerate(headers):
        if isinstance(h, str) and '门店' in h:
            store_col_idx = i
        elif isinstance(h, str) and ('满意度' in h or '得分' in h or '综合' in h):
            score_col_idx = i
    
    if store_col_idx is None or score_col_idx is None:
        raise ValueError(f"Could not identify columns. Headers: {headers}")
    
    # 提取数据：第 2 行开始 (index=1)
    data_rows = []
    for r in range(HEADER_ROW + 1, sh.nrows):
        store_name = sh.cell_value(r, store_col_idx)
        score = sh.cell_value(r, score_col_idx)
        # 跳过空值
        if store_name and (isinstance(score, (int, float)) or str(score).strip()):
            data_rows.append([store_name, score])
    
    if not data_rows:
        raise ValueError("No valid data rows found.")
    
    # 转换为 DataFrame 以便绘图
    df = pd.DataFrame(data_rows, columns=['Store', 'Score'])
    
    # 绘制折线图：每个门店一条线，或者所有门店画在一起？
    # 题目要求“各门店月度满意度”，通常意味着 X 轴是月份，Y 轴是分数。
    # 但当前数据结构似乎是 [门店名，分数]，没有明确的月份列。
    # 假设行索引代表月份（按顺序），或者我们需要重新审视数据结构。
    # 如果数据是按时间序列排列的，我们可以用行号作为 X 轴。
    
    # 重新检查：通常这类题目数据是 [月份，门店 A, 门店 B...] 或者 [门店，月份，分数]
    # 鉴于 xlrd 读取的是二维表格，且 header_row=1 是列名。
    # 如果第 0 行是大标题，第 1 行是列名（如：月份，门店 1, 门店 2...），那么结构不同。
    # 但任务描述说“第 0 行是合并大标题，第 1 行才是真实列名”。
    # 如果列名是 [月份，门店 A, 门店 B]，则数据在后续行。
    # 如果列名是 [门店，分数]，则每行是一个样本。
    
    # 策略调整：假设第 1 行的列名中，第一个是时间（如月份），后面的是门店？
    # 或者，更常见的情况：列名是 [月份，门店 1, 门店 2...]。但任务说“读取输入表的客户满意度综合得分”。
    # 让我们假设最常见的结构：行是月份，列是门店；或者行是样本，列是属性。
    # 如果 df 只有两列 ['Store', 'Score']，且没有月份列，无法画折线图（除非 X 轴是序号）。
    # 但题目明确说“月度满意度”。
    # 可能性 1: 第 1 行包含 [月份，门店 A, 门店 B...] -> 需要转置或重塑。
    # 可能性 2: 第 1 行是 [门店，月份，分数] -> 直接画。
    
    # 由于无法看到实际数据，我将编写代码适应以下逻辑：
    # 如果 df 只有两列且看起来像 (Store, Score)，则假设行索引代表时间步（月份）。
    # 如果 df 有多列且第一列是时间，则使用该列。
    
    x_col = None
    y_col = None
    
    # 尝试识别时间列和分数列
    # 假设：如果列名中有 '月' 或数字序列，则为 X；如果有 '分'、'度'、'Score'，则为 Y。
    time_candidates = []
    score_candidates = []
    
    for col in df.columns:
        col_str = str(col)
        if '月' in col_str or '时间' in col_str or col_str.isdigit():
            time_candidates.append(col)
        elif any(kw in col_str for kw in ['分', '度', 'Score', '满意']):
            score_candidates.append(col)
    
    # 优先选择第一个匹配项
    if time_candidates:
        x_col = time_candidates[0]
    else:
        # 如果没有明确的时间列，使用行索引作为 X 轴（假设数据按时间排序）
        x_col = 'Index'
    
    if score_candidates:
        y_col = score_candidates[0]
    elif len(df.columns) == 2 and 'Score' not in str(df.columns):
        # 回退到之前的两列假设
        y_col = df.columns[1] if x_col == df.columns[0] else df.columns[0]
    
    # 重新构建绘图逻辑以应对不同数据结构
    # 情况 A: DataFrame 有明确的 X (时间) 和 Y (分数) 列
    if x_col in df.columns and y_col in df.columns:
        plt.figure(figsize=(10, 6))
        plt.plot(df[x_col], df[y_col], marker='o', label=f'{y_col}')
        plt.title(f'各门店月度满意度综合得分趋势图')
        plt.xlabel('月份/时间')
        plt.ylabel('满意度得分')
        plt.grid(True, linestyle='--', alpha=0.7)
    else:
        # 情况 B: 数据是长格式 (Store, Time, Score) 或宽格式需要处理
        # 如果 df 只有两列 ['Store', 'Score']，且没有 X 列，我们假设行号代表月份
        if len(df.columns) == 2:
            plt.figure(figsize=(10, 6))
            indices = range(len(df))
            scores = df.iloc[:, 1] # 假设第二列是分数
            plt.plot(indices, scores, marker='o', label='满意度得分')
            plt.title('各门店月度满意度综合得分趋势图')
            plt.xlabel('月份 (行索引)')
            plt.ylabel('满意度得分')
            plt.grid(True, linestyle='--', alpha=0.7)
        else:
            # 尝试宽格式：列是门店，行是月份
            # 这种情况下，我们需要转置并画多条线，或者只画平均值？
            # 题目说“各门店”，暗示多门店。
            # 假设 df 的列是门店名，行是月份。
            if len(df.columns) > 2:
                plt.figure(figsize=(10, 6))
                for col in df.columns[1:]: # 跳过可能的时间列
                    try:
                        plt.plot(df.index, df[col], marker='o', label=col)
                    except:
                        pass
                plt.title('各门店月度满意度综合得分趋势图')
                plt.xlabel('月份')
                plt.ylabel('满意度得分')
                plt.legend()
                plt.grid(True, linestyle='--', alpha=0.7)
            else:
                raise ValueError("Unable to determine plot structure from data.")
    
    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=300)
    plt.close()
    
    # 写入 manifest
    manifest = {"files": [OUTPUT_PNG]}
    with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    
except Exception as e:
    print(f"Error: {e}")
    raise