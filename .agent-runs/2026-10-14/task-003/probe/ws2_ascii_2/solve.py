import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import xlrd
import json
from pathlib import Path

# 配置中文显示
plt.rcParams['font.sans-serif'] = ['SimHei']  # 用来正常显示中文标签
plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号

def main():
    input_file = "data/att1.xls"
    output_image = "figures/satisfaction_line.png"
    manifest_path = "manifest.json"
    
    # 确保输出目录存在
    Path("figures").mkdir(parents=True, exist_ok=True)
    
    try:
        # 使用 xlrd 读取 .xls 文件
        wb = xlrd.open_workbook(input_file)
        sh = wb.sheet_by_index(0)
        
        # 检查表头行 (header_row=1)，即第 2 行数据（索引从 0 开始）
        header_row_idx = 1
        nrows = sh.nrows
        ncols = sh.ncols
        
        if nrows <= header_row_idx:
            raise ValueError(f"表格行数 ({nrows}) 不足以读取表头行 {header_row_idx}")
        
        # 获取表头列名
        headers = [sh.cell_value(header_row_idx, i) for i in range(ncols)]
        print(f"读取到的表头: {headers}")
        print(f"表格总行数: {nrows}, 总列数: {ncols}")
        
        # 定义需要的列索引映射 (基于 schema)
        # 注意：xlrd cell_value 返回的是字符串或数字，直接作为字典键
        col_map = {
            "store_id": 0,
            "satisfaction_overall_m01": 1,
            "satisfaction_overall_m02": 2,
            "satisfaction_overall_m03": 3,
            "satisfaction_overall_m04": 4,
            "satisfaction_overall_m05": 5,
            "satisfaction_overall_m06": 6,
            "satisfaction_overall_m07": 7,
            "satisfaction_overall_m08": 8,
            "satisfaction_overall_m09": 9,
            "satisfaction_overall_m10": 10,
            "satisfaction_overall_m11": 11,
            "satisfaction_overall_m12": 12
        }
        
        # 验证必要列是否存在
        for col_name, idx in col_map.items():
            if idx >= ncols:
                raise ValueError(f"列索引 {idx} 超出表格列数 {ncols}")
            header_val = sh.cell_value(header_row_idx, idx)
            if header_val != col_name:
                # 尝试兼容：如果表头是数字字符串如 '1.0'，而 schema 期望 '1月'
                # 但根据 notes，列名以 columns[].name 为准，不要按语义猜
                # 这里严格匹配 schema 中的 canonical name 与 sheet 中的实际值
                # 如果实际值是 '1.0' 而我们需要的是 'satisfaction_overall_m01'
                # 则必须确保读取逻辑能处理这种差异，或者抛出异常
                # 根据任务描述："列名与表头行以 data_schema 为准"
                # 这意味着我们应该用 schema 中的 canonical name 作为 pandas 的列名
                # 但 xlrd 读出来的是原始值。如果原始值是 '1.0'，我们需要映射。
                # 重新审视 notes: "不要按语义猜列名" -> 必须严格匹配。
                # 假设 sheet 中第 1 行 B 列真的是 '1.0'，而 schema 说 canonical 是 'satisfaction_overall_m01'
                # 这说明我们需要在读取后重命名列，或者 schema 的 raw 字段才是实际值？
                # schema 中: "name": "1月", "raw": "1.0"
                # 这意味着实际文件里写的是 '1.0'，但逻辑上代表 1 月。
                # 为了绘图，我们需要将 '1.0', '2.0'... 映射为月份标签。
                pass
        
        # 读取数据并构建 DataFrame
        # 使用字典推导式从 xlrd sheet 构建 rows
        data_rows = []
        for r_idx in range(header_row_idx + 1, nrows):
            row_values = [sh.cell_value(r_idx, c) for c in range(ncols)]
            store_id = row_values[0]
            # 提取月份数据，跳过非数值（如空值或标题）
            monthly_scores = []
            for m_idx, col_name in col_map.items():
                val = row_values[col_map[m_idx]]
                try:
                    float(val)
                    monthly_scores.append(float(val))
                except (ValueError, TypeError):
                    monthly_scores.append(None)
            data_rows.append({"store_id": store_id} | {f"score_{m_idx}": v for m_idx, v in enumerate(monthly_scores)})
        
        # 更稳健的方法：直接按列索引读取，然后重命名列
        df = pd.DataFrame([sh.row(r_idx).value for r_idx in range(header_row_idx + 1, nrows)], columns=headers)
        
        # 清理数据：将 '1.0', '2.0' 等转换为月份字符串
        month_map = {
            "1.0": "1 月", "2.0": "2 月", "3.0": "3 月", "4.0": "4 月",
            "5.0": "5 月", "6.0": "6 月", "7.0": "7 月", "8.0": "8 月",
            "9.0": "9 月", "10.0": "10 月", "11.0": "11 月", "12.0": "12 月"
        }
        
        # 筛选出包含满意度数据的列 (B 到 M)
        satisfaction_cols = [col for col in df.columns if col in month_map]
        
        # 重命名列以便绘图
        df_plot = df.copy()
        df_plot['store_id'] = df_plot['store_id'].astype(str)
        
        # 提取月份列和满意度列
        x_labels = [month_map[col] for col in satisfaction_cols]
        y_values = df_plot[satisfaction_cols].values
        stores = df_plot['store_id'].unique()
        
        # 为每个门店绘制折线
        fig, ax = plt.subplots(figsize=(12, 6))
        colors = plt.cm.tab10.colors
        
        for i, store in enumerate(stores):
            mask = df_plot['store_id'] == store
            y_data = df_plot.loc[mask, satisfaction_cols[0]].values if len(satisfaction_cols) > 0 else []
            # 确保所有门店都有数据，如果没有则跳过或处理
            if len(y_data) > 0:
                ax.plot(x_labels, y_data, label=store, color=colors[i % len(colors)], linewidth=1.5)
        
        ax.set_title("各门店月度客户满意度综合得分", fontsize=14)
        ax.set_xlabel("月份")
        ax.set_ylabel("满意度得分")
        ax.legend(loc='best')
        ax.grid(True, linestyle='--', alpha=0.7)
        
        # 保存图片
        plt.savefig(output_image, dpi=300, bbox_inches='tight')
        plt.close(fig)
        
        print(f"图表已保存至: {output_image}")
        
        # 写入 manifest.json
        output_list = [output_image]
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump({"files": output_list}, f, ensure_ascii=False)
        
        print(f"清单已保存至: {manifest_path}")
        
    except Exception as e:
        # 打印异常信息以便调试，不吞掉异常
        print(f"发生错误: {e}")
        raise

if __name__ == "__main__":
    main()