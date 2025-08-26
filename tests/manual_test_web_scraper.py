
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from utils.web_scraper import search_donor_articles, RAW_INPUTS_DIR

 
results = search_donor_articles(
    donor_name="Swidesh Government",
    region="Sub-Saharan Africa",
    theme="Health Systems",
    max_results=3
)

print("\n📂 Check saved files in:", RAW_INPUTS_DIR)
print("📄 Existing files:")
for f in os.listdir(RAW_INPUTS_DIR):
    print(" -", f)

print("🧪 Manual Test Results:")
for idx, r in enumerate(results, 1):
    print(f"\nResult {idx}:")
    print(f"Title: {r['title']}")
    print(f"URL: {r['url']}")
    print(f"Date: {r['date']}")
    print(f"Preview:\n{r['body'][:300]}...")

