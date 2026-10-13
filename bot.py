from __future__ import annotations

import asyncio
import logging
import re

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from schedule_parser import (
    RUS_WEEKDAYS,
    Timetable,
    find_group_lessons,
    get_timetable,
    get_distance,
    guess_groups,
    normalize_day,
    today_sheet,
    _norm,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("bot")

DAY_KEYS = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ"]

TPD_ERROR = "Не удалось загрузить расписание. Попробуйте ещё раз чуть позже."


def _make_bot(token: str) -> Bot:
    session = None
    proxy = settings.BOT_PROXY
    if proxy:
        from aiogram.client.session.aiohttp import AiohttpSession

        log.info("Бот использует прокси: %s", proxy)
        session = AiohttpSession(proxy=proxy)
    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )


bot = _make_bot(settings.BOT_TOKEN)
dp = Dispatcher()

MAX_MSG = 4000


async def _timetable() -> Timetable:
    return await asyncio.to_thread(get_timetable)


def fmt_day_schedule(tt: Timetable, group: str, day_key: str) -> str:
    lessons = find_group_lessons(tt, group, [day_key])
    day_rus = RUS_WEEKDAYS.get(day_key, day_key)
    head = f"📅 <b>Расписание {group.upper()}</b>\n<b>{day_rus}</b>"
    note = tt.date_by_day.get(day_key, "")
    if note:
        head += f"\n<code>{note}</code>"
    if not lessons:
        return head + "\n\n<i>Пар нет</i>"
    lines = [head, ""]
    for l in sorted(lessons, key=lambda x: x.number or 0):
        num = f"🔹 {l.number} пара" if l.number else "🔸 пара"
        cab = f"каб. {l.cab}" if l.cab else "каб. —"
        lines.append(f"{num} — {l.teacher}\n\u3000\u200b\u3000└ {cab}")
    return "\n".join(lines)


def fmt_replacements(tt: Timetable, group: str, day_key: str) -> str:
    day_rus = RUS_WEEKDAYS.get(day_key, day_key)
    repls = [r for r in tt.replacements if _norm(r.group) == _norm(group) and r.day_sheet == day_key]
    head = f"🔁 <b>Замены {group.upper()}</b>\n<b>{day_rus}</b>"
    if not repls:
        return head + "\n\n<i>Замен нет</i>"
    lines = [head, ""]
    for r in sorted(repls, key=_para_key):
        para = f"<b>{r.para} пара</b>" if r.para else "<i>пара не указана</i>"
        lines.append(para)
        if r.removed and any(r.removed.values()):
            rm = " · ".join(x for x in [r.removed.get("disc"), r.removed.get("teacher"), r.removed.get("cab")] if x)
            lines.append(f"\u3000❌ Снято: <s>{rm}</s>")
        if r.assigned and any(r.assigned.values()):
            asg = " · ".join(x for x in [r.assigned.get("disc"), r.assigned.get("teacher"), r.assigned.get("cab")] if x)
            lines.append(f"\u3000✅ Назначено: {asg}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _para_key(r) -> int:
    m = re.search(r"(\d+)", r.para or "")
    return int(m.group(1)) if m else 99


def fmt_distance(tt: Timetable, group: str) -> str:
    d = get_distance(tt, group)
    if not d:
        return f"🔗 Дистанционное занятие для <b>{group.upper()}</b> не найдено."
    lines = [
        "🔗 <b>Дистанционное занятие</b>",
        f"Группа: <b>{d.group}</b>",
        f'Встреча: <a href="{d.link}">{d.link}</a>',
    ]
    if d.curator:
        lines.append(f"Куратор: {d.curator}")
    return "\n".join(lines)


def group_suggest_keyboard(tt: Timetable, partial: str, prefix: str) -> InlineKeyboardMarkup | None:
    matches = guess_groups(tt, partial)[:12]
    if not matches:
        return None
    b = InlineKeyboardBuilder()
    for g in matches:
        b.button(text=g, callback_data=f"{prefix}:{g}")
    b.adjust(2)
    return b.as_markup()


def parse_args(text: str):
    parts = re.sub(r"\s+", " ", (text or "").strip()).split(" ", 1)
    if not parts[0]:
        return "", None
    group = parts[0].strip()
    day_raw = parts[1].strip() if len(parts) > 1 else ""
    day_key = normalize_day(day_raw) if day_raw else None
    return group, day_key


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я бот расписания колледжа сервиса и дизайна.\n\n"
        "Как пользоваться:\n"
        "• <b>/расписание СОСУ-24-1</b> — расписание на сегодня\n"
        "• <b>/расписание СОСУ-24-1 Пн</b> — расписание в понедельник\n"
        "• <b>/замены СОСУ-24-1</b> — замены\n"
        "• <b>/дистант СОСУ-24-1</b> — ссылка на дистанционное занятие\n\n"
        "Группу можно вводить в любой раскладке и в любом регистре."
    )


