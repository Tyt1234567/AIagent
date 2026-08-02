"""
本地运行 LLM 示例（使用 Ollama + Qwen2.5-7B-Instruct）

前置条件：
1. 已安装 Ollama（Windows 版），安装后会自动在后台运行本地服务，
   监听 http://localhost:11434
2. 已执行过一次： ollama pull qwen2.5:7b-instruct
3. pip install openai   （Ollama 兼容 OpenAI 的 /v1 接口，可直接复用 openai SDK）

用法：
    python local_llm_example.py
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from openai import OpenAI

# Ollama 暴露了一个兼容 OpenAI 接口的本地服务，api_key 可以随便填一个非空字符串
client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",  # 本地服务不校验，占位即可
)

MODEL = "qwen2.5:7b-instruct"


def chat_once(prompt: str, system_prompt: str = "你是一个乐于助人的助手。") -> str:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content


def chat_stream(prompt: str, system_prompt: str = "你是一个乐于助人的助手。") -> None:
    """流式输出示例：逐字打印，适合交互式场景。"""
    stream = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            print(delta, end="", flush=True)
    print()


if __name__ == "__main__":
    print("=== 非流式调用 ===")
    answer = chat_once("用一句话介绍一下你自己。")
    print(answer)

    print("\n=== 流式调用 ===")
    chat_stream("给我讲一个关于程序员的两句话短笑话。")
