# -*- coding: utf-8 -*-
import os
import subprocess
import io
import pandas as pd
from openai import OpenAI

# ========== 1. 配置硅基流动 API ==========
# 重要：不要把 key 写死在代码里！改成从环境变量读取
# 运行前先在终端执行：export SILICON_API_KEY="你的key"
SILICON_API_KEY = os.environ.get("SILICON_API_KEY")

if not SILICON_API_KEY:
    raise RuntimeError(
        "未找到环境变量 SILICON_API_KEY，请先执行：\n"
        "export SILICON_API_KEY='你的实际key'"
    )

client = OpenAI(
    api_key=SILICON_API_KEY,
    base_url="https://api.siliconflow.cn/v1"
)

# ========== 2. Hive 数据库名 ==========
HIVE_DATABASE = "drug_db"

# ========== 3. 表结构（给大模型看，让它知道有哪些表、哪些字段） ==========
TABLE_SCHEMA = '''
数据库 drug_db 中有以下表：

1. ods_drug（药品信息表）
   - DrugID INT（药品ID）
   - DrugName STRING（药品名称）
   - CategoryID INT（分类ID）
   - Manufacturer STRING（生产厂家）
   - UnitPrice DECIMAL（单价）
   - StockQuantity INT（库存量）
   - IsPrescription INT（是否处方药：1是0否）

2. ods_sales（销售记录表，分区表 dt）
   - SaleID INT（销售ID）
   - DrugID INT（药品ID）
   - SaleDate STRING（销售日期，格式 yyyy-MM-dd）
   - Quantity INT（销售数量）
   - UnitPrice DECIMAL（单价）
   - TotalAmount DECIMAL（总金额）
   - CustomerType STRING（客户类型：医院/零售/批发）
   - Region STRING（地区：深圳/广州/北京/上海/成都）

3. monthly_sales（月度销售汇总表）
   - month STRING（月份）
   - total_sales DECIMAL（总销售额）
   - sale_count INT（订单数）
   - avg_order_value DECIMAL（客单价）

4. drug_top10（药品销售TOP10）
   - drug_name STRING（药品名称）
   - total_quantity INT（总销量）
   - total_sales DECIMAL（总销售额）
   - rank INT（排名）

5. region_sales（地区销售统计）
   - region STRING（地区）
   - total_sales DECIMAL（总销售额）
   - sale_count INT（订单数）
   - avg_order_value DECIMAL（客单价）

6. customer_type_analysis（客户类型分析）
   - customer_type STRING（客户类型）
   - total_sales DECIMAL（总销售额）
   - sale_count INT（订单数）
   - avg_order_value DECIMAL（客单价）

注意：日期字段 SaleDate 格式是 yyyy-MM-dd，查询时用字符串比较即可。
'''


VALID_TABLES = [
    "ods_drug", "ods_sales", "monthly_sales",
    "drug_top10", "region_sales", "customer_type_analysis",
]

# 给模型看的示例：固定格式，减少小模型瞎编表名/漏逗号的概率
FEW_SHOT_EXAMPLES = '''
示例1
问题：查询销量最高的5种药品
SQL：SELECT drug_name, total_quantity, total_sales FROM drug_top10 ORDER BY total_quantity DESC LIMIT 5

示例2
问题：哪个地区销售额最高
SQL：SELECT region, total_sales FROM region_sales ORDER BY total_sales DESC LIMIT 1

示例3
问题：查一下阿莫西林的库存和单价
SQL：SELECT DrugName, StockQuantity, UnitPrice FROM ods_drug WHERE DrugName = '阿莫西林'
'''


def generate_sql(question: str) -> str:
    """调用大模型，把自然语言问题转换成 Hive SQL"""
    prompt = f"""你是 Hive SQL 专家。根据以下表结构，将用户问题转换为 Hive SQL 语句。

可以使用的表名（必须从这6个里选，不要编造其他表名）：
{", ".join(VALID_TABLES)}

表结构：
{TABLE_SCHEMA}

参考示例（注意每个列名之间用英文逗号分隔，不要给列加别名）：
{FEW_SHOT_EXAMPLES}

用户问题：{question}

要求：
1. 只输出一行 SQL 语句，不要输出任何解释或 markdown
2. 每个字段名之间必须用英文逗号 , 分隔
3. 不要给字段加别名（不要写 AS 或直接跟别名）
4. SQL 语句末尾不要加分号
5. 表名必须是上面列出的6个表之一，不要编造
6. 如果涉及时间范围，使用日期字符串比较

SQL：
"""
    response = client.chat.completions.create(
        model="Qwen/Qwen2.5-72B-Instruct",
        messages=[
            {"role": "system", "content": "你是 Hive SQL 专家，只输出 SQL 语句，不输出任何解释。"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.1
    )
    sql = response.choices[0].message.content.strip()
    # 去掉模型可能加的 markdown 代码块标记和多余分号
    sql = sql.replace("```sql", "").replace("```", "").strip()
    sql = sql.rstrip(";").strip()
    return sql


def validate_sql_tables(sql: str):
    """
    简单校验：检查 SQL 里出现的 FROM/JOIN 后面的表名是否都在合法表名单里。
    不是严谨的 SQL 解析，只做粗略拦截，避免把明显瞎编表名的 SQL 发给 Hive。
    """
    import re
    found_tables = re.findall(r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.IGNORECASE)
    invalid = [t for t in found_tables if t not in VALID_TABLES]
    if invalid:
        raise ValueError(
            f"生成的 SQL 引用了不存在的表名：{invalid}，"
            f"合法表名只有：{VALID_TABLES}。请重新提问或换个说法再试一次。"
        )


def execute_sql_via_cli(sql: str):
    """
    用 hive -e 在子进程里执行 SQL，不走 HiveServer2 / Thrift。
    -S：silent 模式，去掉 Hive 启动时的一堆 INFO 日志
    --hiveconf hive.cli.print.header=true：让结果带列名，方便解析成表格
    """
    full_sql = f"USE {HIVE_DATABASE}; {sql};"

    cmd = [
        "hive",
        "-S",
        "--hiveconf", "hive.cli.print.header=true",
        "-e", full_sql,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        # Hive 出错信息在 stderr 里，截取最后一部分给用户看，避免刷屏
        err_tail = result.stderr.strip().splitlines()[-15:]
        raise RuntimeError("Hive 执行出错：\n" + "\n".join(err_tail))

    output = result.stdout.strip()
    if not output:
        return pd.DataFrame()

    # hive -S 输出的是 \t 分隔的文本，第一行是表头
    df = pd.read_csv(io.StringIO(output), sep="\t")
    return df


def main():
    print("=" * 50)
    print("医药销售数仓智能问答系统（Text-to-SQL）")
    print("通过 hive -e 子进程方式查询，不依赖 HiveServer2")
    print("输入自然语言问题，系统自动生成 SQL 并查询")
    print("输入 'exit' 退出")
    print("=" * 50)

    while True:
        question = input("\n请提问：")
        if question.lower() == "exit":
            break

        try:
            print("\n正在生成 SQL...")
            sql = generate_sql(question)
            print(f"\n生成的 SQL：\n{sql}")

            validate_sql_tables(sql)

            print("\n正在查询数仓（hive -e 子进程）...")
            df = execute_sql_via_cli(sql)

            print("\n查询结果：")
            if df.empty:
                print("（无结果）")
            else:
                print(df.head(20).to_string(index=False))

        except subprocess.TimeoutExpired:
            print("查询超时（超过60秒），可能是数据量较大或集群较慢，可以适当调大 timeout")
        except Exception as e:
            print(f"出错：{e}")


if __name__ == "__main__":
    main()