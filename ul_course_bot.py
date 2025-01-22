import json
import logging
import asyncio
import os
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Set
import pytz
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

# Configure logging format
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class UserStorage:
    """User storage management class"""

    def __init__(self, storage_path: str):
        self.storage_path = storage_path
        self.users = self._load_users()

    def _load_users(self) -> Set[int]:
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except FileNotFoundError:
            return set()

    def save_users(self):
        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(list(self.users), f, ensure_ascii=False)

    def add_user(self, user_id: int):
        self.users.add(user_id)
        self.save_users()

    def remove_user(self, user_id: int):
        self.users.discard(user_id)
        self.save_users()

    def get_users(self) -> Set[int]:
        return self.users


class CourseManager:
    """Course management class"""

    def __init__(self, config_path: str):
        self.config = self.load_config(config_path)
        self.tz = pytz.timezone(self.config['reminder_settings']['timezone'])
        self.semester_start = self.parse_semester_start()
        self.holidays = self.parse_holidays()

    def load_config(self, path: str) -> Dict:
        """Load and validate configuration file"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                self.validate_config(config)
                return config
        except Exception as e:
            logger.error(f"Failed to load config: {str(e)}")
            raise

    def validate_config(self, config: Dict):
        """Detailed configuration validation"""
        required_keys = ['semester_start', 'reminder_settings', 'courses', 'holidays']
        for key in required_keys:
            if key not in config:
                raise ValueError(f"Missing required configuration: {key}")

        reminder_required = ['pre_class_minutes', 'morning_summary_time',
                             'evening_preview_time', 'timezone']
        for key in reminder_required:
            if key not in config['reminder_settings']:
                raise ValueError(f"Missing reminder setting: {key}")

        try:
            pytz.timezone(config['reminder_settings']['timezone'])
        except pytz.exceptions.UnknownTimeZoneError:
            raise ValueError(f"Invalid timezone: {config['reminder_settings']['timezone']}")

    def parse_semester_start(self) -> datetime:
        """Parse semester start date"""
        date_str = self.config['semester_start']
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            return self.tz.localize(date_obj)
        except ValueError:
            raise ValueError("Semester start date should be in YYYY-MM-DD format")

    def parse_holidays(self) -> List[Dict]:
        """Parse holiday configurations"""
        holidays = []
        for holiday in self.config.get('holidays', []):
            try:
                start_date = datetime.strptime(holiday['start'], "%Y-%m-%d")
                end_date = datetime.strptime(holiday['end'], "%Y-%m-%d")
                holidays.append({
                    'name': holiday['name'],
                    'start': self.tz.localize(start_date),
                    'end': self.tz.localize(end_date),
                    'type': holiday['type'],
                    'description': holiday.get('description', '')
                })
            except (ValueError, KeyError) as e:
                logger.error(f"Holiday config parsing error: {str(e)}")
        return holidays

    def get_holiday_status(self, target_date: Optional[datetime] = None) -> Optional[Dict]:
        """Get holiday status for specified date"""
        if target_date is None:
            target_date = datetime.now(self.tz)

        target_date = target_date.replace(hour=0, minute=0, second=0, microsecond=0)

        for holiday in self.holidays:
            start = holiday['start'].replace(hour=0, minute=0, second=0, microsecond=0)
            end = holiday['end'].replace(hour=0, minute=0, second=0, microsecond=0)

            if start <= target_date <= end:
                return holiday

        return None

    def get_current_week(self, target_date: Optional[datetime] = None) -> int:
        """Calculate teaching week for specified date"""
        if target_date is None:
            target_date = datetime.now(self.tz)

        target_date = target_date.astimezone(self.tz)
        semester_start = self.semester_start.astimezone(self.tz)

        if target_date < semester_start:
            return 0

        delta = target_date - semester_start
        current_week = (delta.days // 7) + 1

        holiday = self.get_holiday_status(target_date)
        if holiday:
            logger.info(f"Current period: {holiday['name']}")

        return current_week

    def parse_time(self, time_str: str) -> tuple[time, time]:
        """Parse time range"""
        try:
            start_str, end_str = time_str.split('-')
            start_time = datetime.strptime(start_str.strip(), "%H:%M").time()
            end_time = datetime.strptime(end_str.strip(), "%H:%M").time()
            return start_time, end_time
        except ValueError:
            raise ValueError(f"Invalid time format: {time_str}")

    def is_week_valid(self, current_week: int, week_ranges: List[Dict]) -> bool:
        """Validate if current week is within effective range"""
        current_date = self.semester_start + timedelta(weeks=current_week - 1)
        holiday = self.get_holiday_status(current_date)

        if holiday:
            holiday_type = holiday['type']
            if holiday_type in ['reading_week', 'examination']:
                return False

        for week_range in week_ranges:
            start = week_range['start']
            end = week_range['end']
            week_type = week_range.get('type', 'all')

            if not (start <= current_week <= end):
                continue

            if week_type == 'odd' and current_week % 2 == 0:
                continue
            if week_type == 'even' and current_week % 2 == 1:
                continue

            return True
        return False

    def get_today_courses(self, target_date: Optional[datetime] = None) -> List[Dict]:
        """Get course list for specified date"""
        if target_date is None:
            target_date = datetime.now(self.tz)

        current_week = self.get_current_week(target_date)
        weekday = target_date.isoweekday()
        courses = []

        holiday = self.get_holiday_status(target_date)
        if holiday and holiday['type'] in ['reading_week', 'examination']:
            return []

        for course in self.config['courses']:
            for schedule in course['schedule']:
                effective_weeks = schedule.get('custom_weeks', course['default_weeks'])

                if not self.is_week_valid(current_week, effective_weeks):
                    continue

                if schedule['day'] != weekday:
                    continue

                try:
                    start_time, end_time = self.parse_time(schedule['time'])
                    courses.append({
                        "name": course['name'],
                        "code": course['code'],
                        "lecturer": course['lecturer'],
                        "start_time": start_time,
                        "end_time": end_time,
                        "location": schedule['location'],
                        "type": schedule['type']
                    })
                except Exception as e:
                    logger.error(f"Course parsing failed: {course['code']} - {str(e)}")

        return sorted(courses, key=lambda x: x['start_time'])


class CourseBot:
    """Course reminder bot class"""

    def __init__(self, token: str, config_path: str, storage_path: str):
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.manager = CourseManager(config_path)
        self.scheduler = AsyncIOScheduler(timezone=self.manager.tz)
        self.user_storage = UserStorage(storage_path)
        self.subscribed_users = self.user_storage.get_users()

        # Register command handlers
        self.dp.message.register(self.start_handler, Command("start"))
        self.dp.callback_query.register(self.button_handler)

    def create_main_keyboard(self) -> InlineKeyboardMarkup:
        """Create main menu keyboard"""
        keyboard = [
            [
                InlineKeyboardButton(text="📅 Today's Schedule", callback_data="today"),
                InlineKeyboardButton(text="📆 Tomorrow's Schedule", callback_data="tomorrow")
            ],
            [
                InlineKeyboardButton(text="🕒 Current Time", callback_data="time"),
                InlineKeyboardButton(text="📨 Subscribe", callback_data="subscribe")
            ],
            [
                InlineKeyboardButton(text="❌ Unsubscribe", callback_data="unsubscribe")
            ]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    async def start_handler(self, message: types.Message):
        """Handle /start command"""
        welcome_msg = (
            "📚 UL Course Reminder Bot\n\n"
            "Welcome! This bot will help you keep track of your course schedule. "
            "Use the buttons below to navigate:\n\n"
            "• 15-minute pre-class reminders\n"
            "• Daily schedule overview at 8:00\n"
            "• Next day preview at 20:00"
        )
        await message.answer(welcome_msg, reply_markup=self.create_main_keyboard())

    async def button_handler(self, callback_query: types.CallbackQuery):
        """Handle button clicks"""
        if callback_query.data == "subscribe":
            await self.subscribe_handler(callback_query)
        elif callback_query.data == "unsubscribe":
            await self.unsubscribe_handler(callback_query)
        elif callback_query.data == "today":
            await self.today_handler(callback_query)
        elif callback_query.data == "tomorrow":
            await self.tomorrow_handler(callback_query)
        elif callback_query.data == "time":
            await self.time_handler(callback_query)

    async def subscribe_handler(self, callback_query: types.CallbackQuery):
        """Handle subscription requests"""
        user_id = callback_query.from_user.id
        if user_id in self.subscribed_users:
            await callback_query.answer("You are already subscribed!")
        else:
            self.user_storage.add_user(user_id)
            self.subscribed_users = self.user_storage.get_users()
            await callback_query.answer("Successfully subscribed to course reminders!")

    async def unsubscribe_handler(self, callback_query: types.CallbackQuery):
        """Handle unsubscription requests"""
        user_id = callback_query.from_user.id
        if user_id in self.subscribed_users:
            self.user_storage.remove_user(user_id)
            self.subscribed_users = self.user_storage.get_users()
            await callback_query.answer("Successfully unsubscribed from reminders")
        else:
            await callback_query.answer("You are not subscribed!")

    async def today_handler(self, callback_query: types.CallbackQuery):
        """Handle today's schedule request"""
        courses = self.manager.get_today_courses()
        await callback_query.message.answer(
            self._format_courses_message(courses, "Today"),
            reply_markup=self.create_main_keyboard()
        )
        await callback_query.answer()

    async def tomorrow_handler(self, callback_query: types.CallbackQuery):
        """Handle tomorrow's schedule request"""
        tomorrow = datetime.now(self.manager.tz) + timedelta(days=1)
        courses = self.manager.get_today_courses(tomorrow)
        await callback_query.message.answer(
            self._format_courses_message(courses, "Tomorrow"),
            reply_markup=self.create_main_keyboard()
        )
        await callback_query.answer()

    async def time_handler(self, callback_query: types.CallbackQuery):
        """Handle current time info request"""
        now = datetime.now(self.manager.tz)
        current_week = self.manager.get_current_week()
        holiday = self.manager.get_holiday_status()

        time_info = (
            f"🕒 Current Time: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"🌍 Timezone: {self.manager.tz.zone}\n"
            f"📅 Current Week: Week {current_week}\n"
        )

        if holiday:
            time_info += f"📢 {holiday['name']}"
            if holiday['type'] in ['reading_week', 'examination']:
                time_info += " (Classes Suspended)"
            time_info += "\n"
        elif current_week == 0:
            semester_start = self.manager.semester_start
            days_until = (semester_start - now).days
            time_info += f"📢 Semester hasn't started\n⏰ {days_until} days until semester begins"

        await callback_query.message.answer(time_info, reply_markup=self.create_main_keyboard())
        await callback_query.answer()

    def _format_courses_message(self, courses: List[Dict], time_str: str) -> str:
        """Format course information message"""
        holiday = self.manager.get_holiday_status()
        if holiday and holiday['type'] in ['reading_week', 'examination']:
            return f"📅 {time_str} is {holiday['name']}, classes are suspended"

        if not courses:
            return f"🎉 No classes scheduled for {time_str}!"

        message = f"📅 Schedule for {time_str}:\n\n"
        for idx, course in enumerate(courses, 1):
            message += (
                f"{idx}. {course['name']} ({course['code']})\n"
                f"   ⏰ {course['start_time'].strftime('%H:%M')}-{course['end_time'].strftime('%H:%M')}\n"
                f"   👨🏫 {course['lecturer']}\n"
                f"   📍 {course['location']} | Type: {course['type']}\n\n"
            )
        return message

    async def send_reminder(self, message: str):
        """Send reminder to all subscribed users"""
        for user_id in self.subscribed_users:
            try:
                await self.bot.send_message(
                    user_id,
                    message,
                    reply_markup=self.create_main_keyboard()
                )
            except Exception as e:
                logger.error(f"Failed to send message to user {user_id}: {str(e)}")

    async def check_class_reminders(self):
        """Check and send pre-class reminders"""
        now = datetime.now(self.manager.tz)
        pre_minutes = self.manager.config['reminder_settings']['pre_class_minutes']

        for course in self.manager.get_today_courses():
            current_time = now.time()
            course_start = course['start_time']

            reminder_time = datetime.combine(now.date(), course_start) - timedelta(minutes=pre_minutes)
            reminder_time = self.manager.tz.localize(reminder_time)

            if abs((now - reminder_time).total_seconds()) <= 60:
                message = (
                    f"⏰ Class Reminder ({pre_minutes} minutes until start)\n"
                    f"📚 {course['name']} ({course['code']})\n"
                    f"👨🏫 Lecturer: {course['lecturer']}\n"
                    f"🕒 Time: {course_start.strftime('%H:%M')}-{course['end_time'].strftime('%H:%M')}\n"
                    f"📍 Location: {course['location']}\n"
                    f"📝 Type: {course['type']}"
                )
                await self.send_reminder(message)

    async def send_morning_summary(self):
        """Send morning course overview"""
        courses = self.manager.get_today_courses()
        await self.send_reminder(self._format_courses_message(courses, "Today"))

    async def send_evening_preview(self):
        """Send next day course preview"""
        tomorrow = datetime.now(self.manager.tz) + timedelta(days=1)
        courses = self.manager.get_today_courses(tomorrow)
        await self.send_reminder(self._format_courses_message(courses, "Tomorrow"))

    async def schedule_jobs(self):
        """Configure scheduled tasks"""
        settings = self.manager.config['reminder_settings']

        # Pre-class reminders (check every minute)
        self.scheduler.add_job(
            self.check_class_reminders,
            'cron',
            minute='*',
            timezone=self.manager.tz
        )

        # Morning summary
        morning_time = settings['morning_summary_time'].split(':')
        self.scheduler.add_job(
            self.send_morning_summary,
            CronTrigger(
                hour=morning_time[0],
                minute=morning_time[1],
                timezone=self.manager.tz
            )
        )

        # Evening preview
        evening_time = settings['evening_preview_time'].split(':')
        self.scheduler.add_job(
            self.send_evening_preview,
            CronTrigger(
                hour=evening_time[0],
                minute=evening_time[1],
                timezone=self.manager.tz
            )
        )

    async def start(self):
        """Start bot and scheduler"""
        try:
            await self.schedule_jobs()
            self.scheduler.start()
            logger.info("Scheduler started successfully")
            await self.dp.start_polling(self.bot)
        except Exception as e:
            logger.error(f"Bot startup failed: {str(e)}")
            raise


async def main():
    # Configure parameters
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    CONFIG_PATH = "courses_config.json"
    STORAGE_PATH = "users.json"

    # Ensure storage file exists
    if not os.path.exists(STORAGE_PATH):
        with open(STORAGE_PATH, 'w', encoding='utf-8') as f:
            json.dump([], f)

    try:
        # Initialize and start bot
        bot = CourseBot(BOT_TOKEN, CONFIG_PATH, STORAGE_PATH)
        await bot.start()
    except Exception as e:
        logger.error(f"Program startup failed: {str(e)}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
