from setuptools import setup, find_packages

setup(
    name="deepseek_local_tokenizer",  
    version="0.1.0",      
    packages=find_packages(include=['deepseek_tokenizer_v3', 'deepseek_tokenizer_v3.*' ]), 

)