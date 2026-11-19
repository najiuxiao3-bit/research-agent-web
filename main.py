# -*- coding: utf-8 -*-
"""命令行入口：python main.py 后跟要调研的问题。"""

import sys

import config
from agent import run


def main() -> None:
    err = config.check_config()
    if err:
        print(err)
        sys.exit(1)

    query = " ".join(sys.argv[1:]).strip()
    if not query:
        query = input("请输入要调研的问题：").strip()
    if not query:
        print("问题不能为空。")
        sys.exit(1)

    print(f"问题：{query}")
    print("=" * 60)

    result = run(query, verbose=True)

    print("=" * 60)
    if not result["ok"]:
        print(f"运行失败：{result['error']}")
        sys.exit(1)

    print(result["answer"])


if __name__ == "__main__":
    main()
