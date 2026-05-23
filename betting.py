"""Бооцооны логик — харалдаа хосуудын бооцоо ба тооцоо хаах урсгал.

Баг хуваагдаж, оноогоор эрэмбэлэгдсэний дараа мөр бүрийн харалдаа 2
тоглогч хооронд бооцоо үүснэ. Тоглолт дуусаад хожсон баг тэмдэглэгдэхэд
бооцоо бүр шийдэгдэж, хожигдогч хожигчдоо төлбөр шилжүүлнэ.

Бооцооны төлөв:
    PENDING — тоглолт дуусаагүй, үр дүн гараагүй
    UNPAID  — үр дүн гарсан, хожигдогч төлбөр төлөөгүй
    CLAIMED — хожигдогч "шилжүүлсэн" гэж тэмдэглэсэн
    SETTLED — хожигч "хүлээж авсан" — бооцоо амжилттай хаагдсан
    DEBT    — хожигч "аваагүй" — өр болж бүртгэгдэх ёстой
"""

PENDING = "pending"
UNPAID = "unpaid"
CLAIMED = "claimed"
SETTLED = "settled"
DEBT = "debt"


class Bet:
    """Хоёр харалдаа тоглогч хоорондын бооцоо."""

    def __init__(self, player_a, player_b, amount):
        self.player_a = player_a
        self.player_b = player_b
        self.amount = amount
        self.status = PENDING
        self.winner = None     # Player — тогтоогдсоны дараа
        self.loser = None      # Player

    def resolve(self, winner_ids):
        """Хожсон багийн тоглогчдын id-аар хожигч/хожигдогчийг тогтооно."""
        if self.status != PENDING:
            raise ValueError("Бооцоо аль хэдийн шийдэгдсэн.")
        if self.player_a.id in winner_ids:
            self.winner, self.loser = self.player_a, self.player_b
        else:
            self.winner, self.loser = self.player_b, self.player_a
        self.status = UNPAID

    def mark_paid(self):
        """Хожигдогч мөнгөө шилжүүлсэн гэж тэмдэглэнэ (UNPAID -> CLAIMED)."""
        if self.status != UNPAID:
            raise ValueError("Бооцоо төлбөр хүлээж буй төлөвт байхгүй байна.")
        self.status = CLAIMED

    def confirm_received(self):
        """Хожигч мөнгө хүлээж авснаа баталгаажуулна -> SETTLED."""
        if self.status not in (UNPAID, CLAIMED):
            raise ValueError("Бооцоог баталгаажуулах боломжгүй төлөвт байна.")
        self.status = SETTLED

    def reject(self):
        """Хожигч мөнгө аваагүй гэв -> DEBT (өр болно)."""
        if self.status not in (UNPAID, CLAIMED):
            raise ValueError("Бооцоог татгалзах боломжгүй төлөвт байна.")
        self.status = DEBT

    @property
    def is_closed(self):
        """Бооцоо эцэслэгдсэн эсэх (хаагдсан эсвэл өр болсон)."""
        return self.status in (SETTLED, DEBT)


def create_bets(team1, team2, amount):
    """Багуудыг оноогоор эрэмбэлж, харалдаа хосуудаар бооцоо үүсгэнэ.

    Team A-ийн N-р тоглогч  <->  Team B-ийн N-р тоглогч  (нэг бооцоо).
    """
    s1 = sorted(team1.players, key=lambda p: p.rating, reverse=True)
    s2 = sorted(team2.players, key=lambda p: p.rating, reverse=True)
    return [Bet(p1, p2, amount) for p1, p2 in zip(s1, s2)]


class BettingRound:
    """Нэг тоглолтын бүх бооцоог удирдана."""

    def __init__(self, team1, team2, amount):
        self.team1 = team1
        self.team2 = team2
        self.amount = amount
        self.bets = create_bets(team1, team2, amount)
        self.winner_team = None       # 1 эсвэл 2

    def record_result(self, winner_team):
        """Хожсон багийг (1/2) тэмдэглэж, бүх бооцоог шийднэ."""
        if winner_team not in (1, 2):
            raise ValueError("winner_team нь 1 эсвэл 2 байх ёстой.")
        if self.winner_team is not None:
            raise ValueError("Үр дүн аль хэдийн бүртгэгдсэн.")
        self.winner_team = winner_team
        win = self.team1 if winner_team == 1 else self.team2
        winner_ids = {p.id for p in win.players}
        for bet in self.bets:
            bet.resolve(winner_ids)

    def reset_result(self):
        """Үр дүнг цуцлах — зөвхөн ямар нэг бооцоо хаагдаагүй (SETTLED/DEBT
        бус) үед боломжтой. Буруу баг хожсон гэж тэмдэглэсэн бол хэрэглэнэ.
        """
        if self.winner_team is None:
            raise ValueError("Үр дүн бүртгэгдээгүй байна.")
        if any(b.status in (SETTLED, DEBT) for b in self.bets):
            raise ValueError("Зарим бооцоо аль хэдийн хаагдсан тул "
                             "цуцлах боломжгүй.")
        self.winner_team = None
        for bet in self.bets:
            bet.status = PENDING
            bet.winner = None
            bet.loser = None

    @property
    def is_complete(self):
        """Бүх бооцоо эцэслэгдсэн (хаагдсан/өр болсон) эсэх."""
        return all(b.is_closed for b in self.bets)

    def debts_to_register(self):
        """DEBT төлөвт орсон бооцоонууд — өрийн дэвтэрт нэмэх ёстой."""
        return [b for b in self.bets if b.status == DEBT]
