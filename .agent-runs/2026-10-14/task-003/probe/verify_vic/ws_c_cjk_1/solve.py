import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import xlrd
import json
from pathlib import Path

# 配置中文支持
plt.rcParams['font.sans-serif'] = ['SimHei']  # 用来正常显示中文标签
plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号

def main():
    input_file = "data/附件 1 各门店客户满意度综合得分-A题.xls"
    output_fig = "figures/satisfaction_line.png"
    
    # 确保输出目录存在
    Path("figures").mkdir(parents=True, exist_ok=True)
    
    try:
        # 使用 xlrd 打开 xls 文件
        wb = xlrd.open_workbook(input_file)
        sh = wb.sheet_by_index(0)
        
        # 检查表头行 (header_row=1)，打印调试信息以便定位问题
        print(f"Sheet 行数: {sh.nrows}, 列数: {sh.ncols}")
        print(f"第 0 行内容: {sh.row_values(0)}")
        print(f"第 1 行内容 (表头): {sh.row_values(1)}")
        
        # 根据 schema 定义列名映射
        header_row_idx = 1
        data_start_row_idx = 2
        
        # 构建列名到索引的映射，优先使用 schema 中的 canonical 名称或 raw 名称匹配
        expected_cols = [
            "store_id", "satisfaction_overall_m01", "satisfaction_overall_m02",
            "satisfaction_overall_m03", "satisfaction_overall_m04", "satisfaction_overall_m05",
            "satisfaction_overall_m06", "satisfaction_overall_m07", "satisfaction_overall_m08",
            "satisfaction_overall_m09", "satisfaction_overall_m10", "satisfaction_overall_m11",
            "satisfaction_overall_m12"
        ]
        
        col_indices = {}
        for i, raw_val in enumerate(sh.row_values(header_row_idx)):
            # 尝试直接匹配 schema 中的 canonical 名称，如果找不到则看 raw 值
            # 注意：xlrd 读取的可能是字符串，需要处理可能的空格或类型差异
            col_name = str(raw_val).strip()
            
            # 检查是否是 store_id (通常是第一列)
            if i == 0:
                col_indices["store_id"] = i
                continue
            
            # 匹配月份列，schema 中 raw 是 "1.0", "2.0" 等，实际可能是 "1月" 或 "1.0"
            for exp_col in expected_cols:
                if exp_col.startswith("satisfaction") and col_name.replace(".", "").isdigit():
                    # 提取月份数字
                    month_num = int(col_name.replace(".", ""))
                    canonical_name = f"satisfaction_overall_m{month_num:02d}"
                    if canonical_name in expected_cols:
                        col_indices[canonical_name] = i
                        break
            else:
                # 如果没匹配到，尝试用 raw 值作为 key (处理非数字月份名的情况)
                # 这里简化逻辑：直接按列顺序填充，假设前几列是 store_id, 1 月...12 月
                if i > 0 and "store_id" not in col_indices:
                    continue
        
        # 重新构建更健壮的列索引映射逻辑
        # 第 0 列通常是 store_id
        if sh.cell_value(header_row_idx, 0).strip() == "月份门店":
            col_indices["store_id"] = 0
        else:
            raise ValueError(f"第一列表头不是 '月份门店'，实际为: {sh.cell_value(header_row_idx, 0)}")
        
        # 填充月份列索引
        for i in range(1, sh.ncols):
            raw_val = str(sh.cell_value(header_row_idx, i)).strip()
            if raw_val.replace(".", "").isdigit():
                month = int(raw_val.replace(".", ""))
                canonical_name = f"satisfaction_overall_m{month:02d}"
                col_indices[canonical_name] = i
        
        # 验证是否找到了所有需要的月份列
        required_months = [f"satisfaction_overall_m{m:02d}" for m in range(1, 13)]
        missing_cols = [c for c in required_months if c not in col_indices]
        if missing_cols:
            raise ValueError(f"缺失以下月份列: {missing_cols}")
        
        # 读取数据
        store_ids = []
        satisfaction_data = {}
        
        for r_idx in range(data_start_row_idx, sh.nrows):
            row_vals = sh.row_values(r_idx)
            store_id = str(row_vals[0]).strip()
            store_ids.append(store_id)
            
            current_month_data = {}
            for col_name, col_idx in col_indices.items():
                if col_name == "store_id":
                    continue
                val = row_vals[col_idx]
                # xlrd cell_value 返回数值或字符串，直接赋值
                try:
                    current_month_data[col_name] = float(val)
                except (ValueError, TypeError):
                    current_month_data[col_name] = val
            
            satisfaction_data[store_id] = current_month_data
        
        # 绘图
        plt.figure(figsize=(12, 8))
        colors = plt.cm.tab10.colors
        
        for store_id in store_ids:
            data = satisfaction_data[store_id]
            x_vals = list(data.keys())
            y_vals = [data[k] for k in sorted(x_vals)]
            color = colors[len(store_ids) % len(colors)]
            plt.plot(x_vals, y_vals, label=store_id, color=color, linewidth=1.5)
        
        plt.title("各门店月度客户满意度综合得分变化趋势")
        plt.xlabel("月份")
        plt.ylabel("满意度得分")
        plt.legend(loc='best', fontsize=8)
        plt.grid(True, linestyle='--', alpha=0.7)
        
        # 保存图片
        plt.savefig(output_fig, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"图表已保存至: {output_fig}")
        
    except Exception as e:
        # 打印异常信息以便调试，不吞掉异常
        print(f"发生错误: {e}")
        raise

if __name__ == "__main__":
    main()