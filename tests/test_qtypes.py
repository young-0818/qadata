"""题型标签器测试：表驱动钉死六分类行为（题面取自 50 题固定集原文）。

趋势/排名在 50 题集无真实样本，用 BIRD 风格合成样例补齐（注释注明）。
"""
from qadata.eval.qtypes import label_question_type

# (题面, evidence, 期望标签)
CASES = [
    # 对比：百分比/比值/偏差语义
    ("How much of the hydrogen in molecule TR206 is accounted for? Please provide your answer as a percentage with four decimal places.",
     "hydrogen refers to element = 'h'; TR206 is the molecule id",
     "对比"),
    ("What is the ratio in percentage of Santa Clara County schools that are locally funded compared to all other types of charter school funding?",
     "",
     "对比"),
    ("Are there more in-patient or outpatient who were male? What is the deviation in percentage?",
     "",
     "对比"),
    # 极值：单一最值问题（题面含 average 也判极值——这类题 gold 是 ORDER BY LIMIT 1 形态）
    ("Please list the name of the cards in the set Coldsnap with the highest converted mana cost.",
     "card set Coldsnap refers to name = 'Coldsnap'",
     "极值"),
    ("List down most tallest players' name.", "", "极值"),
    ("What is the administrator's email address for the school with the highest number of test takers who received SAT scores of at least 1500?Provide the name of the school.",
     "", "极值"),
    ("In which mailing street address can you find the school that has the lowest average score in reading? Also give the school's name.",
     "", "极值"),
    # 分布：计数/均值形态
    ("Count the number of posts with a tag specified as 'careers'.",
     "tag specified as 'careers' refers to TagName = 'careers'",
     "分布"),
    ("On average how many carcinogenic molecules are single bonded?",
     "carcinogenic molecules refers to label = '+'",
     "分布"),
    ("How many superheroes with blonde hair are there?",
     "superheroes with blonde hair refers to colour = 'Blond'",
     "分布"),
    # 排名：top N
    ("Please list the phone numbers of the schools with the top 3 SAT excellence rate.",
     "Excellence rate = NumGE1500 / NumTstTakr",
     "排名"),
    # 明细：无信号兜底；evidence 词不得误伤边界（"first_name" 不是排名、"cell count" 不是分布）
    ("Which student was able to generate income more than $40?",
     "name of students means the full name; full name refers to first_name, last_name; generate income more than $50 refers to income.amount > 40",
     "明细"),
    ("Write the full name of the club member with the position of 'Secretary' and list which college the club member belongs to.",
     "full name refers to first_name, last name",
     "明细"),
    ("For all the female patient age 50 and above, who has abnormal red blood cell count. State if they were admitted to hospital.",
     "female patient refers to Sex = 'F'; abnormal red blood cell count refers to RBC < = 3.5 or RBC > = 6.0",
     "明细"),
    ("Please specify all of the schools and their related mailing zip codes that are under Avetik Atoian's administration.",
     "", "明细"),
]

# 合成样例：50 题集无趋势类，趋势两例 + 排名补一例（BIRD 风格措辞）
SYNTHETIC = [
    ("What is the trend of the annual number of new members each year?", "", "趋势"),
    ("What is the increase of monthly sales of the store by year?", "", "趋势"),
    ("List the ranking of shops by sales, and show the second highest shop.", "", "排名"),
]


def test_label_question_type_cases():
    for question, evidence, expected in CASES + SYNTHETIC:
        assert label_question_type(question, evidence) == expected, question[:60]


def test_evidence_fallback_only_when_question_detail():
    # 题面无信号时才复扫 evidence（明细兜底后叠加）
    assert label_question_type("How was it?", "sales trend per year") == "趋势"
    # 题面已有信号则 evidence 不参与——注释词不得污染题面语义
    assert label_question_type("How many were sold?", "trend per year") == "分布"