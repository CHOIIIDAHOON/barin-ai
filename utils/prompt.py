"""대화 메시지를 Cursor CLI agent에 넘길 단일 텍스트로 합친다."""

from typing import List

from schemas import ChatMessage


def msgs_to_prompt(messages: List[ChatMessage]) -> str:
    """OpenAI 형식 messages를 cursor agent용 지시문 한 덩어리로 만든다."""
    blocks = []
    labels = {"system": "System", "user": "User", "assistant": "Assistant"}
    for m in messages:
        label = labels[m.role]
        blocks.append(f"## {label}\n{m.content.strip()}")
    return (
        "You are helping via the Cursor CLI agent. Follow the conversation below.\n\n"
        + "\n\n".join(blocks)
    )
