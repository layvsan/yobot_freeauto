from peewee import *
from playhouse.migrate import SqliteMigrator, migrate

from .web_util import rand_string

_db = SqliteDatabase(None)
_version = 21   # 目前版本

MAX_TRY_TIMES = 3


class _BaseModel(Model):
    class Meta:
        database = _db


class Admin_key(_BaseModel):
    key = TextField(primary_key=True)
    valid = BooleanField()
    key_used = BooleanField()
    cookie = TextField(index=True)
    create_time = TimestampField()


class User(_BaseModel):
    qqid = BigIntegerField(primary_key=True)
    nickname = TextField(null=True)

    # 1:主人 10:公会战管理员 100:成员
    authority_group = IntegerField(default=100)

    privacy = IntegerField(default=MAX_TRY_TIMES)   # 密码错误次数
    clan_group_id = BigIntegerField(null=True)
    last_login_time = BigIntegerField(default=0)
    last_login_ipaddr = IPField(default='0.0.0.0')
    password = FixedCharField(max_length=64, null=True)
    must_change_password = BooleanField(default=True)
    notify_preference = IntegerField(default=1)  # 预约提醒偏好，1：提醒一次，2：始终提醒
    login_code = FixedCharField(max_length=6, null=True)
    login_code_available = BooleanField(default=False)
    login_code_expire_time = BigIntegerField(default=0)
    salt = CharField(max_length=16, default=rand_string)
    deleted = BooleanField(default=False)


class User_login(_BaseModel):
    qqid = BigIntegerField()
    auth_cookie = FixedCharField(max_length=64)
    auth_cookie_expire_time = BigIntegerField(default=0)
    last_login_time = BigIntegerField(default=0)
    last_login_ipaddr = IPField(default='0.0.0.0')

    class Meta:
        primary_key = CompositeKey('qqid', 'auth_cookie')


class Clan_group(_BaseModel):
    group_id = BigIntegerField(primary_key=True)
    group_name = TextField(null=True)
    privacy = IntegerField(default=2)  # 0x1：允许游客查看出刀表，0x2：允许api调用出刀表
    game_server = CharField(max_length=2, default='cn')
    notification = IntegerField(default=0xffff)  # 需要接收的通知
    level_4 = BooleanField(default=True)  # 公会战是否存在4阶段
    battle_id = IntegerField(default=0)
    apikey = CharField(max_length=16, default=rand_string)
    boss_cycle = SmallIntegerField(default=1)
    # boss_num = SmallIntegerField(default=1)
    # boss_health = BigIntegerField(default=6000000)
    a_health = BigIntegerField(default=6000000)
    b_health = BigIntegerField(default=8000000)
    c_health = BigIntegerField(default=10000000)
    d_health = BigIntegerField(default=12000000)
    e_health = BigIntegerField(default=15000000)
    a_issecond = BooleanField(default=False)
    b_issecond = BooleanField(default=False)
    c_issecond = BooleanField(default=False)
    d_issecond = BooleanField(default=False)
    e_issecond = BooleanField(default=False)
    a_commit = TextField(null=True)
    b_commit = TextField(null=True)
    c_commit = TextField(null=True)
    d_commit = TextField(null=True)
    e_commit = TextField(null=True)
    challenging_member_qq_id = IntegerField(null=True)
    boss_lock_type = IntegerField(default=0)  # 1出刀中，2其他
    challenging_start_time = BigIntegerField(default=0)
    challenging_comment = TextField(null=True)
    deleted = BooleanField(default=False)


class Clan_member(_BaseModel):
    group_id = BigIntegerField(index=True)
    qqid = BigIntegerField(index=True)
    role = IntegerField(default=100)
    last_save_slot = IntegerField(null=True)
    remaining_status = TextField(null=True)

    class Meta:
        primary_key = CompositeKey('group_id', 'qqid')


