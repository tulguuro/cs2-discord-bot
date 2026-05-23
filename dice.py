"""Шоо — газрын veto-ийн ээлж ба тал (CT/T) сонголтыг шийдэхэд хэрэглэнэ."""

import random


def roll() -> int:
    """Нэг шоо хаяна -> 1-6."""
    return random.randint(1, 6)


def roll_off(label_a, label_b) -> dict:
    """Хоёр тал шоо хаяна, өндөр оноотой нь хожно. Тэнцвэл дахин хаяна.

    Буцаах dict:
      winner  — хожсон талын label
      loser   — хожигдсон талын label
      history — [(a_оноо, b_оноо), ...] бүх шидэлт (тэнцсэн дахилт хүртэл)
    """
    history = []
    while True:
        a, b = roll(), roll()
        history.append((a, b))
        if a != b:
            if a > b:
                return {"winner": label_a, "loser": label_b, "history": history}
            return {"winner": label_b, "loser": label_a, "history": history}
