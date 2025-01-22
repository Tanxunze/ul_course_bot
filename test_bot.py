import pytest
import json
from datetime import datetime, time, timedelta
import pytz
from unittest.mock import Mock, patch, MagicMock
from ul_course_bot import CourseManager, CourseBot, UserStorage
from unittest.mock import AsyncMock

@pytest.fixture
def test_config():
    return {
        "semester_start": "2025-01-27",
        "reminder_settings": {
            "timezone": "Europe/Dublin",
            "pre_class_minutes": 15,
            "morning_summary_time": "08:00",
            "evening_preview_time": "20:00"
        },
        "courses": [
            {
                "name": "Project Management and Practice",
                "code": "CS4457",
                "lecturer": "Ben Bartlett",
                "default_weeks": [
                    {"start": 1, "end": 11, "type": "all"},
                    {"start": 13, "end": 13, "type": "all"}
                ],
                "schedule": [
                    {
                        "day": 1,
                        "time": "09:00-11:00",
                        "location": "P1033",
                        "type": "LEC"
                    }
                ]
            }
        ]
    }


@pytest.fixture
def config_file(tmp_path, test_config):
    config_path = tmp_path / "test_config.json"
    with open(config_path, "w", encoding='utf-8') as f:
        json.dump(test_config, f)
    return str(config_path)


@pytest.fixture
def storage_file(tmp_path):
    storage_path = tmp_path / "test_users.json"
    with open(storage_path, "w", encoding='utf-8') as f:
        json.dump([], f)
    return str(storage_path)


@pytest.fixture
def course_manager(config_file):
    return CourseManager(config_file)


def test_user_storage_operations(storage_file):
    """测试用户存储操作"""
    storage = UserStorage(storage_file)

    # 测试添加用户
    storage.add_user(123)
    assert 123 in storage.get_users()

    # 测试重复添加
    storage.add_user(123)
    assert len(storage.get_users()) == 1

    # 测试移除用户
    storage.remove_user(123)
    assert 123 not in storage.get_users()

    # 测试移除不存在的用户
    storage.remove_user(456)
    assert len(storage.get_users()) == 0


def test_parse_semester_start(course_manager):
    """测试学期开始日期解析"""
    tz = pytz.timezone("Europe/Dublin")
    expected_date = tz.localize(datetime(2025, 1, 27))
    assert course_manager.semester_start == expected_date


def test_parse_time(course_manager):
    """测试时间解析"""
    start, end = course_manager.parse_time("09:00-11:00")
    assert isinstance(start, time)
    assert isinstance(end, time)
    assert start.hour == 9
    assert end.hour == 11


def test_is_week_valid(course_manager):
    """测试周数有效性判断"""
    week_ranges = [
        {"start": 1, "end": 11, "type": "all"},
        {"start": 13, "end": 13, "type": "all"}
    ]
    assert course_manager.is_week_valid(1, week_ranges)
    assert course_manager.is_week_valid(11, week_ranges)
    assert not course_manager.is_week_valid(12, week_ranges)
    assert course_manager.is_week_valid(13, week_ranges)
    assert not course_manager.is_week_valid(14, week_ranges)


def test_get_current_week(course_manager):
    """测试当前周计算"""
    tz = pytz.timezone("Europe/Dublin")

    # 测试学期开始前
    test_date = tz.localize(datetime(2025, 1, 20))
    with patch('datetime.datetime') as mock_datetime:
        mock_datetime.now = Mock(return_value=test_date)
        week = course_manager.get_current_week()
        print(f"\n测试学期开始前: target_date={test_date}, week={week}")
        assert week == 0

    # 测试学期第一周
    test_date = tz.localize(datetime(2025, 1, 27))
    with patch('datetime.datetime') as mock_datetime:
        mock_datetime.now = Mock(return_value=test_date)
        week = course_manager.get_current_week()
        print(f"\n测试学期第一周: target_date={test_date}, semester_start={course_manager.semester_start}")
        print(f"week={week}")
        assert week == 1

    # 测试学期中间
    test_date = tz.localize(datetime(2025, 2, 10))  # 第3周
    with patch('datetime.datetime') as mock_datetime:
        mock_datetime.now = Mock(return_value=test_date)
        week = course_manager.get_current_week()
        print(f"\n测试第三周: target_date={test_date}, week={week}")
        assert week == 3


def test_get_current_week_alternative(course_manager):
    """使用不同的方式测试周数计算"""
    tz = pytz.timezone("Europe/Dublin")

    # 直接传入日期进行测试，不使用 mock
    test_date = tz.localize(datetime(2025, 1, 20))
    assert course_manager.get_current_week(test_date) == 0

    test_date = tz.localize(datetime(2025, 1, 27))
    assert course_manager.get_current_week(test_date) == 1

    test_date = tz.localize(datetime(2025, 2, 10))
    assert course_manager.get_current_week(test_date) == 3


@pytest.mark.asyncio
async def test_bot_handlers(config_file, storage_file):
    """测试机器人命令处理"""
    test_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"

    mock_bot = Mock()
    with patch('aiogram.Bot', return_value=mock_bot) as MockBot:
        bot = CourseBot(test_token, config_file, storage_file)

        # 创建消息模拟对象
        message = Mock()
        message.chat.id = 123
        # 创建异步的 answer 方法
        message.answer = AsyncMock()

        # 测试 start 命令
        await bot.start_handler(message)
        message.answer.assert_called_once()

        # 测试订阅
        await bot.subscribe_handler(message)
        assert 123 in bot.subscribed_users

        # 测试取消订阅
        await bot.unsubscribe_handler(message)
        assert 123 not in bot.subscribed_users


@pytest.mark.asyncio
async def test_course_notifications(config_file, storage_file):
    """测试课程提醒功能"""
    test_token = "7613902246:AAF8yHzFXibLc8pHS8u7M8ZH3UfXPHSlTP0"

    mock_bot = Mock()
    with patch('aiogram.Bot', return_value=mock_bot) as MockBot:
        bot = CourseBot(test_token, config_file, storage_file)
        bot.user_storage.add_user(123)

        # 测试早晨概览
        await bot.send_morning_summary()

        # 测试晚间预览
        await bot.send_evening_preview()

        # 测试课前提醒
        await bot.check_class_reminders()


def test_get_today_courses(course_manager):
    """测试获取当日课程"""
    tz = pytz.timezone("Europe/Dublin")

    # 测试周一的课程
    test_date = tz.localize(datetime(2025, 1, 27, 9, 0))
    courses = course_manager.get_today_courses(test_date)

    assert len(courses) == 1
    assert courses[0]["name"] == "Project Management and Practice"
    assert courses[0]["code"] == "CS4457"
    assert courses[0]["start_time"].hour == 9
    assert courses[0]["end_time"].hour == 11

    # 测试周末的课程（应该为空）
    weekend_date = tz.localize(datetime(2025, 2, 1, 9, 0))
    courses = course_manager.get_today_courses(weekend_date)
    assert len(courses) == 0


if __name__ == '__main__':
    pytest.main(['-v'])