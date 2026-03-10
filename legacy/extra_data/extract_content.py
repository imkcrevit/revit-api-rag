import json
import argparse
import os
import re

def extract_sections(text, sections):
    """从文档文本中提取指定章节内容"""
    extracted = {}
    for section in sections:
        # 使用正则表达式匹配章节标题（不区分大小写）
        pattern = rf'({section}):\s*(.*?)(?=\n\w+:|$)' 
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            extracted[section.lower()] = match.group(2).strip()
    return extracted

def main():
    parser = argparse.ArgumentParser(description='提取JSON数据集中的execute_method和文档内容')
    parser.add_argument('--input', required=True, help='输入JSON文件路径')
    parser.add_argument('--output-dir', default='extracted_content', help='输出目录')
    parser.add_argument('--doc-sections', default='summary,description', help='要提取的文档章节，用逗号分隔')
    parser.add_argument('--doc-files', help='要提取的文档文件名，用逗号分隔（不指定则提取所有文档）')
    args = parser.parse_args()

    # 创建输出目录
    os.makedirs(args.output_dir, exist_ok=True)
    code_path = os.path.join(args.output_dir, 'all_codes.txt')
    doc_path = os.path.join(args.output_dir, 'all_docs.txt')

    # 初始化内容累积变量
    all_code = []
    all_docs = []
    unique_id = 1

    # 加载JSON数据
    with open(args.input, 'r', encoding='utf-8') as f:
        dataset = json.load(f)

    # 解析要提取的章节和文档文件
    target_sections = [s.strip().lower() for s in args.doc_sections.split(',')]
    target_files = [f.strip() for f in args.doc_files.split(',')] if args.doc_files else None

    for i, project in enumerate(dataset):
        project_name = f'project_{i:03d}'
        project_path = project.get('project_path', project_name)
        project_id = os.path.basename(project_path)

        # 提取execute_method
        key_code = project.get('key_code')
        if key_code and 'execute_method' in key_code:
            code = key_code['execute_method'].strip()
            if not code:
                continue
            # 添加唯一标识符
            code_identifier = f"=== CODE_BLOCK_{unique_id:04d} ===\n"
            all_code.append(code_identifier + code + '\n\n')

            unique_id += 1

            # 提取文档内容
            documentation = project.get('documentation', {})
            if not documentation:
                continue

            # 合并所有文档内容为单个块
            combined_docs = []
            # 筛选要提取的文档文件
            doc_files = target_files if target_files else documentation.keys()
            for doc_name in doc_files:
                if doc_name not in documentation:
                    continue

                doc_content = documentation[doc_name]
                # 提取指定章节
                extracted_sections = extract_sections(doc_content, target_sections)
                
                # 合并提取的章节
                for section, content in extracted_sections.items():
                    combined_docs.append(f"[{doc_name}][{section}]:\n{content}")

            if combined_docs:
                # 添加唯一标识符（与代码块使用相同ID）
                doc_identifier = f"=== DOC_BLOCK_{unique_id - 1:04d} ===\n"
                all_docs.append(doc_identifier + '\n\n'.join(combined_docs) + '\n\n')
                unique_id += 1

    # 写入合并后的文件
    with open(code_path, 'w', encoding='utf-8') as f:
        f.write(''.join(all_code))
    print(f'所有代码已合并到: {code_path}')

    with open(doc_path, 'w', encoding='utf-8') as f:
        f.write(''.join(all_docs))
    print(f'所有文档已合并到: {doc_path}')

if __name__ == '__main__':
    main()