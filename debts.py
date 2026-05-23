"""Өрийн дэвтэр — барагдаагүй бооцооноос үүссэн өрийг хөтөлнө.

Өр: debtor (өртэй хүн) -> creditor (авлагатай хүн), тодорхой дүн.
Боломжит үйлдэл: барагдуулах (settle). Өр өөр хүнд шилжихгүй.
"""


class Debt:
    """Нэг өр — debtor-оос creditor руу чиглэсэн дүн."""

    def __init__(self, debtor, creditor, amount, origin="", created=""):
        self.debtor = debtor       # өртэй хүн (Player)
        self.creditor = creditor   # авлагатай хүн (Player)
        self.amount = amount
        self.origin = origin       # эх сурвалж (ж: "Тоглолт #3 — Inferno")
        self.created = created     # үүссэн огноо (ж: "2026-05-23")
        self.settled = False

    def __str__(self):
        mark = "  [барагдсан]" if self.settled else ""
        return (f"{self.debtor.name} -> {self.creditor.name}: "
                f"{self.amount:,}#{mark}").replace("#", "₮")


class DebtLedger:
    """Бүх тоглогчийн өрийн нэгдсэн бүртгэл."""

    def __init__(self):
        self.debts = []

    def add(self, debtor, creditor, amount, origin="", created=""):
        """Шинэ өр бүртгэнэ. Үүсгэсэн Debt-ээ буцаана."""
        debt = Debt(debtor, creditor, amount, origin, created)
        self.debts.append(debt)
        return debt

    @property
    def open_debts(self):
        """Барагдаагүй өрүүд."""
        return [d for d in self.debts if not d.settled]

    def settle(self, debt):
        """Өрийг барагдсан гэж тэмдэглэнэ."""
        if debt.settled:
            raise ValueError("Энэ өр аль хэдийн барагдсан.")
        debt.settled = True

    def player_balance(self, player_id):
        """Тухайн тоглогчийн цэвэр тэнцэл.
        Эерэг = бусдаас авах, сөрөг = бусдад өгөх."""
        total = 0
        for d in self.open_debts:
            if d.creditor.id == player_id:
                total += d.amount
            if d.debtor.id == player_id:
                total -= d.amount
        return total

    def summary(self):
        """{player_id: цэвэр тэнцэл} — өртэй/авлагатай бүх тоглогчийн."""
        bal = {}
        for d in self.open_debts:
            bal[d.debtor.id] = bal.get(d.debtor.id, 0) - d.amount
            bal[d.creditor.id] = bal.get(d.creditor.id, 0) + d.amount
        return bal
