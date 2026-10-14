# FR-2 修复后复跑比对报告（对照 fr2_ground_truth.md 基线）

- 核验人：调查员 Vera（只读，未改动 `mmagent/`、`tests/`、`artifacts/`）
- 时间：2026-10-13（第二段；**含队长澄清后的改判，见 §0 与 §2.1 的「改判说明」**）
- 被核产物：`H:\mm-agent\artifacts\intake\`（**规范目录，20:09 重新生成**，含 `value_counts` / `skipped_files.json`）——注：我首轮误核了较早的 `intake_v5`（20:03，无 value_counts），已按队长澄清改用规范目录重跑
- 复跑脚本：`fr2_reverify.py`（输出 `fr2_reverify_dump.txt`）；FR-9 回归用 `fr9_attack.py`（输出 `fr9_attack_regression_dump.txt`）
- 基线：`fr2_ground_truth.md`（我独立按门店 id 算出的真值）
- 独立交叉验证：脚本**重新用 xlrd 读源 `.xls`**，不依赖产物自证

---

## 0. 结论速览

| 核验点 | 判定 | 一句话 |
|---|---|---|
| 1 附件2/3 12月评级列召回 | **通过（改判）** | 字段 `12月.评级` 已召回；`value_counts` = 附件2{合格:144,不合格:13} / 附件3{合格:154,不合格:3}，与我的独立真值**逐计数一致** |
| 2 附件6 37 字段逐列对齐 | **通过** | 37 字段命名与列区间还原一致；36 个指标字段 `source_column` + samples 全部匹配 → **无错位最终确认** |
| 3 附件1 有效行 + unmapped N 列 | **通过** | phys/eff/blank=160/157/3；unmapped 含 att1 N（derived=true, header='') |
| 4 dirty_cells 17 处 | **通过** | 附件3 恰好 17 处 '97.3.4'（T7/V5/X5），源文件原值未被改写 |
| 5 附件4/5 blank_as_zero | **部分通过（附件5 有残留）** | att4 全 12 列 missing_rate=0 ✓；**att5 的 1月/4月（整列全空）missing_rate 仍 1.0、blank_as_zero=false** |
| 6 冲突覆盖三类真冲突 | **通过（附命名备注）** | C002/C004/C006 三类全覆盖；键名用 `field_base`/`tables` 而非 DoD 的 `field`/`table_id` |
| 7 FR-9 `INSERT OR REPLACE` 回归 | **通过（已封堵）** | 打穿项 6a 现被 `RAISE(ABORT,'adopted immutable')`；正常写入零回归 |

**一句话**：A/B/F/G/H 修复项**基本全部达成**（含队长澄清后改判的「12月评级计数」通过）；唯一残留是「附件5 整列全空列未套用空格=0 口径」，属边界 case。**最终 6.5 / 7 通过。**

---

## 1. 输入清单与表级行数（独立复核）

```
表(sheet)数=8  字段数=153  入账 source_file 去重 = 7 ✓
```

| table_id | 文件 | sheet | phys | 有效 | 空 | 字段 | missing_sum / 备注 |
|---|---|---|---|---|---|---|---|
| summary_master | 总表.xlsx | A题 (2) | 942 | 942 | 0 | 14 | 6 指标×157 门店 |
| summary_master | 总表.xlsx | A题 | 331 | 157 | 174 | 13 | — |
| att1_satisfaction | 附件1 | sheet1 | **160** | **157** | **3** | 13 | 与基线一致 |
| att2_service | 附件2 | Sheet1 | 158 | 157 | 1 | **25** | 13 得分+12 评级 |
| att3_clean | 附件3 | Sheet1 | 157 | 157 | 0 | **25** | — |
| att4_complaint | 附件4 | Sheet1 | 157 | 157 | 0 | 13 | blank_as_zero=True，Σ=**432** ✓ |
| att5_problem | 附件5 | Sheet1 | 157 | 157 | 0 | 13 | blank_as_zero=True，Σ=**131** ✓ |
| att6_profit | 附件6 | Sheet1 | 157 | 157 | 0 | **37** | Σ=27289+31344=**58633** ✓ |

- `0a` 入账文件恰好 7 个（两个预处理 xlsx 已按白名单排除，`intake_summary.md` 明示「输入白名单跳过：2 个文件」）→ **证成**。
- att4/att5/att6 独立重算的年度/全表数值合计 = 432 / 131 / 58633，与产物 `numeric_sum` 一致 → 与基线吻合。

---

## 2. 逐条核验

### 2.1 附件2/3/6 的 12 月评级列召回（核验点 1）——**通过（改判）**

```
att2_service.12月.评级 (source_column=Y,  field_raw=''): value_counts={'合格':144,'不合格':13}  独立重算一致 ✓
att3_clean.12月.评级   (source_column=Y,  field_raw=''): value_counts={'合格':154,'不合格':3}   独立重算一致 ✓
att6_profit.12月.评级  (source_column=AK, field_raw=''): value_counts={'合格':156,'不合格':1}   独立重算一致 ✓  (156+1=157)
```

| 字段 | source_column | 产物 value_counts | 我的独立真值 | 一致 |
|---|---|---|---|---|
| att2_service.12月.评级 | Y (col24) | {合格:144, 不合格:13} | {合格:144, 不合格:13} | ✓ |
| att3_clean.12月.评级 | Y (col24) | {合格:154, 不合格:3} | {合格:154, 不合格:3} | ✓ |
| att6_profit.12月.评级 | AK (col36) | {合格:156, 不合格:1} | {合格:156, 不合格:1} | ✓ |

- **这是「末列召回的是正确那一列」最硬的独立确认**：两条完全不同的计算路径 —— 我用 pandas/xlrd **直读原始 `.xls` 逐格计数** vs 实现方 intake 产物 —— 在同一组计数上**逐计数对上**（含附件6 末列 col36，即我此前判定"整列丢失"的那一列）。
- **改判说明（重要）**：首轮我判「计数不可核」，原因是查错了字段键——产物 `value_counts` 挂在 field 的 **`value_counts`** 键上（语义名在 **`field_name`**，而该列 `field_raw` 为空串）。我首轮只看了 `intake_v5`（20:03，尚不含该键）。经队长澄清后改用规范目录 `artifacts/intake`（20:09）并**同时独立重算**，判定**改判为通过**。
- 附件6 各月评级 value_counts 亦齐（每列合计=157），可作回归基线：

| 月 | 合格 | 不合格 | 月 | 合格 | 不合格 |
|---|---|---|---|---|---|
| 1月 | 157 | 0 | 7月 | 157 | 0 |
| 2月 | 157 | 0 | 8月 | 157 | 0 |
| 3月 | 134 | 23 | 9月 | 151 | 6 |
| 4月 | 154 | 3 | 10月 | 157 | 0 |
| 5月 | 150 | 7 | 11月 | 147 | 10 |
| 6月 | 152 | 5 | 12月 | 156 | 1 |

（注：1/2/7/8/10 月全为「合格」→ 与我此前发现的评级重复列组 `qual_1_2_7_8_10` 互相印证。）

> 附：`sample_values` 的语义是「按出现顺序的前 ≤5 个 distinct 值」，**不是**前 5 行、也不是频次（证据：`3月.评级` 只有 2 个值）。比对时勿混。

### 2.2 附件6 37 字段逐列对齐（核验点 2）

- `att6_profit` 字段数 = 37 ✓；dtype 构成 = `str13 / int12 / float12` ✓（门店1+评级12=str，定额12=int，实际利润12=float）。
- **逐列对齐（36 个指标字段全部逐一核）**：

| 种类 | 期望 source_column（0-based→字母） | 结果 |
|---|---|---|
| m 月.利润定额 | col(3m-2) → 第 m 组第 1 列 | 全部匹配 ✓ |
| m 月.实际利润 | col(3m-1) → 第 m 组第 2 列 | 全部匹配 ✓ |
| m 月.评级 | col(3m) → 第 m 组第 3 列 | 全部匹配 ✓ |

- 每个字段的 `sample_values` 与我从源 `.xls` **独立重读该列**得到的前 5 个 distinct 值**逐值相等** → **「没有错位」的最终确认**（不是只看列号，而是取值签名匹配）。
- 合计：`numeric_sum` 各月求和 = **58633**，与独立重算（定额 27289 + 实际 31344）**完全一致**。
- 命名/口径（备注）：产物 `table_id = att6_profit`（与文档 `docx_dump` 中的 `att6_profit` 写法一致）；示例字段 `1月.利润定额` 的 `entity_scope='门店'`、`period='M01'`、`unit='万元'`、`canonical='profit_quota_m01'`。全表 153 字段的 `entity_scope` 分布：`门店`×126、空×27。
- 判定：**通过**。

### 2.3 附件1 有效门店行 & unmapped N 列（核验点 3）

```
3a: att1 phys/eff/blank = 160/157/3（期望 160/157/3）✓
3b: unmapped 含 att1 N：derived=True, header_raw='', non_empty=157, header=''
    note='派生列（= 该门店各月列算术均值，与源文件既有列一致）', canonical_hint='satisfaction_overall'
