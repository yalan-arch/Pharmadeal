import requests
from bs4 import BeautifulSoup
import json
import os
from datetime import datetime

# ==========================================
# 配置区域：你可以根据需要添加更多资讯源
# ==========================================
SOURCES = [
    {
        "name": "医药魔方资讯",
        "url": "https://www.pharmcube.com/news", # 示例地址
        "selector": ".news-item" # 示例选择器
    }
]

def get_news():
    news_list = []
    
    # 这里是一个通用的模拟抓取逻辑
    # 实际应用中，由于各家网站反爬不同，我们通常抓取一些开放的 RSS 或 行业快讯
    print(f"开始抓取资讯... 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 模拟抓取到的数据结构（你可以根据实际爬虫逻辑替换此处）
    # 如果你有特定的网站想爬，可以把网址发给我，我再为你精修
    mock_data = [
        {
            "title": "全球医疗健康融资周报：多家生物技术公司完成超亿元融资",
            "link": "https://example.com/news1",
            "date": datetime.now().strftime('%Y-%m-%d'),
            "summary": "本周全球医疗健康领域共发生多起融资事件，涉及肿瘤免疫、基因治疗等多个细分领域..."
        },
        {
            "title": "某大药企完成对创新药企业的10亿美元BD授权合作",
            "link": "https://example.com/news2",
            "date": datetime.now().strftime('%Y-%m-%d'),
            "summary": "该交易涵盖了临床前候选药物的全球开发和商业化权益，标志着双方在心血管领域的深耕..."
        },
        {
            "title": "医疗器械巨头今日正式在纳斯达克挂牌IPO",
            "link": "https://example.com/news3",
            "date": datetime.now().strftime('%Y-%m-%d'),
            "summary": "首日表现强劲，募资将主要用于下一代手术机器人的研发和全球市场扩张。"
        }
    ]
    
    return mock_data

def update_json():
    file_path = 'data.json'
    
    # 1. 抓取新数据
    new_news = get_news()
    
    # 2. 读取旧数据（如果存在）
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                old_data = json.load(f)
            except:
                old_data = []
    else:
        old_data = []

    # 3. 合并并去重 (以标题为准)
    existing_titles = {item['title'] for item in old_data}
    added_count = 0
    
    for item in new_news:
        if item['title'] not in existing_titles:
            old_data.insert(0, item) # 新闻放在最前面
            added_count += 1
    
    # 4. 只保留最近的 50 条记录，防止文件过大
    final_data = old_data[:50]

    # 5. 保存回文件
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, ensure_ascii=False, indent=4)
    
    print(f"更新完成！新增了 {added_count} 条资讯，当前总计 {len(final_data)} 条。")

if __name__ == "__main__":
    update_json()
