import asyncio
import logging
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urljoin

import peewee
from aiocqhttp.api import Api
from apscheduler.triggers.cron import CronTrigger
from quart import (Quart, jsonify, make_response, redirect, request, session,
                   url_for)

from ..templating import render_template
from ..web_util import async_cached_func
from ..ybdata import (Clan_challenge, Clan_group, Clan_member, Clan_subscribe,Clan_subscribe_new,Clan_subscribe_layv,
                      User)
from .exception import (
    ClanBattleError, GroupError, GroupNotExist, InputError, UserError,
    UserNotInGroup)
from .typing import BossStatus, ClanBattleReport, Groupid, Pcr_date, QQid
from .util import atqq, pcr_datetime, pcr_timestamp, timed_cached_func

_logger = logging.getLogger(__name__)


class ClanBattle:
    Passive = True
    Active = True
    Request = True

    Commands = {
        '创建': 1,
        '加入': 2,
        '状态': 3,
        '进度': 3,
        '报告': 3,
        '刀1': 4,
        '刀2': 4,
        '刀3': 4,
        '刀4': 4,
        '刀5': 4,
        '报刀': 4,
        '尾1': 5,
        '尾2': 5,
        '尾3': 5,
        '尾4': 5,
        '尾5': 5,
        '尾刀': 5,
        '撤销': 6,
        '修正': 7,
        '修改': 7,
        '选择': 8,
        '切换': 8,
        '查刀': 9,
        '预约': 10,
        '挂树': 11,
        # '申请': 12,
        # '锁定': 12,
        '取消': 13,
        # '解锁': 14,
        '面板': 15,
        '后台': 15,
        'sl': 16,
        'SL': 16,
        '查树': 20,
        '树1': 21,
        '树2': 22,
        '树3': 23,
        '树4': 24,
        '树5': 25,
        '查进':30,
        '查1': 31,
        '查2': 32,
        '查3': 33,
        '查4': 34,
        '查5': 35,
        '留言':36,
        '查尾':97,
        '进刀':99,
        '进1':99,
        '进2':99,
        '进3':99,
        '进4':99,
        '进5':99,
    }

    Server = {
        '日': 'jp',
        '台': 'tw',
        '韩': 'kr',
        '国': 'cn',
    }

    def __init__(self,
                 glo_setting: Dict[str, Any],
                 bot_api: Api,
                 *args, **kwargs):
        self.setting = glo_setting
        self.bossinfo = glo_setting['boss']
        self.api = bot_api

        # log
        if not os.path.exists(os.path.join(glo_setting['dirname'], 'log')):
            os.mkdir(os.path.join(glo_setting['dirname'], 'log'))

        formater = logging.Formatter(
            '[%(asctime)s] %(levelname)s: %(message)s')
        filehandler = logging.FileHandler(
            os.path.join(glo_setting['dirname'], 'log', '公会战日志.log'),
            encoding='utf-8',
        )
        filehandler.setFormatter(formater)
        consolehandler = logging.StreamHandler()
        consolehandler.setFormatter(formater)
        _logger.addHandler(filehandler)
        _logger.addHandler(consolehandler)
        _logger.setLevel(logging.INFO)

        # data initialize
        self._boss_status: Dict[str, asyncio.Future] = {}

        for group in Clan_group.select().where(
            Clan_group.deleted == False,
        ):
            self._boss_status[group.group_id] = (
                asyncio.get_event_loop().create_future()
            )

        # super-admin initialize
        User.update({User.authority_group: 100}).where(
            User.authority_group == 1
        ).execute()
        User.update({User.authority_group: 1}).where(
            User.qqid.in_(self.setting['super-admin'])
        ).execute()

    def _level_by_cycle(self, cycle, *, game_server=None):
        if cycle <= 3:
            return 0  # 1~3 周目：一阶段
        if cycle <= 10:
            return 1  # 4~10 周目：二阶段
        server_total = len(self.setting['boss'][game_server])
        if cycle <= 30 or server_total <= 3:
            return 2  # 11~34 周目：三阶段
        if cycle <= 40 or server_total <= 4:
            return 3  # 35~44 周目：四阶段
        return 4  # 45~ 周目：五阶段
    
    def switch_boss(self,bossnum: int):
        if bossnum==1:
            return 'a_health'
        if bossnum==2:
            return 'b_health'
        if bossnum==3:
            return 'c_health'
        if bossnum==4:
            return 'd_health'
        if bossnum==5:
            return 'e_health'
    
    @timed_cached_func(128, 3600, ignore_self=True)
    def _get_nickname_by_qqid(self, qqid) -> Union[str, None]:
        user = User.get_or_create(qqid=qqid)[0]
        if user.nickname is None:
            asyncio.ensure_future(self._update_user_nickname_async(
                qqid=qqid,
                group_id=None,
            ))
        return user.nickname or str(qqid)

    def _get_group_previous_challenge(self, group: Clan_group):
        Clan_challenge_alias = Clan_challenge.alias()
        query = Clan_challenge.select().where(
            Clan_challenge.cid == Clan_challenge_alias.select(
                peewee.fn.MAX(Clan_challenge_alias.cid)
            ).where(
                Clan_challenge_alias.gid == group.group_id,
                Clan_challenge_alias.bid == group.battle_id,
            )
        )
        try:
            return query.get()
        except peewee.DoesNotExist:
            return None
            
    def _get_group_previous_challenge_boss(self, group: Clan_group, boss_num):
        Clan_challenge_alias = Clan_challenge.alias()
        query = Clan_challenge.select().where(
            Clan_challenge.cid == Clan_challenge_alias.select(
                peewee.fn.MAX(Clan_challenge_alias.cid)
            ).where(
                Clan_challenge_alias.gid == group.group_id,
                Clan_challenge_alias.bid == group.battle_id,
                Clan_challenge_alias.boss_num == boss_num,
            )
        )
        try:
            return query.get().boss_health_ramain, query.get().is_second
        except peewee.DoesNotExist:
            return None
    
    def _get_group_previous_challenge_layv(self, group: Clan_group, boss_num):
        Clan_challenge_alias = Clan_challenge.alias()
        query = Clan_challenge.select().where(
            Clan_challenge.cid == Clan_challenge_alias.select(
                peewee.fn.MAX(Clan_challenge_alias.cid)
            ).where(
                Clan_challenge_alias.gid == group.group_id,
                Clan_challenge_alias.bid == group.battle_id,
                Clan_challenge_alias.boss_num == boss_num,
            )
        )
        try:
            return query.get().boss_health_ramain , query.get().is_second
        except peewee.DoesNotExist:
            return None

    async def _update_group_list_async(self):
        try:
            group_list = await self.api.get_group_list()
        except Exception as e:
            _logger.exception('获取群列表错误'+str(e))
            return False
        for group_info in group_list:
            group = Clan_group.get_or_none(
                group_id=group_info['group_id'],
            )
            if group is None:
                continue
            group.group_name = group_info['group_name']
            group.save()
        return True

    @async_cached_func(16)
    async def _fetch_member_list_async(self, group_id):
        try:
            group_member_list = await self.api.get_group_member_list(group_id=group_id)
        except Exception as e:
            _logger.exception('获取群成员列表错误'+str(type(e))+str(e))
            asyncio.ensure_future(self.api.send_group_msg(
                group_id=group_id, message='获取群成员错误，这可能是缓存问题，请重启酷Q后再试'))
            return []
        return group_member_list

    async def _update_all_group_members_async(self, group_id):
        group_member_list = await self._fetch_member_list_async(group_id)
        for member in group_member_list:
            user = User.get_or_create(qqid=member['user_id'])[0]
            membership = Clan_member.get_or_create(
                group_id=group_id, qqid=member['user_id'])[0]
            user.nickname = member.get('card') or member['nickname']
            user.clan_group_id = group_id
            if user.authority_group >= 10:
                user.authority_group = (
                    100 if member['role'] == 'member' else 10)
                membership.role = user.authority_group
            user.save()
            membership.save()

        # refresh member list
        self.get_member_list(group_id, nocache=True)

    async def _update_user_nickname_async(self, qqid, group_id=None):
        try:
            user = User.get_or_create(qqid=qqid)[0]
            if group_id is None:
                userinfo = await self.api.get_stranger_info(user_id=qqid)
                user.nickname = userinfo['nickname']
            else:
                userinfo = await self.api.get_group_member_info(
                    group_id=group_id, user_id=qqid)
                user.nickname = userinfo['card'] or userinfo['nickname']
            user.save()

            # refresh
            if user.nickname is not None:
                self._get_nickname_by_qqid(qqid, nocache=True)
        except Exception as e:
            _logger.exception(e)

    def _boss_data_dict(self, group: Clan_group) -> Dict[str, Any]:
        return {
            'cycle': group.boss_cycle,
            'num': 1,
            #layv 这里好像是网页数据？
            'a_health': group.a_health, 
            'b_health': group.b_health, 
            'c_health': group.c_health, 
            'd_health': group.d_health, 
            'e_health': group.e_health,
            'a_issecond': group.a_issecond, 
            'b_issecond': group.b_issecond, 
            'c_issecond': group.c_issecond, 
            'd_issecond': group.d_issecond, 
            'e_issecond': group.e_issecond,
            'level': self._level_by_cycle(group.boss_cycle, game_server=group.game_server),
            'challenger': group.challenging_member_qq_id,
            'lock_type': group.boss_lock_type,
            'challenging_comment': group.challenging_comment,
            'full_a_health': (self.bossinfo[group.game_server][self._level_by_cycle(group.boss_cycle, game_server=group.game_server)][0]),
            'full_b_health': (self.bossinfo[group.game_server][self._level_by_cycle(group.boss_cycle, game_server=group.game_server)][1]),
            'full_c_health': (self.bossinfo[group.game_server][self._level_by_cycle(group.boss_cycle, game_server=group.game_server)][2]),
            'full_d_health': (self.bossinfo[group.game_server][self._level_by_cycle(group.boss_cycle, game_server=group.game_server)][3]),
            'full_e_health': (self.bossinfo[group.game_server][self._level_by_cycle(group.boss_cycle, game_server=group.game_server)][4]),
        }

    def creat_group(self, group_id: Groupid, game_server, group_name=None) -> None:
        """
        create a group for clan-battle

        Args:
            group_id: group id
            game_server: name of game server("jp" "tw" "cn" "kr")
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            group = Clan_group.create(
                group_id=group_id,
                group_name=group_name,
                game_server=game_server,
                boss_health=self.bossinfo[game_server][0][0],
            )
        elif group.deleted:
            group.deleted = False
            group.game_server = game_server
            group.save()
        else:
            raise GroupError('群已经存在')
        self._boss_status[group_id] = asyncio.get_event_loop().create_future()

        # refresh group list
        asyncio.ensure_future(self._update_group_list_async())

    async def bind_group(self, group_id: Groupid, qqid: QQid, nickname: str):
        """
        set user's default group

        Args:
            group_id: group id
            qqid: qqid
            nickname: displayed name
        """
        user = User.get_or_create(qqid=qqid)[0]
        user.clan_group_id = group_id
        user.nickname = nickname
        user.deleted = False
        try:
            groupmember = await self.api.get_group_member_info(
                group_id=group_id, user_id=qqid)
            role = 100 if groupmember['role'] == 'member' else 10
        except Exception as e:
            _logger.exception(e)
            role = 100
        membership = Clan_member.get_or_create(
            group_id=group_id,
            qqid=qqid,
            defaults={
                'role': role,
            }
        )[0]
        user.save()

        # refresh
        self.get_member_list(group_id, nocache=True)
        if nickname is None:
            asyncio.ensure_future(self._update_user_nickname_async(
                qqid=qqid,
                group_id=group_id,
            ))

        return membership

    def drop_member(self, group_id: Groupid, member_list: List[QQid]):
        """
        delete members from group member list

        permission should be checked before this function is called.

        Args:
            group_id: group id
            member_list: a list of qqid to delete
        """
        delete_count = Clan_member.delete().where(
            Clan_member.group_id == group_id,
            Clan_member.qqid.in_(member_list)
        ).execute()

        for user_id in member_list:
            user = User.get_or_none(qqid=user_id)
            if user is not None:
                user.clan_group_id = None
                user.save()

        # refresh member list
        self.get_member_list(group_id, nocache=True)
        return delete_count

    def boss_status_summary(self, group_id: Groupid) -> str:
        """
        get a summary of boss status

        Args:
            group_id: group id
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        boss_summary = (
            f'现在{group.boss_cycle}周目\n'
        )
        now_level = self._level_by_cycle(group.boss_cycle, game_server=group.game_server)
        group.a_issecond = '（副圈）' if group.a_issecond else ''
        group.b_issecond = '（副圈）' if group.b_issecond else ''
        group.c_issecond = '（副圈）' if group.c_issecond else ''
        group.d_issecond = '（副圈）' if group.d_issecond else ''
        group.e_issecond = '（副圈）' if group.e_issecond else ''
        boss_a = (
            f'----------------------------\n'
            f'1王[{group.a_health}/{self.bossinfo[group.game_server][now_level][0]}] {group.a_issecond}\n'
            f'{self.boss_status_layv(group_id,1)}' 
            if group.a_health>0 else ''
        )
        boss_b = (
            f'----------------------------\n'
            f'2王[{group.b_health}/{self.bossinfo[group.game_server][now_level][1]}] {group.b_issecond}\n'
            f'{self.boss_status_layv(group_id,2)}' 
            if group.b_health>0 else ''
        )
        boss_c = (
            f'----------------------------\n'
            f'3王[{group.c_health}/{self.bossinfo[group.game_server][now_level][2]}] {group.c_issecond}\n'
            f'{self.boss_status_layv(group_id,3)}' 
            if group.c_health>0 else ''
        )
        boss_d = (
            f'----------------------------\n'
            f'4王[{group.d_health}/{self.bossinfo[group.game_server][now_level][3]}] {group.d_issecond}\n'
            f'{self.boss_status_layv(group_id,4)}' 
            if group.d_health>0 else ''
        )
        boss_e = (
            f'----------------------------\n'
            f'5王[{group.e_health}/{self.bossinfo[group.game_server][now_level][4]}] {group.e_issecond}\n'
            f'{self.boss_status_layv(group_id,5)}' 
            if group.e_health>0 else ''
        )
        return boss_summary+boss_a+boss_b+boss_c+boss_d+boss_e
    
    def boss_status_layv(self,group_id: Groupid,boss_num)->str:
        lens = len(self.get_subscribe_list_layv(group_id,boss_num))
        lens2 = len(self.get_subscribe_list(group_id,boss_num))
        res = ''
        if lens > 0:
            res +=  f'进🔪人数：{lens}\n'
        if lens2 > 0:
            res +=  f'挂树人数：{lens2}\n'
        return res

    def challenge(self,
                  group_id: Groupid,
                  qqid: QQid,
                  defeat: bool,
                  bossnum: Optional[int] = None,
                  damage: Optional[int] = None,
                  behalfed: Optional[QQid] = None,
                  is_continue: Optional[bool] = False,
                  continue_num:Optional[int] = 0,
                  *,
                  extra_msg: Optional[str] = None,
                  previous_day=False,
                  ) -> BossStatus:
        """
        record a non-defeat challenge to boss

        Args:
            group_id: group id
            qqid: qqid of member who do the record
            damage: the damage dealt to boss
            behalfed: the real member who did the challenge
        """
        if (not defeat) and (damage is None):
            raise InputError('未击败boss需要提供伤害值')
        if (not defeat) and (damage < 0):
            raise InputError('伤害不可以是负数')
        if (not bossnum):
            raise InputError('请输入王编号')
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        if bossnum==1:
            boss_health =  group.a_health
            damage = damage if damage!=None else group.a_health
            group.a_health = group.a_health - damage
            is_second = group.a_issecond
        if bossnum==2:
            boss_health = group.b_health
            damage = damage if damage!=None else group.b_health
            group.b_health = group.b_health - damage
            is_second = group.b_issecond
        if bossnum==3:
            boss_health = group.c_health
            damage = damage if damage!=None else group.c_health
            group.c_health = group.c_health - damage
            is_second = group.c_issecond
        if bossnum==4:
            boss_health =  group.d_health
            damage = damage if damage!=None else group.d_health
            group.d_health = group.d_health - damage
            is_second = group.d_issecond
        if bossnum==5:
            boss_health =  group.e_health
            damage = damage if damage!=None else group.e_health
            group.e_health = group.e_health - damage
            is_second = group.e_issecond
        if damage < 0 or boss_health==0:
            raise InputError(str(bossnum)+'王已经盒了')
        if (not defeat) and (damage >= boss_health):
            raise InputError('伤害超出剩余血量，如击败请使用尾🔪')
        behalf = None
        if behalfed is not None:
            behalf = qqid
            qqid = behalfed
        user = User.get_or_create(
            qqid=qqid,
            defaults={
                'clan_group_id': group_id,
            }
        )[0]
        is_member = Clan_member.get_or_none(
            group_id=group_id, qqid=qqid)
        if not is_member:
            raise UserNotInGroup
        d, t = pcr_datetime(area=group.game_server)
        if previous_day:
            today_count = Clan_challenge.select().where(
                Clan_challenge.gid == group_id,
                Clan_challenge.bid == group.battle_id,
                Clan_challenge.challenge_pcrdate == d,
            ).count()
            if today_count != 0:
                raise GroupError('今日报🔪记录不为空，无法将记录添加到昨日')
            d -= 1
            t += 86400
        challenges = Clan_challenge.select().where(
            Clan_challenge.gid == group_id,
            Clan_challenge.qqid == qqid,
            Clan_challenge.bid == group.battle_id,
            Clan_challenge.challenge_pcrdate == d,
        ).order_by(Clan_challenge.cid)
        challenges = list(challenges)
        finished = sum(bool(c.boss_health_ramain or c.is_continue)
                       for c in challenges)
        if finished >= 3:
            if previous_day:
                raise InputError('昨日上报次数已达到3次')
            raise InputError('今日上报次数已达到3次')
            
        # 可出的补偿
        can_continue = sum(bool(c.boss_health_ramain==0 and c.is_continue==False)
                           for c in challenges)
        # 非补偿🔪次数
        not_continue = sum(bool(c.is_continue==False)
                           for c in challenges)
        # 已出的补偿
        user_continue = sum(bool(c.is_continue)
                            for c in challenges)   
        
        if not_continue>=3:
            is_continue = True
        
        #如果是补偿🔪
        if is_continue:
            if can_continue <= user_continue:
                raise InputError('有补🔪🐎¿')
                
        is_member.last_message = extra_msg
        is_member.save()
        
        if defeat:
            boss_health_ramain = 0
            challenge_damage = boss_health
        else:
            boss_health_ramain = boss_health-damage
            challenge_damage = damage
        
        Clan_subscribe_layv.delete().where(
            Clan_subscribe_layv.gid == group_id,
            Clan_subscribe_layv.qqid == qqid,
        ).execute()
        
        Clan_subscribe.delete().where(
            Clan_subscribe.gid == group_id,
            Clan_subscribe.qqid == qqid,
        ).execute()
        
        Clan_subscribe_new.delete().where(
            Clan_subscribe_new.gid == group_id,
            Clan_subscribe_new.qqid == qqid,
            Clan_subscribe_new.subscribe_item == bossnum,
        ).execute()
        
        #确定当前尾刀编号
        con_num = 0
        if is_continue:
            con_num = continue_num
            if con_num>0:
                chall = Clan_challenge.select().where(
                    Clan_challenge.gid==group_id,
                    Clan_challenge.qqid==user.qqid,
                    Clan_challenge.bid==group.battle_id,
                    Clan_challenge.is_continue==False,
                    Clan_challenge.is_used==False,
                    Clan_challenge.boss_health_ramain == 0,
                    Clan_challenge.challenge_pcrdate == d,
                    Clan_challenge.continue_num == con_num,
                )
                if chall:
                    for ttt in chall:
                        ttt.is_used = True
                        ttt.save()
                else:
                    raise InputError('该补偿已使用，或该编号无补偿')
            else:
                chall = Clan_challenge.select().where(
                    Clan_challenge.gid==group_id,
                    Clan_challenge.qqid==user.qqid,
                    Clan_challenge.bid==group.battle_id,
                    Clan_challenge.is_continue==False,
                    Clan_challenge.is_used==False,
                    Clan_challenge.boss_health_ramain==0,
                    Clan_challenge.challenge_pcrdate == d,
                    Clan_challenge.continue_num>0,
                )
                for ttt in chall:
                    con_num = ttt.continue_num
                    ttt.is_used = True
                    ttt.save()
                    break
        elif con_num==0:
            con_num = sum(bool(c.is_continue is not True)
                       for c in challenges) + 1
            
        
        challenge = Clan_challenge.create(
            gid=group_id,
            qqid=user.qqid,
            bid=group.battle_id,
            challenge_pcrdate=d,
            challenge_pcrtime=t,
            boss_cycle=group.boss_cycle,
            boss_num=bossnum,
            boss_health_ramain=boss_health_ramain,
            challenge_damage=challenge_damage,
            continue_num = con_num,
            is_continue=is_continue,
            is_used = False,
            is_second = is_second,#这🔪是否🔪副圈
            message=extra_msg,
            behalf=behalf,
        )
        
        if defeat:
            # 如果所有boss都死了，开新周目
            group = self.layv_defeat_boss(group)
        # 如果当前正在挑战，则取消挑战
        if user.qqid == group.challenging_member_qq_id:
            #layv 清理该用户挑战状态
            group.challenging_member_qq_id = None
        challenge.save()
        group.save()

        nik = user.nickname or user.qqid
        #layv 准备清理状态
        if defeat:
            msg = '{}造成了{:,}点伤害，击败了boss\n（今日第{}🔪，{}）'.format(
                nik, damage, finished+can_continue-user_continue+1, '补偿🔪' if is_continue else '尾'
            )
            group.challenging_member_qq_id = None
            group.save()
        else:
            msg = '{}造成了{:,}点伤害\n（今日第{}🔪，{}）'.format(
                nik, damage, finished+can_continue-user_continue+1, '补偿🔪' if is_continue else '完整'
            )
        if is_continue:
            msg += '使用的补偿'
        if con_num>0:
            msg += '编号'+str(con_num)
        
        status = BossStatus(
            group.boss_cycle,
            group.a_health,
            group.b_health,
            group.c_health,
            group.d_health,
            group.e_health,
            group.a_issecond,
            group.b_issecond,
            group.c_issecond,
            group.d_issecond,
            group.e_issecond,
            0,
            msg,
        )
        self._boss_status[group_id].set_result(
            (self._boss_data_dict(group), msg)
        )
        self._boss_status[group_id] = asyncio.get_event_loop().create_future()

        if defeat:
            self.notify_subscribe(group_id, bossnum,True)
            self.notify_subscribe_new(group_id,True)
            self.notify_subscribe_layv(group_id, bossnum,True)

        return status
    
    def layv_defeat_boss(self,group):
        # 判断当前周目是否是转阶段前一周目,true即为马上转阶段，false就不是
        now_level = self._level_by_cycle(group.boss_cycle, game_server=group.game_server)
        next_level = self._level_by_cycle(group.boss_cycle+1, game_server=group.game_server)
        is_change_level = ( now_level!=next_level )
        is_second = False
        if is_change_level:
            #转阶段专用，只有转阶段才会出现所有boss均为0血这种情况
            if group.a_health == group.b_health == group.c_health == group.d_health == group.e_health == 0:
                    group.boss_cycle += 1
                    group.a_health = (
                        self.bossinfo[group.game_server]
                        [next_level]
                        [0])
                    group.b_health = (
                        self.bossinfo[group.game_server]
                        [next_level]
                        [1])
                    group.c_health = (
                        self.bossinfo[group.game_server]
                        [next_level]
                        [2])
                    group.d_health = (
                        self.bossinfo[group.game_server]
                        [next_level]
                        [3])
                    group.e_health = (
                        self.bossinfo[group.game_server]
                        [next_level]
                        [4])
            group.a_issecond = group.b_issecond = group.c_issecond = group.d_issecond = group.e_issecond = False
        else:
            #如果x王没血了,并且当前x王不在副圈，进入副圈的该王
            if group.a_health == 0 and group.a_issecond==False:
                group.a_health = (
                        self.bossinfo[group.game_server]
                        [now_level]
                        [0])
                is_second = group.a_issecond=True
            if group.b_health == 0 and group.b_issecond==False:
                group.b_health = (
                        self.bossinfo[group.game_server]
                        [now_level]
                        [1])
                is_second = group.b_issecond=True
            if group.c_health == 0 and group.c_issecond==False:
                group.c_health = (
                        self.bossinfo[group.game_server]
                        [now_level]
                        [2])
                is_second = group.c_issecond =True
            if group.d_health == 0 and group.d_issecond==False:
                group.d_health = (
                        self.bossinfo[group.game_server]
                        [now_level]
                        [3])
                is_second = group.d_issecond=True
            if group.e_health == 0 and group.e_issecond==False:
                group.e_health = (
                        self.bossinfo[group.game_server]
                        [now_level]
                        [4])
                is_second = group.e_issecond=True
            #如果所有boss都进入副圈
            if group.a_issecond == group.b_issecond == group.c_issecond == group.d_issecond == group.e_issecond == True:
                group.boss_cycle +=1
                group.a_issecond = group.b_issecond = group.c_issecond = group.d_issecond = group.e_issecond = False
                new_change_level = (next_level == self._level_by_cycle(group.boss_cycle+1, game_server=group.game_server))
                #如果新周目不是转阶端前周目(比如新周目是第三周，第十周目)
                if group.a_health == 0:
                    group.a_health = (
                            self.bossinfo[group.game_server]
                            [now_level]
                            [0]) if new_change_level else 0
                    group.a_issecond = True
                if group.b_health == 0:
                    group.b_health = (
                            self.bossinfo[group.game_server]
                            [now_level]
                            [1]) if new_change_level else 0
                    group.b_issecond = True
                if group.c_health == 0:
                    group.c_health = (
                            self.bossinfo[group.game_server]
                            [now_level]
                            [2]) if new_change_level else 0
                    group.c_issecond = True
                if group.d_health == 0:
                    group.d_health = (
                            self.bossinfo[group.game_server]
                            [now_level]
                            [3]) if new_change_level else 0
                    group.d_issecond = True
                if group.e_health == 0:
                    group.e_health = (
                            self.bossinfo[group.game_server]
                            [now_level]
                            [4]) if new_change_level else 0
                    group.e_issecond = True
                
        return group
    
    def undo(self, group_id: Groupid, qqid: QQid) -> BossStatus:
        """
        rollback last challenge record.

        Args:
            group_id: group id
            qqid: qqid of member who ask for the undo
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        user = User.get_or_create(
            qqid=qqid,
            defaults={
                'clan_group_id': group_id,
            }
        )[0]
        last_challenge = self._get_group_previous_challenge(group)
        if last_challenge is None:
            raise GroupError('本群无出🔪记录')
        if (last_challenge.qqid != qqid) and (user.authority_group >= 100):
            raise UserError('无权撤销')
        
        if group.boss_cycle!=last_challenge.boss_cycle:
            now_level = self._level_by_cycle(group.boss_cycle, game_server=group.game_server)
            before_level = self._level_by_cycle(last_challenge.boss_cycle, game_server=group.game_server)
            bosshealth = self.bossinfo[group.game_server][before_level]
            if now_level!=before_level:
                group.a_health = 0
                group.b_health = 0
                group.c_health = 0
                group.d_health = 0
                group.e_health = 0
            else:
                if group.a_issecond:
                    group.a_health = 0
                    group.a_issecond = True
                else:
                    group.a_issecond = True
                    group.a_health = bosshealth[0]
                    
                if group.b_issecond:
                    group.b_health = 0
                    group.b_issecond = True
                else:
                    group.b_issecond = True
                    group.b_health = bosshealth[1]
                    
                if group.c_issecond:
                    group.c_health = 0
                    group.c_issecond = True
                else:
                    group.c_issecond = True
                    group.c_health = bosshealth[2]
                    
                if group.d_issecond:
                    group.d_health = 0
                    group.d_issecond = True
                else:
                    group.d_issecond = True
                    group.d_health = bosshealth[3]
                    
                if group.e_issecond:
                    group.e_health = 0
                    group.e_issecond = True
                else:
                    group.e_issecond = True
                    group.e_health = bosshealth[4]
                
            
        group.boss_cycle = last_challenge.boss_cycle
        if last_challenge.boss_num==1:
            group.a_health = (last_challenge.boss_health_ramain + last_challenge.challenge_damage)
            group.a_issecond = last_challenge.is_second
        if last_challenge.boss_num==2:
            group.b_health = (last_challenge.boss_health_ramain + last_challenge.challenge_damage)
            group.b_issecond = last_challenge.is_second
        if last_challenge.boss_num==3:
            group.c_health = (last_challenge.boss_health_ramain + last_challenge.challenge_damage)
            group.c_issecond = last_challenge.is_second
        if last_challenge.boss_num==4:
            group.d_health = (last_challenge.boss_health_ramain + last_challenge.challenge_damage)
            group.d_issecond = last_challenge.is_second
        if last_challenge.boss_num==5:
            group.e_health = (last_challenge.boss_health_ramain + last_challenge.challenge_damage)
            group.e_issecond = last_challenge.is_second
        last_challenge.delete_instance()
        group.save()

        nik = self._get_nickname_by_qqid(last_challenge.qqid)
        
        #重置尾刀使用状态
        d, t = pcr_datetime(area=group.game_server)
        chall = Clan_challenge.select().where(
            Clan_challenge.gid==group_id,
            Clan_challenge.qqid==last_challenge.qqid,
            Clan_challenge.bid==group.battle_id,
            Clan_challenge.is_continue==False,
            Clan_challenge.is_used==True,
            Clan_challenge.boss_health_ramain==0,
            Clan_challenge.challenge_pcrdate == d,
            Clan_challenge.continue_num==last_challenge.continue_num,
        )
        if chall:
            for ttt in chall:
                ttt.is_used = False
                ttt.save()
        
        
        status = BossStatus(
            group.boss_cycle,
            group.a_health,
            group.b_health,
            group.c_health,
            group.d_health,
            group.e_health,
            group.a_issecond,
            group.b_issecond,
            group.c_issecond,
            group.d_issecond,
            group.e_issecond,
            0,
            f'{nik}的出🔪记录已被撤销',
        )
        self._boss_status[group_id].set_result(
            (self._boss_data_dict(group), status.info)
        )
        self._boss_status[group_id] = asyncio.get_event_loop().create_future()
        return status
        
    def commit(self, group_id: Groupid, qqid: QQid,boss_num,msg) -> str:
        """
        rollback last challenge record.

        Args:
            group_id: group id
            qqid: qqid of member who ask for the undo
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        user = User.get_or_create(
            qqid=qqid,
            defaults={
                'clan_group_id': group_id,
            }
        )[0]
        if user.authority_group >= 100:
            raise UserError('无权留言')
        if msg is not None:
            msg += '('+self._get_nickname_by_qqid(qqid)+'留言)'
        if boss_num == 1:
            group.a_commit = msg
        if boss_num == 2:
            group.b_commit = msg
        if boss_num == 3:
            group.c_commit = msg
        if boss_num == 4:
            group.d_commit = msg
        if boss_num == 5:
            group.e_commit = msg
        group.save()
        return '留言成功'

    def modify(self, group_id: Groupid, cycle=None, a_health=None, b_health=None, c_health=None, d_health=None, e_health=None,a_issecond=False,b_issecond=False,c_issecond=False,d_issecond=False,e_issecond=False):
        """
        modify status of boss.

        permission should be checked before this function is called.

        Args:
            group_id: group id
            cycle: new number of clan-battle cycle
            boss_num: new number of boss
            boss_health: new value of boss health
        """
        if cycle and cycle < 1:
            raise InputError('周目数不能为负')
        if a_health and a_health < 0:
            raise InputError('boss生命值不能为负')
        if b_health and b_health < 0:
            raise InputError('boss生命值不能为负')
        if c_health and c_health < 0:
            raise InputError('boss生命值不能为负')
        if d_health and d_health < 0:
            raise InputError('boss生命值不能为负')
        if e_health and e_health < 0:
            raise InputError('boss生命值不能为负')
        if a_health == b_health == c_health == d_health == e_health == 0:
            raise InputError('boss生命值不能全tm是0')
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        if cycle is not None:
            group.boss_cycle = cycle
        group.a_health = a_health
        group.b_health = b_health
        group.c_health = c_health
        group.d_health = d_health
        group.e_health = e_health
        group.e_issecond = e_issecond
        group.a_issecond = a_issecond
        group.b_issecond = b_issecond
        group.c_issecond = c_issecond
        group.d_issecond = d_issecond
        group.save()

        status = BossStatus(
            group.boss_cycle,
            group.a_health,
            group.b_health,
            group.c_health,
            group.d_health,
            group.e_health,
            group.a_issecond,
            group.b_issecond,
            group.c_issecond,
            group.d_issecond,
            group.e_issecond,
            0,
            'boss状态已修改',
        )
        self._boss_status[group_id].set_result(
            (self._boss_data_dict(group), status.info)
        )
        self._boss_status[group_id] = asyncio.get_event_loop().create_future()
        return status

    def change_game_server(self, group_id: Groupid, game_server):
        """
        change game server.

        permission should be checked before this function is called.

        Args:
            group_id: group id
            game_server: name of game server("jp" "tw" "cn" "kr")
        """
        if game_server not in ("jp", "tw", "cn", "kr"):
            raise InputError(f'不存在{game_server}游戏服务器')
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        group.game_server = game_server
        group.save()

    def get_data_slot_record_count(self, group_id: Groupid):
        """
        creat new new_data_slot for challenge data and reset boss status.

        challenge data should be backuped and comfirm and
        permission should be checked before this function is called.

        Args:
            group_id: group id
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        counts = []
        for c in Clan_challenge.select(
            Clan_challenge.bid,
            peewee.fn.COUNT(Clan_challenge.cid).alias('record_count'),
        ).where(
            Clan_challenge.gid == group_id
        ).group_by(
            Clan_challenge.bid,
        ):
            counts.append({
                'battle_id': c.bid,
                'record_count': c.record_count,
            })
        return counts

    # def new_data_slot(self, group_id: Groupid):
    #     """
    #     creat new new_data_slot for challenge data and reset boss status.

    #     challenge data should be backuped and comfirm and
    #     permission should be checked before this function is called.

    #     Args:
    #         group_id: group id
    #     """
    #     group = Clan_group.get_or_none(group_id=group_id)
    #     if group is None:
    #         raise GroupNotExist
    #     group.boss_cycle = 1
    #     group.boss_num = 1
    #     group.boss_health = self.bossinfo[group.game_server][0][0]
    #     group.battle_id += 1
    #     group.save()
    #     Clan_subscribe.delete().where(
    #         Clan_subscribe.gid == group_id,
    #     ).execute()

    def clear_data_slot(self, group_id: Groupid, battle_id: Optional[int] = None):
        """
        clear data_slot for challenge data and reset boss status.

        challenge data should be backuped and comfirm and
        permission should be checked before this function is called.

        Args:
            group_id: group id
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        group.boss_cycle = 1
        group.boss_num = 1
        group.a_health = self.bossinfo[group.game_server][0][0]
        group.b_health = self.bossinfo[group.game_server][0][1]
        group.c_health = self.bossinfo[group.game_server][0][2]
        group.d_health = self.bossinfo[group.game_server][0][3]
        group.e_health = self.bossinfo[group.game_server][0][4]
        group.a_commit = None
        group.b_commit = None
        group.c_commit = None
        group.d_commit = None
        group.e_commit = None
        group.a_issecond = False
        group.b_issecond = False
        group.c_issecond = False
        group.d_issecond = False
        group.e_issecond = False
        #layv 清理所有挑战者名单
        group.challenging_member_qq_id = None
        group.save()
        if battle_id is None:
            battle_id = group.battle_id
        Clan_challenge.delete().where(
            Clan_challenge.gid == group_id,
            Clan_challenge.bid == battle_id,
        ).execute()
        Clan_subscribe.delete().where(
            Clan_subscribe.gid == group_id,
        ).execute()
        Clan_subscribe_layv.delete().where(
            Clan_subscribe_layv.gid == group_id,
        ).execute()
        _logger.info(f'群{group_id}的{battle_id}号存档已清空')

    def switch_data_slot(self, group_id: Groupid, battle_id: int):
        """
        switch data_slot for challenge data and reset boss status.

        challenge data should be backuped and comfirm and
        permission should be checked before this function is called.

        Args:
            group_id: group id
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        group.battle_id = battle_id
        last_challenge = self._get_group_previous_challenge(group)
        if last_challenge is None:
            group.boss_cycle = 1
            group.a_health = self.bossinfo[group.game_server][0][0]
            group.b_health = self.bossinfo[group.game_server][0][1]
            group.c_health = self.bossinfo[group.game_server][0][2]
            group.d_health = self.bossinfo[group.game_server][0][3]
            group.e_health = self.bossinfo[group.game_server][0][4]
            group.a_issecond = group.b_issecond = group.c_issecond = group.d_issecond = group.e_issecond = False
        else:
            group.boss_cycle = last_challenge.boss_cycle
            group.a_health,group.a_issecond = self._get_group_previous_challenge_layv(group,boss_num = 1)
            group.b_health,group.b_issecond = self._get_group_previous_challenge_layv(group,boss_num = 2)
            group.c_health,group.c_issecond = self._get_group_previous_challenge_layv(group,boss_num = 3)
            group.d_health,group.d_issecond = self._get_group_previous_challenge_layv(group,boss_num = 4)
            group.e_health,group.e_issecond = self._get_group_previous_challenge_layv(group,boss_num = 5)
            if group.a_health == None or (group.a_health==0 and group.a_issecond==False):
                group.a_health = self.bossinfo[group.game_server][group.boss_cycle][0]
                group.a_issecond = True if group.a_health==0 else False
            if group.b_health == None or (group.b_health==0 and group.b_issecond==False):
                group.b_health = self.bossinfo[group.game_server][group.boss_cycle][1]
                group.b_issecond = True if group.b_health==0 else False
            if group.c_health == None or (group.c_health==0 and group.c_issecond==False):
                group.c_health = self.bossinfo[group.game_server][group.boss_cycle][2]
                group.c_issecond = True if group.c_health==0 else False
            if group.d_health == None or (group.d_health==0 and group.d_issecond==False):
                group.d_health = self.bossinfo[group.game_server][group.boss_cycle][3]
                group.d_issecond = True if group.d_health==0 else False
            if group.e_health == None or (group.e_health==0 and group.e_issecond==False):
                group.e_health = self.bossinfo[group.game_server][group.boss_cycle][4]
                group.e_issecond = True if group.e_health==0 else False
            if group.a_health == group.b_health == group.c_health == group.d_health == group.e_health == 0:
                group.boss_cycle += 1
                group.a_health = self.bossinfo[group.game_server][group.boss_cycle][0]
                group.b_health = self.bossinfo[group.game_server][group.boss_cycle][1]
                group.c_health = self.bossinfo[group.game_server][group.boss_cycle][2]
                group.d_health = self.bossinfo[group.game_server][group.boss_cycle][3]
                group.e_health = self.bossinfo[group.game_server][group.boss_cycle][4]
        group.challenging_member_qq_id = None
        group.save()
        Clan_subscribe.delete().where(
            Clan_subscribe.gid == group_id,
        ).execute()
        Clan_subscribe_layv.delete().where(
            Clan_subscribe_layv.gid == group_id,
        ).execute()
        _logger.info(f'群{group_id}切换至{battle_id}号存档')
    
    async def layv_send(self, qqid: int, message: str):
        await asyncio.sleep(random.randint(3, 10))
        try:
            _logger.info(f'向{qqid}发送出🔪提醒{message}')
            await self.send_private_msg(user_id=qqid,group_id=group_id, message=message)
            _logger.info(f'向{qqid}发送出🔪提醒')
        except Exception as e:
            _logger.exception(e)
    
    async def send_private_remind(self, member_list: List[QQid],group_id: int, content: str):
        for qqid in member_list:
            await asyncio.sleep(random.randint(3, 10))
            try:
                await self.api.send_private_msg(user_id=qqid,group_id=group_id, message=content)
                _logger.info(f'向{qqid}发送出🔪提醒')
            except Exception as e:
                _logger.exception(e)

    def send_remind(self,
                    group_id: Groupid,
                    member_list: List[QQid],
                    sender: QQid,
                    send_private_msg: bool = False):
        """
        remind members to finish challenge

        permission should be checked before this function is called.

        Args:
            group_id: group id
            member_list: a list of qqid to reminder
        """
        sender_name = self._get_nickname_by_qqid(sender)
        if send_private_msg:
            asyncio.ensure_future(self.send_private_remind(
                member_list=member_list,
                group_id=group_id,
                content=f'{sender_name}提醒您及时完成今日出🔪',
            ))
        else:
            message = ' '.join((
                atqq(qqid) for qqid in member_list
            ))
            asyncio.ensure_future(self.api.send_group_msg(
                group_id=group_id,
                message=message+f'\n=======\n{sender_name}提醒您及时完成今日出🔪',
            ))

    def add_subscribe(self, group_id: Groupid, qqid: QQid, boss_num, message=None):
        """
        subscribe a boss, get notification when boss is defeated.

        subscribe for all boss when `boss_num` is `0`

        Args:
            group_id: group id
            qqid: qq id of subscriber
            boss_num: number of boss to subscribe, `0` for all
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        user = User.get_or_none(qqid=qqid)
        if user is None:
            raise GroupError('请先加入公会')
        subscribe = Clan_subscribe.get_or_none(
            gid=group_id,
            qqid=qqid,
        )
        if subscribe is not None:
            raise UserError('您已经在树上了')
        if (group.challenging_member_qq_id == qqid):
            # 如果挂树时当前正在挑战，则取消挑战
            #layv 清理该用户挑战状态
            group.challenging_member_qq_id = None
            Clan_subscribe_layv.delete().where(
                Clan_subscribe_layv.gid == group_id,
                Clan_subscribe_layv.qqid == qqid,
                Clan_subscribe_layv.subscribe_item == boss_num,
            ).execute()
            group.save()
        subscribe = Clan_subscribe.create(
            gid=group_id,
            qqid=qqid,
            subscribe_item=boss_num,
            message=message,
            create_time=int(time.time()),
        )
        
    def add_subscribe_new(self, group_id: Groupid, qqid: QQid, now_cycle, boss_num, message=None):
        """
        subscribe a boss, get notification when boss is defeated.

        Args:
            group_id: group id
            qqid: qq id of subscriber
            boss_num: number of boss to subscribe, `0` for all
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        user = User.get_or_none(qqid=qqid)
        if user is None:
            raise GroupError('请先加入公会')
        subscribe = Clan_subscribe_new.get_or_none(
            gid=group_id,
            qqid=qqid,
            subscribe_item=boss_num
        )
        if subscribe is not None:
            raise UserError('您已经预约过了')
        subscribe = Clan_subscribe_new.create(
            gid=group_id,
            qqid=qqid,
            cycle=now_cycle,
            subscribe_item=boss_num,
            message=message,
            create_time=int(time.time()),
        )
        
    def add_subscribe_layv(self, group_id: Groupid, qqid: QQid, boss_num, message=None):
        """
        subscribe a boss, get notification when boss is defeated.

        subscribe for all boss when `boss_num` is `0`

        Args:
            group_id: group id
            qqid: qq id of subscriber
            boss_num: number of boss to subscribe, `0` for all
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        user = User.get_or_none(qqid=qqid)
        if user is None:
            raise GroupError('请先加入公会')
        subscribe = Clan_subscribe_layv.get_or_none(
            gid=group_id,
            qqid=qqid,
        )
        if subscribe is not None:
            if message is None:
                raise UserError('请输入【进🔪 伤害】更新当前伤害值，如需代人进🔪使用【进🔪xxx 伤害】')
            else:
                Clan_subscribe_layv.delete().where(
                    Clan_subscribe_layv.gid == group_id,
                    Clan_subscribe_layv.qqid == qqid,
                ).execute()
        if (group.challenging_member_qq_id == qqid):
            # 如果挂树时当前正在挑战，则取消挑战
            #layv 清理该用户挑战状态
            group.challenging_member_qq_id = None
            group.save()
        subscribe = Clan_subscribe_layv.create(
            gid=group_id,
            qqid=qqid,
            subscribe_item=boss_num,
            message=message,
            create_time=int(time.time()),
        )
        
    def get_clan_daily_challenge_counts(self,
                                        group_id: Groupid,
                                        pcrdate: Optional[Pcr_date] = None,
                                        battle_id: Union[int, None] = None,
                                        ):
        """
        get the records
        Args:
            group_id: group id
            battle_id: battle id
            pcrdate: pcrdate of report
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        if pcrdate is None:
            pcrdate = pcr_datetime(group.game_server)[0]
        if battle_id is None:
            battle_id = group.battle_id
        full_challenge_count = 0
        tailing_challenge_count = 0
        continued_challenge_count = 0
        continued_tailing_challenge_count = 0
        for challenge in Clan_challenge.select().where(
            Clan_challenge.gid == group_id,
            Clan_challenge.bid == battle_id,
            Clan_challenge.challenge_pcrdate == pcrdate,
        ):
            if challenge.boss_health_ramain != 0:
                if challenge.is_continue:
                    # 剩余🔪
                    continued_challenge_count += 1
                else:
                    # 完整🔪
                    full_challenge_count += 1
            else:
                if challenge.is_continue:
                    # 尾余🔪
                    continued_tailing_challenge_count += 1
                else:
                    # 尾🔪
                    tailing_challenge_count += 1
        return (
            full_challenge_count,
            tailing_challenge_count,
            continued_challenge_count,
            continued_tailing_challenge_count,
        )

    def get_subscribe_list(self, group_id: Groupid, boss_num=None) -> List[Tuple[int, QQid, dict]]:
        """
        get the subscribe lists.

        return a list of subscribe infomation,
        each item is a tuple of (boss_id, qq_id, message)

        Args:
            group_id: group id
        """
        subscribe_list = []
        query = [Clan_subscribe.gid == group_id]
        now = int(time.time())
        if boss_num is not None:
            query.append(Clan_subscribe.subscribe_item == boss_num)
        for subscribe in Clan_subscribe.select().where(
            *query
        ).order_by(
            Clan_subscribe.subscribe_item
        ):
            subscribe_list.append({
                'boss': subscribe.subscribe_item,
                'qqid': subscribe.qqid,
                'message': subscribe.message,
                'time': self.layvchange(now - subscribe.create_time,1)
            })
        return subscribe_list
        
    def get_subscribe_list_new(self, group_id: Groupid, boss_num=None) -> List[Tuple[int, QQid, dict]]:
        """
        get the subscribe lists.

        return a list of subscribe infomation,
        each item is a tuple of (boss_id, qq_id, message)

        Args:
            group_id: group id
        """
        subscribe_list = []
        query = [Clan_subscribe_new.gid == group_id]
        now = int(time.time())
        if boss_num is not None:
            query.append(Clan_subscribe_new.subscribe_item == boss_num)
        for subscribe in Clan_subscribe_new.select().where(
            *query
        ).order_by(
            Clan_subscribe_new.subscribe_item
        ):
            subscribe_list.append({
                'boss': subscribe.subscribe_item,
                'cycle': subscribe.cycle,
                'qqid': subscribe.qqid,
                'message': subscribe.message,
            })
        return subscribe_list
        
    def get_subscribe_list_layv(self, group_id: Groupid, boss_num=None) -> List[Tuple[int, QQid, dict]]:
        """
        get the subscribe lists.

        return a list of subscribe infomation,
        each item is a tuple of (boss_id, qq_id, message)

        Args:
            group_id: group id
        """
        subscribe_list_layv = []
        query = [Clan_subscribe_layv.gid == group_id]
        now = int(time.time())
        if boss_num is not None:
            query.append(Clan_subscribe_layv.subscribe_item == boss_num)
        for subscribe in Clan_subscribe_layv.select().where(
            *query
        ).order_by(
            Clan_subscribe_layv.subscribe_item
        ):
            subscribe_list_layv.append({
                'boss': subscribe.subscribe_item,
                'qqid': subscribe.qqid,
                'message': subscribe.message,
                'time': self.layvchange(now - subscribe.create_time,2)
            })
        return subscribe_list_layv
        
    def layvchange(self,second,type):
        if second<=0:
            return ''   
        strs = ''
        if type==1:
            h = int(second/3600)
            strs = ''
            if h>0:
                second = second%3600
                strs += str(h)+'小时'
            
            m = int(second/60)
            if m>0:
                second = second%60
                strs += str(m)+'分'
            strs += str(second)+'秒'
        elif type==2:
            m = int(second/60)
            if m>=20:
                strs = '💩'
            elif m>0:
                second = second%60
                strs = '(已进🔪'+str(m)+'分'+str(second)+'秒)'
        return strs

    def cancel_subscribe(self, group_id: Groupid, qqid: QQid) -> int:
        """
        cancel a subscription.

        Args:
            group_id: group id
            qqid: qq id of subscriber
            boss_num: number of boss to be canceled
        """
        deleted_counts = Clan_subscribe.delete().where(
            Clan_subscribe.gid == group_id,
            Clan_subscribe.qqid == qqid,
        ).execute()
        return deleted_counts
        
    def cancel_subscribe_new(self, group_id: Groupid, qqid: QQid, boss_num) -> int:
        """
        cancel a subscription.

        Args:
            group_id: group id
            qqid: qq id of subscriber
            boss_num: number of boss to be canceled
        """
        deleted_counts = Clan_subscribe_new.delete().where(
            Clan_subscribe_new.gid == group_id,
            Clan_subscribe_new.qqid == qqid,
            Clan_subscribe_new.subscribe_item == boss_num,
        ).execute()
        return deleted_counts
    
    def cancel_subscribe_layv(self, group_id: Groupid, qqid: QQid) -> int:
        """
        cancel a subscription.

        Args:
            group_id: group id
            qqid: qq id of subscriber
            boss_num: number of boss to be canceled
        """
        deleted_counts = Clan_subscribe_layv.delete().where(
            Clan_subscribe_layv.gid == group_id,
            Clan_subscribe_layv.qqid == qqid,
        ).execute()
        return deleted_counts

    def notify_subscribe(self, group_id: Groupid, boss_num=None, send_private_msg=False):
        """
        send notification to subsciber and remove them (when boss is defeated).
        Args:
            group_id: group id
            boss_num: number of new boss
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        if boss_num is None:
            boss_num = group.boss_num
        commit = ''
        if boss_num ==1:
            commit = group.a_commit if group.a_commit is not None else ''
        if boss_num ==2:
            commit = group.b_commit if group.b_commit is not None else ''
        if boss_num ==3:
            commit = group.c_commit if group.c_commit is not None else ''
        if boss_num ==4:
            commit = group.d_commit if group.d_commit is not None else ''
        if boss_num ==5:
            commit = group.e_commit if group.e_commit is not None else ''
        notice = []
        for subscribe in Clan_subscribe.select().where(
            Clan_subscribe.gid == group_id,
            (Clan_subscribe.subscribe_item == boss_num) |
            (Clan_subscribe.subscribe_item == 0),
        ).order_by(Clan_subscribe.sid):
            msg = atqq(subscribe.qqid)
            if subscribe.message:
                msg += subscribe.message
            notice.append(msg)
            subscribe.delete_instance()
            continue
        if notice:
            asyncio.ensure_future(self.api.send_group_msg(
                group_id=group_id,
                message='boss已被XX\n'+str(commit)+'\n'+'\n'.join(notice),
            ))
            
    def notify_subscribe_new(self, group_id: Groupid, send_private_msg=False):
        """
        send notification to subsciber and remove them (when boss is defeated).
        Args:
            group_id: group id
            boss_num: number of new boss
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        notice = []
        print(group.boss_cycle + int(group.e_issecond))
        for subscribe in Clan_subscribe_new.select().where(
            Clan_subscribe_new.gid == group_id,
            ((Clan_subscribe_new.subscribe_item == 1) & (Clan_subscribe_new.cycle == (group.boss_cycle + int(group.a_issecond)) ) ) |
            ((Clan_subscribe_new.subscribe_item == 2) & (Clan_subscribe_new.cycle == (group.boss_cycle + int(group.b_issecond)) ) ) |
            ((Clan_subscribe_new.subscribe_item == 3) & (Clan_subscribe_new.cycle == (group.boss_cycle + int(group.c_issecond)) ) ) |
            ((Clan_subscribe_new.subscribe_item == 4) & (Clan_subscribe_new.cycle == (group.boss_cycle + int(group.d_issecond)) ) ) |
            ((Clan_subscribe_new.subscribe_item == 5) & (Clan_subscribe_new.cycle == (group.boss_cycle + int(group.e_issecond)) ) ) 
        ).order_by(Clan_subscribe_new.sid):
            msg = atqq(subscribe.qqid)
            if subscribe.message:
                msg += subscribe.message
            notice.append(msg)
            # 如果预约者选择了“仅提醒一次”，则删除
            try:
                notify_user = User.get_by_id(subscribe.qqid)
            except peewee.DoesNotExist:
                _logger.warning('预约者用户不存在')
                continue
            if notify_user.notify_preference == 1:
                subscribe.delete_instance()
                continue
            else:
                subscribe.cycle += 1
                subscribe.save()
            continue
        if notice:
            asyncio.ensure_future(self.api.send_group_msg(
                group_id=group_id,
                message='盒了,速来\n'+'    \n'+'\n'.join(notice),
            ))
            
    def notify_subscribe_layv(self, group_id: Groupid, boss_num=None, send_private_msg=False):
        """
        send notification to subsciber and remove them (when boss is defeated).

        Args:
            group_id: group id
            boss_num: number of new boss
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        notice = []
        commit = ''
        if boss_num ==1:
            commit = group.a_commit if group.a_commit is not None else ''
        if boss_num ==2:
            commit = group.b_commit if group.b_commit is not None else ''
        if boss_num ==3:
            commit = group.c_commit if group.c_commit is not None else ''
        if boss_num ==4:
            commit = group.d_commit if group.d_commit is not None else ''
        if boss_num ==5:
            commit = group.e_commit if group.e_commit is not None else ''
        for subscribe in Clan_subscribe_layv.select().where(
            Clan_subscribe_layv.gid == group_id,
            (Clan_subscribe_layv.subscribe_item == boss_num) |
            (Clan_subscribe_layv.subscribe_item == 0),
        ).order_by(Clan_subscribe_layv.sid):
            msg = atqq(subscribe.qqid)
            if subscribe.message:
                msg += subscribe.message
            notice.append(msg)
            subscribe.delete_instance()
        if notice:
            asyncio.ensure_future(self.api.send_group_msg(
                group_id=group_id,
                message='boss已被XX\n'+str(commit)+'\n'+'\n'.join(notice),
            ))

    def apply_for_challenge(self,
                            group_id: Groupid,
                            qqid: QQid,
                            *,
                            extra_msg: Optional[str] = None,
                            appli_type: int = 0,
                            ) -> BossStatus:
        """
        apply for a challenge to boss.

        Args:
            group_id: group id
            qqid: qq id
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        user = User.get_or_none(qqid=qqid)
        if user is None:
            raise UserNotInGroup
        if (appli_type != 1) and (extra_msg is None):
            raise InputError('锁定boss时必须留言')
        if group.challenging_member_qq_id is not None:
            nik = self._get_nickname_by_qqid(
                group.challenging_member_qq_id,
            ) or group.challenging_member_qq_id
            action = '正在挑战' if group.boss_lock_type == 1 else '锁定了'
            msg = f'申请失败，{nik}{action}boss'
            if group.boss_lock_type != 1:
                msg += '\n留言：'+group.challenging_comment
                raise GroupError(msg)
        group.challenging_member_qq_id = qqid
        group.challenging_start_time = int(time.time())
        group.challenging_comment = extra_msg
        group.boss_lock_type = appli_type
        group.save()

        nik = self._get_nickname_by_qqid(qqid) or qqid
        info = (f'{nik}已开始挑战boss' if appli_type == 1 else
                f'{nik}锁定了boss\n留言：{extra_msg}')
        status = BossStatus(
            group.boss_cycle,
            group.a_health,
            group.b_health,
            group.c_health,
            group.d_health,
            group.e_health,
            group.a_issecond,
            group.b_issecond,
            group.c_issecond,
            group.d_issecond,
            group.e_issecond,
            qqid,
            info,
        )
        self._boss_status[group_id].set_result(
            (self._boss_data_dict(group), status.info)
        )
        self._boss_status[group_id] = asyncio.get_event_loop().create_future()
        return status

    def cancel_application(self, group_id: Groupid, qqid: QQid) -> BossStatus:
        """
        cancel a application of boss challenge 3 minutes after the challenge starts.

        Args:
            group_id: group id
            qqid: qq id of the canceler
            force_cancel: ignore the 3-minutes restriction
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        if group.challenging_member_qq_id is None:
            raise GroupError('boss没有锁定')
        user = User.get_or_create(
            qqid=qqid,
            defaults={
                'clan_group_id': group_id,
            }
        )[0]
        if (group.challenging_member_qq_id != qqid) and (user.authority_group >= 100):
            challenge_duration = (int(time.time())
                                  - group.challenging_start_time)
            is_challenge = (group.boss_lock_type == 1)
            if (not is_challenge) or (challenge_duration < 180):
                nik = self._get_nickname_by_qqid(
                    group.challenging_member_qq_id,
                ) or group.challenging_member_qq_id
                msg = f'失败，{nik}在{challenge_duration}秒前'+(
                    '开始挑战boss' if is_challenge else
                    ('锁定了boss\n留言：'+group.challenging_comment)
                )
                raise GroupError(msg)
        #layv 清理该用户挑战状态
        group.challenging_member_qq_id = None
        group.save()

        status = BossStatus(
            group.boss_cycle,
            group.a_health,
            group.b_health,
            group.c_health,
            group.d_health,
            group.e_health,
            group.a_issecond,
            group.b_issecond,
            group.c_issecond,
            group.d_issecond,
            group.e_issecond,
            0,
            'boss挑战已可申请',
        )
        self._boss_status[group_id].set_result(
            (self._boss_data_dict(group), status.info)
        )
        self._boss_status[group_id] = asyncio.get_event_loop().create_future()
        return status

    def save_slot(self, group_id: Groupid, qqid: QQid, todaystatus: bool = True, only_check: bool = False):
        """
        record today's save slot

        Args:
            group_id: group id
            qqid: qqid of member who do the record
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        membership = Clan_member.get_or_none(
            group_id=group_id, qqid=qqid)
        if membership is None:
            raise UserNotInGroup
        today, _ = pcr_datetime(group.game_server)
        if only_check:
            return (membership.last_save_slot == today)
        if todaystatus:
            if membership.last_save_slot == today:
                raise UserError('您今天已经存在SL记录了')
            membership.last_save_slot = today

            # 如果当前正在挑战，则取消挑战
            if (group.challenging_member_qq_id == qqid):
                #layv 清理该用户挑战状态
                group.challenging_member_qq_id = None
                group.save()
            # 如果当前正在挂树，则取消挂树
            Clan_subscribe.delete().where(
                Clan_subscribe.gid == group_id,
                Clan_subscribe.qqid == qqid,
            ).execute()
            Clan_subscribe_layv.delete().where(
                Clan_subscribe_layv.gid == group_id,
                Clan_subscribe_layv.qqid == qqid,
            ).execute()
        else:
            if membership.last_save_slot != today:
                raise UserError('您今天没有SL记录')
            membership.last_save_slot = 0
        membership.save()

        # refresh
        self.get_member_list(group_id, nocache=True)

        return todaystatus
    #layv 获取当前尾🔪成员
    def layv_weidao(self,group_id: Groupid):
        group = Clan_group.get_or_none(group_id=group_id)
        report = self.get_report(group_id,None,None,pcr_datetime(group.game_server, int(time.time()))[0])
        res = []
        
        #筛选出所有出刀数据
        for i in report:
            if i['health_ramain']==0 and (i['is_continue'] is False) and (i['is_used'] is False):
                nowarr = {
                    'finished':0,
                    'boss':str(i['cycle'])+'-'+str(i['boss_num']),
                    'damage':i['damage'],
                    'qqid':i['qqid'],
                    'message':i['message']
                }
                res.append(nowarr)
        return res
    
    @timed_cached_func(max_len=64, max_age_seconds=10, ignore_self=True)
    def get_report(self,
                   group_id: Groupid,
                   battle_id: Union[str, int, None],
                   qqid: Optional[QQid] = None,
                   pcrdate: Optional[Pcr_date] = None,
                   # start_time: Optional[Pcr_time] = None,
                   # end_time: Optional[Pcr_time] = None,
                   ) -> ClanBattleReport:
        """
        get the records

        Args:
            group_id: group id
            qqid: user id of report
            pcrdate: pcrdate of report
            start_time: start time of report
            end_time: end time of report
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        report = []
        expressions = [
            Clan_challenge.gid == group_id,
        ]
        if battle_id is None:
            battle_id = group.battle_id
        if isinstance(battle_id, str):
            if battle_id == 'all':
                pass
            else:
                raise InputError(
                    f'unexceptd value "{battle_id}" for battle_id')
        else:
            expressions.append(Clan_challenge.bid == battle_id)
        if qqid is not None:
            expressions.append(Clan_challenge.qqid == qqid)
        if pcrdate is not None:
            expressions.append(Clan_challenge.challenge_pcrdate == pcrdate)
        # if start_time is not None:
        #     expressions.append(Clan_challenge.challenge_pcrtime >= start_time)
        # if end_time is not None:
        #     expressions.append(Clan_challenge.challenge_pcrtime <= end_time)
        for c in Clan_challenge.select().where(
            *expressions
        ):
            report.append({
                'cid': c.cid,
                'battle_id': c.bid,
                'qqid': c.qqid,
                'challenge_time': pcr_timestamp(
                    c.challenge_pcrdate,
                    c.challenge_pcrtime,
                    group.game_server,
                ),
                'challenge_pcrdate': c.challenge_pcrdate,
                'challenge_pcrtime': c.challenge_pcrtime,
                'cycle': c.boss_cycle,
                'boss_num': c.boss_num,
                'health_ramain': c.boss_health_ramain,
                'damage': c.challenge_damage,
                'is_continue': c.is_continue,
                'is_second': c.is_second,
                'is_used': c.is_used,
                'continue_num': c.continue_num,
                'message': c.message,
                'behalf': c.behalf,
            })
        return report

    @timed_cached_func(max_len=64, max_age_seconds=10, ignore_self=True)
    def get_battle_member_list(self,
                               group_id: Groupid,
                               battle_id: Union[str, int, None],
                               ):
        """
        get the member lists for clan-battle report

        return a list of member infomation,

        Args:
            group_id: group id
        """
        group = Clan_group.get_or_none(group_id=group_id)
        if group is None:
            raise GroupNotExist
        expressions = [
            Clan_challenge.gid == group_id,
        ]
        if battle_id is None:
            battle_id = group.battle_id
        if isinstance(battle_id, str):
            if battle_id == 'all':
                pass
            else:
                raise InputError(
                    f'unexceptd value "{battle_id}" for battle_id')
        else:
            expressions.append(Clan_challenge.bid == battle_id)
        member_list = []
        for u in Clan_challenge.select(
            Clan_challenge.qqid,
            User.nickname,
        ).join(
            User,
            on=(Clan_challenge.qqid == User.qqid),
            attr='user',
        ).where(
            *expressions
        ).distinct():
            member_list.append({
                'qqid': u.qqid,
                'nickname': u.user.nickname,
            })
        return member_list

    @timed_cached_func(max_len=16, max_age_seconds=3600, ignore_self=True)
    def get_member_list(self, group_id: Groupid) -> List[Dict[str, Any]]:
        """
        get the member lists from database

        return a list of member infomation,

        Args:
            group_id: group id
        """
        member_list = []
        for user in User.select(
            User, Clan_member,
        ).join(
            Clan_member,
            on=(User.qqid == Clan_member.qqid),
            attr='clan_member',
        ).where(
            Clan_member.group_id == group_id,
            User.deleted == False,
        ):
            member_list.append({
                'qqid': user.qqid,
                'nickname': user.nickname,
                'sl': user.clan_member.last_save_slot,
            })
        return member_list

    def jobs(self):
        trigger = CronTrigger(hour=5)

        def ensure_future_update_all_group_members():
            asyncio.ensure_future(self._update_group_list_async())

        return ((trigger, ensure_future_update_all_group_members),)

    def match(self, cmd):
        if self.setting['clan_battle_mode'] != 'web':
            return 0
        if len(cmd) < 2:
            return 0
        return self.Commands.get(cmd[0:2], 0)

    def execute(self, match_num, ctx):
        if ctx['message_type'] != 'group':
            return None
        cmd = ctx['raw_message']
        group_id = ctx['group_id']
        user_id = ctx['user_id']
        if match_num == 1:  # 创建
            match = re.match(r'^创建(?:([日台韩国])服)?[公工行]会$', cmd)
            if not match:
                return
            game_server = self.Server.get(match.group(1), 'cn')
            try:
                self.creat_group(group_id, game_server)
            except ClanBattleError as e:
                _logger.info('群聊 失败 {} {} {}'.format(user_id, group_id, cmd))
                return str(e)
            _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
            return ('公会创建成功，请登录后台查看，'
                    '公会战成员请发送“加入公会”，'
                    '或发送“加入全部成员”')
        elif match_num == 2:  # 加入
            if cmd == '加入全部成员':
                if ctx['sender']['role'] == 'member':
                    return '只有管理员才可以加入全部成员'
                _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
                asyncio.ensure_future(
                    self._update_all_group_members_async(group_id))
                return '本群所有成员已添加记录'
            match = re.match(r'^加入[公工行]会 *(?:\[CQ:at,qq=(\d+)\])? *$', cmd)
            if match:
                if match.group(1):
                    if ctx['sender']['role'] == 'member':
                        return '只有管理员才可以加入其他成员'
                    user_id = int(match.group(1))
                    nickname = None
                else:
                    nickname = (ctx['sender'].get('card')
                                or ctx['sender'].get('nickname'))
                asyncio.ensure_future(
                    self.bind_group(group_id, user_id, nickname))
                _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
                return '{}已加入本公会'.format(atqq(user_id))
        elif match_num == 3:  # 状态
            if len(cmd) != 2:
                return
            if cmd in ['查刀', '报告']:
                url = '详情请在面板中查看：'
                url += urljoin(
                    self.setting['public_address'],
                    '{}clan/{}/progress/'.format(
                        self.setting['public_basepath'],
                        group_id
                    )
                )
                url += '\n'
            else:
                url = ''
            try:
                boss_summary = self.boss_status_summary(group_id)
            except ClanBattleError as e:
                return str(e)
            try:
                (
                    full_challenge_count,
                    tailing_challenge_count,
                    continued_challenge_count,
                    continued_tailing_challenge_count,
                ) = self.get_clan_daily_challenge_counts(group_id)
            except GroupNotExist as e:
                return str(e)
            finished = (full_challenge_count
                        + continued_challenge_count
                        + continued_tailing_challenge_count)
            unfinished = (tailing_challenge_count
                          - continued_challenge_count
                          - continued_tailing_challenge_count)
            progress = '----------------------------\n今天已出:          {}🔪\n完整🔪:            {}🔪\n补偿🔪:            {}🔪'.format(
                finished+unfinished*0.5, 90 - finished - unfinished, unfinished
            )
            return f'{url}{boss_summary}{progress}\n因为举报太多，私聊链接已关闭，请牢记自己面板密码\n人多的群请清理下自己群内闲杂人'
        elif match_num == 4:  # 报🔪
            match = re.match(r'^刀([1-5]) ?(\d+)([Ww万Kk千])? *(?:\[CQ:at,qq=(\d+)\])? *(昨[日天])? *(?:[\:：](.*))? *([补b])? *([1-3])?$', cmd)
            if not match:
                match = re.match(r'^报刀([1-5]) ?(\d+)([Ww万Kk千])? *(?:\[CQ:at,qq=(\d+)\])? *(昨[日天])? *(?:[\:：](.*))? *([补b])? *([1-3])?$', cmd)
            if not match:
                return
            unit = {
                'W': 10000,
                'w': 10000,
                '万': 10000,
                'k': 1000,
                'K': 1000,
                '千': 1000,
            }.get(match.group(3), 1)
            bossnum = int(match.group(1))
            damage = int(match.group(2)) * unit
            behalf = match.group(4) and int(match.group(4))
            previous_day = bool(match.group(5))
            extra_msg = match.group(6)
            is_continue = bool(match.group(7))
            continue_num = match.group(8)
            if continue_num:
                continue_num = int(continue_num)
            else:
                continue_num  = 0
            print(continue_num)
            if isinstance(extra_msg, str):
                extra_msg = extra_msg.strip()
                if not extra_msg:
                    extra_msg = None
            try:
                boss_status = self.challenge(
                    group_id,
                    user_id,
                    False,
                    bossnum,
                    damage,
                    behalf,
                    is_continue,
                    continue_num,
                    extra_msg=extra_msg,
                    previous_day=previous_day)
            except ClanBattleError as e:
                _logger.info('群聊 失败 {} {} {}'.format(user_id, group_id, cmd))
                return str(e)
            
            _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
            return str(boss_status)
        elif match_num == 5:  # 尾🔪
            match = re.match(r'^尾([1-5]) ?(?:\[CQ:at,qq=(\d+)\])? *(昨[日天])? *(?:[\:：](.*))? *([补b])? *([1-3])?$', cmd)
            if not match:
                match = re.match(r'^尾刀([1-5]) ?(?:\[CQ:at,qq=(\d+)\])? *(昨[日天])? *(?:[\:：](.*))? *([补b])? *([1-3])?$', cmd)
            if not match:
                return
            bossnum = int(match.group(1))
            behalf = match.group(2) and int(match.group(2))
            previous_day = bool(match.group(3))
            extra_msg = match.group(4)
            is_continue = bool(match.group(5))
            continue_num = match.group(6)
            if continue_num:
                continue_num = int(continue_num)
            else:
                continue_num  = 0
            print(continue_num)
            if isinstance(extra_msg, str):
                extra_msg = extra_msg.strip()
                if not extra_msg:
                    extra_msg = None
            try:
                boss_status = self.challenge(
                    group_id,
                    user_id,
                    True,
                    bossnum,
                    None,
                    behalf,
                    is_continue,
                    continue_num,
                    extra_msg=extra_msg,
                    previous_day=previous_day)
            except ClanBattleError as e:
                _logger.info('群聊 失败 {} {} {}'.format(user_id, group_id, cmd))
                return str(e)
            _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
            return str(boss_status)
        elif match_num == 6:  # 撤销
            if cmd != '撤销':
                return
            try:
                boss_status = self.undo(group_id, user_id)
            except ClanBattleError as e:
                _logger.info('群聊 失败 {} {} {}'.format(user_id, group_id, cmd))
                return str(e)
            _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
            return str(boss_status)
        elif match_num == 7:  # 修正
            if len(cmd) != 2:
                return
            url = urljoin(
                self.setting['public_address'],
                '{}clan/{}/'.format(
                    self.setting['public_basepath'],
                    group_id
                )
            )
            return '请登录面板操作：'+url
        elif match_num == 8:  # 选择
            if len(cmd) != 2:
                return
            url = urljoin(
                self.setting['public_address'],
                '{}clan/{}/setting/'.format(
                    self.setting['public_basepath'],
                    group_id
                )
            )
            return '请登录面板操作：'+url
        elif match_num == 9:  # 报告
            # if len(cmd) != 2:
            #     return
            match = re.match(r'^(?:查刀) *(?:\[CQ:at,qq=(\d+)\])? *$', cmd)
            behalf = match.group(1) and int(match.group(1))
            re2 = '您当前已出'
            if behalf:
                re2 = '他当前已出'
                user_id = behalf
            url = urljoin(
                self.setting['public_address'],
                '{}clan/{}/progress/'.format(
                    self.setting['public_basepath'],
                    group_id
                )
            )
            group = Clan_group.get_or_none(group_id=group_id)
            d,t = pcr_datetime(area=group.game_server)
            challenges = Clan_challenge.select().where(
                Clan_challenge.gid == group_id,
                Clan_challenge.qqid == user_id,
                Clan_challenge.bid == group.battle_id,
                Clan_challenge.challenge_pcrdate == d,
            ).order_by(Clan_challenge.cid)
            challenges = list(challenges)
            # 可出的补偿
            can_continue = sum(bool(c.boss_health_ramain==0 and c.is_continue==False)
                               for c in challenges)
            # 非补偿🔪次数
            not_continue = sum(bool(c.is_continue==False)
                               for c in challenges)
            # 已出的补偿
            user_continue = sum(bool(c.is_continue)
                                for c in challenges)
            msg = None
            for c in challenges:
                if c.boss_health_ramain==0 and c.is_continue==False and c.is_used==False:
                    if msg is None:
                        msg = '\n------------未出尾🔪------------\n编号     王     伤害     留言/补时'
                    msg += '\n'+str(c.continue_num)
                    msg += '        '+str(c.boss_cycle)+'-' + str(c.boss_num) + '    ' + str(c.challenge_damage) + '    ' + str(c.message) 
            cotinue = max(0,can_continue-user_continue)
            if msg is not None:
                msg+='\n------------------------------\n如需对应编号报刀方便管理查尾，可使用【尾2b2】对应2号刀补偿'
            return re2+str(not_continue)+'🔪，还有'+str(cotinue)+'尾🔪未出'+msg
        elif match_num == 10:  # 预约
            match = re.match(r'^预约([1-5]) *(?:[\:：](.*))?$', cmd)
            if not match:
                return
            boss_num = int(match.group(1))
            extra_msg = match.group(2)
            group = Clan_group.get_or_none(group_id=group_id)
            if isinstance(extra_msg, str):
                extra_msg = extra_msg.strip()
                if not extra_msg:
                    extra_msg = None
            now_cycle = group.boss_cycle+1#当前周目
            if boss_num == 1 :
                now_cycle += int(group.a_issecond)
            if boss_num == 2 :
                now_cycle += int(group.b_issecond)
            if boss_num == 3 :
                now_cycle += int(group.c_issecond)
            if boss_num == 4 :
                now_cycle += int(group.d_issecond)
            if boss_num == 5 :
                now_cycle += int(group.e_issecond)
            try:
                self.add_subscribe_new(group_id, user_id, now_cycle, boss_num, extra_msg)
            except ClanBattleError as e:
                _logger.info('群聊 失败 {} {} {}'.format(user_id, group_id, cmd))
                return str(e)
            _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
            return f'预约{now_cycle}周目{boss_num}王成功'
        elif match_num == 11:  # 挂树
            match = re.match(r'^挂树([1-5]) *(?:[\:：](.*))?$', cmd)
            if not match:
                match2 = re.match(r'^挂树 *(?:[\:：](.*))?$', cmd)
                if not match2:        
                    return
            if match:
                boss_num = int(match.group(1))
                extra_msg = match.group(2)
            elif match2:
                subscribe = Clan_subscribe_layv.get_or_none(
                    gid=group_id,
                    qqid=user_id,
                )
                if not subscribe:
                    return '你还没进刀呢，我咋知道你挂哪儿了?'
                boss_num = subscribe.subscribe_item
                extra_msg = match2.group(1)       
                
            #清理进刀状态
            Clan_subscribe_layv.delete().where(
                    Clan_subscribe_layv.gid == group_id,
                    Clan_subscribe_layv.qqid == user_id,
                ).execute()    
            group = Clan_group.get_or_none(group_id=group_id)
            if boss_num == 0:
                return '请带上王的编号，不然我怎么知道你挂几王？'
            else:
                if boss_num ==1:
                    bossheal = group.a_health
                if boss_num ==2:
                    bossheal = group.b_health
                if boss_num ==3:
                    bossheal = group.c_health
                if boss_num ==4:
                    bossheal = group.d_health
                if boss_num ==5:
                    bossheal = group.e_health
                if bossheal == 0 :
                    return str(boss_num)+'王都盒了¿'
            if isinstance(extra_msg, str):
                extra_msg = extra_msg.strip()
                if not extra_msg:
                    extra_msg = None
            try:
                self.add_subscribe(group_id, user_id, boss_num, extra_msg)
            except ClanBattleError as e:
                _logger.info('群聊 失败 {} {} {}'.format(user_id, group_id, cmd))
                return str(e)
            _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
            return '已挂树'
        elif match_num == 12:  # 申请/锁定
            if cmd == '申请出🔪':
                appli_type = 1
                extra_msg = None
            elif cmd == '锁定':
                return '锁定时请留言'
            else:
                match = re.match(r'^锁定(?:boss)? *(?:[\:：](.*))?$', cmd)
                if not match:
                    return
                appli_type = 2
                extra_msg = match.group(1)
                if isinstance(extra_msg, str):
                    extra_msg = extra_msg.strip()
                    if not extra_msg:
                        return '锁定时请留言'
                else:
                    return
            try:
                boss_status = self.apply_for_challenge(
                    group_id, user_id, extra_msg=extra_msg, appli_type=appli_type)
            except ClanBattleError as e:
                _logger.info('群聊 失败 {} {} {}'.format(user_id, group_id, cmd))
                return str(e)
            _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
            return str(boss_status)
        elif match_num == 13:  # 取消
            match = re.match(r'^取消(?:预约)?([1-5]|进刀|挂树)$', cmd)
            if not match:
                return
            b = match.group(1)
            if b == '挂树':
                counts = self.cancel_subscribe(group_id, user_id)
                event = b
            elif b=='进刀':
                counts = self.cancel_subscribe_layv(group_id, user_id)
                event = b
            else:
                boss_num = int(b)
                event = f'预约{b}号boss'
                counts = self.cancel_subscribe_new(group_id, user_id, boss_num)
            if counts == 0:
                return '您没有'+event
                _logger.info('群聊 失败 {} {} {}'.format(user_id, group_id, cmd))
            _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
            return '已取消'+event
        elif match_num == 14:  # 解锁
            if cmd != '解锁':
                return
            try:
                boss_status = self.cancel_application(group_id, user_id)
            except ClanBattleError as e:
                _logger.info('群聊 失败 {} {} {}'.format(user_id, group_id, cmd))
                return str(e)
            _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
            return str(boss_status)
        elif match_num == 15:  # 面板
            if len(cmd) != 2:
                return
            url = urljoin(
                self.setting['public_address'],
                '{}clan/{}/'.format(
                    self.setting['public_basepath'],
                    group_id
                )
            )
            return f'公会战面板：\n{url}\n建议添加到浏览器收藏夹或桌面快捷方式\n老马风控严重,部分群得更换二号机,管理员可以加群729199824联系辣鱼'
        elif match_num == 16:  # SL
            match = re.match(r'^(?:SL|sl) *([\?？])? *(?:\[CQ:at,qq=(\d+)\])? *([\?？])? *$', cmd)
            if not match:
                return
            behalf = match.group(2) and int(match.group(2))
            only_check = bool(match.group(1) or match.group(3))
            if behalf:
                user_id = behalf
            if only_check:
                sl_ed = self.save_slot(group_id, user_id, only_check=True)
                if sl_ed:
                    return '今日已使用SL'
                else:
                    return '今日未使用SL'
            else:
                try:
                    self.save_slot(group_id, user_id)
                except ClanBattleError as e:
                    _logger.info('群聊 失败 {} {} {}'.format(
                        user_id, group_id, cmd))
                    return str(e)
                _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
                return '已记录SL'
        elif 20 <= match_num <= 25:
            if len(cmd) != 2:
                return
            beh = '挂树' 
            if match_num-20 == 0:
                subscribers = self.get_subscribe_list(group_id)
            else:
                subscribers = self.get_subscribe_list(group_id, match_num-20)
            if not subscribers:
                return '没有人'+beh
            reply = beh+'的成员：\n'
            boss_num_use = 0
            for m in subscribers:
                if (match_num-20)==0 and boss_num_use!=m['boss']:
                    boss_num_use = m['boss']
                    reply += '\n--------'+str(m['boss'])+'王--------'
                reply += '\n'+self._get_nickname_by_qqid(m['qqid'])
                if m.get('message'):
                    reply += '：' + m['message']
                reply += '(已挂树'+str(m['time'])+')'
            return reply
        elif match_num == 97: #查询进🔪
            match = re.match(r'^查尾 *(?:[\:：](.*))?$', cmd)
            if not match:
                return
            beh = '尾🔪' 
            weidao = self.layv_weidao(group_id)
            if not weidao:
                return '当前没有人有未出完的'+beh
            num = 0
            msg = ''
            for m in weidao:
                msg += '\n'+self._get_nickname_by_qqid(m['qqid'])
                msg += '    ' + str(m['boss']) + '    ' + str(m['damage']) + '    ' + str(m['message'])
                num += 1
            reply = '当前尾刀总数' + str(num)
            reply += '\n用户名     王     伤害     留言/补时'
            reply += msg
            return reply
        elif 30 <= match_num <= 35: #查询进🔪
            if len(cmd) != 2:
                return
            beh = '进🔪'
            boss_num = match_num-30
            if boss_num==0:
                subscribers = self.get_subscribe_list_layv(group_id)
            else:
                subscribers = self.get_subscribe_list_layv(group_id,boss_num)
            if not subscribers:
                return '当前没有人'+beh
            reply = beh+'的成员：\n'
            group = Clan_group.get_or_none(group_id=group_id)
            #layv 新增查X显示本王血量
            if boss_num==1:
                reply = str(group.boss_cycle+  int(group.a_issecond) )+'周目1王\n剩余生命值'+str(group.a_health) +'\n' +  reply
            if boss_num==2:
                reply = str(group.boss_cycle+  int(group.b_issecond) )+'周目2王\n剩余生命值'+str(group.b_health) +'\n' +  reply
            if boss_num==3:
                reply = str(group.boss_cycle+  int(group.c_issecond) )+'周目3王\n剩余生命值'+str(group.c_health) +'\n' +  reply
            if boss_num==4:
                reply = str(group.boss_cycle+  int(group.d_issecond) )+'周目4王\n剩余生命值'+str(group.d_health) +'\n' +  reply
            if boss_num==5:
                reply = str(group.boss_cycle+  int(group.e_issecond) )+'周目5王\n剩余生命值'+str(group.e_health) +'\n' +  reply
            
            boss_num_use = 0
            for m in subscribers:
                if boss_num==0 and boss_num_use!=m['boss']:
                    boss_num_use = m['boss']
                    reply += '\n--------'+str(m['boss'])+'王--------'
                    
                reply += '\n'+self._get_nickname_by_qqid(m['qqid'])
                if m.get('message'):
                    reply += '：' + m['message']
                else:
                    reply += m['time']
            return reply
        elif match_num == 36:
            match = re.match(r'^留言([1-5]) *(?:[\:： ](.*))?$', cmd)
            if not match:
                return
            extra_msg = match.group(2)
            if isinstance(extra_msg, str):
                extra_msg = extra_msg.strip()
                if not extra_msg:
                    return '锁定时请留言'
            boss_num = int(match.group(1))
            try:
                status = self.commit(group_id, user_id,boss_num,extra_msg)
            except ClanBattleError as e:
                _logger.info('群聊 失败 {} {} {}'.format(user_id, group_id, cmd))
                return str(e)
            _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
            return str(status)
        elif match_num == 99:  # 进🔪
            match = re.match(r'^进刀([1-5]) *(?:\[CQ:at,qq=(\d+)\])? *(?:[\:： ](.*))?$', cmd)
            add_msg = ' 进🔪可以改为 [进3] 了'
            if not match:
                match = re.match(r'^进([1-5]) *(?:\[CQ:at,qq=(\d+)\])? *(?:[\:： ](.*))?$', cmd)
                add_msg = ''
            if not match:
                return
            boss_num = int(match.group(1))
            behalf = match.group(2) and int(match.group(2))
            
            group = Clan_group.get_or_none(group_id=group_id)
            if boss_num == 0:
                return '请带上王的编号，不然我怎么知道你挂几王？'
            else:
                if boss_num ==1:
                    bossheal = group.a_health
                if boss_num ==2:
                    bossheal = group.b_health
                if boss_num ==3:
                    bossheal = group.c_health
                if boss_num ==4:
                    bossheal = group.d_health
                if boss_num ==5:
                    bossheal = group.e_health
                if bossheal == 0 :
                    return str(boss_num)+'w都死了，你进啥¿'
            
            old_user = None
            if behalf:
                old_user = '('+self._get_nickname_by_qqid(user_id)+'代🔪)'
                user_id = behalf
            extra_msg = match.group(3)
            if isinstance(extra_msg, str):
                extra_msg = extra_msg.strip()
                if old_user:
                    extra_msg = extra_msg+old_user
                if not extra_msg:
                    if not old_user:
                        extra_msg = None
                    else:
                        extra_msg = old_user
            elif old_user:
                extra_msg = old_user
            try:
                self.add_subscribe_layv(group_id, user_id, boss_num, extra_msg)
            except ClanBattleError as e:
                _logger.info('群聊 失败 {} {} {}'.format(user_id, group_id, cmd))
                return str(e)
            _logger.info('群聊 成功 {} {} {}'.format(user_id, group_id, cmd))
            return '已进🔪'+add_msg

    def register_routes(self, app: Quart):

        @app.route(
            urljoin(self.setting['public_basepath'], 'clan/<int:group_id>/'),
            methods=['GET'])
        async def yobot_clan(group_id):
            if 'yobot_user' not in session:
                return redirect(url_for('yobot_login', callback=request.path))
            user = User.get_by_id(session['yobot_user'])
            group = Clan_group.get_or_none(group_id=group_id)
            if group is None:
                return await render_template('404.html', item='公会'), 404
            is_member = Clan_member.get_or_none(
                group_id=group_id, qqid=session['yobot_user'])
            if (not is_member and user.authority_group >= 10):
                return await render_template('clan/unauthorized.html')
            return await render_template(
                'clan/panel.html',
                is_member=is_member,
            )

        @app.route(
            urljoin(self.setting['public_basepath'],
                    'clan/<int:group_id>/subscribers/'),
            methods=['GET'])
        async def yobot_clan_subscribers(group_id):
            if 'yobot_user' not in session:
                return redirect(url_for('yobot_login', callback=request.path))
            user = User.get_by_id(session['yobot_user'])
            group = Clan_group.get_or_none(group_id=group_id)
            if group is None:
                return await render_template('404.html', item='公会'), 404
            is_member = Clan_member.get_or_none(
                group_id=group_id, qqid=session['yobot_user'])
            if (not is_member and user.authority_group >= 10):
                return await render_template('clan/unauthorized.html')
            return await render_template(
                'clan/subscribers.html',
            )

        @app.route(
            urljoin(self.setting['public_basepath'],
                    'clan/<int:group_id>/api/'),
            methods=['POST'])
        async def yobot_clan_api(group_id):
            group = Clan_group.get_or_none(group_id=group_id)
            if group is None:
                return jsonify(
                    code=20,
                    message='Group not exists',
                )
            if 'yobot_user' not in session:
                if not(group.privacy & 0x1):
                    return jsonify(
                        code=10,
                        message='Not logged in',
                    )
                user_id = 0
            else:
                user_id = session['yobot_user']
                user = User.get_by_id(user_id)
                is_member = Clan_member.get_or_none(
                    group_id=group_id, qqid=user_id)
                if (not is_member and user.authority_group >= 10):
                    return jsonify(
                        code=11,
                        message='Insufficient authority',
                    )
            try:
                payload = await request.get_json()
                if payload is None:
                    return jsonify(
                        code=30,
                        message='Invalid payload',
                    )
                if (user_id != 0) and (payload.get('csrf_token') != session['csrf_token']):
                    return jsonify(
                        code=15,
                        message='Invalid csrf_token',
                    )
                action = payload['action']
                if user_id == 0:
                    # 允许游客查看
                    if action not in ['get_member_list', 'get_challenge']:
                        return jsonify(
                            code=10,
                            message='Not logged in',
                        )
                if action == 'get_member_list':
                    return jsonify(
                        code=0,
                        members=self.get_member_list(group_id),
                    )
                elif action == 'get_data':
                    return jsonify(
                        code=0,
                        groupData={
                            'group_id': group.group_id,
                            'group_name': group.group_name,
                            'game_server': group.game_server,
                            'level_4': group.level_4,
                        },
                        bossData=self._boss_data_dict(group),
                        selfData={
                            'is_admin': (is_member and user.authority_group < 100),
                            'user_id': user_id,
                            'today_sl': is_member and (is_member.last_save_slot == pcr_datetime(group.game_server)[0]),
                        }
                    )
                elif action == 'get_challenge':
                    d, _ = pcr_datetime(group.game_server)
                    report = self.get_report(
                        group_id,
                        None,
                        None,
                        pcr_datetime(group.game_server, payload['ts'])[0],
                    )
                    return jsonify(
                        code=0,
                        challenges=report,
                        today=d,
                    )
                elif action == 'get_user_challenge':
                    report = self.get_report(
                        group_id,
                        None,
                        payload['qqid'],
                        None,
                    )
                    try:
                        visited_user = User.get_by_id(payload['qqid'])
                    except peewee.DoesNotExist:
                        return jsonify(code=20, message='user not found')
                    return jsonify(
                        code=0,
                        challenges=report,
                        game_server=group.game_server,
                        user_info={
                            'qqid': payload['qqid'],
                            'nickname': visited_user.nickname,
                        }
                    )
                elif action == 'update_boss':
                    try:
                        bossData, notice = await asyncio.wait_for(
                            asyncio.shield(self._boss_status[group_id]),
                            timeout=30)
                        return jsonify(
                            code=0,
                            bossData=bossData,
                            notice=notice,
                        )
                    except asyncio.TimeoutError:
                        return jsonify(
                            code=1,
                            message='not changed',
                        )
                elif action == 'addrecord':
                    if payload['boss_num'] == None:
                        return jsonify(
                            code=1,
                            message='请选择上报的王',
                        )
                    
                    if payload['defeat']:
                        try:
                            status = self.challenge(group_id,
                                                    user_id,
                                                    True,
                                                    bossnum = int(payload['boss_num']),
                                                    damage = None,
                                                    behalfed = payload['behalf'],
                                                    is_continue = payload['is_continue'],
                                                    continue_num = 0,
                                                    extra_msg=payload.get(
                                                        'message'),
                                                    )
                        except ClanBattleError as e:
                            _logger.info('网页 失败 {} {} {}'.format(
                                user_id, group_id, action))
                            return jsonify(
                                code=10,
                                message=str(e),
                            )
                        _logger.info('网页 成功 {} {} {}'.format(
                            user_id, group_id, action))
                        if group.notification & 0x01:
                            asyncio.ensure_future(
                                self.api.send_group_msg(
                                    group_id=group_id,
                                    message=str(status),
                                )
                            )
                        return jsonify(
                            code=0,
                            bossData=self._boss_data_dict(group),
                        )
                    else:
                        try:
                            status = self.challenge(group_id,
                                                    user_id,
                                                    False,
                                                    bossnum = int(payload['boss_num']),
                                                    damage = int(payload['damage']),
                                                    behalfed = payload['behalf'],
                                                    is_continue = payload['is_continue'],
                                                    continue_num = 0,
                                                    extra_msg=payload.get(
                                                        'message'),
                                                    )
                        except ClanBattleError as e:
                            _logger.info('网页 失败 {} {} {}'.format(
                                user_id, group_id, action))
                            return jsonify(
                                code=10,
                                message=str(e),
                            )
                        _logger.info('网页 成功 {} {} {}'.format(
                            user_id, group_id, action))
                        if group.notification & 0x01:
                            asyncio.ensure_future(
                                self.api.send_group_msg(
                                    group_id=group_id,
                                    message=str(status),
                                )
                            )
                        return jsonify(
                            code=0,
                            bossData=self._boss_data_dict(group),
                        )
                elif action == 'undo':
                    try:
                        status = self.undo(
                            group_id, user_id)
                    except ClanBattleError as e:
                        _logger.info('网页 失败 {} {} {}'.format(
                            user_id, group_id, action))
                        return jsonify(
                            code=10,
                            message=str(e),
                        )
                    _logger.info('网页 成功 {} {} {}'.format(
                        user_id, group_id, action))
                    if group.notification & 0x02:
                        asyncio.ensure_future(
                            self.api.send_group_msg(
                                group_id=group_id,
                                message=str(status),
                            )
                        )
                    return jsonify(
                        code=0,
                        bossData=self._boss_data_dict(group),
                    )
                elif action == 'apply':
                    try:
                        status = self.apply_for_challenge(
                            group_id, user_id,
                            extra_msg=payload['extra_msg'],
                            appli_type=payload['appli_type'],
                        )
                    except ClanBattleError as e:
                        _logger.info('网页 失败 {} {} {}'.format(
                            user_id, group_id, action))
                        return jsonify(
                            code=10,
                            message=str(e),
                        )
                    _logger.info('网页 成功 {} {} {}'.format(
                        user_id, group_id, action))
                    if group.notification & 0x04:
                        asyncio.ensure_future(
                            self.api.send_group_msg(
                                group_id=group_id,
                                message=status.info,
                            )
                        )
                    return jsonify(
                        code=0,
                        bossData=self._boss_data_dict(group),
                    )
                elif action == 'cancelapply':
                    try:
                        status = self.cancel_application(
                            group_id, user_id)
                    except ClanBattleError as e:
                        _logger.info('网页 失败 {} {} {}'.format(
                            user_id, group_id, action))
                        return jsonify(
                            code=10,
                            message=str(e),
                        )
                    _logger.info('网页 成功 {} {} {}'.format(
                        user_id, group_id, action))
                    if group.notification & 0x08:
                        asyncio.ensure_future(
                            self.api.send_group_msg(
                                group_id=group_id,
                                message='boss挑战已可申请',
                            )
                        )
                    return jsonify(
                        code=0,
                        bossData=self._boss_data_dict(group),
                    )
                elif action == 'save_slot':
                    todaystatus = payload['today']
                    try:
                        self.save_slot(group_id, user_id,
                                       todaystatus=todaystatus)
                    except ClanBattleError as e:
                        _logger.info('网页 失败 {} {} {}'.format(
                            user_id, group_id, action))
                        return jsonify(
                            code=10,
                            message=str(e),
                        )
                    sw = '添加' if todaystatus else '删除'
                    _logger.info('网页 成功 {} {} {}'.format(
                        user_id, group_id, action))
                    if group.notification & 0x200:
                        asyncio.ensure_future(
                            self.api.send_group_msg(
                                group_id=group_id,
                                message=(self._get_nickname_by_qqid(user_id)
                                         + f'已{sw}SL'),
                            )
                        )
                    return jsonify(code=0, notice=f'已{sw}SL')
                elif action == 'get_subscribers':
                    subscribers = self.get_subscribe_list_new(group_id)
                    return jsonify(
                        code=0,
                        group_name=group.group_name,
                        subscribers=subscribers)
                elif action == 'addsubscribe':
                    boss_num = payload['boss_num']
                    message = payload.get('message')
                    if boss_num == 0 or boss_num == None:
                        return jsonify(code=0, notice='你挂的那个？')
                    else:
                        if boss_num ==1:
                            bossheal = group.a_health
                        if boss_num ==2:
                            bossheal = group.b_health
                        if boss_num ==3:
                            bossheal = group.c_health
                        if boss_num ==4:
                            bossheal = group.d_health
                        if boss_num ==5:
                            bossheal = group.e_health
                        if bossheal == 0 :
                            return jsonify(code=0, notice=str(boss_num)+'w都死了，你挂啥¿')
                    try:
                        self.add_subscribe(
                            group_id,
                            user_id,
                            boss_num,
                            message,
                        )
                    except ClanBattleError as e:
                        _logger.info('网页 失败 {} {} {}'.format(
                            user_id, group_id, action))
                        return jsonify(
                            code=10,
                            message=str(e),
                        )
                    _logger.info('网页 成功 {} {} {}'.format(
                        user_id, group_id, action))
                    if boss_num == 0:
                        return jsonify(code=0, notice='你挂的那个？')
                    else:
                        notice = '挂树成功'
                        if group.notification & 0x10:
                            asyncio.ensure_future(
                                self.api.send_group_msg(
                                    group_id=group_id,
                                    message='{}已挂树'.format(user.nickname),
                                )
                            )
                    return jsonify(code=0, notice=notice)
                elif action == 'cancelsubscribe':
                    counts = self.cancel_subscribe(
                        group_id,
                        user_id,
                    )
                    if counts == 0:
                        _logger.info('网页 失败 {} {} {}'.format(
                            user_id, group_id, action))
                        return jsonify(code=0, notice=('没有挂树记录'))
                    _logger.info('网页 成功 {} {} {}'.format(
                        user_id, group_id, action))
                    notice = '取消挂树成功'
                    if group.notification & 0x20:
                        asyncio.ensure_future(
                            self.api.send_group_msg(
                                group_id=group_id,
                                message='{}已取消挂树'.format(
                                    user.nickname),
                            )
                        )
                    return jsonify(code=0, notice=notice)
                elif action == 'modify':
                    if user.authority_group >= 100:
                        return jsonify(code=11, message='Insufficient authority')
                    try:
                        status = self.modify(
                            group_id,
                            cycle=payload['cycle'],
                            a_health=payload['a_health'],
                            b_health=payload['b_health'],
                            c_health=payload['c_health'],
                            d_health=payload['d_health'],
                            e_health=payload['e_health'],
                            a_issecond=payload['a_issecond'],
                            b_issecond=payload['b_issecond'],
                            c_issecond=payload['c_issecond'],
                            d_issecond=payload['d_issecond'],
                            e_issecond=payload['e_issecond'],
                        )
                    except ClanBattleError as e:
                        _logger.info('网页 失败 {} {} {}'.format(
                            user_id, group_id, action))
                        return jsonify(code=10, message=str(e))
                    _logger.info('网页 成功 {} {} {}'.format(
                        user_id, group_id, action))
                    if group.notification & 0x100:
                        asyncio.ensure_future(
                            self.api.send_group_msg(
                                group_id=group_id,
                                message=str(status),
                            )
                        )
                    return jsonify(
                        code=0,
                        bossData=self._boss_data_dict(group),
                    )
                elif action == 'send_remind':
                    if user.authority_group >= 100:
                        return jsonify(code=11, message='Insufficient authority')
                    sender = user_id
                    private = payload.get('send_private_msg', False)
                    if private and not self.setting['allow_bulk_private']:
                        return jsonify(
                            code=12,
                            message='私聊通知已禁用',
                        )
                    self.send_remind(group_id,
                                     payload['memberlist'],
                                     sender=sender,
                                     send_private_msg=private)
                    return jsonify(
                        code=0,
                        notice='发送成功',
                    )
                elif action == 'drop_member':
                    if user.authority_group >= 100:
                        return jsonify(code=11, message='Insufficient authority')
                    count = self.drop_member(group_id, payload['memberlist'])
                    return jsonify(
                        code=0,
                        notice=f'已删除{count}条记录',
                    )
                else:
                    return jsonify(code=32, message='unknown action')
            except KeyError as e:
                _logger.error(e)
                return jsonify(code=31, message='missing key: '+str(e))
            except asyncio.CancelledError:
                pass
            except Exception as e:
                _logger.exception(e)
                return jsonify(code=40, message='server error')

        @app.route(
            urljoin(self.setting['public_basepath'],
                    'clan/<int:group_id>/my/'),
            methods=['GET'])
        async def yobot_clan_user_auto(group_id):
            if 'yobot_user' not in session:
                return redirect(url_for('yobot_login', callback=request.path))
            return redirect(url_for(
                'yobot_clan_user',
                group_id=group_id,
                qqid=session['yobot_user'],
            ))

        @app.route(
            urljoin(self.setting['public_basepath'],
                    'clan/<int:group_id>/<int:qqid>/'),
            methods=['GET'])
        async def yobot_clan_user(group_id, qqid):
            if 'yobot_user' not in session:
                return redirect(url_for('yobot_login', callback=request.path))
            user = User.get_by_id(session['yobot_user'])
            group = Clan_group.get_or_none(group_id=group_id)
            if group is None:
                return await render_template('404.html', item='公会'), 404
            is_member = Clan_member.get_or_none(
                group_id=group_id, qqid=session['yobot_user'])
            if (not is_member and user.authority_group >= 10):
                return await render_template('clan/unauthorized.html')
            return await render_template(
                'clan/user.html',
                qqid=qqid,
            )

        @app.route(
            urljoin(self.setting['public_basepath'],
                    'clan/<int:group_id>/setting/'),
            methods=['GET'])
        async def yobot_clan_setting(group_id):
            if 'yobot_user' not in session:
                return redirect(url_for('yobot_login', callback=request.path))
            user = User.get_by_id(session['yobot_user'])
            group = Clan_group.get_or_none(group_id=group_id)
            if group is None:
                return await render_template('404.html', item='公会'), 404
            is_member = Clan_member.get_or_none(
                group_id=group_id, qqid=session['yobot_user'])
            if (not is_member):
                return await render_template(
                    'unauthorized.html',
                    limit='本公会成员',
                    uath='无')
            if (user.authority_group >= 100):
                return await render_template(
                    'unauthorized.html',
                    limit='公会战管理员',
                    uath='成员')
            return await render_template('clan/setting.html')

        @app.route(
            urljoin(self.setting['public_basepath'],
                    'clan/<int:group_id>/setting/api/'),
            methods=['POST'])
        async def yobot_clan_setting_api(group_id):
            if 'yobot_user' not in session:
                return jsonify(
                    code=10,
                    message='Not logged in',
                )
            user_id = session['yobot_user']
            user = User.get_by_id(user_id)
            group = Clan_group.get_or_none(group_id=group_id)
            if group is None:
                return jsonify(
                    code=20,
                    message='Group not exists',
                )
            is_member = Clan_member.get_or_none(
                group_id=group_id, qqid=session['yobot_user'])
            if (user.authority_group >= 100 or not is_member):
                return jsonify(
                    code=11,
                    message='Insufficient authority',
                )
            try:
                payload = await request.get_json()
                if payload is None:
                    return jsonify(
                        code=30,
                        message='Invalid payload',
                    )
                if payload.get('csrf_token') != session['csrf_token']:
                    return jsonify(
                        code=15,
                        message='Invalid csrf_token',
                    )
                action = payload['action']
                if action == 'get_setting':
                    return jsonify(
                        code=0,
                        groupData={
                            'group_name': group.group_name,
                            'game_server': group.game_server,
                            'battle_id': group.battle_id,
                        },
                        privacy=group.privacy,
                        notification=group.notification,
                    )
                elif action == 'put_setting':
                    group.game_server = payload['game_server']
                    group.notification = payload['notification']
                    group.privacy = payload['privacy']
                    group.save()
                    _logger.info('网页 成功 {} {} {}'.format(
                        user_id, group_id, action))
                    return jsonify(code=0, message='success')
                elif action == 'get_data_slot_record_count':
                    counts = self.get_data_slot_record_count(group_id)
                    _logger.info('网页 成功 {} {} {}'.format(
                        user_id, group_id, action))
                    return jsonify(code=0, message='success', counts=counts)
                # elif action == 'new_data_slot':
                #     self.new_data_slot(group_id)
                #     _logger.info('网页 成功 {} {} {}'.format(
                #         user_id, group_id, action))
                #     return jsonify(code=0, message='success')
                elif action == 'clear_data_slot':
                    battle_id = payload.get('battle_id')
                    self.clear_data_slot(group_id, battle_id)
                    _logger.info('网页 成功 {} {} {}'.format(
                        user_id, group_id, action))
                    return jsonify(code=0, message='success')
                elif action == 'switch_data_slot':
                    battle_id = payload['battle_id']
                    self.switch_data_slot(group_id, battle_id)
                    _logger.info('网页 成功 {} {} {}'.format(
                        user_id, group_id, action))
                    return jsonify(code=0, message='success')
                else:
                    return jsonify(code=32, message='unknown action')
            except KeyError as e:
                _logger.error(e)
                return jsonify(code=31, message='missing key: '+str(e))
            except Exception as e:
                _logger.exception(e)
                return jsonify(code=40, message='server error')

        @app.route(
            urljoin(self.setting['public_basepath'],
                    'clan/<int:group_id>/statistics/'),
            methods=['GET'])
        async def yobot_clan_statistics(group_id):
            if 'yobot_user' not in session:
                return redirect(url_for('yobot_login', callback=request.path))
            user = User.get_by_id(session['yobot_user'])
            group = Clan_group.get_or_none(group_id=group_id)
            if group is None:
                return await render_template('404.html', item='公会'), 404
            is_member = Clan_member.get_or_none(
                group_id=group_id, qqid=session['yobot_user'])
            if (not is_member and user.authority_group >= 10):
                return await render_template('clan/unauthorized.html')
            return await render_template(
                'clan/statistics.html',
                allow_api=(group.privacy & 0x2),
                apikey=group.apikey,
            )

        @app.route(
            urljoin(self.setting['public_basepath'],
                    'clan/<int:group_id>/statistics/<int:sid>/'),
            methods=['GET'])
        async def yobot_clan_boss(group_id, sid):
            if 'yobot_user' not in session:
                return redirect(url_for('yobot_login', callback=request.path))
            user = User.get_by_id(session['yobot_user'])
            group = Clan_group.get_or_none(group_id=group_id)
            if group is None:
                return await render_template('404.html', item='公会'), 404
            is_member = Clan_member.get_or_none(
                group_id=group_id, qqid=session['yobot_user'])
            if (not is_member and user.authority_group >= 10):
                return await render_template('clan/unauthorized.html')
            return await render_template(
                f'clan/statistics/statistics{sid}.html',
            )

        @app.route(
            urljoin(self.setting['public_basepath'],
                    'clan/<int:group_id>/statistics/api/'),
            methods=['GET'])
        async def yobot_clan_statistics_api(group_id):
            group = Clan_group.get_or_none(group_id=group_id)
            if group is None:
                return jsonify(code=20, message='Group not exists')
            apikey = request.args.get('apikey')
            if apikey:
                # 通过 apikey 外部访问
                if not (group.privacy & 0x2):
                    return jsonify(code=11, message='api not allowed')
                if apikey != group.apikey:
                    return jsonify(code=12, message='Invalid apikey')
            else:
                # 内部直接访问
                if 'yobot_user' not in session:
                    return jsonify(code=10, message='Not logged in')
                user = User.get_by_id(session['yobot_user'])
                is_member = Clan_member.get_or_none(
                    group_id=group_id, qqid=session['yobot_user'])
                if (not is_member and user.authority_group >= 10):
                    return jsonify(code=11, message='Insufficient authority')
            battle_id = request.args.get('battle_id')
            if battle_id is None:
                pass
            else:
                if battle_id.isdigit():
                    battle_id = int(battle_id)
                elif battle_id == 'all':
                    pass
                elif battle_id == 'current':
                    battle_id = None
                else:
                    return jsonify(code=20, message=f'unexceptd value "{battle_id}" for battle_id')
            # start = int(request.args.get('start')) if request.args.get('start') else None
            # end = int(request.args.get('end')) if request.args.get('end') else None
            # report = self.get_report(group_id, None, None, start, end)
            report = self.get_report(group_id, battle_id, None, None)
            # member_list = self.get_member_list(group_id)
            member_list = self.get_battle_member_list(group_id, battle_id)
            groupinfo = {
                'group_id': group.group_id,
                'group_name': group.group_name,
                'game_server': group.game_server,
                'battle_id': group.battle_id,
            },
            response = await make_response(jsonify(
                code=0,
                message='OK',
                api_version=1,
                challenges=report,
                groupinfo=groupinfo,
                members=member_list,
            ))
            if (group.privacy & 0x2):
                response.headers['Access-Control-Allow-Origin'] = '*'
            return response

        @app.route(
            urljoin(self.setting['public_basepath'],
                    'clan/<int:group_id>/progress/'),
            methods=['GET'])
        async def yobot_clan_progress(group_id):
            group = Clan_group.get_or_none(group_id=group_id)
            if group is None:
                return await render_template('404.html', item='公会'), 404
            if not(group.privacy & 0x1):
                if 'yobot_user' not in session:
                    return redirect(url_for('yobot_login', callback=request.path))
                user = User.get_by_id(session['yobot_user'])
                is_member = Clan_member.get_or_none(
                    group_id=group_id, qqid=session['yobot_user'])
                if (not is_member and user.authority_group >= 10):
                    return await render_template('clan/unauthorized.html')
            return await render_template(
                'clan/progress.html',
            )
