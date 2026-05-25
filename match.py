"""Тоглолт бэлтгэх сесс — бүртгэлээс багууд+газар бэлэн болтол удирдана.

Шат дамжлага:
    REGISTRATION -> DIVISION -> VETO -> READY        (эсвэл CANCELLED)

Энэ класс нь бүх дэд логикийг (teams, veto, dice) нэгтгэдэг "тархи" юм.
"""

import random
from enum import Enum

from config import MATCH_SIZE
from teams import pick_captains, random_split, Draft, ManualDivision
from veto import MapVeto
from dice import roll_off


class Phase(Enum):
    REGISTRATION = "Бүртгэл"
    DIVISION = "Баг хуваах"
    VETO = "Газар сонгох"
    READY = "Бэлэн"
    CANCELLED = "Цуцлагдсан"


class MatchSession:
    """Нэг тоглолт бэлтгэх явцыг бүхэлд нь хадгалах төлөв машин."""

    def __init__(self):
        self.phase = Phase.REGISTRATION
        self.all_joined = []          # бүртгүүлсэн дарааллаараа
        self.previous_teams = None    # /remake-ын үед өмнөх багуудыг хадгална
        self._reset_division()

    # ---------- Дотоод туслахууд ----------

    def _reset_division(self):
        """Хуваалт ба түүнээс хойших бүх төлвийг цэвэрлэнэ."""
        self.captain1 = None
        self.captain2 = None
        self.method_votes = {}        # {captain_num: "draft"/"random"/"manual"}
        self.confirm_votes = {}       # {captain_num: True}  # 2/2 баталгаажуулах
        self.division_method = None   # "draft" / "random" / "manual"
        self.draft = None
        self.manual = None            # ManualDivision (гар хуваалтын үед)
        self.teams = None             # (Team, Team)
        self.veto_first_roll = None
        self.veto = None
        self.maps = []                # тоглох газрууд (BO3: 3)
        self.sides = {}               # {газрын нэр: T талаас эхлэх багийн дугаар}

    def _index_of(self, player):
        for i, p in enumerate(self.all_joined):
            if p.id == player.id:
                return i
        return None

    def _enter_division(self):
        self.phase = Phase.DIVISION
        self.captain1, self.captain2 = pick_captains(self.players)
        self.method_votes = {}

    # ---------- Шинж чанарууд ----------

    @property
    def players(self):
        """Тоглох эхний MATCH_SIZE хүн."""
        return self.all_joined[:MATCH_SIZE]

    @property
    def reserves(self):
        """MATCH_SIZE-аас хойшхи нөөц тоглогчид."""
        return self.all_joined[MATCH_SIZE:]

    @property
    def is_full(self):
        return len(self.all_joined) >= MATCH_SIZE

    @property
    def slots_left(self):
        return max(0, MATCH_SIZE - len(self.all_joined))

    @property
    def unrated_players(self):
        """Үнэлгээ аваагүй (rating 0) тоглогчид — баг хуваахаас өмнө шалгана."""
        return [p for p in self.players if not p.is_rated]

    # ---------- Бүртгэл ----------

    def join(self, player):
        """Тоглогч бүртгүүлнэ. Шинэ phase-г буцаана.

        10 хүн бүрдсэн ч АВТОМАТААР эхлэхгүй — admin/ахлагч begin_division()
        дуудаж эхлүүлнэ. Тиймээс 11 дэх хүнээс хойш нөөцөд бүртгэгдэнэ.
        """
        if self.phase in (Phase.READY, Phase.CANCELLED):
            raise ValueError("Бүртгэл хаагдсан.")
        if self._index_of(player) is not None:
            raise ValueError(f"{player.name} аль хэдийн бүртгүүлсэн.")
        self.all_joined.append(player)
        return self.phase

    def leave(self, player):
        """Тоглогч бүртгэлээс гарна. Шинэ phase-г буцаана."""
        idx = self._index_of(player)
        if idx is None:
            raise ValueError("Бүртгэлд байхгүй тоглогч.")
        if self.phase in (Phase.READY, Phase.CANCELLED):
            raise ValueError("Тоглолт бэлдсэн тул одоо гарах боломжгүй.")
        was_player = idx < MATCH_SIZE
        self.all_joined.pop(idx)

        if self.phase == Phase.REGISTRATION:
            return self.phase
        # DIVISION/VETO үед нөөц гарвал юу ч өөрчлөгдөхгүй
        if not was_player:
            return self.phase
        # Тоглогч гарвал хуваалт тэглэгдэж бүртгэл рүү буцна — нөөц байсан ч
        # admin/ахлагч begin_division()-г дахин дуудаж эхлүүлэх ёстой
        self._reset_division()
        self.phase = Phase.REGISTRATION
        return self.phase

    def swap_player(self, out_player, in_player):
        """Тоглох 10 доторх out_player-г нөөцийн in_player-тэй солино.

        Зөвхөн бүртгэлийн шатанд. Байр солих учир автоматаар нөхөгдөхгүй —
        admin/ахлагч хэнийг оруулахаа өөрөө шийднэ.
        """
        if self.phase != Phase.REGISTRATION:
            raise ValueError("Зөвхөн бүртгэлийн шатанд тоглогч солино.")
        out_idx = self._index_of(out_player)
        in_idx = self._index_of(in_player)
        if out_idx is None or in_idx is None:
            raise ValueError("Тоглогч бүртгэлд алга.")
        if out_idx >= MATCH_SIZE:
            raise ValueError("Хасах тоглогч тоглох 10-д алга байна.")
        if in_idx < MATCH_SIZE:
            raise ValueError("Оруулах тоглогч нөөцөд алга байна.")
        self.all_joined[out_idx], self.all_joined[in_idx] = (
            self.all_joined[in_idx], self.all_joined[out_idx])

    def begin_division(self):
        """Бүртгэлийг хааж баг хуваах шат руу шилжинэ.

        admin эсвэл ахлагч (хамгийн өндөр үнэлгээтэй тоглогч) гар аргаар
        эхлүүлэх үед дуудагдана.
        """
        if self.phase != Phase.REGISTRATION:
            raise ValueError("Бүртгэлийн шатанд байхгүй байна.")
        if not self.is_full:
            raise ValueError(f"Дор хаяж {MATCH_SIZE} тоглогч хэрэгтэй.")
        self._enter_division()
        return self.phase

    def restart_division(self):
        """Баг хуваалтыг тэглэж арга сонгохоос дахин эхлүүлнэ."""
        if self.phase != Phase.DIVISION:
            raise ValueError("Хуваалтын шатанд байхгүй байна.")
        self._reset_division()
        self._enter_division()
        return self.phase

    # ---------- Баг хуваах ----------

    def vote_method(self, captain_num, choice):
        """Ахлагч хуваах аргаа сонгоно: 'draft' / 'random' / 'manual' / 'previous'.

        Дүрэм:
          - Бүх 4 төрөл хоёр ахлагч ХОЁУЛАА ижил сонгох ёстой (2/2).
          - Ахлагч санал солих гэж шинэ товч даравал хуучин санал
            автоматаар орлогдоно (vote count харгалзан тэг рүү буцна).
        Үр дүн: 'draft' / 'random' / 'manual' / 'previous' / 'waiting'.
        """
        if self.phase != Phase.DIVISION:
            raise ValueError("Хуваалтын шатанд байхгүй байна.")
        if captain_num not in (1, 2):
            raise ValueError("captain_num нь 1 эсвэл 2 байх ёстой.")
        if choice not in ("draft", "random", "manual", "previous"):
            raise ValueError(
                "choice нь 'draft'/'random'/'manual'/'previous' байх ёстой.")
        if self.division_method is not None:
            raise ValueError("Хуваах арга аль хэдийн сонгогдсон.")
        if choice == "previous" and self.previous_teams is None:
            raise ValueError("Өмнөх багууд алга — /matchprep шинэ сесст байхгүй.")

        # Хуучин санал шинэ сонголтоор орлогдоно (vote counter автоматаар тэгшинэ)
        self.method_votes[captain_num] = choice
        # Хоёр ахлагч ижил сонгосон бол л арга идэвхэжнэ
        if (self.method_votes.get(1) == choice
                and self.method_votes.get(2) == choice):
            if choice == "draft":
                self._begin_draft()
            elif choice == "random":
                self._begin_random()
            elif choice == "manual":
                self._begin_manual()
            else:  # previous
                self._begin_previous()
            return choice
        return "waiting"

    def _begin_draft(self):
        pool = [p for p in self.players
                if p.id not in (self.captain1.id, self.captain2.id)]
        self.draft = Draft(self.captain1, self.captain2, pool)
        self.division_method = "draft"

    def _begin_random(self):
        self.division_method = "random"
        self.teams = random_split(self.players)

    def _begin_manual(self):
        self.division_method = "manual"
        self.manual = ManualDivision(self.captain1, self.captain2, self.players)

    def _begin_previous(self):
        """Өмнөх багуудыг шууд хадгалж, VETO бэлэн төлвийг үүсгэнэ."""
        self.division_method = "previous"
        self.teams = self.previous_teams

    def draft_pick(self, captain_num, player):
        """Ээлжит ахлагч тоглогч сонгоно."""
        if self.division_method != "draft":
            raise ValueError("Draft горимд биш байна.")
        if captain_num != self.draft.current_captain:
            raise ValueError("Энэ ахлагчийн сонгох ээлж биш байна.")
        self.draft.pick(player)
        if self.draft.is_complete:
            self.teams = self.draft.teams()

    def manual_assign(self, player, team_num):
        """Гар хуваалтын үед тоглогчийг багт ононо. Багуудын бүрэлдэхүүн
        өөрчлөгдсөн тул өмнөх confirm-уудыг тэглэнэ."""
        if self.division_method != "manual":
            raise ValueError("Гар хуваалтын горимд биш байна.")
        self.manual.assign(player, team_num)
        self.confirm_votes = {}
        if self.manual.is_complete:
            self.teams = self.manual.teams()

    def manual_unassign(self, player):
        """Гар хуваалтын үед тоглогчийг багаас нь буцаана. Багууд өөрчлөгдсөн
        тул өмнөх confirm-уудыг тэглэнэ."""
        if self.division_method != "manual":
            raise ValueError("Гар хуваалтын горимд биш байна.")
        self.manual.unassign(player)
        self.confirm_votes = {}
        if not self.manual.is_complete:
            self.teams = None

    def reroll(self):
        """Санамсаргүй хуваалтыг дахин хийнэ (таалагдаагүй тохиолдолд).
        Шинэ багууд гарах учраас өмнөх confirm-уудыг тэглэнэ."""
        if self.division_method != "random":
            raise ValueError("Зөвхөн санамсаргүй хуваалтыг дахин хийж болно.")
        self.teams = random_split(self.players)
        self.confirm_votes = {}
        return self.teams

    def vote_confirm(self, captain_num):
        """Ахлагч багуудыг баталгаажуулна. 2 ахлагч хоёулаа дарвал True
        буцаах ба тэр үед confirm_division() автоматаар дуудах ёстой."""
        if self.phase != Phase.DIVISION:
            raise ValueError("Хуваалтын шатанд байхгүй байна.")
        if self.teams is None:
            raise ValueError("Багууд хараахан бэлэн болоогүй байна.")
        if captain_num not in (1, 2):
            raise ValueError("captain_num нь 1 эсвэл 2 байх ёстой.")
        self.confirm_votes[captain_num] = True
        return bool(self.confirm_votes.get(1)
                    and self.confirm_votes.get(2))

    def confirm_division(self):
        """Багуудыг баталж газрын veto руу шилжинэ. Эхэлж ban хийх багийг буцаана."""
        if self.phase != Phase.DIVISION:
            raise ValueError("Хуваалтын шатанд байхгүй байна.")
        if self.teams is None:
            raise ValueError("Багууд хараахан бэлэн болоогүй байна.")
        t1, t2 = self.teams
        self.veto_first_roll = roll_off(t1.name, t2.name)
        first_team = 1 if self.veto_first_roll["winner"] == t1.name else 2
        self.veto = MapVeto(first_team)
        self.phase = Phase.VETO
        return first_team

    # ---------- Газрын veto ----------

    def veto_act(self, team_num, map_name):
        """Баг газартай veto үйлдэл (ban/pick) хийнэ. Үйлдлийн төрлийг буцаана.

        Veto дуусмагц тоглох 3 газар тогтож, тал автоматаар хуваарилагдан
        тоглолт шууд БЭЛЭН (READY) болно.
        """
        if self.phase != Phase.VETO:
            raise ValueError("Veto-ийн шатанд байхгүй байна.")
        action = self.veto.act(team_num, map_name)
        if self.veto.is_complete:
            self.maps = self.veto.maps_in_order
            self._assign_sides()
            self.phase = Phase.READY
        return action

    def _assign_sides(self):
        """Тал автоматаар хуваарилна (сонголтгүй):
          - pick хийсэн газар -> pick хийсэн баг T талаас эхэлнэ.
          - decider           -> аль баг T талд тоглохыг санамсаргүй шийднэ.
        self.sides = {газрын нэр: T талаас эхлэх багийн дугаар}.
        """
        self.sides = {}
        for m in self.maps:
            picker = self.veto.map_picker(m)
            self.sides[m] = picker if picker is not None else random.choice([1, 2])

    def cancel(self):
        """Тоглолт бэлтгэхийг цуцална."""
        self.phase = Phase.CANCELLED
