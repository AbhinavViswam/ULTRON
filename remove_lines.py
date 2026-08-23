def remove_lines(filename, ranges_to_remove):
    with open(filename, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    # lines is 0-indexed. 
    # ranges are 1-indexed inclusive
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

# (1143, 1173) for find_files (includes a blank line)
# (1341, 1373) for list_directory
# (1374, 1427) for open_folder
ranges = [
    (1143, 1173),
    (1341, 1373),
    (1374, 1427)
]

remove_lines('ultron/automation.py', ranges)
print("Removed functions from automation.py")