class Clan_challenge(_BaseModel):
    cid = AutoField(primary_key=True)
    bid = IntegerField(default=0)
    gid = BigIntegerField()
    qqid = BigIntegerField(index=True)
    challenge_pcrdate = IntegerField()
    challenge_pcrtime = IntegerField()
    boss_cycle = SmallIntegerField()
    boss_num = SmallIntegerField()
    boss_health_ramain = BigIntegerField()  # MMP拼错了，没法改了
    challenge_damage = BigIntegerField()
    is_continue = BooleanField()  # 此刀是结余刀
    is_used = BooleanField(default=False)  # 此刀是结余刀
    is_second = BooleanField()  # 此刀是副圈刀
    continue_num = IntegerField(default=0)# 尾刀编号
    message = TextField(null=True)
    behalf = IntegerField(null=True)

    class Meta:
        indexes = (
            (('bid', 'gid'), False),
            (('qqid', 'challenge_pcrdate'), False),
            (('bid', 'gid', 'challenge_pcrdate'), False),
        )


class Clan_subscribe(_BaseModel):
    sid = AutoField(primary_key=True)
    gid = BigIntegerField(index=True)
    qqid = IntegerField()
    subscribe_item = SmallIntegerField()
    message = TextField(null=True)
    create_time = BigIntegerField(default=0)

    class Meta:
        indexes = (
            (('gid', 'qqid', 'subscribe_item'), False),
        )
        
class Clan_subscribe_new(_BaseModel):
    sid = AutoField(primary_key=True)
    gid = BigIntegerField(index=True)
    qqid = IntegerField()
    cycle = IntegerField()
    subscribe_item = SmallIntegerField()
    message = TextField(null=True)
    create_time = BigIntegerField(default=0)

    class Meta:
        indexes = (
            (('gid', 'qqid', 'subscribe_item'), False),
        )
        
class Clan_subscribe_layv(_BaseModel):
    sid = AutoField(primary_key=True)
    gid = BigIntegerField(index=True)
    qqid = IntegerField()
    subscribe_item = SmallIntegerField()
    message = TextField(null=True)
    create_time = BigIntegerField(default=0)

    class Meta:
        indexes = (
            (('gid', 'qqid', 'subscribe_item'), False),
        )


class Character(_BaseModel):
    chid = IntegerField(primary_key=True)
    name = CharField(max_length=64)
    frequent = BooleanField(default=True)


class Chara_nickname(_BaseModel):
    name = CharField(max_length=64, primary_key=True)
    chid = IntegerField()


class User_box(_BaseModel):
    qqid = BigIntegerField()
    chid = IntegerField()
    last_use = IntegerField()
    rank = IntegerField()
    stars = IntegerField()
    equit = BooleanField()

    class Meta:
        primary_key = CompositeKey('qqid', 'chid')


class DB_schema(_BaseModel):
    key = CharField(max_length=64, primary_key=True)
    value = TextField()


def init(sqlite_filename):
    _db.init(
        database=sqlite_filename,
        pragmas={
            'journal_mode': 'wal',
            'cache_size': -1024 * 64,
        },
    )

    old_version = 1
    if not DB_schema.table_exists():
        DB_schema.create_table()
        DB_schema.create(key='version', value=str(_version))
    else:
        old_version = int(DB_schema.get(key='version').value)
    
    if not User.table_exists():
        Admin_key.create_table()
        User.create_table()
        User_login.create_table()
        Clan_group.create_table()
        Clan_member.create_table()
        Clan_challenge.create_table()
        Clan_subscribe.create_table()
        Clan_subscribe_new.create_table()
        Clan_subscribe_layv.create_table()
        Character.create_table()
        User_box.create_table()
        old_version = _version
    if old_version > _version:
        print('数据库版本高于程序版本，请升级yobot')
        raise SystemExit()
    if old_version < _version:
        print('正在升级数据库')
        db_upgrade(old_version)
        print('数据库升级完毕')


