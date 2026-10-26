import subprocess
import json
import time

tab_id = "F7B26F7BC7838C13CA35CE563F713950"

def get_controls():
    cursor = None
    all_c = []
    while True:
        cmd = ["omni", "--observe", "--tab", tab_id]
        if cursor:
            cmd.extend(["--cursor", cursor])
        res = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(res.stdout)
        all_c.extend(data.get("controls", []))
        if data.get("has_more") and data.get("next_cursor"):
            cursor = data.get("next_cursor")
        else:
            break
    return all_c

def click_ref(ref):
    cmd = ["omni", "--act", "--tab", tab_id, "--ref", ref, "--action", "click"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode == 0

# Stepwise reordering:
# 1. Fetch current controls
# 2. Locate the dynamically mounted "Move option up" button
# 3. Click it and wait for DOM stabilization
# 4. Repeat until target position is reached
for step in range(4):
    controls = get_controls()
    up_btn = None
    for c in controls:
        if c.get("name") == "Move option up":
            up_btn = c.get("ref")
            break
    if not up_btn:
        print(f"Step {step}: 'Move option up' button not found!")
        break
    print(f"Step {step}: Clicking Move option up ({up_btn})")
    click_ref(up_btn)
    time.sleep(0.5)

print("Done moving option up.")
