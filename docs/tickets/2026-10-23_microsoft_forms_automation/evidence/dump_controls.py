import subprocess
import json
import sys

tab_id = "F7B26F7BC7838C13CA35CE563F713950"

all_controls = []
cursor = None

while True:
    cmd = ["omni", "--observe", "--tab", tab_id]
    if cursor:
        cmd.extend(["--cursor", cursor])
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("Error:", res.stderr)
        break
    data = json.loads(res.stdout)
    controls = data.get("controls", [])
    all_controls.extend(controls)
    print(f"Fetched {len(controls)} controls. Total: {len(all_controls)}")
    if data.get("has_more") and data.get("next_cursor"):
        cursor = data.get("next_cursor")
    else:
        break

with open("controls_dump.json", "w") as f:
    json.dump(all_controls, f, indent=2)

print(f"Done. Collected {len(all_controls)} controls across all cursor pages.")