def db_upgrade(old_version):
    migrator = SqliteMigrator(_db)
    if old_version < 2:
        User_login.create_table()
        migrate(
            migrator.add_column('clan_member', 'remaining_status',
                                TextField(null=True)),
            migrator.add_column('clan_challenge', 'message',
                                TextField(null=True)),
            migrator.add_column('clan_group', 'boss_lock_type',
                                IntegerField(default=0)),
            migrator.drop_column('user', 'last_save_slot'),
        )
    if old_version < 3:
        migrate(
            migrator.drop_column('user', 'auth_cookie'),
            migrator.drop_column('user', 'auth_cookie_expire_time'),
        )
    if old_version < 4:
        migrate(
            migrator.add_column('user', 'deleted',
                                BooleanField(default=False)),
        )
    if old_version < 5:
        migrate(
            migrator.add_column('user', 'must_change_password',
                                BooleanField(default=True)),
        )
    if old_version < 7:
        migrate(
            migrator.drop_column('clan_challenge', 'comment'),
            migrator.add_column('clan_challenge', 'behalf',
                                IntegerField(null=True)),
            migrator.drop_column('clan_subscribe', 'comment'),
            migrator.add_column('clan_subscribe', 'message',
                                TextField(null=True)),
            migrator.drop_column('Clan_subscribe_layv', 'comment'),
            migrator.add_column('Clan_subscribe_layv', 'message',
                                TextField(null=True)),
            migrator.add_column('clan_group', 'apikey',
                                CharField(max_length=16, default=rand_string)),
        )
    if old_version < 8:
        migrate(
            migrator.add_column('clan_group', 'deleted',
                                BooleanField(default=False)),
            migrator.add_column('clan_group', 'battle_id',
                                IntegerField(default=0)),
            migrator.add_column('clan_challenge', 'bid',
                                IntegerField(default=0)),
            migrator.add_index('clan_challenge', ('bid', 'gid'), False)
        )
    if old_version < 9:
        migrate(
            migrator.add_index('clan_member', ('qqid',), False)
        )
    if old_version < 10:
        migrate(
            migrator.add_index('clan_member', ('group_id',), False),
            migrator.add_index('clan_subscribe', ('gid',), False),
            migrator.add_index('Clan_subscribe_layv', ('gid',), False),
            migrator.add_index('clan_challenge', ('qqid',), False),
            migrator.add_index('clan_challenge', ('qqid', 'challenge_pcrdate'), False),
            migrator.add_index('clan_challenge', ('bid', 'gid', 'challenge_pcrdate'), False),
        )
    if old_version < 11:
        migrate(
            migrator.add_column('user', 'notify_preference',
                                IntegerField(default=1)),
        )
    if old_version < 12:
        migrate(
            migrator.add_column('clan_group', 'a_health',
                                IntegerField(default=6000000)),
            migrator.add_column('clan_group', 'b_health',
                                IntegerField(default=8000000)),
            migrator.add_column('clan_group', 'c_health',
                                IntegerField(default=10000000)),
            migrator.add_column('clan_group', 'd_health',
                                IntegerField(default=12000000)),
            migrator.add_column('clan_group', 'e_health',
                                IntegerField(default=15000000)),
            migrator.drop_column('clan_group', 'boss_health'),
            migrator.drop_column('clan_group', 'boss_num'),
        )
    if old_version < 14:
        migrate(
            migrator.add_column('clan_group', 'a_commit',
                                TextField(null=True)),
            migrator.add_column('clan_group', 'b_commit',
                                TextField(null=True)),
            migrator.add_column('clan_group', 'c_commit',
                                TextField(null=True)),
            migrator.add_column('clan_group', 'd_commit',
                                TextField(null=True)),
            migrator.add_column('clan_group', 'e_commit',
                                TextField(null=True)),
        )
    if old_version < 15:
        migrate(
            migrator.add_column('clan_group', 'a_issecond',
                                BooleanField(default=False)),
            migrator.add_column('clan_group', 'b_issecond',
                                BooleanField(default=False)),
            migrator.add_column('clan_group', 'c_issecond',
                                BooleanField(default=False)),
            migrator.add_column('clan_group', 'd_issecond',
                                BooleanField(default=False)),
            migrator.add_column('clan_group', 'e_issecond',
                                BooleanField(default=False)),
        )
    if old_version < 17:
        migrate(
            migrator.add_column('clan_challenge', 'is_second',
                                BooleanField(default=False)),
        )
    if old_version < 18:
        migrate(
            migrator.add_column('clan_subscribe', 'create_time',
                                BigIntegerField(default=0)),
            migrator.add_column('Clan_subscribe_layv', 'create_time',
                                BigIntegerField(default=0)),
        )
    if old_version < 20:
        migrate(
            migrator.add_column('clan_challenge', 'continue_num',
                                IntegerField(default=0)),
        )
    if old_version < 21:
        migrate(
            migrator.add_column('clan_challenge', 'is_used',
                                BooleanField(default=False)),
        )
        
    DB_schema.replace(key='version', value=str(_version)).execute()
