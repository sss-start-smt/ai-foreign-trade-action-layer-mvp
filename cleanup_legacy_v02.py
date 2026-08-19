"""清理 app.js 中 D11 V0.2 的 legacy 重复函数定义。

V0.3 版本的 pageToday/pageOrders/pageConfirm/openCaseDrawer 等函数
定义在第 559 行之后，会覆盖前面 V0.2 的同名函数。
本脚本删除 V0.2 的旧版本（死代码），保留 V0.3 版本。
"""

import re
from pathlib import Path

APP_JS = Path(__file__).parent / "static" / "app.js"


def find_balanced_braces(text: str, start_pos: int) -> int:
    """从 start_pos 开始找到平衡的闭合大括号位置。"""
    depth = 0
    i = start_pos
    while i < len(text):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def remove_function_block(lines: list[str], start_line: int, func_name: str) -> tuple[list[str], int]:
    """删除指定行号开始的函数块。返回（新行列表, 删除的行数）。"""
    text = '\n'.join(lines)
    # 找到函数定义的起始位置
    pattern = rf'(async\s+)?function\s+{re.escape(func_name)}\s*\('
    match = re.search(pattern, text[start_line * 200:])  # 粗略搜索范围
    if not match:
        # 精确在行号附近查找
        search_start = max(0, start_line - 5)
        search_text = '\n'.join(lines[search_start:])
        match = re.search(pattern, search_text)
        if not match:
            print(f"  警告: 找不到函数 {func_name}")
            return lines, 0
        # 转换回全文位置
        abs_start = search_start * 200 + match.start()
    else:
        abs_start = start_line * 200 + match.start()

    # 找到第一个 {
    brace_start = text.find('{', abs_start)
    if brace_start == -1:
        print(f"  警告: 找不到函数体的开始 brace")
        return lines, 0

    # 找到平衡的 }
    brace_end = find_balanced_braces(text, brace_start)
    if brace_end == -1:
        print(f"  警告: 找不到函数体的闭合 brace")
        return lines, 0

    # 找到要删除的行范围
    # 找到 brace_start 所在的行
    text_before = text[:brace_start]
    start_line_idx = text_before.count('\n')
    # 找到 brace_end 后面的换行
    text_end = text[brace_end + 1:]
    end_line_idx = start_line_idx + text_end.count('\n') + 1  # 包含到下一行

    print(f"  删除函数 {func_name}: 行 {start_line_idx + 1} - {end_line_idx}")

    # 删除这些行
    new_lines = lines[:start_line_idx] + lines[end_line_idx:]
    removed = len(lines) - len(new_lines)
    return new_lines, removed


def main():
    print(f"读取 {APP_JS}")
    lines = APP_JS.read_text(encoding='utf-8').split('\n')
    original_count = len(lines)
    print(f"原始行数: {original_count}")

    # 需要删除的 V0.2 legacy 函数（按行号倒序删除，避免行号偏移）
    # 格式: (行号, 函数名)
    legacy_functions = [
        # 第 416 行: 旧版 pageConfirm
        (416, 'pageConfirm'),
        # 第 388 行: 旧版 pageOrders
        (388, 'pageOrders'),
        # 第 378 行附近: 旧版 openD11ReplyModal 等辅助函数
        # 这些是 openCaseDrawer 的依赖，在旧版中只有一份
        # 但 V0.3 版本在第 712-714 行有同名新版本
        # 第 367-377 行: 旧版 bindD11DrawerActions + d11SimpleTaskAction + 3个modal函数
        (375, 'openD11ReplyModal'),
        (374, 'd11SimpleTaskAction'),
        (373, ''),  # 跳过这行
        (372, ''),
        (371, ''),
        (370, ''),
        (369, ''),
        (368, 'bindD11DrawerActions'),
        # 第 358 行: 旧版 openCaseDrawer
        (358, 'openCaseDrawer'),
        # 第 329 行: 旧版 pageToday
        (329, 'pageToday'),
    ]

    # 由于函数嵌套，需要更智能的方式
    # 直接按行号范围删除

    # 先确定需要保留的内容边界
    # 保留: 1-328行, 349-357行(pageTasks), 557行之后
    # 删除: 329-347(旧版pageToday), 358-377(旧版openCaseDrawer等), 388-392(旧版pageOrders), 416-426(旧版pageConfirm)

    # 更简单的方法：标记删除区域
    delete_ranges = [
        (328, 346),   # 旧版 pageToday (行329-347, 0-indexed: 328-346)
        (357, 376),   # 旧版 openCaseDrawer + bindD11DrawerActions + 3个modal (行358-377, 0-indexed: 357-376)
        (387, 391),   # 旧版 pageOrders (行388-392, 0-indexed: 387-391)
        (415, 425),   # 旧版 pageConfirm (行416-426, 0-indexed: 415-425)
    ]

    # 构建新行列表
    new_lines = []
    for i, line in enumerate(lines):
        line_num = i  # 0-indexed
        should_delete = False
        for start, end in delete_ranges:
            if start <= line_num <= end:
                should_delete = True
                break
        if not should_delete:
            new_lines.append(line)

    removed = original_count - len(new_lines)
    print(f"删除行数: {removed}")
    print(f"新行数: {len(new_lines)}")

    # 验证关键函数存在
    text = '\n'.join(new_lines)
    checks = [
        ('V0.3 pageToday', r'async function pageToday\('),
        ('V0.3 pageOrders', r'async function pageOrders\('),
        ('V0.3 pageConfirm', r'async function pageConfirm\('),
        ('V0.3 openCaseDrawer', r'async function openCaseDrawer\('),
        ('pageTasks', r'async function pageTasks\('),
        ('pageRecap', r'async function pageRecap\('),
        ('pageAgent', r'async function pageAgent\('),
        ('init', r'async function init\('),
        ('d11v2SubmitInfo', r'async function d11v2SubmitInfo\('),
    ]

    print("\n验证关键函数存在性:")
    for name, pattern in checks:
        matches = re.findall(pattern, text)
        status = "✓" if len(matches) == 1 else f"⚠ (找到 {len(matches)} 个)"
        print(f"  {status} {name}")

    # 写入文件
    APP_JS.write_text('\n'.join(new_lines), encoding='utf-8')
    print(f"\n已写入 {APP_JS}")

    # 简单语法检查
    bracket_count = text.count('{') - text.count('}')
    print(f"括号平衡检查: {{ - }} = {bracket_count} (应为 0)")


if __name__ == '__main__':
    main()