```

- **通过**。另：总表 A题 的 N 列（表头 '平均'）也被登记为派生列，共 2 条 unmapped，与 `intake_summary.md` 一致。
- （队长澄清项：附件6 各月 `N月.评级` 的 `value_counts` 亦齐备，已在 §2.1 列出；本次「第 3 项」即指该组计数，已改判为通过。）

### 2.4 dirty_cells 17 处（核验点 4）

```
4a: 附件3 dirty=17  列分布={'T':7,'V':5,'X':5}  value 均 '97.3.4'=True ✓
    （T=10月.得分, V=11月.得分, X=12月.得分）
4b: 源 .xls 原值仍为 '97.3.4'（未被自动改写）：True ✓
```

- **通过**。7+5+5=17，坐标与原值均留存，且源文件未被改写（符合队长「只出清单、不自动改」的定线）。

### 2.5 附件4/5 blank_as_zero 与 missing_rate（核验点 5）

```
att4_complaint: blank_as_zero=True  fillna_rule=0  12 个月份列 missing_rate>0 的列=[]            → 通过 ✓
att5_problem:   blank_as_zero=True  fillna_rule=0  12 个月份列 missing_rate>0 的列=[('1月',1.0,False),
                                                        ('4月',1.0,False)]                        → 部分不通过
