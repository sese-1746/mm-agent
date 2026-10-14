"""mmagent —— 数学建模竞赛协作 Agent（全本地、零云支出）。

依赖方向单向：api → agents → tools → pipeline → store → db。
store/ 与 checks/ 不得 import agents/ 或 api/。
"""

__version__ = "0.1.0"
