"""互動式 CLI：在本地終端機直接與 agent 多輪對話，不經過 Telegram / webhook / SQS。

用法：
    make chat
    # 或
    PYTHONPATH=src uv run --env-file .env python -m agent.chat_cli

輸入 exit 或 quit，或按 Ctrl-D / Ctrl-C 離開。
"""

from __future__ import annotations

import logging

from agent.agent import run_agent
from agent.db.conversations import HISTORY_LIMIT

EXIT_COMMANDS = {"exit", "quit"}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print("美股財報 AI 助理（本地 CLI）。輸入 exit 或 quit 離開。")
    history: list[dict] = []

    while True:
        try:
            user_message = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再見！")
            return

        if not user_message:
            continue
        if user_message.lower() in EXIT_COMMANDS:
            print("再見！")
            return

        reply = run_agent(user_message, history)
        print(f"助理：{reply}")

        # 不直接 mutate history，改成產生新 list，避免呼叫方持有的參照被後續 append 污染。
        history = history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": reply},
        ]
        history = history[-HISTORY_LIMIT:]


if __name__ == "__main__":
    main()