```

- 附件4：**通过**（空格=0 生效，月份列 missing_rate 全为 0）。
- 附件5：**部分不通过**——**1月、4月是整列全空**（`raw_missing_rate=1.0`），产物对这两列 **`blank_as_zero=False`、`missing_rate` 仍为 1.0**。
  - 按表标题「空格处为 0」语义，整列全空 = 该月所有门店均为 0，`missing_rate` 应记 0；
  - 现状后果：`intake_summary.md` 的「缺失率 Top10」**仍被 att5 的 1月/4月 以 100% 占据前两名**（该 Top10 用的是 raw_missing_rate），会误导下游；
  - 数值上不影响 `numeric_sum`（1月/4月 本就全 0，合计仍 131 ✓），属**报表口径不一致**的边界缺陷。
  - 建议：`blank_as_zero` 判定不要以「该列是否已有非空值」为前提；或 Top10 改用 `missing_rate`（post-fillna）排序。

### 2.6 口径冲突覆盖三类真冲突（核验点 6）

产物 5 条：`C002 / C003 / C004 / C006 / C007`（`count=5`）。

| 我要求的真冲突 | 对应条目 | 覆盖 |
|---|---|---|
| 评级三套定义（同名不同义） | **C006**（definition，评级，tables=att2/att3/att6） | ✓ |
| 得分 dtype float/str | **C004**（definition，得分，含 17 处文本混入证据） | ✓ |
| N月 同名不同义 | **C002**（definition，N月，tables=att1/att4/att5/summary） | ✓ |

- **通过（三类真冲突全覆盖）**。另 C003（N月 单位不一致：无单位/个数/分/次数）、C007（评级 单位不一致：万元/分）为附加的 unit 类冲突。
- **备注（命名偏差）**：文档 FR-2 DoD 要求每条含 `conflict_id / table_id / field / conflict_type / evidence_rows[] / suggested_caliber`；产物用的是 **`field_base`（而非 `field`）** 与 **`tables[]`（而非单数 `table_id`）**。`conflict_id/conflict_type/evidence_rows/suggested_caliber` 齐全。
  - 由于这些冲突本就**跨多表**，用 `tables[]` 比单数 `table_id` 更合理 → 属**有意设计偏差**，建议与 Max 确认是否需为对齐 DoD 而**同时**输出 `field`/`table_id` 兼容键，避免严格断言失败。

### 2.7 FR-9 `INSERT OR REPLACE` 回归（核验点 7）

Max 已落地：`schema.sql` 新增 `trg_profile_adopted_guard_insert` + `db.py::connect()` 加 `PRAGMA recursive_triggers=ON`。用我的 `fr9_attack.py` 原样复跑：

```
6a 证成  INSERT OR REPLACE 被 ABORT：adopted immutable      ← 原打穿项，现已封堵 ✓
6b 证成  开启 recursive_triggers 后被 ABORT：adopted immutable ✓
6c 限制  原生 INSERT 一条新的 adopted 行仍未拦（append-only 已知限制）
6d 证成  候选修复后 INSERT OR REPLACE 被 ABORT（修复有效、不依赖 pragma）

