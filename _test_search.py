import sys
sys.path.insert(0, ".")
from mk2.tools.web_tools import ddg_results

r = ddg_results("best laptop under 50000", max_results=3)
print(f"search: {len(r)} results")
for x in r:
    print(f"  {x['title'][:60]} -> {x['url'][:60]}")
