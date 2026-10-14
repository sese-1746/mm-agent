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
        print(f"Sheet 行数: {sh.nrows}, 列数: {sh.ncols}")
        print(f"第 0 行内容: {sh.row(0)}")
        print(f"第 1 行内容 (表头): {sh.row(1)}")
        
        # 根据 schema 定义获取列索引
        # 注意：xlrd 的 row() 返回 Cell 对象列表，需通过 .value 取值
        # 但 sh.cell_value(row, col) 直接返回数值/字符串，无需再取 .value
        header_row = 1
        
        # 定义列映射 (基于 schema 中的 raw 名称或 col 索引)
        # A=0, B=1, ... M=12
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
        
        # 验证表头是否存在
        for name, idx in col_map.items():
            if idx < sh.ncols:
                val = sh.cell_value(header_row, idx)
                print(f"列 {name} (idx={idx}) 值: {val}")
            else:
                raise ValueError(f"列索引 {idx} 超出范围")
        
        # 提取数据
        store_ids = []
        months_data = {}
        month_names = [f"{i+1}月" for i in range(12)]
        
        for row_idx in range(header_row + 1, sh.nrows):
            store_id_val = sh.cell_value(row_idx, col_map["store_id"])
            store_ids.append(store_id_val)
            
            row_data = {}
            for month_name, idx in zip(month_names, range(1, 13)):
                val = sh.cell_value(row_idx, col_map[f"satisfaction_overall_m{idx:02d}"])
                # xlrd cell_value 返回 float 或 str，直接赋值
                row_data[month_name] = val
            
            months_data[store_id_val] = row_data
        
        # 绘图
        plt.figure(figsize=(14, 8))
        colors = plt.cm.viridis(range(len(store_ids)))
        
        for i, store_id in enumerate(store_ids):
            data = months_data[store_id]
            x = list(range(1, 13))
            y = [data[f"{m}"] for m in month_names]
            plt.plot(x, y, label=store_id, color=colors[i], linewidth=2)
        
        plt.title("各门店月度客户满意度综合得分趋势")
        plt.xlabel("月份")
        plt.ylabel("满意度得分")
        plt.legend(loc='best')
        plt.grid(True, linestyle='--', alpha=0.7)
        
        # 保存图片
        plt.savefig(output_fig, dpi=300)
        plt.close()
        
        print(f"图表已保存至: {output_fig}")
        
    except Exception as e:
        # 打印异常信息，让程序非正常退出 (exit code != 0)
        print(f"发生错误: {e}")
        raise
    
    # 写入 manifest.json
    output_list = [output_fig]
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump({"files": output_list}, f, ensure_ascii=False)
    
    print(f"清单已写入: {manifest_path}")

if __name__ == "__main__":
    main()