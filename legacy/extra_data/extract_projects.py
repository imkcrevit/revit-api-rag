import os
import re
import json
from pathlib import Path
from bs4 import BeautifulSoup
from striprtf.striprtf import rtf_to_text

# 配置
EXCLUDE_DIRS = ['VB', 'VB.NET']
CS_EXTENSIONS = ['.cs']
DOC_EXTENSIONS = ['.htm', '.html', '.rtf', '.txt']
OUTPUT_FILE = 'project_dataset.json'

# 关键代码提取模式
IEXTERNALCOMMAND_PATTERN = re.compile(r'public class (\w+) : IExternalCommand', re.MULTILINE)
# 使用贪婪匹配模式捕获完整的Execute方法体, 包括嵌套结构、using语句和#region块
EXECUTE_METHOD_PATTERN = re.compile(r'public Autodesk\.Revit\.UI\.Result Execute\(.*?\)\s*\{[\s\S]*\}', re.DOTALL)


def is_cs_project_dir(path):
    """检查目录是否包含C#项目文件"""
    if any(exclude in path.lower() for exclude in EXCLUDE_DIRS):
        return False
    for ext in CS_EXTENSIONS:
        if any(file.endswith(ext) for file in os.listdir(path)):
            return True
    return False


def find_project_directories(root_dir):
    """查找所有C#项目目录"""
    project_dirs = []
    for dirpath, _, _ in os.walk(root_dir):
        if is_cs_project_dir(dirpath):
            project_dirs.append(dirpath)
    return project_dirs


def extract_key_code(cs_file_path):
    """从C#文件中提取关键代码"""
    try:
        with open(cs_file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 提取IExternalCommand实现类
        command_match = IEXTERNALCOMMAND_PATTERN.search(content)
        if not command_match:
            return None

        class_name = command_match.group(1)

        # 提取Execute方法
        execute_match = EXECUTE_METHOD_PATTERN.search(content)
        execute_code = execute_match.group() if execute_match else ''

        # 仅保留Execute方法代码
        full_code = execute_code

        return {
            'class_name': class_name,
            'execute_method': full_code
        }
    except Exception as e:
        print(f"提取代码时出错 {cs_file_path}: {e}")
        return None


def find_documentation_files(project_dir):
    """查找项目目录中的文档文件"""
    doc_files = []
    for ext in DOC_EXTENSIONS:
        for doc_file in Path(project_dir).rglob(f'*{ext}'):
            # 跳过obj和bin目录
            if 'obj' in str(doc_file).lower() or 'bin' in str(doc_file).lower():
                continue
            doc_files.append(str(doc_file))
    return doc_files


def extract_document_summary(doc_file_path):
    """从文档文件中提取摘要"""
    try:
        with open(doc_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # 处理RTF文件
        if doc_file_path.lower().endswith('.rtf'):
            sections = extract_rtf_sections(content)
            doc_text = ''
            if 'summary' in sections:
                doc_text += f"Summary: {sections['summary']}\n\n"
            if 'description' in sections:
                doc_text += f"Description: {sections['description']}"
            return doc_text.strip() or "No summary or description found"

        # 处理HTML文件
        elif doc_file_path.lower().endswith(('.htm', '.html')):
            soup = BeautifulSoup(content, 'html.parser')
            # 提取<body>标签内容或前500字符
            body = soup.body
            if body:
                text = body.get_text(separator='\n', strip=True)
            else:
                text = content
            if len(text) > 500:
                return text[:500] + '...'
            return text

        # 处理文本文件
        else:
            if len(content) > 500:
                return content[:500] + '...'
            return content
    except Exception as e:
        print(f"提取文档时出错 {doc_file_path}: {e}")
        return ''


def extract_rtf_sections(rtf_content):
    try:
        text = rtf_to_text(rtf_content)
    except Exception as e:
        print(f"RTF解析错误: {e}")
        return {}
    
    sections = {}
    current_section = None
    current_content = []
    section_pattern = re.compile(r'^(summary|description)[:：]?\s*$', re.IGNORECASE)
    
    for line in text.split('\n'):
        stripped_line = line.strip()
        if section_pattern.match(stripped_line):
            if current_section:
                sections[current_section] = '\n'.join(current_content).strip()
            current_section = stripped_line.lower().replace(':', '').replace('：', '').strip()
            current_content = []
        elif current_section in ['summary', 'description']:
            current_content.append(line)
    
    if current_section in ['summary', 'description']:
        sections[current_section] = '\n'.join(current_content).strip()
    
    return sections


def process_project(project_dir):
    """处理单个项目并返回提取的数据"""
    print(f"处理项目: {project_dir}")

    # 查找C#文件
    cs_files = []
    for ext in CS_EXTENSIONS:
        cs_files.extend(Path(project_dir).rglob(f'*{ext}'))

    # 提取关键代码
    key_code = None
    for cs_file in cs_files:
        extracted = extract_key_code(str(cs_file))
        if extracted:
            key_code = extracted
            break  # 只需要一个IExternalCommand实现

    # 查找文档文件
    doc_files = find_documentation_files(project_dir)
    doc_summaries = {}
    for doc_file in doc_files:
        rel_path = os.path.relpath(doc_file, project_dir)
        doc_summaries[rel_path] = extract_document_summary(doc_file)

    return {
        'project_path': project_dir,
        'key_code': key_code,
        'documentation': doc_summaries
    }


def main():
    # 获取当前目录作为根目录
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"开始处理项目，根目录: {root_dir}")

    # 查找所有C#项目目录
    project_dirs = find_project_directories(root_dir)
    print(f"找到 {len(project_dirs)} 个C#项目目录")

    # 处理每个项目
    dataset = []
    projects = []
    for project_dir in project_dirs:
        project_data = process_project(project_dir)
        if project_data.get('key_code') is not None:
            projects.append(project_data)
            dataset.append(project_data)

    # 保存数据集
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    print(f"数据集已保存到 {OUTPUT_FILE}")

if __name__ == '__main__':
    main()