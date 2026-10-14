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
    manifest_path = "manifest.json"
    
    # 确保输出目录存在
    Path("figures").mkdir(parents=True, exist_ok=True)
    
    try:
        # 使用 xlrd 打开 xls 文件
        wb = xlrd.open_workbook(input_file)
        sh = wb.sheet_by_index(0)
        
        # 检查表头行 (header_row=1)，打印调试信息以便定位问题
        header_row_idx = 1
        print(f"Sheet 行数: {sh.nrows}, 列数: {sh.ncols}")
        print(f"第 0 行内容: {[sh.cell_value(r, c) for c in range(sh.ncols)]}")
        print(f"第 1 行 (表头) 内容: {[sh.cell_value(header_row_idx, c) for c in range(sh.ncols)]}")
        
        # 根据 schema 定义列名映射
        # 注意：xlrd cell_value() 返回的是字符串或数值，直接作为字典键即可
        col_map = {
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
        
        # 验证表头是否存在
        for raw_name, canonical_name in col_map.items():
            if raw_name not in [sh.cell_value(header_row_idx, c) for c in range(sh.ncols)]:
                raise ValueError(f"表头中未找到列名: {raw_name}")
        
        # 提取数据
        store_ids = []
        satisfaction_data = {}
        
        # 从第 2 行开始读取数据 (data_start_row=2)
        data_start_row_idx = 2
        for r in range(data_start_row_idx, sh.nrows):
            store_id_val = sh.cell_value(r, 0)  # A 列
            store_ids.append(store_id_val)
            satisfaction_data[store_id_val] = []
            
            for c in range(1, sh.ncols):
                raw_val = sh.cell_value(r, c)
                if raw_val is None:
                    continue
                # 尝试转换为 float，如果失败则保持原样（虽然 schema 说是 float）
                try:
                    satisfaction_data[store_id_val].append(float(raw_val))
                except (ValueError, TypeError):
                    satisfaction_data[store_id_val].append(raw_val)
        
        # 绘图
        fig, ax = plt.subplots(figsize=(12, 8))
        
        colors = plt.cm.tab10.colors
        for i, store_id in enumerate(store_ids):
            values = satisfaction_data[store_id]
            if len(values) == 0:
                continue
            # x 轴为月份索引 (0-11)
            ax.plot(range(len(values)), values, label=store_id, color=colors[i % len(colors)], linewidth=1.5)
        
        ax.set_title("各门店月度客户满意度综合得分趋势", fontsize=14)
        ax.set_xlabel("月份")
        ax.set_ylabel("满意度得分")
        ax.legend(loc='best', bbox_to_anchor=(1.05, 1), ncol=1)
        ax.grid(True, linestyle='--', alpha=0.7)
        
        # 保存图片
        plt.savefig(output_fig, dpi=300, bbox_inches='tight')
        plt.close(fig)
        
        print(f"图表已保存至: {output_fig}")
        
    except Exception as e:
        # 打印异常信息，让程序非正常退出
        print(f"错误发生: {e}")
        raise
    
    # 写入 manifest.json
    outputs = [output_fig]
    manifest = {"files": outputs}
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    
    print(f"清单已写入: {manifest_path}")

if __name__ == "__main__":
    main()