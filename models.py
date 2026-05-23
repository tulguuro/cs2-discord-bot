"""Үндсэн өгөгдлийн загвар — тоглогч ба баг."""

from dataclasses import dataclass, field


@dataclass
class BankAccount:
    """Тоглогчийн банкны данс — тооцоо хаах үед нэрний хажууд харагдана."""

    bank: str        # банкны нэр (ж: "Хаан банк")
    number: str      # дансны дугаар
    holder: str      # данс эзэмшигчийн нэр

    def __str__(self) -> str:
        return f"{self.bank} · {self.number} ({self.holder})"


@dataclass
class Player:
    """Тоглогч: Discord ID, нэр, чадварын үнэлгээ (од), банкны данс.

    rating = 0.0 бол "үнэлгээ өгөөгүй" гэсэн үг.
    bank = None бол данс бүртгээгүй.
    """

    id: int
    name: str
    rating: float = 0.0
    bank: object = None       # BankAccount эсвэл None

    @property
    def is_rated(self) -> bool:
        return self.rating > 0

    def __str__(self) -> str:
        return f"{self.name} [{self.rating}*]"


@dataclass
class Team:
    """Баг: нэр + тоглогчдын жагсаалт."""

    name: str
    players: list = field(default_factory=list)

    @property
    def total_rating(self) -> float:
        """Багийн одны нийлбэр."""
        return round(sum(p.rating for p in self.players), 2)

    @property
    def captain(self):
        """Багийн хамгийн өндөр үнэлгээтэй тоглогч (ахлагч / veto төлөөлөгч)."""
        if not self.players:
            return None
        return max(self.players, key=lambda p: p.rating)

    def has(self, player) -> bool:
        return any(p.id == player.id for p in self.players)

    def __str__(self) -> str:
        names = ", ".join(p.name for p in self.players)
        return f"{self.name} ({self.total_rating}*): {names}"
