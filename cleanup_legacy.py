import re

with open('static/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove old pageToday (lines 329-347)
# It starts with "async function pageToday(root){" and ends with the closing "}"
# We need to be careful to only remove the FIRST occurrence (old version)
# Strategy: Find the first "async function pageToday" and remove it up to its closing brace

# Actually, let's use a simpler approach: remove specific line ranges
# But line numbers might change... Let's use content-based matching

# Find and remove old pageToday (first occurrence)
old_pageToday_start = content.find('async function pageToday(root){')
if old_pageToday_start != -1:
    # Find the second occurrence (new version)
    second_pageToday = content.find('async function pageToday(root){', old_pageToday_start + 1)
    if second_pageToday != -1:
        # Extract the old function: from start to just before the next function
        # Find where the next function starts (pageTasks)
        pageTasks_pos = content.find('async function pageTasks(root){', old_pageToday_start)
        if pageTasks_pos != -1 and pageTasks_pos < second_pageToday:
            # Remove from old pageToday to just before pageTasks
            # But we need to find the closing brace of old pageToday
            # Let's find the balanced braces
            brace_count = 0
            in_template = False
            i = old_pageToday_start
            while i < len(content):
                c = content[i]
                if c == '`':
                    in_template = not in_template
                elif not in_template:
                    if c == '{':
                        brace_count += 1
                    elif c == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            # Found the closing brace
                            end_pos = i + 1
                            # Remove from start to end_pos
                            content = content[:old_pageToday_start] + content[end_pos:]
                            print(f"Removed old pageToday (chars {old_pageToday_start}-{end_pos})")
                            break
                i += 1

# Find and remove old pageOrders
# First pageOrders is the old one (before pageOrderDetail)
# New pageOrders is at line ~690
first_pageOrders = content.find('async function pageOrders(root){')
if first_pageOrders != -1:
    # Find second occurrence
    second_pageOrders = content.find('async function pageOrders(root){', first_pageOrders + 1)
    if second_pageOrders != -1:
        # The old one is the first one
        # Find its closing brace
        brace_count = 0
        in_template = False
        i = first_pageOrders
        while i < len(content):
            c = content[i]
            if c == '`':
                in_template = not in_template
            elif not in_template:
                if c == '{':
                    brace_count += 1
                elif c == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = i + 1
                        content = content[:first_pageOrders] + content[end_pos:]
                        print(f"Removed old pageOrders (chars {first_pageOrders}-{end_pos})")
                        break
            i += 1

# Find and remove old pageConfirm
first_pageConfirm = content.find('async function pageConfirm(root){')
if first_pageConfirm != -1:
    second_pageConfirm = content.find('async function pageConfirm(root){', first_pageConfirm + 1)
    if second_pageConfirm != -1:
        brace_count = 0
        in_template = False
        i = first_pageConfirm
        while i < len(content):
            c = content[i]
            if c == '`':
                in_template = not in_template
            elif not in_template:
                if c == '{':
                    brace_count += 1
                elif c == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = i + 1
                        content = content[:first_pageConfirm] + content[end_pos:]
                        print(f"Removed old pageConfirm (chars {first_pageConfirm}-{end_pos})")
                        break
            i += 1

with open('static/app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done! Legacy functions removed.")