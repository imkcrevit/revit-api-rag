import re
from typing import List


def get_block_content(path: str, block_type: str) -> List[str]:
    with open(path, 'r', encoding='utf-8') as file:
        content = file.read()
    pattern = re.compile(f"=== {block_type}_\d{{4}} ===")
    return [chunk.strip() for chunk in pattern.split(content) if chunk.strip()]


def main():
    # 获取代码块和文档块内容
    code_chunks = get_block_content('extracted_content/all_codes.txt', 'CODE_BLOCK')
    doc_chunks = get_block_content('extracted_content/all_docs.txt', 'DOC_BLOCK')

    # 比较数量
    code_count = len(code_chunks)
    doc_count = len(doc_chunks)

    print(f"代码块数量: {code_count}")
    print(f"文档块数量: {doc_count}")

    if code_count == doc_count:
        print("✅ 代码块和文档块数量相同")
    else:
        print(f"❌ 代码块和文档块数量不同 (差异: {abs(code_count - doc_count)})")


if __name__ == "__main__":
    main()