def remove_lines(filename, ranges_to_remove):
    with open(filename, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    keep_lines = []
    for i, line in enumerate(lines):
        line_num = i + 1
        keep = True
        for start, end in ranges_to_remove:
            if start <= line_num <= end:
                keep = False
                break
        if keep:
            keep_lines.append(line)
            
    with open(filename, 'w', encoding='utf-8') as f:
        f.writelines(keep_lines)

ranges = [
    (119, 185)
]

remove_lines('ultron/plugins/explorer_plugin.py', ranges)
print("Removed functions from explorer_plugin.py")
