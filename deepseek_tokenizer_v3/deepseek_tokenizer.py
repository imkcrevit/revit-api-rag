# pip3 install transformers
# python3 deepseek_tokenizer.py
import transformers
import os 


def get_local_tokenizer_length(content : str) :
        """
        Input the content and return the token length by tokenizer
        """
        root_path = "/root/autodl-tmp/python_revit_train"
        chat_tokenizer_dir = os.path.join(root_path , 'deepseek_tokenizer_v3')

        tokenizer = transformers.AutoTokenizer.from_pretrained( 
                chat_tokenizer_dir, trust_remote_code=True
                )

        result = tokenizer.encode(content)
        print(len(result))
        return len(result)


if __name__ == "__main__" :
        get_local_tokenizer_length("hello!1")