回归总览：打穿/失败项 = []；1a–1e, 1f, 1g, 2a–2c, 3a–3c, 4a–4c, 5a/5b/5d, 6a/6b/6d 全绿
```

- **打穿项已封堵**：`INSERT OR REPLACE` 现在被 `RAISE(ABORT,'adopted immutable')` 拦下。✓
- **正常写入零误伤**：8 次 `write_version`（id 1..8、draft=5）、6 进程并发（30 个 id 无重复、1..30 连续）、`adopt_new_version`、JSON 往返——全部通过 → 守卫触发器**未误伤正常路径**。✓
- 6c（原生 INSERT 伪造新 adopted 行）**仍为已知限制**，非本轮修复目标，建议文档声明威胁模型边界。

---

## 3. 未通过 / 存疑项汇总

| 项 | 判定 | 影响 | 建议（供队长/Max 决策） |
|---|---|---|---|
| 附件5 的 1月/4月 整列全空未套用空格=0 | **部分不通过（唯一残留）** | missing_rate 仍 1.0、`blank_as_zero=false`；污染 Top10；不影响数值合计 | 让 `blank_as_zero` 对整列全空也为 true（或 Top10 改用 post-fillna `missing_rate` 排序） |
| 12月评级计数 | ~~不可核~~ → **通过（改判）** | 无 | 已用 `value_counts`（挂 `field_name`）核对，三处计数与独立真值一致 |
| 冲突键名 `field_base`/`tables` vs DoD `field`/`table_id` | **备注（设计偏差）** | 严格键断言可能失败 | 确认是否需兼容键 |

**A/B/F/G/H 修复项均通过**：7 文件白名单、25/25/37 字段召回、附件6 逐列无错位、附件1 157 行、附件4/5 空格=0（附件5 边界除外）、dirty_cells 17 处、三类真冲突覆盖、12月评级计数三处一致、FR-9 REPLACE 封堵。

**最终结论**：**6.5 / 7 通过** —— 唯一未通过项是「附件5 的 1月/4月 整列全空未套用空格=0」（边界 case，已在主报告点名、队长已派 Max 修）；其余全部通过。

---

## 4. 复跑说明

```bash
PY="C:/Users/Lenovo/.workbuddy/binaries/python/versions/3.13.12/python.exe"
cd "H:/mm-agent/.agent-runs/2026-10-13/task-001"
"$PY" fr2_reverify.py     # → fr2_reverify_dump.txt（本报告数据来源）
"$PY" fr9_attack.py       # → fr9_attack_regression_dump.txt（FR-9 回归，隔离库自动建/删）
```

- 脚本只读产物 + 只读源 `.xls`，不写 `mmagent/`、`tests/`、`artifacts/`。
- 产物目录 = `artifacts/intake`（规范路径）；脚本内路径已指向此处。
- 字段键提示：**语义名在 `field_name`**，`field_raw` 对「原表无表头的列」（如各末列评级）为空串；**`value_counts`、`sample_values`、`source_column` 均挂在 field 上**。查字段/计数请用 `field_name`。
- `sample_values` 语义是「前 ≤5 个 distinct 值」，**不是**前 5 行、也不是频次——比对时勿混。