@dp.message(Command("help", "помощь"))
async def cmd_help(message: Message):
    await message.answer(
        "🆘 <b>Помощь</b>\n\n"
        "Команды бота:\n"
        "/расписание ГРУППА — на сегодня (кнопками можно сменить день)\n"
        "/расписание ГРУППА ДЕНЬ — на конкретный день\n"
        "/замены ГРУППА [ДЕНЬ]\n"
        "/дистант ГРУППА — ссылка на встречу\n"
        "/помощь — эта справка\n\n"
        "Примеры:\n"
        "  /расписание СОСУ-24-1\n"
        "  /расписание СосУ-24-1 ПТ\n"
        "  /замены СДЗН-25-1 Ср"
    )


def _day_switch_keyboard(group: str, current: str, prefix: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key in DAY_KEYS:
        if key == current:
            b.button(text=f"● {RUS_WEEKDAYS[key]}", callback_data="noop")
        else:
            b.button(text=RUS_WEEKDAYS[key], callback_data=f"{prefix}:{group}:{key}")
    b.adjust(3)
    return b.as_markup()


@dp.message(Command("расписание", "schedule"))
async def cmd_schedule(message: Message, command: CommandObject):
    try:
        tt = await _timetable()
    except Exception:
        await message.answer(TPD_ERROR)
        return

    group, day_key = parse_args(command.args)
    if not group:
        await message.answer("📋 Укажите группу. Например:\n<code>/расписание СОСУ-24-1 Пн</code>")
        return

    if day_key is None:
        day_key = today_sheet()
        if day_key not in DAY_KEYS:
            day_key = "ПН"

    matches = guess_groups(tt, group.split(" ")[0])
    if not matches:
        await message.answer(
            f"📅 Группа <b>{group.upper()}</b> не найдена. Проверьте написание."
        )
        return

    out = fmt_day_schedule(tt, group, day_key)
    kb = _day_switch_keyboard(group, day_key, "sch")
    await message.answer(out[:MAX_MSG], reply_markup=kb)


@dp.message(Command("замены", "replacements", "zameny"))
async def cmd_replacements(message: Message, command: CommandObject):
    try:
        tt = await _timetable()
    except Exception:
        await message.answer(TPD_ERROR)
        return

    group, day_key = parse_args(command.args)
    if not group:
        await message.answer("🔁 Укажите группу. Например:\n<code>/замены СДЗН-25-1</code>")
        return

    if day_key is None:
        day_key = today_sheet()
        if day_key not in DAY_KEYS:
            day_key = "ПН"

    if not guess_groups(tt, group.split(" ")[0]):
        await message.answer(f"🔁 Группа <b>{group.upper()}</b> не найдена. Проверьте написание.")
        return

    out = fmt_replacements(tt, group, day_key)
    kb = _day_switch_keyboard(group, day_key, "rep")
    await message.answer(out[:MAX_MSG], reply_markup=kb)


@dp.message(Command("дистант", "distance", "dist"))
async def cmd_distance(message: Message, command: CommandObject):
    try:
        tt = await _timetable()
    except Exception:
        await message.answer(TPD_ERROR)
        return

    group, _ = parse_args(command.args)
    if not group:
        await message.answer("🔗 Укажите группу. Например:\n<code>/дистант СДЗН-25-1</code>")
        return
    out = fmt_distance(tt, group)
    await message.answer(out[:MAX_MSG])


@dp.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


@dp.callback_query(F.data.startswith("sch:"))
async def cb_schedule_day(cb: CallbackQuery):
    _, group, day_key = cb.data.split(":")
    try:
        tt = await _timetable()
    except Exception:
        await cb.answer(TPD_ERROR, show_alert=True)
        return
    out = fmt_day_schedule(tt, group, day_key)
    kb = _day_switch_keyboard(group, day_key, "sch")
    await cb.message.edit_text(out[:MAX_MSG], reply_markup=kb, disable_web_page_preview=True)
    await cb.answer()


@dp.callback_query(F.data.startswith("rep:"))
async def cb_replacements_day(cb: CallbackQuery):
    _, group, day_key = cb.data.split(":")
    try:
        tt = await _timetable()
    except Exception:
        await cb.answer(TPD_ERROR, show_alert=True)
        return
    out = fmt_replacements(tt, group, day_key)
    kb = _day_switch_keyboard(group, day_key, "rep")
    await cb.message.edit_text(out[:MAX_MSG], reply_markup=kb, disable_web_page_preview=True)
    await cb.answer()


@dp.message()
async def fallback(message: Message):
    await message.answer(
        "Не понял команду. Воспользуйтесь /помощь или нажмите /start.\n"
        "Пример: <code>/расписание СОСУ-24-1 Пн</code>"
    )


def main() -> None:
    if not settings.BOT_TOKEN:
        raise SystemExit(
            "Не задан BOT_TOKEN. Получите токен у @BotFather и задайте "
            "переменную окружения BOT_TOKEN либо впишите его в config.py."
        )
    log.info("Запускаю бота...")
    dp.run_polling(bot)


if __name__ == "__main__":
    main()