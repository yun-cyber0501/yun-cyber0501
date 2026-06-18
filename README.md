# 医药销售大数据分析平台

基于自研药品管理系统（Java + SQL Server），使用 Hadoop/Hive 构建数据仓库，分析 100 万条销售记录；并在此基础上集成大模型，实现自然语言查询数仓的智能问数能力。

## 技术栈

- Hadoop 3.3.5
- Hive 3.1.3
- Sqoop 1.4.7
- SQL Server
- Java
- Python（Pandas / Matplotlib）
- LLM API（Qwen2.5-72B-Instruct，硅基流动）

## 数据规模

- 19 种药品
- 100 万条销售记录
- 时间跨度：2024-2026 年
- 地区：深圳、广州、北京、上海、成都

## 项目架构

```
药品管理系统（SQL Server）
        │  Sqoop 迁移
        ▼
      HDFS / Hive
        │  Hive SQL 分析
        ▼
   月度趋势 / 药品TOP10 / 地区分析 / 客户类型分析
        │
        ├─ Python Pandas + Matplotlib → 可视化图表
        │
        └─ LLM（Text-to-SQL） → 自然语言问数
```

---

## 项目一：药品管理系统（数据源）

基于 Java + SQL Server 实现的药品管理系统，支持药品的增删查改、库存管理、用户登录，为后续大数据分析提供真实业务数据源。

界面截图：见 `drug_system.png`

---

## 项目二：医药销售数仓分析

将药品管理系统的数据通过 Sqoop 迁移到 Hive，建立数仓表结构：

- `ods_drug` / `ods_sales`：原始数据层
- `monthly_sales` / `drug_top10` / `region_sales` / `customer_type_analysis`：分析结果层

编写 Hive SQL 完成月度趋势、药品销售排名、地区分析、客户类型分析四个维度的报表，并用 Python Pandas 清洗数据、Matplotlib 生成可视化图表（见 `*.png`）。

---

## 项目三：Text-to-SQL 智能问数助手（新增）

在数仓基础上集成大模型，用户可以用自然语言提问，系统自动生成 Hive SQL 并返回查询结果，无需手写 SQL。

### 使用方式

```bash
export SILICON_API_KEY="你的key"
python3 text_to_sql.py
```

```
请提问：查询销量最高的5种药品

生成的 SQL：
SELECT drug_name, total_quantity, total_sales FROM drug_top10 ORDER BY total_quantity DESC LIMIT 5

查询结果：
...
```

### 关键工程决策

**为什么不用 HiveServer2 + Thrift 连接？**

伪分布式环境下 HiveServer2 服务不稳定（内存吃紧、启动慢、容易掉线）。改用 `subprocess` 直接调用 `hive -e` 命令行执行 SQL，绕开了 HiveServer2 这个不稳定的中间服务，直接复用已验证稳定的 Hive CLI 路径。

**如何降低小模型生成 SQL 的出错率？**

早期使用 7B 参数模型时，生成的 SQL 经常出现漏逗号、表名幻觉等问题。两个改进：

1. 换用 72B 参数模型，代码/SQL 生成能力明显更强
2. Prompt 中加入 few-shot 示例 + 明确列出合法表名清单，并在执行前做表名白名单校验，提前拦截明显错误的 SQL，而不是等 Hive 报错才发现

### 数据安全说明

API Key 通过环境变量读取，不在代码中硬编码，避免泄露。

---

## 文件说明

| 文件 | 说明 |
|---|---|
| `text_to_sql.py` | Text-to-SQL 智能问数主程序 |
| `*.csv` | Hive 分析结果导出数据 |
| `*.png` | 数据可视化图表与系统截图 |
