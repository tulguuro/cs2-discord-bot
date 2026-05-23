"""Баг хуваах логик — ахлагч сонгох, тэнцвэртэй/санамсаргүй хуваалт, draft."""

import itertools
import random

from config import DRAFT_ORDER, RANDOM_MODE, BALANCE_TOLERANCE, TEAM_SIZE
from models import Team


def pick_captains(players):
    """Хамгийн өндөр үнэлгээтэй 2 тоглогчийг ахлагчаар буцаана.

    Үнэлгээ тэнцсэн тохиолдолд санамсаргүйгээр шийднэ.
    Буцаах: (captain1, captain2) — captain1 нь илүү/тэнцүү үнэлгээтэй.
    """
    if len(players) < 2:
        raise ValueError("Ахлагч сонгоход дор хаяж 2 тоглогч хэрэгтэй.")
    ranked = sorted(players, key=lambda p: (p.rating, random.random()), reverse=True)
    return ranked[0], ranked[1]


def _possible_splits(players):
    """Бүх боломжит тэнцүү хуваалтыг (team1, team2, зөрүү) болгож буцаана.

    Эхний тоглогчийг үргэлж 1-р талд байлгаснаар давхардсан хуваалтыг арилгана.
    """
    half = len(players) // 2
    first, rest = players[0], players[1:]
    splits = []
    for combo in itertools.combinations(rest, half - 1):
        team1 = [first] + list(combo)
        team1_ids = {p.id for p in team1}
        team2 = [p for p in players if p.id not in team1_ids]
        diff = abs(sum(p.rating for p in team1) - sum(p.rating for p in team2))
        splits.append((team1, team2, round(diff, 2)))
    return splits


def _make_teams(p1, p2):
    return Team("Team A", list(p1)), Team("Team B", list(p2))


def balanced_split(players):
    """Одны нийлбэрийн зөрүү ХАМГИЙН БАГА хуваалтыг буцаана.

    Ижил зэргийн хэд хэдэн хувилбар байвал санамсаргүйг нь сонгоно.
    """
    splits = _possible_splits(players)
    best_diff = min(s[2] for s in splits)
    best = [s for s in splits if s[2] == best_diff]
    team1, team2, _ = random.choice(best)
    return _make_teams(team1, team2)


def random_split(players):
    """config.RANDOM_MODE-ийн дагуу санамсаргүй хуваалт хийнэ."""
    if RANDOM_MODE == "pure":
        shuffled = players[:]
        random.shuffle(shuffled)
        half = len(shuffled) // 2
        return _make_teams(shuffled[:half], shuffled[half:])
    # "balanced": хамгийн тэнцвэртэйд ойр хувилбаруудаас санамсаргүй
    splits = _possible_splits(players)
    best_diff = min(s[2] for s in splits)
    good = [s for s in splits if s[2] <= best_diff + BALANCE_TOLERANCE]
    team1, team2, _ = random.choice(good)
    return _make_teams(team1, team2)


def draft_order(num_picks, mode=None):
    """Draft-ын ээлжийг гаргана — [1, 2, ...] (ахлагчийн дугаар).

    "alternating": 1,2,1,2,...
    "snake":       1,2,2,1,1,2,2,1,...
    """
    if mode is None:
        mode = DRAFT_ORDER
    order = []
    if mode == "alternating":
        for i in range(num_picks):
            order.append(1 if i % 2 == 0 else 2)
    else:  # snake
        round_idx = 0
        while len(order) < num_picks:
            pair = [1, 2] if round_idx % 2 == 0 else [2, 1]
            for c in pair:
                if len(order) < num_picks:
                    order.append(c)
            round_idx += 1
    return order


