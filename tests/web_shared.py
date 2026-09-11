"""票 02.5 web 测试共享夹具数据（评审收紧：两份逐字重复的注册表样本合一）。"""

# 一条合法的最小指标注册表（六要素齐，time_slot 与模板占位符对账通过）
ONE_METRIC_YAML = """\
metrics:
  - name: loan_count
    display_name: 贷款笔数
    meaning: 统计期内银行批准的贷款合同数量
    definition: 以贷款批准日期（loan.date）落入时间范围计条；一笔合同计 1
    sql_template: SELECT COUNT(loan.loan_id) FROM loan WHERE loan.date BETWEEN {time_start} AND {time_end}
    aliases: [贷款合同数]
    time_slot: {column: loan.date, date_format: YYYY-MM-DD}
    available_dimensions: {}
    source_tables: [loan]
"""
