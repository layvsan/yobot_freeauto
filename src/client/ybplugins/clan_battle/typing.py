from dataclasses import dataclass
from typing import Any, Dict, List, NewType, Optional

Pcr_date = NewType('Pcr_date', int)
Pcr_time = NewType('Pcr_time', int)
QQid = NewType('QQid', int)
Groupid = NewType('Groupid', int)


@dataclass
class BossStatus:
    cycle: int
    ahealth: int
    bhealth: int
    chealth: int
    dhealth: int
    ehealth: int
    a_issecond:bool
    b_issecond:bool
    c_issecond:bool
    d_issecond:bool
    e_issecond:bool
    challenger: QQid
    info: str

    def __str__(self):
        summary = (
            '现在{}周目\n'
            '1w生命值       {:,}{}\n'
            '2w生命值       {:,}{}\n'
            '3w生命值       {:,}{}\n'
            '4w生命值       {:,}{}\n'
            '5w生命值       {:,}{}\n'
        ).format(self.cycle, 
                 self.ahealth,
                 '(副圈)' if self.a_issecond else '', 
                 self.bhealth,
                 '(副圈)' if self.b_issecond else '',
                 self.chealth,
                 '(副圈)' if self.c_issecond else '',
                 self.dhealth,
                 '(副圈)' if self.d_issecond else '',
                 self.ehealth,'(副圈)' if self.e_issecond else '')
        # if self.challenger:
        #     summary += '\n' + '{}正在挑战boss'.format(self.challenger)
        if self.info:
            summary = self.info + '\n' + summary
        return summary


@dataclass
class BossChallenge:
    date: Pcr_date
    time: Pcr_time
    cycle: int
    num: int
    health_ramain: int
    damage: int
    is_continue: bool
    team: Optional[List[int]]
    message: Optional[str]


ClanBattleReport = NewType(
    'ClanBattleReport',
    List[Dict[str, Any]]
)