class Draft:
    """Ахлагчийн сонголтыг алхам алхмаар удирдана.

    captain1, captain2 — ахлагчид (тус бүр өөрийн багт автоматаар орно).
    pool — сонгогдох үлдсэн тоглогчид.
    """

    def __init__(self, captain1, captain2, pool):
        self.captain1 = captain1
        self.captain2 = captain2
        self.team1 = Team("Team A", [captain1])
        self.team2 = Team("Team B", [captain2])
        self.available = list(pool)
        self.order = draft_order(len(pool))
        self.step = 0

    @property
    def is_complete(self):
        return self.step >= len(self.order)

    @property
    def current_captain(self):
        """Одоо сонгох ээлжтэй ахлагчийн дугаар (1/2), дууссан бол None."""
        if self.is_complete:
            return None
        return self.order[self.step]

    def _team(self, num):
        return self.team1 if num == 1 else self.team2

    def pick(self, player):
        """Ээлжит ахлагч нэг тоглогч сонгоно. Сонгосон багаа буцаана.

        Үлдсэн ганц тоглогчийг (сонгох зүйлгүй тул) автоматаар хуваарилна.
        """
        if self.is_complete:
            raise ValueError("Draft аль хэдийн дууссан.")
        chosen = next((p for p in self.available if p.id == player.id), None)
        if chosen is None:
            raise ValueError("Энэ тоглогчийг сонгох боломжгүй.")
        team = self._team(self.current_captain)
        team.players.append(chosen)
        self.available.remove(chosen)
        self.step += 1
        # Үлдсэн ганц тоглогчийг автоматаар хуваарилна
        if len(self.available) == 1 and not self.is_complete:
            last = self.available.pop()
            self._team(self.current_captain).players.append(last)
            self.step += 1
        return team

    def teams(self):
        """(team1, team2)-г буцаана."""
        return self.team1, self.team2


def rating_groups(players):
    """Ижил оноотой тоглогчдыг бүлэглэнэ — draft үед сануулга болгон үзүүлнэ.

    Буцаах: {rating: [players]} — зөвхөн 2+ хүнтэй (ижил оноотой) бүлгүүд.
    """
    groups = {}
    for p in players:
        groups.setdefault(p.rating, []).append(p)
    return {r: ps for r, ps in groups.items() if len(ps) >= 2}


class ManualDivision:
    """Гар хуваалт — ахлагчид тоглогчдыг чөлөөтэй (ээлж баримталалгүй) хуваана.

    captain1, captain2 — ахлагчид (тус бүр өөрийн багт автоматаар орно).
    players — тоглох MATCH_SIZE хүн.
    """

    def __init__(self, captain1, captain2, players):
        self.team1 = Team("Team A", [captain1])
        self.team2 = Team("Team B", [captain2])
        self._captain_ids = {captain1.id, captain2.id}
        self.unassigned = [p for p in players if p.id not in self._captain_ids]

    @property
    def is_complete(self):
        """Бүх тоглогч 2 багт хуваарилагдсан уу."""
        return not self.unassigned

    def _team(self, num):
        if num not in (1, 2):
            raise ValueError("team_num нь 1 эсвэл 2 байх ёстой.")
        return self.team1 if num == 1 else self.team2

    def assign(self, player, team_num):
        """Тоглогчийг team_num (1/2) багт ононо. Оноосон багаа буцаана."""
        chosen = next((p for p in self.unassigned if p.id == player.id), None)
        if chosen is None:
            raise ValueError("Энэ тоглогчийг оноох боломжгүй (жагсаалтад алга).")
        team = self._team(team_num)
        if len(team.players) >= TEAM_SIZE:
            raise ValueError(f"{team.name} дүүрсэн байна ({TEAM_SIZE} хүн).")
        team.players.append(chosen)
        self.unassigned.remove(chosen)
        return team

    def unassign(self, player):
        """Тоглогчийг багаас нь буцааж жагсаалтад оруулна (ахлагчийг үл хөдлөнө)."""
        if player.id in self._captain_ids:
            raise ValueError("Ахлагчийг буцаах боломжгүй.")
        for team in (self.team1, self.team2):
            match = next((p for p in team.players if p.id == player.id), None)
            if match is not None:
                team.players.remove(match)
                self.unassigned.append(match)
                return
        raise ValueError("Энэ тоглогч аль ч багт алга.")

    def teams(self):
        """(team1, team2)-г буцаана."""
        return self.team1, self.team2
