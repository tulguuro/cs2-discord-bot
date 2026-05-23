"""Газрын veto — CS2 Major BO3 дүрэм.

Veto бол ban/pick үйлдлийн дараалал:
    ban, ban, pick, pick, ban, ban  ->  үлдсэн нь decider (3 дахь газар)
Үр дүнд 3 газар тоглоно: 2 нь pick хийгдсэн, 1 нь decider.
"""

from config import MAP_POOL

# CS2 Major BO3 veto-гийн үйлдлийн дараалал. Орц бүр: (action, team_offset)
#   action      : "ban" (хасах) эсвэл "pick" (сонгох)
#   team_offset : 0 = эхлэх баг (first_team), 1 = нөгөө баг
SEQUENCE = [
    ("ban", 0), ("ban", 1),
    ("pick", 0), ("pick", 1),
    ("ban", 0), ("ban", 1),
]


class MapVeto:
    """BO3 газрын ban/pick дарааллыг алхам алхмаар удирдана.

    first_team : эхэлж үйлдэл хийх багийн дугаар (1 эсвэл 2) — шоогоор сонгогдоно.
    """

    def __init__(self, first_team, pool=None):
        if first_team not in (1, 2):
            raise ValueError("first_team нь 1 эсвэл 2 байх ёстой.")
        self.first_team = first_team
        self.remaining = list(pool if pool is not None else MAP_POOL)
        self.step = 0
        self.bans = []        # [(team, map), ...]
        self.picks = []       # [(team, map), ...] — pick хийгдсэн дарааллаар
        self.decider = None   # үлдсэн газар (3 дахь)

    @property
    def is_complete(self) -> bool:
        """Veto-гийн бүх ban/pick хийгдэж дуусав уу."""
        return self.step >= len(SEQUENCE)

    @property
    def current_team(self):
        """Одоо үйлдэл хийх ээлжтэй багийн дугаар (1/2), эсвэл None."""
        if self.is_complete:
            return None
        _action, offset = SEQUENCE[self.step]
        return self.first_team if offset == 0 else self._other(self.first_team)

    @property
    def current_action(self):
        """Одоогийн үйлдлийн төрөл: 'ban' / 'pick' / None."""
        if self.is_complete:
            return None
        return SEQUENCE[self.step][0]

    @staticmethod
    def _other(team):
        return 2 if team == 1 else 1

    def act(self, team, map_name) -> str:
        """Ээлжит баг газартай үйлдэл (ban эсвэл pick) хийнэ.

        Хийгдсэн үйлдлийн төрлийг ('ban'/'pick') буцаана.
        """
        if self.is_complete:
            raise ValueError("Veto аль хэдийн дууссан.")
        if team != self.current_team:
            raise ValueError(f"Энэ нь {team}-р багийн ээлж биш байна.")
        if map_name not in self.remaining:
            raise ValueError(f"'{map_name}' газрыг сонгох боломжгүй.")
        action = self.current_action
        self.remaining.remove(map_name)
        if action == "ban":
            self.bans.append((team, map_name))
        else:
            self.picks.append((team, map_name))
        self.step += 1
        if self.is_complete:
            self.decider = self.remaining[0]
        return action

    @property
    def maps_in_order(self) -> list:
        """Тоглох 3 газар дарааллаараа: [pick1, pick2, decider]."""
        if not self.is_complete:
            return []
        return [m for (_t, m) in self.picks] + [self.decider]

    def map_picker(self, map_name):
        """Газрыг pick хийсэн багийн дугаар. Pick хийгээгүй (decider) бол None."""
        for team, m in self.picks:
            if m == map_name:
                return team
        return None
