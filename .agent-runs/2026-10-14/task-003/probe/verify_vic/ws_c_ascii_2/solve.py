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
    input_file = "data/att1.xls"
    output_fig = "figures/satisfaction_line.png"
    manifest_path = "manifest.json"
    
    # 确保输出目录存在
    Path("figures").mkdir(parents=True, exist_ok=True)
    
    try:
        # 使用 xlrd 打开.xls 文件
        wb = xlrd.open_workbook(input_file)
        sh = wb.sheet_by_index(0)
        
        # 检查表头行 (header_row=1)，第 0 行通常是合并大标题
        header_row_idx = 1
        nrows = sh.nrows
        ncols = sh.ncols
        
        # 打印调试信息以便定位问题
        print(f"Sheet 行数: {nrows}, 列数: {ncols}")
        print(f"第 0 行内容: {[sh.cell_value(0, i) for i in range(ncols)]}")
        print(f"第 1 行 (表头) 内容: {[sh.cell_value(header_row_idx, i) for i in range(ncols)]}")
        
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
        store_ids = [sh.cell_value(r, 0) for r in range(header_row_idx + 1, nrows)]
        satisfaction_cols = expected_headers[1:]
        data = {}
        
        for col_name in satisfaction_cols:
            col_idx = actual_headers.index(col_name)
            data[col_name] = [float(sh.cell_value(r, col_idx)) for r in range(header_row_idx + 1, nrows)]
            
        # 绘图
        plt.figure(figsize=(12, 8))
        colors = plt.cm.viridis(range(len(satisfaction_cols)))
        
        for i, col_name in enumerate(satisfaction_cols):
            plt.plot(store_ids, data[col_name], label=col_name.replace('satisfaction_overall_', ''), color=colors[i])
            
        plt.title("各门店月度客户满意度综合得分变化趋势")
        plt.xlabel("月份门店")
        plt.ylabel("满意度得分")
        plt.legend(loc='best')
        plt.grid(True, linestyle='--', alpha=0.7)
        
        # 保存图片
        plt.savefig(output_fig, dpi=300)
        plt.close()
        
        # 写入 manifest.json
        output_list = [output_fig]
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump({"files": output_list}, f, ensure_ascii=False)
            
        print(f"图表已保存至: {output_fig}")
        print("Manifest 已更新")
        
    except Exception as e:
        # 打印异常信息，让程序非正常退出
        print(f"发生错误: {e}")
        raise

if __name__ == "__main__":
    main()