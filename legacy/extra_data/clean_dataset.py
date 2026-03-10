import json
import os

def clean_dataset(input_path, output_path):
    with open(input_path, 'r', encoding='utf-8') as f:
        dataset = json.load(f)
    
    cleaned_dataset = []
    for item in dataset:
        # 检查execute_method是否存在且不为空
        key_code = item.get('key_code', {})
        execute_method = key_code.get('execute_method', '').strip()
        if not execute_method:
            continue  # 跳过execute_method为空的条目
        
        # 检查其他必要字段是否为空
        project_path = item.get('project_path', '').strip()
        documentation = item.get('documentation', {})
        class_name = key_code.get('class_name', '').strip()
        
        # 如果关键字段为空则跳过
        if not all([project_path, documentation, class_name]):
            continue
        
        cleaned_dataset.append(item)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(cleaned_dataset, f, ensure_ascii=False, indent=2)
    return cleaned_dataset

if __name__ == '__main__':
    input_file = 'project_dataset.json'
    output_file = 'project_dataset_cleaned.json'
    cleaned_dataset = clean_dataset(input_file, output_file)
    # 替换原文件
    os.replace(output_file, input_file)
    print(f"数据集清理完成，共保留{len(cleaned_dataset)}个有效条目")